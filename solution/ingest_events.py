from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from solution.crypto import hash_phone
from solution.normalizers import normalize_phone, parse_event_timestamp
from solution.schema_loader import SchemaRegistry


def compute_file_hash(file_path: Path) -> str:
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def link_orphans(conn: sqlite3.Connection):
    """Resolves unlinked events against leads via phone hash and call_id."""
    with conn:
        # Step 1: Link events that have phone_hash directly to leads
        conn.execute(
            """
            UPDATE interaction_ledger
            SET lead_id = (
                SELECT leads.lead_id
                FROM leads
                WHERE leads.workflow_id = interaction_ledger.workflow_id
                  AND leads.phone_hash = interaction_ledger.phone_hash
            ),
            is_orphan = 0
            WHERE is_orphan = 1
              AND phone_hash IS NOT NULL
              AND EXISTS (
                SELECT 1 FROM leads
                WHERE leads.workflow_id = interaction_ledger.workflow_id
                  AND leads.phone_hash = interaction_ledger.phone_hash
              );
            """
        )

        # Step 2: Build fast lookup of call_id -> lead_id from already-linked events
        conn.execute("DROP TABLE IF EXISTS temp_call_to_lead;")
        conn.execute(
            """
            CREATE TEMP TABLE temp_call_to_lead AS
            SELECT call_id, lead_id
            FROM interaction_ledger
            WHERE lead_id IS NOT NULL
            GROUP BY call_id;
            """
        )
        conn.execute("CREATE INDEX idx_temp_c2l_cid ON temp_call_to_lead(call_id);")

        conn.execute(
            """
            UPDATE interaction_ledger
            SET lead_id = (
                SELECT t.lead_id
                FROM temp_call_to_lead t
                WHERE t.call_id = interaction_ledger.call_id
            ),
            is_orphan = 0
            WHERE is_orphan = 1
              AND call_id IN (SELECT call_id FROM temp_call_to_lead);
            """
        )
        conn.execute("DROP TABLE IF EXISTS temp_call_to_lead;")


def ingest_event_file(
    file_path: Path,
    conn: sqlite3.Connection,
) -> dict[str, int]:
    """Ingests a single events JSONL file into the interaction ledger."""
    canonical_file_path = str(file_path.resolve())

    cursor = conn.cursor()
    cursor.execute(
        "SELECT file_hash FROM ingested_files WHERE file_path = ?",
        (canonical_file_path,),
    )
    existing_file = cursor.fetchone()
    current_hash = compute_file_hash(file_path)
    if existing_file and existing_file["file_hash"] == current_hash:
        return {
            "events_total": 0,
            "malformed_events": 0,
            "duplicate_events_dropped": 0,
        }

    stats = {
        "events_total": 0,
        "malformed_events": 0,
        "duplicate_events_dropped": 0,
    }

    # Load known event IDs to deduplicate in memory + check DB
    cursor.execute("SELECT event_id FROM interaction_ledger")
    known_events = {row[0] for row in cursor.fetchall()}

    batch_to_insert = []

    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue

            stats["events_total"] += 1

            # 1. Malformed check
            try:
                data = json.loads(stripped)
            except Exception:
                stats["malformed_events"] += 1
                continue

            if not isinstance(data, dict):
                stats["malformed_events"] += 1
                continue

            event_id = data.get("event_id")
            if not event_id:
                stats["malformed_events"] += 1
                continue

            # 2. Duplicate check
            if event_id in known_events:
                stats["duplicate_events_dropped"] += 1
                continue
            known_events.add(event_id)

            event_type = data.get("event_type", "")
            workflow_id = data.get("workflow_id", "")
            call_id = data.get("call_id", "")
            occurred_at_raw = data.get("occurred_at", "")
            payload = data.get("payload", {})
            phone_raw = data.get("phone")

            try:
                dt_ist = parse_event_timestamp(occurred_at_raw)
                occurred_at_ist = dt_ist.strftime("%Y-%m-%dT%H:%M:%S+05:30")
            except Exception:
                stats["malformed_events"] += 1
                continue

            phone_hash_val = None
            if phone_raw:
                is_valid_phone, canonical_p = normalize_phone(phone_raw)
                if is_valid_phone:
                    phone_hash_val = hash_phone(canonical_p)

            batch_to_insert.append(
                (
                    event_id,
                    workflow_id,
                    call_id,
                    event_type,
                    occurred_at_ist,
                    occurred_at_raw,
                    phone_hash_val,
                    json.dumps(payload),
                    1,  # is_orphan = 1 initially until link_orphans runs
                )
            )

    # Persist in DB
    with conn:
        if batch_to_insert:
            conn.executemany(
                """
                INSERT INTO interaction_ledger (
                    event_id, workflow_id, call_id, event_type,
                    occurred_at_ist, occurred_at_raw, phone_hash,
                    payload_json, is_orphan
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                batch_to_insert,
            )

        conn.execute(
            """
            INSERT OR REPLACE INTO ingested_files (
                file_path, file_hash, file_type,
                events_total, malformed_events, duplicate_events_dropped
            ) VALUES (?, ?, 'events', ?, ?, ?)
            """,
            (
                canonical_file_path,
                current_hash,
                stats["events_total"],
                stats["malformed_events"],
                stats["duplicate_events_dropped"],
            ),
        )

    return stats


def ingest_all_events_in_drop(drop_dir: Path, conn: sqlite3.Connection):
    """
    Ingests all event files in <drop_dir>/events in filename order,
    then resolves orphan links once for the entire drop.
    """
    events_base = drop_dir / "events"
    if not events_base.exists():
        return

    jsonl_files = sorted(events_base.glob("*.jsonl"))
    for jf in jsonl_files:
        ingest_event_file(jf, conn)

    # Link orphans once per drop batch
    link_orphans(conn)
