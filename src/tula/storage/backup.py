"""Offline, path-preserving runtime backup: python -m tula.storage.backup --help."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import zipfile
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from ..config import runtime_root

ROOTS = {"data", "out", "rules"}
DATABASE = "data/tula.db"
FORMAT = "tula-offline-backup-v1"
MAX_FILES = 100_000
MAX_BYTES = 50 * 1024**3
MAX_MANIFEST = 16 * 1024**2
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_DRAFT_ID = re.compile(r"[0-9a-f]{32}\Z")
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


def _plain(path: Path) -> Path:
    """Reject symlinks, Windows junctions and other reparse points before resolve."""
    path = path.expanduser().absolute()
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("Runtime, archive and destination paths must not contain links or junctions.")
    return path.resolve()


@contextmanager
def runtime_lease(root: str | Path, *, exclusive: bool = False):
    """Shared ASGI lease or exclusive maintenance lease; closes release OS locks.

    The stable sibling lock survives removal/restoration of the runtime itself.
    Do not delete this lock file; operating-system locks, not its existence,
    determine whether a runtime is live. Multiple ASGI workers share the lock.
    """
    root = _plain(Path(root))
    if not exclusive:
        # First server startup may use a new nested TULA_DATA_DIR. The lease
        # must be acquired before Repository creates the runtime database.
        root.parent.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(os.path.normcase(str(root)).encode()).hexdigest()[:20]
    lock = _plain(root.parent / f".tula-runtime-{key}.lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if os.fstat(fd).st_nlink != 1:
            raise ValueError("The runtime lock must not be a hard link.")
        if sys.platform == "win32":
            import ctypes
            import msvcrt
            from ctypes import wintypes

            class Overlapped(ctypes.Structure):
                _fields_ = [("Internal", ctypes.c_size_t), ("InternalHigh", ctypes.c_size_t),
                            ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD),
                            ("hEvent", wintypes.HANDLE)]

            api = ctypes.WinDLL("kernel32", use_last_error=True)
            api.LockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                      wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(Overlapped)]
            api.LockFileEx.restype = wintypes.BOOL
            api.UnlockFileEx.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                        wintypes.DWORD, ctypes.POINTER(Overlapped)]
            api.UnlockFileEx.restype = wintypes.BOOL
            handle, overlap = msvcrt.get_osfhandle(fd), Overlapped()
            if not api.LockFileEx(handle, 1 | (2 if exclusive else 0), 0, 1, 0, ctypes.byref(overlap)):
                raise ValueError("Runtime is active or maintenance is in progress. Stop every server and worker first.")
            try:
                yield
            finally:
                api.UnlockFileEx(handle, 0, 1, 0, ctypes.byref(overlap))
        else:
            import fcntl

            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Runtime is active or maintenance is in progress. Stop every server and worker first.") from exc
            try:
                yield
            finally:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _member(name: str) -> str:
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or any(ord(c) < 32 for c in name) or name.startswith("/")):
        raise ValueError("Unsafe archive member path.")
    parts = name.split("/")
    if (any(p in {"", ".", ".."} or p.endswith((" ", "."))
            or p.split(".")[0].upper() in _RESERVED for p in parts)
            or parts[0] not in ROOTS or len(parts) < 2):
        raise ValueError("Unsafe archive member path.")
    if name in {DATABASE + suffix for suffix in ("-wal", "-shm", "-journal")}:
        raise ValueError("A backup must contain the consistent database snapshot, not SQLite sidecars.")
    return name


def _hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _inventory(root: Path) -> dict[str, tuple]:
    files = {}
    for name in sorted(ROOTS):
        folder = _plain(root / name)
        if not folder.exists():
            continue
        if not folder.is_dir():
            raise ValueError("Runtime data, out and rules paths must be directories.")
        for directory, subdirs, filenames in os.walk(folder, followlinks=False):
            for item in [*subdirs, *filenames]:
                path = _plain(Path(directory) / item)
                info = path.stat()
                if path.is_dir():
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise ValueError("Backup files must be regular files without hard links.")
                relative = path.relative_to(root).as_posix()
                if relative in {DATABASE + suffix for suffix in ("-wal", "-shm", "-journal")}:
                    continue
                _member(relative)
                files[relative] = (info.st_size, info.st_mtime_ns, info.st_ino, info.st_dev)
    if DATABASE not in files:
        raise ValueError("The runtime has no data/tula.db database.")
    if len({name.casefold() for name in files}) != len(files):
        raise ValueError("Runtime filenames collide under case-insensitive restoration.")
    if len(files) > MAX_FILES or sum(info[0] for info in files.values()) > MAX_BYTES:
        raise ValueError("Runtime exceeds the supported backup size or file-count limit.")
    return files


def _tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _archive_time(value):
    try:
        moment = datetime.fromisoformat(value)
        if moment.tzinfo is None:
            raise ValueError()
        return moment.timestamp()
    except (ValueError, TypeError, OverflowError):
        raise ValueError("Archive creation time must be an absolute timestamp with a timezone.") from None


def _draft_references(conn, files, as_of):
    """Check the draft's own original-image manifest, not only the outer ZIP hashes.

    Use the snapshot time on every verification. Later wall-clock expiration
    must not remove the integrity requirement for an original active at backup.
    Paths are derived from the service's UUID session and ordered image index;
    a filename or other arbitrary JSON path can never select an archive member.
    """
    checked, active, sessions = set(), 0, set()
    for state, expires, total, fingerprint, raw in conn.execute(
            "SELECT state,expires_at,total_bytes,fingerprint,record FROM capture_draft"):
        if (state not in {"active", "deleted", "expired"} or type(expires) not in (int, float)
                or not math.isfinite(expires)):
            raise ValueError("A saved capture draft has invalid lifecycle metadata.")
        if state != "active" or expires <= as_of:
            continue
        try:
            record = json.loads(raw)
            session, images = record["session"], record["images"]
            if (not isinstance(session, str) or not _DRAFT_ID.fullmatch(session)
                    or session in sessions or not isinstance(images, list) or not 1 <= len(images) <= 12
                    or not isinstance(record["details"], dict) or type(total) is not int
                    or not 0 < total <= 100 * 1024**2):
                raise ValueError()
            calculated = hashlib.sha256(json.dumps({"details": record["details"], "images": images},
                                                   sort_keys=True, allow_nan=False).encode()).hexdigest()
            if fingerprint != calculated:
                raise ValueError()
            size = 0
            for index, item in enumerate(images):
                digest, count = item["sha256"], item["bytes"]
                dimensions, rotation, crop = item["original_size"], item["rotation"], item["crop"]
                if (not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
                        or type(count) is not int or not 0 < count <= 25 * 1024**2
                        or not isinstance(dimensions, list) or len(dimensions) != 2
                        or any(type(v) is not int or v < 16 for v in dimensions)
                        or dimensions[0] * dimensions[1] > 25_000_000
                        or type(rotation) is not int or rotation not in (0, 90, 180, 270)):
                    raise ValueError()
                if crop is not None:
                    if (not isinstance(crop, list) or len(crop) != 4
                            or any(type(v) not in (int, float) or not math.isfinite(v) for v in crop)
                            or not (0 <= crop[0] < crop[2] <= 1 and 0 <= crop[1] < crop[3] <= 1)):
                        raise ValueError()
                    width, height = dimensions if rotation in (0, 180) else dimensions[::-1]
                    if min(round(crop[2] * width) - round(crop[0] * width),
                           round(crop[3] * height) - round(crop[1] * height)) < 16:
                        raise ValueError()
                name = f"data/capture-drafts/{session}/{index:02d}-original.bin"
                source = files.get(name)
                if source is None:
                    raise ValueError("A saved capture draft original is missing from the backup.")
                if source["sha256"] != digest or source["size"] != count:
                    raise ValueError("A saved capture draft original failed its hash or size check.")
                checked.add(name)
                size += count
            if size != total:
                raise ValueError()
        except (KeyError, TypeError, ValueError, OverflowError) as exc:
            if isinstance(exc, ValueError) and str(exc).startswith("A saved capture draft original"):
                raise
            raise ValueError("A saved capture draft image manifest or edit record is invalid.") from None
        sessions.add(session)
        active += 1
    return active, checked


def _validate_database(conn, *, origin: Path, working_directory: Path, files: dict, as_of: float):
    """Validate retained bytes directly; never deserialize/rewrite domain records."""
    conn.execute("PRAGMA trusted_schema=OFF")
    if [row[0] for row in conn.execute("PRAGMA integrity_check")] != ["ok"]:
        raise ValueError("The database failed its integrity check.")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise ValueError("The database has broken relational references.")
    tables = _tables(conn)
    if not tables.intersection({"inspection", "security_user"}):
        raise ValueError("This is not a supported TATVA database.")
    if "inspection_job" in tables and conn.execute(
            "SELECT 1 FROM inspection_job WHERE state IN ('queued','running') LIMIT 1").fetchone():
        raise ValueError("Queued or running inspections remain. Finish or recover them before offline backup.")
    versions = set()
    if "rule_version" in tables:
        for version, digest, record in conn.execute("SELECT version,sha256,record FROM rule_version"):
            if hashlib.sha256(record.encode()).hexdigest() != digest:
                raise ValueError("A retained rule archive failed its hash check.")
            versions.add(version)

    checked = set()

    def reference(value, digest=None):
        if not isinstance(value, str) or not value:
            raise ValueError("A retained evidence path is invalid.")
        path = Path(value)
        path = path if path.is_absolute() else working_directory / path
        # Do not resolve against the current live filesystem during archive verification.
        path = Path(os.path.abspath(path))
        try:
            relative = path.relative_to(origin).as_posix()
        except ValueError as exc:
            raise ValueError("Retained evidence lies outside the runtime. Preserve it separately; this backup cannot relocate references.") from exc
        item = files.get(relative)
        if item is None:
            raise ValueError("A retained evidence or report file is missing from the backup.")
        if digest is not None and (not isinstance(digest, str) or not _DIGEST.fullmatch(digest)
                                   or item["sha256"] != digest):
            raise ValueError("A retained evidence or report file failed its hash check.")
        checked.add(relative)

    def nested_frames(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"frame", "source_frame"} and item:
                    reference(item)
                else:
                    nested_frames(item)
        elif isinstance(value, list):
            for item in value:
                nested_frames(item)

    required_versions = set()
    records = 0
    for table in ("inspection", "inspection_revision"):
        if table not in tables:
            continue
        for (raw,) in conn.execute(f"SELECT record FROM {table}"):
            data = json.loads(raw)
            scan = data["scan"]
            hashes = scan.get("frame_hashes", {})
            for frame in scan.get("frames", []):
                digest = hashes.get(frame) or hashes.get(Path(frame).name)
                if not digest:
                    raise ValueError("A retained source image has no recorded integrity hash.")
                reference(frame, digest)
            for frame, digest in scan.get("original_frame_hashes", {}).items():
                reference(frame, digest)
            nested_frames(data)
            if data.get("rules_version"):
                required_versions.add(data["rules_version"])
            records += 1
    if required_versions - versions:
        raise ValueError("A rule version used by retained inspections is missing from the database archive.")
    if "report_artifact" in tables:
        for path, digest in conn.execute("SELECT path,sha256 FROM report_artifact"):
            reference(path, digest)
    if "inspection_job" in tables:
        for (raw,) in conn.execute("SELECT payload FROM inspection_job"):
            payload = json.loads(raw)
            for capture in payload.get("captures", []):
                reference(capture["path"])
            for path, digest in payload.get("hashes", {}).items():
                reference(path, digest)
    drafts, draft_files = _draft_references(conn, files, as_of) if "capture_draft" in tables else (0, set())
    checked.update(draft_files)
    return {"inspection_records": records, "verified_reference_files": len(checked),
            "archived_rule_versions": len(versions), "active_capture_drafts": drafts,
            "draft_original_files": len(draft_files)}


def _read_manifest(archive):
    entries = archive.infolist()
    names = [info.filename for info in entries]
    if len(entries) > MAX_FILES + 1 or len({n.casefold() for n in names}) != len(names):
        raise ValueError("Archive has duplicate names or too many entries.")
    if "manifest.json" not in names:
        raise ValueError("Archive manifest is missing.")
    for info in entries:
        if info.orig_filename != info.filename:
            raise ValueError("Unsafe archive member path normalization.")
        mode = info.external_attr >> 16
        if info.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG) or info.flag_bits & 1:
            raise ValueError("Archive must contain ordinary unencrypted files, without links.")
        if info.filename != "manifest.json":
            _member(info.filename)
    if archive.getinfo("manifest.json").file_size > MAX_MANIFEST:
        raise ValueError("Archive manifest is too large.")
    manifest = json.loads(archive.read("manifest.json"))
    if (not isinstance(manifest, dict) or manifest.get("format") != FORMAT
            or manifest.get("platform") != os.name or not isinstance(manifest.get("files"), dict)):
        raise ValueError("Archive format or operating-system path convention is unsupported.")
    for field in ("runtime_root", "working_directory"):
        if not isinstance(manifest.get(field), str) or not Path(manifest[field]).is_absolute():
            raise ValueError("Archive must record its original absolute runtime and working directory.")
    _archive_time(manifest.get("created_at"))
    files = manifest["files"]
    if set(names) != {"manifest.json", *files} or DATABASE not in files:
        raise ValueError("Archive entries do not match the manifest.")
    for name, item in files.items():
        _member(name)
        if (not isinstance(item, dict) or set(item) != {"sha256", "size"}
                or not isinstance(item["sha256"], str) or not _DIGEST.fullmatch(item["sha256"])
                or type(item["size"]) is not int or item["size"] < 0
                or item["size"] != archive.getinfo(name).file_size):
            raise ValueError("Archive manifest contains invalid hashes or file sizes.")
    if sum(item["size"] for item in files.values()) > MAX_BYTES:
        raise ValueError("Archive exceeds the supported uncompressed size limit.")
    return manifest


def _unpack_verified(archive_path: Path, stage: Path):
    with zipfile.ZipFile(archive_path) as archive:
        manifest = _read_manifest(archive)
        for name, item in manifest["files"].items():
            target = stage.joinpath(*PurePosixPath(name).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with archive.open(name) as source, target.open("xb") as output:
                while block := source.read(1024 * 1024):
                    size += len(block)
                    if size > item["size"]:
                        raise ValueError("Archive content exceeds its declared size.")
                    digest.update(block)
                    output.write(block)
                output.flush()
                os.fsync(output.fileno())
            if digest.hexdigest() != item["sha256"] or size != item["size"]:
                raise ValueError("Archive content failed its manifest hash check.")
    database = stage / DATABASE
    with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        summary = _validate_database(conn, origin=Path(manifest["runtime_root"]),
                                    working_directory=Path(manifest["working_directory"]), files=manifest["files"],
                                    as_of=_archive_time(manifest["created_at"]))
    return {**manifest, "verification": summary}


def verify_backup(archive: str | Path):
    archive = _plain(Path(archive))
    with tempfile.TemporaryDirectory(prefix="tula-verify-") as directory:
        return _unpack_verified(archive, Path(directory))


def create_backup(root: str | Path, archive: str | Path):
    root, archive = _plain(Path(root)), _plain(Path(archive))
    if archive.is_relative_to(root):
        raise ValueError("Backup destination must be outside the runtime tree.")
    if archive.exists() or not archive.parent.is_dir():
        raise ValueError("Choose a new archive filename in an existing destination directory.")
    with runtime_lease(root, exclusive=True), tempfile.TemporaryDirectory(
            prefix=".tula-backup-", dir=archive.parent) as directory:
        stage = Path(directory)
        before = _inventory(root)
        database = root / DATABASE
        with closing(sqlite3.connect(database.as_uri() + "?mode=rw", uri=True, timeout=0,
                                     isolation_level=None)) as guard:
            try:
                guard.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:
                raise ValueError("Database has an active writer. Stop all runtime processes before backup.") from exc
            try:
                snapshot = stage / DATABASE
                snapshot.parent.mkdir()
                with (closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source,
                      closing(sqlite3.connect(snapshot)) as target):
                    source.backup(target)
                files = {}
                for name in sorted(before):
                    target = stage / name
                    if name != DATABASE:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(_plain(root / name), target)
                    files[name] = {"sha256": _hash(target), "size": target.stat().st_size}
                after = _inventory(root)
                if before != after or any(_hash(root / name) != item["sha256"]
                                          for name, item in files.items() if name != DATABASE):
                    raise ValueError("Runtime files changed during backup. Stop all writers and retry.")
                with closing(sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
                    created_at = datetime.now(UTC).isoformat()
                    summary = _validate_database(conn, origin=root, working_directory=Path.cwd(), files=files,
                                                 as_of=_archive_time(created_at))
                manifest = {"format": FORMAT, "platform": os.name, "runtime_root": str(root),
                            "working_directory": str(Path.cwd()), "created_at": created_at,
                            "files": files, "verification": summary}
                temporary = stage / "archive.zip"
                with zipfile.ZipFile(temporary, "x", compression=zipfile.ZIP_DEFLATED) as bundle:
                    bundle.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
                    for name in files:
                        bundle.write(stage / name, name)
                os.chmod(temporary, 0o600)
                # Linking publishes a complete archive atomically without replacing an existing file.
                with temporary.open("r+b") as stream:
                    os.fsync(stream.fileno())
                os.link(temporary, archive)
                return manifest
            finally:
                guard.rollback()


def _publish_directory(stage: Path, destination: Path):
    if os.name == "nt":
        os.rename(stage, destination)  # Windows refuses an existing destination.
        return
    import ctypes
    import errno

    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    if rename is None:
        raise ValueError("Atomic no-replace restore is unsupported on this operating system. No destination was changed.")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1):
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise ValueError("Restore destination appeared during verification; nothing was overwritten.")
        raise OSError(error, "Atomic no-replace restoration failed; destination was not replaced.")


def restore_backup(archive: str | Path, destination: str | Path):
    archive, destination = _plain(Path(archive)), _plain(Path(destination))
    if destination.exists() or not destination.parent.is_dir():
        raise ValueError("Restore requires an absent destination under an existing parent directory.")
    with zipfile.ZipFile(archive) as bundle:
        manifest = _read_manifest(bundle)
    if os.path.normcase(str(destination)) != os.path.normcase(manifest["runtime_root"]):
        raise ValueError("Restore must use the original absolute runtime path; relocation would break retained evidence references.")
    with runtime_lease(destination, exclusive=True), tempfile.TemporaryDirectory(
            prefix=".tula-restore-", dir=destination.parent) as directory:
        stage = Path(directory) / "runtime"
        stage.mkdir(mode=0o700)
        verified = _unpack_verified(archive, stage)
        if destination.exists():
            raise ValueError("Restore destination appeared during verification; nothing was overwritten.")
        _publish_directory(stage, destination)
        return verified


def main(argv=None):
    parser = argparse.ArgumentParser(description="Offline TATVA runtime backup and verified, path-preserving restore")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create", help="Back up a stopped runtime; archive must be outside it")
    create.add_argument("archive", type=Path)
    create.add_argument("--root", type=Path, default=runtime_root())
    verify = commands.add_parser("verify", help="Verify archive hashes, SQLite and retained evidence references")
    verify.add_argument("archive", type=Path)
    restore = commands.add_parser("restore", help="Restore to its original, currently absent absolute path")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_backup(args.root, args.archive)
        elif args.command == "verify":
            result = verify_backup(args.archive)
        else:
            result = restore_backup(args.archive, args.destination)
    except (ValueError, OSError, sqlite3.Error, zipfile.BadZipFile, KeyError, TypeError,
            AttributeError, NotImplementedError, RecursionError) as exc:
        # Do not print raw database rows, passwords or retained label text.
        message = str(exc) if isinstance(exc, ValueError) and not isinstance(exc, json.JSONDecodeError) else "Backup operation failed; check archive integrity, file permissions and stopped-runtime requirements."
        print(message, file=sys.stderr)
        return 2
    print(f"{args.command.capitalize()} complete: {len(result['files'])} files; "
          f"{result['verification']['inspection_records']} retained inspection records verified.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
