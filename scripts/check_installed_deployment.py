"""Exercise a freshly installed wheel using disposable data and real loopback HTTP.

Run with the new venv's ``python -I`` from outside the source checkout. This
script never removes a runtime: the original test runtime is retained beside
its restored copy. A failed check retains its evidence for diagnosis.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import io
import ipaddress
import json
import os
import platform
import secrets
import site
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def table_snapshot(database):
    """Compare retained bytes and every security/catalog table without exposing rows."""
    with closing(sqlite3.connect(database)) as conn:
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        result = {}
        for name in names:
            quoted = '"' + name.replace('"', '""') + '"'
            rows = sorted(repr(row) for row in conn.execute("SELECT * FROM " + quoted))
            result[name] = {"rows": len(rows), "sha256": hashlib.sha256(
                "\n".join(rows).encode()).hexdigest()}
        return result


def serve(port, receipt):
    """Run each installation/recovery startup in a genuinely fresh interpreter."""
    sys.addaudithook(forbid_remote_python_sockets)
    import uvicorn

    from tula.web import app as web

    module = Path(web.__file__).resolve()
    Path(receipt).write_text(json.dumps({"pid": os.getpid(), "runtime_root": str(web.ROOT),
        "database": str(web.repo.path), "app_module": {"path": str(module), "sha256": digest(module)}},
        indent=2), encoding="utf-8")
    server = uvicorn.Server(uvicorn.Config(web.app, host="127.0.0.1", port=port,
                                          log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, name="deployment-acceptance-server", daemon=True)
    thread.start()
    try:
        # Parent requests graceful ASGI/worker shutdown; EOF also releases the child.
        for line in sys.stdin:
            if line.strip() == "stop":
                break
    finally:
        server.should_exit = True
        thread.join(timeout=45)
    require(not thread.is_alive(), "Installed server did not shut down gracefully")
    return 0


@contextmanager
def running_server(log):
    import httpx

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    with log.open("w", encoding="utf-8") as output:
        process = subprocess.Popen([sys.executable, "-I", str(Path(__file__).resolve()), "--serve",
                                    str(port), str(log.with_suffix(".json"))],
                                   stdin=subprocess.PIPE, stdout=output, stderr=subprocess.STDOUT,
                                   text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            with httpx.Client(base_url=base, trust_env=False, timeout=2) as client:
                for _ in range(150):
                    try:
                        if client.get("/healthz").status_code == 200:
                            break
                    except httpx.TransportError:
                        pass
                    require(process.poll() is None, "Installed server exited during startup")
                    time.sleep(.2)
                else:
                    raise RuntimeError("Installed server did not become healthy")
            yield base
        finally:
            if process.poll() is None:
                try:
                    process.stdin.write("stop\n")
                    process.stdin.flush()
                except (BrokenPipeError, OSError):
                    pass
            process.stdin.close()
            try:
                process.wait(timeout=50)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
                raise RuntimeError("Server shutdown did not finish; runtime was not moved") from None
            require(process.returncode == 0, "Installed server process exited unsuccessfully; inspect its retained log")


def login(base, username, password):
    import httpx

    client = httpx.Client(base_url=base, trust_env=False, timeout=60, follow_redirects=False)
    require(client.get("/login").status_code == 200, "Installed login template failed")
    response = client.post("/login", data={"username": username, "password": password,
                           "csrf_token": client.cookies["tula_login_csrf"]})
    require(response.status_code == 303, "Disposable account login failed")
    session = client.get("/v1/session").json()
    client.headers["X-CSRF-Token"] = session["csrf_token"]
    return client, session["user"]


def forbid_remote_python_sockets(event, args):
    """A Python-level outbound guard; this is not a machine-wide air-gap test."""
    if event != "socket.connect":
        return
    address = args[1]
    if isinstance(address, tuple):
        try:
            permitted = address[0] == "localhost" or ipaddress.ip_address(address[0]).is_loopback
        except ValueError:
            permitted = False
        if not permitted:
            raise RuntimeError("Deployment acceptance forbids non-loopback Python sockets")


def check_draft_resume(client, draft_id, original, *, expected=None):
    """Exercise restored originals and editing metadata through the installed API."""
    response = client.get("/v1/capture/drafts/" + draft_id)
    require(response.status_code == 200, "Saved capture draft could not be resumed")
    draft = response.json()
    if expected is not None:
        require(draft == expected, "Restored capture draft metadata changed")
    require(draft["revision"] == 1 and len(draft["images"]) == 1, "Draft revision or image count changed")
    item = draft["images"][0]
    require(item["rotation"] == 90 and item["crop"] == [.1, .1, .9, .9] and item["panel"] == "back",
            "Saved capture edit geometry or panel was not restored")
    require(draft["details"]["region"] == "Draft recovery QA" and draft["details"]["concerns"] == "milk, soy",
            "Capture draft details were not restored")
    require(draft["details"]["dimensions"] == {"pdp_width_mm": 120, "pdp_height_mm": 180},
            "Capture draft panel dimensions were not restored")
    context = draft["details"]["context"]
    require(context["category"] == "cosmetic" and context["is_imported"] is False,
            "Capture draft package selections were not restored")
    require(draft["details"]["complete"] is False and all(
        context[flag] is False for flag in ("category_confirmed", "bundle_confirmed", "shape_confirmed", "imported_confirmed")),
        "Capture resume improperly retained package or completeness attestations")
    response = client.get(item["url"])
    require(response.status_code == 200 and response.content == original,
            "Saved draft original bytes could not be recovered")
    require("no-store" in response.headers.get("cache-control", ""), "Private draft original was cacheable")
    require(item["sha256"] == hashlib.sha256(original).hexdigest() and item["bytes"] == len(original),
            "Draft original disagrees with its recorded hash or size")
    response = client.post("/v1/capture/preview", files={"file": (item["filename"], original, item["mime"])},
                           data={"edit": json.dumps({"rotation": item["rotation"], "crop": item["crop"]})})
    require(response.status_code == 200, "Recovered draft could not regenerate its edited preview")
    preview = response.json()
    width, height = item["original_size"][::-1]  # The retained edit rotates clockwise by 90 degrees.
    require(preview["original_size"] == item["original_size"] and preview["retained"] is False
            and preview["working_size"] == [round(.9 * width) - round(.1 * width),
                                             round(.9 * height) - round(.1 * height)],
            "Recovered draft preview does not apply the retained crop and rotation")
    return draft, {"working_size": preview["working_size"], "original_size": preview["original_size"]}


def check(args, evidence):
    import httpx

    root, checkout, wheel = args.work.resolve(), args.checkout.resolve(), args.wheel.resolve()
    require(sys.prefix != sys.base_prefix, "Run with the disposable virtual environment's Python")
    require(not root.is_relative_to(checkout), "Acceptance workspace must be outside the checkout")
    require(not Path.cwd().resolve().is_relative_to(checkout), "Launch from outside the checkout")
    require(not root.exists(), "Acceptance workspace must be new; existing files are never replaced")
    root.mkdir(parents=True)
    venv_config = (Path(sys.prefix) / "pyvenv.cfg").read_text(encoding="utf-8")
    shared_dependencies = any(line.strip().lower() == "include-system-site-packages = true"
                              for line in venv_config.splitlines())
    if args.require_clean:
        require(not shared_dependencies and site.ENABLE_USER_SITE is False,
                "Clean acceptance requires no system-site-packages and disabled user site")
    baseline = None
    if args.baseline_evidence:
        baseline = json.loads(args.baseline_evidence.read_text(encoding="utf-8"))
        require(baseline.get("status") == "passed" and baseline.get("wheel_sha256") == digest(wheel),
                "Baseline evidence must identify a previously accepted copy of this exact wheel")
        require(isinstance(baseline.get("source_members_verified"), dict), "Baseline has no retained source hashes")
    runtime = root / "nested" / "service" / "runtime"
    os.environ["TULA_DATA_DIR"] = str(runtime)
    os.environ.pop("TULA_OCR", None)
    evidence.update({"started_at": datetime.now(UTC).isoformat(), "python": sys.executable,
                     "checker": {"path": str(Path(__file__).resolve()), "sha256": digest(__file__)},
                     "prefix": sys.prefix, "base_prefix": sys.base_prefix,
                     "working_directory": str(Path.cwd()), "runtime_root": str(runtime),
                     "wheel": str(wheel), "wheel_sha256": digest(wheel),
                     "platform": platform.platform(), "python_version": platform.python_version(),
                     "system_site_packages": shared_dependencies, "user_site_enabled": site.ENABLE_USER_SITE,
                     "dependency_installation": ("Disposable venv with system-site-packages; not a clean dependency install"
                        if shared_dependencies else "Isolated venv without system-site-packages; dependency locations verified below"),
                     "source_comparison": ("Previously accepted source manifest; current checkout is not verified"
                        if baseline else "Current frozen checkout compared byte-for-byte with wheel"),
                     "network_scope": "Non-loopback Python socket.connect denied; not an OS-level air-gap test"})
    if baseline:
        evidence["baseline_evidence"] = {"path": str(args.baseline_evidence.resolve()),
                                          "sha256": digest(args.baseline_evidence)}
    sys.addaudithook(forbid_remote_python_sockets)
    modules = {}
    for name in ("tula", "tula.config", "tula.analyse", "tula.labgen", "tula.ocr.engines",
                 "tula.extract.pipeline", "tula.report.pdf", "tula.storage.backup", "tula.security"):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        require(path.is_relative_to(Path(sys.prefix).resolve()), f"{name} was not loaded from installed venv")
        require(not path.is_relative_to(checkout), f"{name} leaked the source checkout")
        modules[name] = {"path": str(path), "sha256": digest(path)}
    evidence["imports"] = modules
    package = Path(importlib.import_module("tula").__file__).parent
    for relative in ("web/templates/inspection.html", "web/templates/api_docs.html", "web/templates/processing.html",
                     "web/templates/index.html", "web/static/capture.js", "web/static/review.js",
                     "web/static/console.css", "web/static/htmx.min.js",
                     "web/static/sw.js", "web/static/manifest.webmanifest",
                     "web/static/offline.html", "web/static/icons/icon-192.png"):
        require((package / relative).is_file(), f"Installed asset missing: {relative}")
    evidence["installed_assets"] = [p.relative_to(package).as_posix()
                                   for folder in ("web/templates", "web/static")
                                   for p in sorted((package / folder).iterdir()) if p.is_file()]
    with zipfile.ZipFile(wheel) as bundle:
        checked_members = []
        source_members = {}

        def source_check(relative, payload):
            fingerprint = hashlib.sha256(payload).hexdigest()
            if baseline:
                require(baseline["source_members_verified"].get(relative) == fingerprint,
                        f"Wheel no longer matches accepted source snapshot: {relative}")
            else:
                source = checkout / relative
                require(source.is_file() and source.read_bytes() == payload,
                        f"Source changed since wheel build or wheel contains stale file: {relative}")
            source_members[relative] = fingerprint

        for name in bundle.namelist():
            if name.startswith("tula/") and not name.endswith("/"):
                installed = package.parent / name
                require(installed.is_file() and installed.read_bytes() == bundle.read(name),
                        f"Installed file does not match selected wheel: {name}")
                source_check("src/" + name, bundle.read(name))
                checked_members.append(name)
            elif ".data/data/share/tula/rules/" in name:
                installed = Path(sys.prefix) / name.split(".data/data/", 1)[1]
                require(installed.is_file() and installed.read_bytes() == bundle.read(name),
                        f"Installed rule file does not match selected wheel: {name}")
                source_check("rules/lmpcr-2011/" + Path(name).name, bundle.read(name))
                checked_members.append(name)
        evidence["wheel_members_verified"] = checked_members
        if baseline:
            require(source_members == baseline["source_members_verified"], "Wheel is missing previously accepted source files")
        else:
            expected_sources = [*(checkout / "src" / "tula").rglob("*.py"),
                                *(checkout / "src" / "tula" / "web" / "templates").glob("*.html"),
                                *(checkout / "src" / "tula" / "web" / "static").glob("*"),
                                *(checkout / "rules" / "lmpcr-2011").glob("*.json")]
            require(all(p.relative_to(checkout).as_posix() in source_members for p in expected_sources if p.is_file()),
                    "A current source module, web asset, or active rule file is absent from the wheel")
        evidence["source_members_verified"] = source_members
    from tula.config import runtime_root
    from tula.rules.engine import DEFAULT_PACK_DIR, RulesEngine

    require(runtime_root() == runtime, "Application runtime disagrees with configured location")
    os.environ.pop("TULA_DATA_DIR")
    try:
        default_runtime = runtime_root()
        require(default_runtime == Path.home() / ".tula", "Installed default runtime incorrectly resolves to checkout")
        evidence["installed_default_runtime_read_only"] = str(default_runtime)
    finally:
        os.environ["TULA_DATA_DIR"] = str(runtime)
    require(DEFAULT_PACK_DIR.is_relative_to(Path(sys.prefix)), "Rules were loaded outside installed environment")
    pack = RulesEngine.from_directory().pack
    evidence["rule_pack"] = {"directory": str(DEFAULT_PACK_DIR), "version": pack.version,
                              "rule_count": len(pack.rules)}
    ocr_package = Path(importlib.import_module("rapidocr_onnxruntime").__file__).parent
    models = [{"path": str(p), "bytes": p.stat().st_size, "sha256": digest(p)}
              for p in sorted(ocr_package.rglob("*.onnx"))]
    require(len(models) >= 3, "Required bundled detector/classifier/recognizer models are unavailable")
    evidence["ocr_models"] = models
    import cv2
    import numpy as np
    import onnxruntime

    from tula.imaging.metrology import from_aruco

    marker = cv2.aruco.generateImageMarker(
        cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50), 0, 200)
    marker_image = np.full((300, 300), 255, dtype=np.uint8)
    marker_image[50:250, 50:250] = marker
    marker_path = root / "dependency-aruco.png"
    require(cv2.imwrite(str(marker_path), marker_image), "Could not write generated scale-card probe")
    scale = from_aruco(str(marker_path))
    require(scale is not None and abs(scale.mm_per_px - 25 / 199) < .01,
            "Installed OpenCV did not detect the known 25 mm marker geometry")
    evidence["opencv_scale_probe"] = {"version": cv2.__version__, "module": cv2.__file__,
                                       "image": str(marker_path), "scale": scale.model_dump(mode="json")}
    evidence["onnx_providers"] = onnxruntime.get_available_providers()
    require("CPUExecutionProvider" in evidence["onnx_providers"], "Installed ONNX Runtime has no CPU provider")
    evidence["dependencies"] = {name: importlib.metadata.version(name) for name in
        ("tula", "fastapi", "uvicorn", "rapidocr-onnxruntime", "onnxruntime", "Pillow", "reportlab", "pypdf", "python-docx")}
    installed_distributions = {}
    for distribution in importlib.metadata.distributions():
        name = distribution.metadata["Name"]
        location = Path(distribution.locate_file("")).resolve()
        if not shared_dependencies:
            require(location.is_relative_to(Path(sys.prefix)), f"Dependency {name} escaped the clean virtual environment")
        installed_distributions[name] = {"version": distribution.version, "location": str(location)}
    evidence["installed_distributions"] = dict(sorted(installed_distributions.items()))
    from PIL import features

    from tula.labgen import _font
    from tula.ocr.auxiliary import tesseract_config

    font = _font(False, 40, "शुद्ध वजन")
    font_path = Path(font.path).resolve()
    auxiliary = tesseract_config()
    evidence["system_font"] = {"path": str(font_path), "face_index": font.index,
                               "sha256": digest(font_path), "pillow_raqm": features.check_feature("raqm")}
    evidence["optional_tesseract"] = ({"path": auxiliary[0], "languages": auxiliary[1],
                                        "missing_languages": auxiliary[2]} if auxiliary else None)
    evidence["ocr_environment"] = {name: os.environ[name] for name in
        ("TULA_TESSERACT", "TULA_TESSERACT_LANGS", "TULA_OCR_MAX_PASSES", "TULA_OCR_BUDGET_SECONDS") if name in os.environ}

    def command(arguments, password=None):
        invocation = [sys.executable, "-I", "-m", *arguments]
        result = subprocess.run(invocation, input=password + "\n" if password else None,
                                capture_output=True, text=True, timeout=90, check=False)
        evidence.setdefault("commands", []).append({"argv": invocation, "exit_code": result.returncode,
                                                    "stdout": result.stdout.strip(), "stderr": result.stderr.strip()})
        require(result.returncode == 0, f"Installed command failed: {' '.join(arguments)}")

    admin_password, inspector_password, supervisor_password = (secrets.token_urlsafe(32) for _ in range(3))
    command(["tula.security", "bootstrap", "--username", "deployment.admin", "--password-stdin"], admin_password)
    require((runtime / "data" / "tula.db").is_file(), "Bootstrap did not use nested TULA_DATA_DIR")
    from tula.labgen import LabelSpec, render

    label = render(LabelSpec(brand="Installer QA", generic="Shampoo", package_category="cosmetic",
                             net_qty_value="6", net_qty_unit="ml", unit_price=None, gtin=None), root / "input")
    evidence["sample"] = {"image": str(label.png), "sha256": digest(label.png),
                            "kind": "Generated Hindi/English cosmetic label; actual image uploaded, no fixture sidecars"}
    health_request_id = "installed-health-123456"
    upload_request_id = "installed-upload-123456"
    report_request_ids = {}
    with running_server(root / "server-initial.log") as base:
        with httpx.Client(base_url=base, trust_env=False) as guest:
            health = guest.get("/healthz", headers={"X-Request-ID": health_request_id})
            require(health.headers.get("X-Request-ID") == health_request_id,
                    "Installed service did not echo a valid request correlation ID")
            evidence["health"] = health.json()
            denied = guest.get("/v1/analytics")
            require(denied.status_code == 401, "Anonymous inspection API was accessible")
            require(len(denied.headers.get("X-Request-ID", "")) == 32,
                    "Authentication rejection had no generated request correlation ID")
        admin, _ = login(base, "deployment.admin", admin_password)
        with closing(admin):
            for role, password in (("inspector", inspector_password), ("supervisor", supervisor_password)):
                response = admin.post("/admin/users", data={"username": "deployment." + role, "role": role,
                                       "display_name": "Deployment " + role.title(), "password": password})
                require(response.status_code == 303, "Administrator account creation failed")
            require(len(admin.get("/v1/admin/users").json()["users"]) == 3, "Account API disagrees with creation")
        inspector, user = login(base, "deployment.inspector", inspector_password)
        with closing(inspector):
            require(inspector.get("/admin/users").status_code == 403, "Inspector could access administration")
            pages = ("/", "/dashboard", "/repository", "/processing", "/rules", "/bench", "/docs", "/openapi.json",
                     "/static/console.css", "/static/capture.js", "/static/review.js", "/static/htmx.min.js",
                     "/sw.js", "/static/manifest.webmanifest", "/static/offline.html",
                     "/static/icons/icon-192.png")
            evidence["http_pages"] = {page: inspector.get(page).status_code for page in pages}
            require(all(code == 200 for code in evidence["http_pages"].values()), "Installed asset or page failed")
            context = {"category": "cosmetic", "category_confirmed": True, "bundle_type": "single",
                       "bundle_confirmed": True, "shape": "rectangular", "shape_confirmed": True,
                       "is_imported": False, "imported_confirmed": True}
            draft_id = secrets.token_hex(16)
            draft_original = label.png.read_bytes()
            response = inspector.post("/v1/capture/drafts/" + draft_id,
                files={"files": ("unfinished-package.png", draft_original, "image/png")},
                data={"revision": 0, "save_token": secrets.token_hex(16), "panels": "back",
                      "edits": json.dumps([{"rotation": 90, "crop": [.1, .1, .9, .9]}]),
                      "details": json.dumps({"title": "Installer recovery draft", "lane": "field",
                          "region": "Draft recovery QA", "concerns": "milk, soy", "context": context,
                          "dimensions": {"pdp_width_mm": 120, "pdp_height_mm": 180}, "complete": True})})
            require(response.status_code == 200, "Installed private capture-draft save failed")
            draft_before, draft_preview = check_draft_resume(inspector, draft_id, draft_original)
            listed = inspector.get("/v1/capture/drafts").json()["drafts"]
            require(len(listed) == 1 and listed[0]["id"] == draft_id, "Saved draft owner list failed")
            require(inspector.get("/v1/jobs", params={"state": "all"}).json()["total"] == 0,
                    "Saving an unfinished draft incorrectly queued an inspection")
            evidence["capture_draft"] = {"id": draft_id, "revision": draft_before["revision"],
                "original_sha256": hashlib.sha256(draft_original).hexdigest(), "original_bytes": len(draft_original),
                "retained_preview_geometry": draft_preview, "attestations_cleared": True,
                "created_without_processing_job": True}
            with label.png.open("rb") as image:
                response = inspector.post("/v1/inspections", headers={"X-Request-ID": upload_request_id},
                    files={"files": (label.png.name, image, "image/png")},
                    data={"panels": "pdp", "complete": "1", "lane": "field", "region": "Deployment QA",
                          "dimensions": json.dumps({"pdp_width_mm": 120, "pdp_height_mm": 180}),
                          "legal_context": json.dumps(context)})
            require(response.status_code == 202, f"Real image upload failed with status {response.status_code}")
            require(response.headers.get("X-Request-ID") == upload_request_id,
                    "Upload response lost its request correlation ID")
            job = response.json()
            deadline = time.monotonic() + 180
            while job["state"] not in ("complete", "failed") and time.monotonic() < deadline:
                time.sleep(.5)
                job = inspector.get("/v1/jobs/" + job["id"]).json()
            require(job["state"] == "complete", f"Real OCR did not complete: {job.get('error', job['state'])}")
            worklist = inspector.get("/v1/jobs", params={"state": "complete"}).json()
            require(worklist["total"] == 1 and worklist["rows"][0]["id"] == job["id"],
                    "Saved processing job was not available in the authenticated worklist")
            require(inspector.get("/processing?state=complete").status_code == 200, "Saved processing template failed")
            evidence["processing_worklist"] = {"total": worklist["total"], "state": worklist["state"]}
            scan_id = job["scan_id"]
            report_base = "/inspections/" + scan_id
            analysis = inspector.get(report_base + "/report.json").json()
            require(analysis["engine"] == "rapidocr", "Inspection did not use real OCR")
            require(len(analysis["spans"]) >= 10, "Image did not produce useful recognized text")
            require(analysis["scan"]["inspector_id"] == user["id"], "Inspection owner was not the authenticated inspector")
            require(analysis["declarations"]["net_quantity"]["norm"]["value"] == 6, "Real OCR missed generated 6 ml declaration")
            require(inspector.get(report_base).status_code == 200, "Installed inspection template failed")
            require(all(item["status"] == "verified" for item in inspector.get(report_base + "/integrity").json()),
                    "Uploaded image integrity failed")
            response = inspector.post(report_base + "/review", data={"revision": analysis["review"]["revision"],
                "action": "comment", "reason": "Deployment acceptance: generated sample and original image reviewed."})
            require(response.status_code == 303, "Review comment did not persist")
            reviewed = inspector.get(report_base + "/report.json").json()
            require(reviewed["review"]["revision"] == 1, "Review revision was not retained")
            require(inspector.get(report_base + "/revisions/0").json() == analysis, "Original revision changed after review")
            response = inspector.post(report_base + "/review", data={"revision": 1, "action": "submit",
                "reason": "Synthetic deployment sample: package facts and scoped exemption checked against generator."})
            require(response.status_code == 303, "Generated sample could not be submitted for independent review")
            denied = inspector.post(report_base + "/review", data={"revision": 2, "action": "approve",
                "reason": "Synthetic acceptance checks inspector approval is refused."})
            require(denied.status_code == 403, "Inspector was allowed to approve their own inspection")
            supervisor, _ = login(base, "deployment.supervisor", supervisor_password)
            with closing(supervisor):
                require(supervisor.get("/v1/capture/drafts/" + draft_id).status_code == 404
                        and supervisor.get(draft_before["images"][0]["url"]).status_code == 404
                        and supervisor.get("/v1/capture/drafts").json() == {"drafts": []},
                        "Another supervisor could read the inspector's private draft")
                evidence["capture_draft"]["other_supervisor_denied"] = True
                response = supervisor.post(report_base + "/review", data={"revision": 2, "action": "approve",
                    "reason": "Synthetic deployment acceptance: independent sample review; no real product enforcement."})
                require(response.status_code == 303, "Independent supervisor could not approve submitted sample")
            reviewed = inspector.get(report_base + "/report.json").json()
            require(reviewed["review"]["revision"] == 3 and reviewed["review"]["status"] == "approved",
                    "Independent approval was not retained")
            evidence["inspection"] = {"scan_id": scan_id, "engine": analysis["engine"],
                "ocr_spans": len(analysis["spans"]), "declarations": list(analysis["declarations"]),
                "finding_count": len(analysis["findings"]), "rules_version": analysis["rules_version"],
                "revision": reviewed["review"]["revision"], "workflow_status": reviewed["review"]["status"],
                "warnings": analysis["warnings"]}
            exports = {}
            for fmt, route in (("pdf", "/report.pdf"), ("docx", "/report.docx"), ("notice.docx", "/notice.docx")):
                request_id = "installed-report-" + fmt.replace(".", "-") + "-123456"
                report_request_ids[fmt] = request_id
                response = inspector.get(report_base + route, headers={"X-Request-ID": request_id})
                require(response.status_code == 200, f"Installed {fmt} export failed")
                require(response.headers.get("X-Request-ID") == request_id,
                        f"Installed {fmt} response lost its request correlation ID")
                path = root / ("inspection." + fmt)
                path.write_bytes(response.content)
                detail = {"path": str(path), "bytes": path.stat().st_size, "sha256": digest(path)}
                if fmt == "pdf":
                    from pypdf import PdfReader
                    reader = PdfReader(path)
                    require(len(reader.pages) >= 1 and scan_id in "\n".join(p.extract_text() for p in reader.pages),
                            "PDF inspection identity could not be read")
                    detail["pages"] = len(reader.pages)
                else:
                    with zipfile.ZipFile(io.BytesIO(response.content)) as document:
                        require("word/document.xml" in document.namelist(), "Export is not a readable Word document")
                exports[fmt] = detail
            evidence["exports"] = exports
            require(inspector.get("/v1/analytics").status_code == 200, "Analytics API failed")
            require(inspector.get("/v1/rules/current").json()["version"] == pack.version, "Rule API version mismatch")
        admin, _ = login(base, "deployment.admin", admin_password)
        with closing(admin):
            require(admin.get("/v1/capture/drafts/" + draft_id).status_code == 404
                    and admin.get(draft_before["images"][0]["url"]).status_code == 404,
                    "Another administrator could read the inspector's private draft")
            evidence["capture_draft"]["other_administrator_denied"] = True
            events = admin.get("/v1/admin/audit", params={"entity_id": scan_id}).json()["events"]
            generated = [item for item in events if item["action"] == "report.generated"]
            require({item["after"]["format"] for item in generated} == {"pdf", "docx", "notice.docx"},
                    "Every document export must be retained and audited")
            evidence["audit_actions"] = sorted({item["action"] for item in events})
    startup = json.loads((root / "server-initial.json").read_text(encoding="utf-8"))
    require(Path(startup["runtime_root"]) == runtime and Path(startup["database"]) == runtime / "data" / "tula.db",
            "Server used a different runtime")
    evidence["imports"]["tula.web.app"] = startup["app_module"]
    require(Path(startup["app_module"]["path"]).is_relative_to(Path(sys.prefix)), "App import escaped venv")
    evidence["server_processes"] = {"initial": startup}
    log_text = (root / "server-initial.log").read_text(encoding="utf-8")
    operational_events = [json.loads(line) for line in log_text.splitlines() if line.strip()]
    require(all(isinstance(item, dict) and item.get("event") for item in operational_events),
            "Installed operational output contained a raw or unstructured access line")
    upload_events = [item for item in operational_events if item.get("request_id") == upload_request_id]
    require({item["event"] for item in upload_events} >= {
        "http_request_completed", "inspection_started", "inspection_stage", "inspection_completed"
    }, "Installed background analysis lost upload request correlation")
    require({item.get("stage") for item in upload_events if item["event"] == "inspection_stage"} >= {
        "received", "ocr", "extraction", "applicability", "measurement", "rules", "complete"
    }, "Installed logs did not expose every analysis stage")
    for fmt, request_id in report_request_ids.items():
        correlated = [item["event"] for item in operational_events if item.get("request_id") == request_id]
        require(correlated == ["report_generation_started", "report_generation_completed",
                               "http_request_completed"],
                f"Installed {fmt} report events were not correlated")
    require("unfinished-package" not in log_text and "Installer recovery draft" not in log_text,
            "Operational logs exposed an uploaded filename or private draft title")
    evidence["observability"] = {
        "events": len(operational_events), "health_request_id": health_request_id,
        "upload_request_id": upload_request_id,
        "analysis_stages": sorted({item.get("stage") for item in upload_events
                                    if item["event"] == "inspection_stage"}),
        "report_request_ids": report_request_ids, "raw_access_lines": 0,
        "sensitive_values_absent": True,
    }
    before = table_snapshot(runtime / "data" / "tula.db")
    archive = root / "offline-backup.zip"
    command(["tula.storage.backup", "create", str(archive), "--root", str(runtime)])
    command(["tula.storage.backup", "verify", str(archive)])
    retained = runtime.with_name("runtime-before-restore")
    require(runtime.is_relative_to(root) and retained.is_relative_to(root) and not retained.exists(),
            "Disposable runtime move escaped the acceptance workspace")
    runtime.rename(retained)
    command(["tula.storage.backup", "restore", str(archive), "--destination", str(runtime)])
    after = table_snapshot(runtime / "data" / "tula.db")
    require(before == after, "Restoration changed retained database rows")
    with zipfile.ZipFile(archive) as bundle:
        manifest = json.loads(bundle.read("manifest.json"))
    require(manifest["verification"]["active_capture_drafts"] == 1
            and manifest["verification"]["draft_original_files"] == 1,
            "Backup did not verify the active draft's original against its database manifest")
    for name, item in manifest["files"].items():
        require(digest(runtime / name) == item["sha256"], "Restoration changed retained file bytes")
    evidence["backup"] = {"path": str(archive), "sha256": digest(archive), "verification": manifest["verification"],
                           "files": len(manifest["files"]), "tables_identical": before,
                           "original_preserved_at": str(retained), "restored_at": str(runtime)}
    with running_server(root / "server-restored.log") as base:
        inspector, _ = login(base, "deployment.inspector", inspector_password)
        with closing(inspector):
            _, restored_preview = check_draft_resume(inspector, draft_id, draft_original, expected=draft_before)
            require(restored_preview == draft_preview, "Restored draft preview geometry changed")
            evidence["capture_draft"]["restored_state_and_original_identical"] = True
            evidence["capture_draft"]["restored_preview_regenerated"] = True
            restored = inspector.get("/inspections/" + scan_id + "/report.json").json()
            require(restored == reviewed, "Restored inspection differs after application restart")
            require(inspector.get("/inspections/" + scan_id + "/frames/0").status_code == 200,
                    "Restored original evidence is not accessible")
    restarted = json.loads((root / "server-restored.json").read_text(encoding="utf-8"))
    require(restarted["pid"] != startup["pid"] and restarted["app_module"] == startup["app_module"]
            and restarted["runtime_root"] == startup["runtime_root"] and restarted["database"] == startup["database"],
            "Restoration was not checked using a new matching installed server process")
    evidence["server_processes"]["restored"] = restarted
    evidence["restored_startup"] = "Healthy; authenticated inspection and private draft originals/state retrieved; edited preview regenerated"
    evidence["status"] = "passed"
    evidence["completed_at"] = datetime.now(UTC).isoformat()
    return root


def main():
    if len(sys.argv) == 4 and sys.argv[1] == "--serve":
        return serve(int(sys.argv[2]), sys.argv[3])
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True, help="New disposable directory outside source checkout")
    parser.add_argument("--checkout", type=Path, required=True, help="Forbidden source import location")
    parser.add_argument("--wheel", type=Path, required=True, help="Freshly installed wheel retained for identification")
    parser.add_argument("--require-clean", action="store_true", help="Require a venv with no shared or user site packages")
    parser.add_argument("--baseline-evidence", type=Path,
                        help="Compare with a previously accepted source manifest; explicitly does not verify current source")
    args = parser.parse_args()
    evidence = {}
    try:
        root = check(args, evidence)
    except Exception as exc:
        evidence.update({"status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        root = args.work.resolve()
        if root.is_dir() and "runtime_root" in evidence:
            (root / "acceptance.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        raise
    (root / "acceptance.json").write_text(json.dumps(evidence, indent=2), encoding="utf-8")
    print(f"Installed deployment acceptance passed: {root / 'acceptance.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
