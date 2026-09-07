"""Create the first administrator without shipping or printing credentials."""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

from ..config import runtime_root
from .store import SecurityStore


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Tula account administration")
    commands = parser.add_subparsers(dest="command", required=True)
    bootstrap = commands.add_parser("bootstrap", help="Create the first administrator only")
    bootstrap.add_argument("--database", type=Path,
                           default=runtime_root() / "data" / "tula.db")
    bootstrap.add_argument("--username", required=True)
    bootstrap.add_argument("--display-name", default="")
    bootstrap.add_argument("--password-stdin", action="store_true",
                           help="Read one password line from standard input, for controlled provisioning")
    args = parser.parse_args(argv)
    store = SecurityStore(args.database)
    if store.has_users():
        print("An account already exists. Sign in as an administrator to manage users.", file=sys.stderr)
        return 2
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\r\n")
    else:
        password = getpass.getpass("Administrator password (at least 12 characters): ")
        if password != getpass.getpass("Confirm password: "):
            print("Passwords did not match.", file=sys.stderr)
            return 2
    try:
        user = store.create_user(args.username, password, display_name=args.display_name,
                                 role="admin", bootstrap=True)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"Administrator {user.username!r} created in {store.path.resolve()}. Sign in through /login.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
