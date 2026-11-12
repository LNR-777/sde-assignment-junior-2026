from __future__ import annotations

import csv
import json
import sqlite3
from pathlib import Path
from solution.schema_loader import SchemaRegistry


def sync_disposition_catalogue(registry: SchemaRegistry, conn: sqlite3.Connection):
    """Syncs the dispositions catalogue into the database for SQL aggregation."""
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS disposition_catalogue (
                workflow_id TEXT NOT NULL,
                code TEXT NOT NULL,
                label TEXT NOT NULL,
                is_terminal INTEGER NOT NULL,
                is_success INTEGER NOT NULL,
                PRIMARY KEY (workflow_id, code)
            );
            """
        )
        for (wf_id, code), rule in registry.dispositions.items():
            conn.execute(
                """
                INSERT OR REPLACE INTO disposition_catalogue (
                    workflow_id, code, label, is_terminal, is_success
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (wf_id, code, rule.label, 1 if rule.is_terminal else 0, 1 if rule.is_success else 0),
            )


def prepare_reporting_tables(conn: sqlite3.Connection):
    """Builds indexed temporary tables in SQLite for fast report generation."""
    with conn:
        conn.execute("DROP TABLE IF EXISTS temp_call_summary;")
        conn.execute("DROP TABLE IF EXISTS temp_lead_current_disp;")
        conn.execute("DROP TABLE IF EXISTS temp_lead_facts;")

        # 1. temp_call_summary
        conn.execute(
            """
            CREATE TEMP TABLE temp_call_summary AS
            WITH valid_initiated AS (
                SELECT
                    call_id,
                    workflow_id,
                    lead_id,
                    occurred_at_ist AS initiated_at_ist,
                    substr(occurred_at_ist, 1, 10) AS call_date_ist
                FROM interaction_ledger
                WHERE event_type = 'call.initiated' AND is_orphan = 0
            ),
            connected_calls AS (
                SELECT DISTINCT call_id
                FROM interaction_ledger
                WHERE event_type = 'call.connected' AND is_orphan = 0
            ),
            telephony_failed_calls AS (
                SELECT DISTINCT call_id
                FROM interaction_ledger
                WHERE event_type = 'call.telephony_failed' AND is_orphan = 0
            ),
            valid_dispositions AS (
                SELECT
                    l.call_id,
                    l.event_type,
                    l.occurred_at_ist,
                    json_extract(l.payload_json, '$.disposition_code') AS disp_code,
                    d.is_terminal,
                    d.is_success
                FROM interaction_ledger l
                JOIN disposition_catalogue d
                  ON d.workflow_id = l.workflow_id
                 AND d.code = json_extract(l.payload_json, '$.disposition_code')
                WHERE l.is_orphan = 0
                  AND l.event_type IN ('disposition.set', 'disposition.override')
            ),
            latest_overrides AS (
                SELECT
                    call_id,
                    disp_code,
                    is_terminal,
                    is_success,
                    ROW_NUMBER() OVER (PARTITION BY call_id ORDER BY occurred_at_ist DESC) as rn
                FROM valid_dispositions
                WHERE event_type = 'disposition.override'
            ),
            latest_sets AS (
                SELECT
                    call_id,
                    disp_code,
                    is_terminal,
                    is_success,
                    ROW_NUMBER() OVER (PARTITION BY call_id ORDER BY occurred_at_ist DESC) as rn
                FROM valid_dispositions
                WHERE event_type = 'disposition.set'
            )
            SELECT
                vi.call_id,
                vi.workflow_id,
                vi.lead_id,
                vi.initiated_at_ist,
                vi.call_date_ist,
                CASE WHEN cc.call_id IS NOT NULL THEN 1 ELSE 0 END AS is_connected,
                CASE WHEN tfc.call_id IS NOT NULL THEN 1 ELSE 0 END AS has_telephony_failed,
                COALESCE(lo.disp_code, ls.disp_code) AS effective_disposition,
                COALESCE(lo.is_terminal, ls.is_terminal, 0) AS is_terminal,
                COALESCE(lo.is_success, ls.is_success, 0) AS is_success
            FROM valid_initiated vi
            LEFT JOIN connected_calls cc ON vi.call_id = cc.call_id
            LEFT JOIN telephony_failed_calls tfc ON vi.call_id = tfc.call_id
            LEFT JOIN latest_overrides lo ON vi.call_id = lo.call_id AND lo.rn = 1
            LEFT JOIN latest_sets ls ON vi.call_id = ls.call_id AND ls.rn = 1 AND lo.disp_code IS NULL;
            """
        )
        conn.execute("CREATE INDEX idx_temp_cs_lead ON temp_call_summary(lead_id);")
        conn.execute("CREATE INDEX idx_temp_cs_wf_date ON temp_call_summary(workflow_id, call_date_ist);")

        # 2. temp_lead_current_disp (latest effective disposition per lead)
        conn.execute(
            """
            CREATE TEMP TABLE temp_lead_current_disp AS
            WITH ranked AS (
                SELECT
                    lead_id,
                    effective_disposition,
                    ROW_NUMBER() OVER (
                        PARTITION BY lead_id
                        ORDER BY initiated_at_ist DESC, call_id DESC
                    ) AS rn
                FROM temp_call_summary
                WHERE effective_disposition IS NOT NULL
            )
            SELECT lead_id, effective_disposition AS current_disposition
            FROM ranked
            WHERE rn = 1;
            """
        )
        conn.execute("CREATE INDEX idx_temp_disp_lead ON temp_lead_current_disp(lead_id);")

        # 3. temp_lead_facts
        conn.execute(
            """
            CREATE TEMP TABLE temp_lead_facts AS
            SELECT
                l.lead_id,
                l.workflow_id,
                l.external_ref,
                l.batch_id,
                l.phone_masked,
                COUNT(cs.call_id) AS total_calls,
                COALESCE(SUM(cs.is_connected), 0) AS connected_calls,
                MAX(cs.initiated_at_ist) AS last_call_at,
                lcd.current_disposition,
                CASE WHEN MAX(cs.is_success) = 1 THEN 1 ELSE 0 END AS ever_success,
                CASE
                    WHEN COUNT(cs.call_id) = 0 THEN 'yet_to_reach_out'
                    WHEN MAX(cs.is_terminal) = 1 THEN 'completed'
                    WHEN MIN(cs.has_telephony_failed) = 1 THEN 'in_progress_telephony_failed'
                    ELSE 'in_progress_dialed'
                END AS bucket
            FROM leads l
            LEFT JOIN temp_call_summary cs ON l.lead_id = cs.lead_id
            LEFT JOIN temp_lead_current_disp lcd ON l.lead_id = lcd.lead_id
            GROUP BY l.lead_id;
            """
        )
        conn.execute("CREATE INDEX idx_temp_lf_wf_batch ON temp_lead_facts(workflow_id, batch_id);")
        conn.execute("CREATE INDEX idx_temp_lf_wf_ref ON temp_lead_facts(workflow_id, external_ref);")


def generate_all_reports(
    output_dir: Path,
    registry: SchemaRegistry,
    conn: sqlite3.Connection,
):
    """Generates all five report files into output_dir."""
    output_dir.mkdir(parents=True, exist_ok=True)
    sync_disposition_catalogue(registry, conn)
    prepare_reporting_tables(conn)

    cursor = conn.cursor()

    # 1. batch_summary.csv
    cursor.execute(
        """
        SELECT
            workflow_id,
            batch_id,
            COUNT(*) AS total_leads,
            SUM(CASE WHEN bucket = 'yet_to_reach_out' THEN 1 ELSE 0 END) AS yet_to_reach_out,
            SUM(CASE WHEN bucket = 'in_progress_telephony_failed' THEN 1 ELSE 0 END) AS in_progress_telephony_failed,
            SUM(CASE WHEN bucket = 'in_progress_dialed' THEN 1 ELSE 0 END) AS in_progress_dialed,
            SUM(CASE WHEN bucket = 'completed' THEN 1 ELSE 0 END) AS completed
        FROM temp_lead_facts
        GROUP BY workflow_id, batch_id
        ORDER BY workflow_id ASC, batch_id ASC;
        """
    )
    batch_rows = cursor.fetchall()
    with open(output_dir / "batch_summary.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "workflow_id",
            "batch_id",
            "total_leads",
            "yet_to_reach_out",
            "in_progress_telephony_failed",
            "in_progress_dialed",
            "completed",
        ])
        for r in batch_rows:
            writer.writerow([
                r["workflow_id"],
                r["batch_id"],
                r["total_leads"],
                r["yet_to_reach_out"],
                r["in_progress_telephony_failed"],
                r["in_progress_dialed"],
                r["completed"],
            ])

    # 2. daily_overview.csv
    cursor.execute(
        """
        SELECT
            workflow_id,
            call_date_ist AS date,
            COUNT(call_id) AS total_calls,
            SUM(is_connected) AS connected_calls,
            PRINTF('%.4f', CAST(SUM(is_connected) AS FLOAT) / COUNT(call_id)) AS connectivity_rate,
            COUNT(DISTINCT CASE WHEN is_success = 1 THEN lead_id END) AS success_leads
        FROM temp_call_summary
        GROUP BY workflow_id, call_date_ist
        HAVING COUNT(call_id) > 0
        ORDER BY workflow_id ASC, call_date_ist ASC;
        """
    )
    daily_rows = cursor.fetchall()
    with open(output_dir / "daily_overview.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "workflow_id",
            "date",
            "total_calls",
            "connected_calls",
            "connectivity_rate",
            "success_leads",
        ])
        for r in daily_rows:
            writer.writerow([
                r["workflow_id"],
                r["date"],
                r["total_calls"],
                r["connected_calls"],
                r["connectivity_rate"],
                r["success_leads"],
            ])

    # 3. lead_report.csv
    cursor.execute(
        """
        SELECT
            workflow_id,
            external_ref,
            batch_id,
            phone_masked,
            bucket,
            total_calls,
            connected_calls,
            COALESCE(last_call_at, '') AS last_call_at,
            COALESCE(current_disposition, '') AS current_disposition,
            CASE WHEN ever_success = 1 THEN 'true' ELSE 'false' END AS ever_success
        FROM temp_lead_facts
        ORDER BY workflow_id ASC, external_ref ASC;
        """
    )
    lead_rows = cursor.fetchall()
    with open(output_dir / "lead_report.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            "workflow_id",
            "external_ref",
            "batch_id",
            "phone_masked",
            "bucket",
            "total_calls",
            "connected_calls",
            "last_call_at",
            "current_disposition",
            "ever_success",
        ])
        for r in lead_rows:
            writer.writerow([
                r["workflow_id"],
                r["external_ref"],
                r["batch_id"],
                r["phone_masked"],
                r["bucket"],
                r["total_calls"],
                r["connected_calls"],
                r["last_call_at"],
                r["current_disposition"],
                r["ever_success"],
            ])

    # 4. rejections.csv (No PII)
    cursor.execute(
        """
        SELECT
            workflow_id,
            batch_id,
            row_number,
            reason
        FROM rejections
        ORDER BY workflow_id ASC, batch_id ASC, row_number ASC;
        """
    )
    rej_rows = cursor.fetchall()
    with open(output_dir / "rejections.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["workflow_id", "batch_id", "row_number", "reason"])
        for r in rej_rows:
            writer.writerow([
                r["workflow_id"],
                r["batch_id"],
                r["row_number"],
                r["reason"],
            ])

    # 5. data_quality.json
    cursor.execute(
        """
        SELECT
            COALESCE(SUM(lead_rows_total), 0) AS lead_rows_total,
            COALESCE(SUM(lead_rows_rejected), 0) AS lead_rows_rejected,
            COALESCE(SUM(duplicate_lead_rows_merged), 0) AS duplicate_lead_rows_merged,
            COALESCE(SUM(field_warnings), 0) AS field_warnings
        FROM ingested_files
        WHERE file_type = 'leads';
        """
    )
    leads_metrics = cursor.fetchone()

    cursor.execute(
        """
        SELECT
            COALESCE(SUM(events_total), 0) AS events_total,
            COALESCE(SUM(malformed_events), 0) AS malformed_events,
            COALESCE(SUM(duplicate_events_dropped), 0) AS duplicate_events_dropped
        FROM ingested_files
        WHERE file_type = 'events';
        """
    )
    events_metrics = cursor.fetchone()

    cursor.execute(
        """
        SELECT reason, COUNT(*) AS cnt
        FROM rejections
        GROUP BY reason
        ORDER BY reason ASC;
        """
    )
    rejections_by_reason = {row["reason"]: row["cnt"] for row in cursor.fetchall()}

    cursor.execute("SELECT COUNT(*) AS cnt FROM interaction_ledger WHERE is_orphan = 1;")
    orphan_events = cursor.fetchone()["cnt"]

    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM interaction_ledger l
        WHERE l.is_orphan = 0
          AND l.event_type IN ('disposition.set', 'disposition.override')
          AND NOT EXISTS (
              SELECT 1 FROM disposition_catalogue d
              WHERE d.workflow_id = l.workflow_id
                AND d.code = json_extract(l.payload_json, '$.disposition_code')
          );
        """
    )
    unknown_disposition_events = cursor.fetchone()["cnt"]

    data_quality = {
        "lead_rows_total": leads_metrics["lead_rows_total"],
        "lead_rows_rejected": leads_metrics["lead_rows_rejected"],
        "rejections_by_reason": rejections_by_reason,
        "duplicate_lead_rows_merged": leads_metrics["duplicate_lead_rows_merged"],
        "field_warnings": leads_metrics["field_warnings"],
        "events_total": events_metrics["events_total"],
        "malformed_events": events_metrics["malformed_events"],
        "duplicate_events_dropped": events_metrics["duplicate_events_dropped"],
        "orphan_events": orphan_events,
        "unknown_disposition_events": unknown_disposition_events,
    }

    with open(output_dir / "data_quality.json", "w", encoding="utf-8") as f:
        json.dump(data_quality, f, indent=2)
