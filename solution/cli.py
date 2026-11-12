from __future__ import annotations

import argparse
import sys
from pathlib import Path

from solution.config import DEFAULT_DB_PATH, DEFAULT_SCHEMAS_DIR
from solution.db import get_db_connection, init_db
from solution.ingest_events import ingest_all_events_in_drop, link_orphans
from solution.ingest_leads import ingest_all_leads_in_drop
from solution.reports import generate_all_reports
from solution.schema_loader import SchemaRegistry


def run_ingest(drop_path: Path, db_path: Path, schemas_dir: Path):
    if not drop_path.exists():
        print(f"Error: drop directory '{drop_path}' does not exist.", file=sys.stderr)
        sys.exit(1)

    print(f"--> Ingesting drop from: {drop_path}")
    conn = get_db_connection(db_path)
    init_db(conn)
    registry = SchemaRegistry(schemas_dir)

    # 1. Ingest leads in this drop
    print("  * Ingesting leads...")
    ingest_all_leads_in_drop(drop_path, registry, conn)

    # 2. Ingest events in this drop
    print("  * Ingesting events...")
    ingest_all_events_in_drop(drop_path, conn)

    # 3. Resolve any previously orphaned events that now match new leads
    print("  * Resolving orphan links...")
    link_orphans(conn)

    conn.close()
    print("--> Ingestion complete.")


def run_report(out_dir: Path, db_path: Path, schemas_dir: Path):
    print(f"--> Generating reports into: {out_dir}")
    conn = get_db_connection(db_path)
    init_db(conn)
    registry = SchemaRegistry(schemas_dir)

    generate_all_reports(out_dir, registry, conn)
    conn.close()
    print("--> Reports successfully generated.")


def main():
    parser = argparse.ArgumentParser(
        prog="solution",
        description="Interaction Ledger & Reporting Pipeline for Agentic Universe",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB_PATH,
        help="Path to SQLite database file (default: solution.db)",
    )
    parser.add_argument(
        "--schemas",
        type=Path,
        default=DEFAULT_SCHEMAS_DIR,
        help="Path to directory containing schema JSONs and dispositions.csv",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # Ingest command
    ingest_parser = subparsers.add_parser("ingest", help="Ingest a data drop (leads and events)")
    ingest_parser.add_argument("drop_path", type=Path, help="Path to drop folder (e.g. data/drop_1)")

    # Report command
    report_parser = subparsers.add_parser("report", help="Generate all reporting files")
    report_parser.add_argument("--out", type=Path, default=Path("reports"), help="Output directory for reports")

    args = parser.parse_args()

    if args.command == "ingest":
        run_ingest(args.drop_path, args.db, args.schemas)
    elif args.command == "report":
        run_report(args.out, args.db, args.schemas)


if __name__ == "__main__":
    main()
