from __future__ import annotations

import sqlite3
from pathlib import Path
from solution.config import DEFAULT_DB_PATH


def get_db_connection(db_path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Creates a connection to SQLite with WAL mode and foreign keys enabled."""
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA synchronous = NORMAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: sqlite3.Connection):
    """Initializes all necessary tables and indexes if they do not exist."""
    with conn:
        conn.executescript(
            """
            -- Ingested files log for idempotency and per-file audit stats
            CREATE TABLE IF NOT EXISTS ingested_files (
                file_path TEXT PRIMARY KEY,
                file_hash TEXT NOT NULL,
                file_type TEXT NOT NULL,
                lead_rows_total INTEGER NOT NULL DEFAULT 0,
                lead_rows_rejected INTEGER NOT NULL DEFAULT 0,
                duplicate_lead_rows_merged INTEGER NOT NULL DEFAULT 0,
                field_warnings INTEGER NOT NULL DEFAULT 0,
                events_total INTEGER NOT NULL DEFAULT 0,
                malformed_events INTEGER NOT NULL DEFAULT 0,
                duplicate_events_dropped INTEGER NOT NULL DEFAULT 0,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );

            -- Canonical leads table (PII protected at rest)
            CREATE TABLE IF NOT EXISTS leads (
                lead_id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                phone_hash TEXT NOT NULL,
                phone_masked TEXT NOT NULL,
                phone_encrypted TEXT NOT NULL,
                customer_name_encrypted TEXT NOT NULL,
                external_ref TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                attributes_json TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(workflow_id, phone_hash)
            );

            -- Rejections table (no PII)
            CREATE TABLE IF NOT EXISTS rejections (
                rejection_id INTEGER PRIMARY KEY AUTOINCREMENT,
                workflow_id TEXT NOT NULL,
                batch_id TEXT NOT NULL,
                row_number INTEGER NOT NULL,
                reason TEXT NOT NULL,
                UNIQUE(workflow_id, batch_id, row_number)
            );

            -- Append-only interaction ledger
            CREATE TABLE IF NOT EXISTS interaction_ledger (
                event_id TEXT PRIMARY KEY,
                workflow_id TEXT NOT NULL,
                call_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                occurred_at_ist TEXT NOT NULL,
                occurred_at_raw TEXT NOT NULL,
                phone_hash TEXT,
                lead_id INTEGER,
                payload_json TEXT NOT NULL,
                is_orphan INTEGER NOT NULL DEFAULT 1,
                FOREIGN KEY(lead_id) REFERENCES leads(lead_id)
            );

            -- Indexes for fast query and join performance
            CREATE INDEX IF NOT EXISTS idx_leads_wf_phone ON leads(workflow_id, phone_hash);
            CREATE INDEX IF NOT EXISTS idx_leads_wf_ref ON leads(workflow_id, external_ref);
            CREATE INDEX IF NOT EXISTS idx_rejections_lookup ON rejections(workflow_id, batch_id, row_number);
            CREATE INDEX IF NOT EXISTS idx_ledger_call_id ON interaction_ledger(call_id);
            CREATE INDEX IF NOT EXISTS idx_ledger_lead_id ON interaction_ledger(lead_id);
            CREATE INDEX IF NOT EXISTS idx_ledger_wf_phone ON interaction_ledger(workflow_id, phone_hash);
            CREATE INDEX IF NOT EXISTS idx_ledger_occurred_at ON interaction_ledger(occurred_at_ist);
            CREATE INDEX IF NOT EXISTS idx_ledger_orphan ON interaction_ledger(is_orphan);
            """
        )
