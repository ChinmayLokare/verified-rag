"""Command-line entry point: ``vrag <command>``."""

import argparse
import sys

from verified_rag.config import get_settings
from verified_rag.store import migrate


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vrag", description="VerifiedRAG command line")
    commands = parser.add_subparsers(dest="command", required=True)

    migrate_cmd = commands.add_parser("migrate", help="apply pending database migrations")
    migrate_cmd.add_argument(
        "--status", action="store_true", help="show applied and pending migrations only"
    )

    args = parser.parse_args(argv)

    if args.command == "migrate":
        url = get_settings().database_url
        if args.status:
            for line in migrate.status(url):
                print(line)
            return 0
        applied = migrate.apply_pending(url)
        if applied:
            for m in applied:
                print(f"applied {m.filename}")
        else:
            print("database is up to date")
        return 0

    parser.error(f"unknown command {args.command}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
