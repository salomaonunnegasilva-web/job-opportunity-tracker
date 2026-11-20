"""Run from the project directory with python -m tracker."""

import argparse
import csv
import json
import re
import sqlite3
import sys
from pathlib import Path

from .core import read_state, refresh
from .output import atomic_text, export_labels
from .presentation import ranked_jobs, render_digest


def load_boards(path: Path) -> dict:
    with path.open(encoding="utf-8-sig") as stream:
        boards = json.load(stream)
    if not isinstance(boards, dict) or not boards:
        raise ValueError(f"{path} must map at least one Greenhouse board token to an employer name")
    for token, name in boards.items():
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]*", token):
            raise ValueError(f"Invalid Greenhouse board token: {token!r}")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Missing employer name for {token!r}")
    return boards


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tracker", description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    sync = commands.add_parser("refresh", help="Fetch boards and write the ranked HTML digest")
    sync.add_argument("--boards", type=Path, default=Path("boards.json"), help="Board configuration")
    sync.add_argument("--output", type=Path, default=Path("site/index.html"), help="HTML destination")
    labels = commands.add_parser("export-labels", help="Export open jobs and preserve your existing labels")
    labels.add_argument("--output", type=Path, default=Path("data/labels.csv"), help="Private CSV destination")
    for command in (sync, labels):
        command.add_argument("--db", type=Path, default=Path("data/jobs.sqlite3"), help="SQLite history file")
    args = parser.parse_args(argv)

    try:
        paths = [args.db, args.output]
        if args.command == "refresh":
            paths.append(args.boards)
        if len({p.resolve() for p in paths}) != len(paths):
            raise ValueError("Choose distinct paths for the database, configuration and output")

        if args.command == "refresh":
            boards = load_boards(args.boards)
            args.db.parent.mkdir(parents=True, exist_ok=True)
            results = refresh(args.db, boards)
            state = read_state(args.db)
            with atomic_text(args.output) as stream:
                stream.write(render_digest(state))
            for token, error in results.items():
                print(f"{boards[token]}: {error or 'OK'}")
            count = sum(bool(job["is_open"]) for job in state["jobs"])
            print(f"{count} open listings | Digest: {args.output.resolve()}")
            return 1 if any(error is not None for error in results.values()) else 0

        if not args.db.is_file():
            raise ValueError("Run 'python -m tracker refresh' first to create the local job history")
        state = read_state(args.db)
        count = export_labels(args.output, ranked_jobs(state), state["jobs"])
        print(f"{count} rows | Private labels: {args.output.resolve()}")
        return 0
    except (OSError, ValueError, sqlite3.Error, csv.Error) as error:
        print(f"Tracker: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
