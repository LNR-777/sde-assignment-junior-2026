from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
from pathlib import Path
from solution.crypto import encrypt_pii, hash_phone, mask_phone
from solution.normalizers import (
    normalize_date,
    normalize_decimal,
    normalize_enum,
    normalize_phone,
)
from solution.schema_loader import SchemaRegistry, WorkflowSchema


def compute_file_hash(file_path: Path) -> str:
    """Computes SHA-256 hash of a file for idempotency tracking."""
    hasher = hashlib.sha256()
    with open(file_path, "rb") as f:
        while chunk := f.read(65536):
            hasher.update(chunk)
    return hasher.hexdigest()


def ingest_lead_file(
    file_path: Path,
    workflow_schema: WorkflowSchema,
    conn: sqlite3.Connection,
) -> dict[str, int]:
    """Ingests a single lead batch CSV: normalizes headers, validates fields, and merges duplicates."""
    file_stem = file_path.stem  # e.g. "batch_001"
    wf_id = workflow_schema.workflow_id
    canonical_file_path = str(file_path.resolve())

    # Check if this exact file has already been ingested
    cursor = conn.cursor()
    cursor.execute(
        "SELECT file_hash FROM ingested_files WHERE file_path = ?",
        (canonical_file_path,),
    )
    existing_file = cursor.fetchone()
    current_hash = compute_file_hash(file_path)
    if existing_file and existing_file["file_hash"] == current_hash:
        # Already ingested; idempotent skip
        return {
            "lead_rows_total": 0,
            "lead_rows_rejected": 0,
            "duplicate_lead_rows_merged": 0,
            "field_warnings": 0,
        }

    # Load existing leads for this workflow into memory for fast deduplication & merging
    cursor.execute(
        "SELECT lead_id, phone_hash, batch_id, external_ref, attributes_json, customer_name_encrypted FROM leads WHERE workflow_id = ?",
        (wf_id,),
    )
    leads_by_phone_hash = {
        row["phone_hash"]: {
            "lead_id": row["lead_id"],
            "batch_id": row["batch_id"],
            "external_ref": row["external_ref"],
            "attributes": json.loads(row["attributes_json"]),
            "customer_name_encrypted": row["customer_name_encrypted"],
        }
        for row in cursor.fetchall()
    }

    stats = {
        "lead_rows_total": 0,
        "lead_rows_rejected": 0,
        "duplicate_lead_rows_merged": 0,
        "field_warnings": 0,
    }

    rejections_to_insert = []
    leads_to_insert = []
    leads_to_update = []

    with open(file_path, "r", encoding="utf-8-sig") as f:
        reader = csv.reader(f)
        try:
            raw_header = next(reader)
        except StopIteration:
            return stats  # empty file

        # Build header index mapping: column_index -> SchemaField
        col_to_field = {}
        for idx, col_name in enumerate(raw_header):
            col_norm = col_name.strip()
            # Try to match in schema header_map
            from solution.normalizers import normalize_header
            matched_field = workflow_schema.header_map.get(normalize_header(col_norm))
            if matched_field:
                col_to_field[idx] = matched_field

        row_number = 0
        for raw_row in reader:
            # Check if row is completely blank (every cell empty or whitespace)
            if not raw_row or all(c.strip() == "" for c in raw_row):
                continue  # Blank rows are skipped and NOT counted

            row_number += 1
            stats["lead_rows_total"] += 1

            # Map raw row cells by field name
            cell_by_field_name: dict[str, str] = {}
            for idx, val in enumerate(raw_row):
                if idx in col_to_field:
                    field_obj = col_to_field[idx]
                    trimmed = val.strip()
                    if trimmed:
                        cell_by_field_name[field_obj.name] = trimmed

            # Walk fields in schema order
            rejection_reason = None
            row_field_warnings = 0
            validated_values: dict[str, any] = {}

            for schema_field in workflow_schema.fields:
                val = cell_by_field_name.get(schema_field.name)

                # Check required
                if schema_field.required and (val is None or val == ""):
                    rejection_reason = f"missing_required:{schema_field.name}"
                    break

                if val is None or val == "":
                    validated_values[schema_field.name] = None
                    continue

                # Field type validations
                if schema_field.type == "string":
                    validated_values[schema_field.name] = val

                elif schema_field.type == "phone":
                    is_valid, canonical = normalize_phone(val)
                    if not is_valid:
                        rejection_reason = "invalid_phone"
                        break
                    validated_values[schema_field.name] = canonical

                elif schema_field.type == "date":
                    is_valid, parsed_date = normalize_date(val)
                    if not is_valid:
                        if schema_field.required:
                            rejection_reason = f"invalid_date:{schema_field.name}"
                            break
                        else:
                            # Optional field present but invalid -> store null, count field warning
                            row_field_warnings += 1
                            validated_values[schema_field.name] = None
                    else:
                        validated_values[schema_field.name] = parsed_date

                elif schema_field.type == "decimal":
                    is_valid, parsed_dec = normalize_decimal(val)
                    if not is_valid:
                        if schema_field.required:
                            rejection_reason = f"invalid_decimal:{schema_field.name}"
                            break
                        else:
                            # Optional field present but invalid -> store null, count field warning
                            row_field_warnings += 1
                            validated_values[schema_field.name] = None
                    else:
                        validated_values[schema_field.name] = parsed_dec

                elif schema_field.type == "enum":
                    is_valid, parsed_enum = normalize_enum(val, schema_field.values)
                    if not is_valid:
                        if schema_field.required:
                            rejection_reason = f"invalid_enum:{schema_field.name}"
                            break
                        else:
                            # Optional field present but invalid -> store null, count field warning
                            row_field_warnings += 1
                            validated_values[schema_field.name] = None
                    else:
                        validated_values[schema_field.name] = parsed_enum

            # If rejected
            if rejection_reason:
                stats["lead_rows_rejected"] += 1
                rejections_to_insert.append(
                    (wf_id, file_stem, row_number, rejection_reason)
                )
                continue

            # Row is valid: count field warnings
            stats["field_warnings"] += row_field_warnings

            # Lead identity is (workflow_id, canonical phone)
            canonical_phone = validated_values["phone"]
            p_hash = hash_phone(canonical_phone)
            external_ref = str(validated_values.get(workflow_schema.reference_field, ""))

            # Attributes dict (exclude phone and customer_name since they are handled separately)
            attr_keys = [
                f.name
                for f in workflow_schema.fields
                if f.name not in ("phone", "customer_name", workflow_schema.reference_field)
            ]
            row_attrs = {k: validated_values.get(k) for k in attr_keys}

            if p_hash in leads_by_phone_hash:
                # Duplicate lead: merge
                stats["duplicate_lead_rows_merged"] += 1
                existing_lead = leads_by_phone_hash[p_hash]

                # Overwrite only fields where this row's value is non-null (last write wins)
                new_cust_name_enc = existing_lead["customer_name_encrypted"]
                if validated_values.get("customer_name"):
                    new_cust_name_enc = encrypt_pii(validated_values["customer_name"])
                    existing_lead["customer_name_encrypted"] = new_cust_name_enc

                new_ref = existing_lead["external_ref"]
                if external_ref:
                    new_ref = external_ref
                    existing_lead["external_ref"] = new_ref

                merged_attrs = existing_lead["attributes"]
                for k, v in row_attrs.items():
                    if v is not None:
                        merged_attrs[k] = v

                leads_to_update.append(
                    (
                        new_cust_name_enc,
                        new_ref,
                        json.dumps(merged_attrs),
                        existing_lead["lead_id"],
                    )
                )
            else:
                # New lead
                p_masked = mask_phone(canonical_phone)
                p_enc = encrypt_pii(canonical_phone)
                c_name_enc = encrypt_pii(validated_values.get("customer_name", ""))

                leads_by_phone_hash[p_hash] = {
                    "lead_id": None,  # Will be assigned
                    "batch_id": file_stem,
                    "external_ref": external_ref,
                    "attributes": row_attrs,
                    "customer_name_encrypted": c_name_enc,
                }

                leads_to_insert.append(
                    (
                        wf_id,
                        p_hash,
                        p_masked,
                        p_enc,
                        c_name_enc,
                        external_ref,
                        file_stem,
                        json.dumps(row_attrs),
                    )
                )

    # Persist in a single database transaction
    with conn:
        if rejections_to_insert:
            conn.executemany(
                """
                INSERT OR IGNORE INTO rejections (workflow_id, batch_id, row_number, reason)
                VALUES (?, ?, ?, ?)
                """,
                rejections_to_insert,
            )

        if leads_to_insert:
            conn.executemany(
                """
                INSERT INTO leads (
                    workflow_id, phone_hash, phone_masked, phone_encrypted,
                    customer_name_encrypted, external_ref, batch_id, attributes_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                leads_to_insert,
            )

        if leads_to_update:
            conn.executemany(
                """
                UPDATE leads
                SET customer_name_encrypted = ?, external_ref = ?, attributes_json = ?, updated_at = CURRENT_TIMESTAMP
                WHERE lead_id = ?
                """,
                leads_to_update,
            )

        # Record file in ingested_files
        conn.execute(
            """
            INSERT OR REPLACE INTO ingested_files (
                file_path, file_hash, file_type,
                lead_rows_total, lead_rows_rejected,
                duplicate_lead_rows_merged, field_warnings
            ) VALUES (?, ?, 'leads', ?, ?, ?, ?)
            """,
            (
                canonical_file_path,
                current_hash,
                stats["lead_rows_total"],
                stats["lead_rows_rejected"],
                stats["duplicate_lead_rows_merged"],
                stats["field_warnings"],
            ),
        )

    return stats


def ingest_all_leads_in_drop(drop_dir: Path, registry: SchemaRegistry, conn: sqlite3.Connection):
    """
    Ingests all lead files in <drop_dir>/leads:
    Files are processed in workflow and filename order.
    """
    leads_base = drop_dir / "leads"
    if not leads_base.exists():
        return

    # Process workflows in sorted order
    for wf_dir in sorted(leads_base.iterdir()):
        if not wf_dir.is_dir():
            continue
        wf_id = wf_dir.name
        schema = registry.get_workflow(wf_id)
        if not schema:
            continue

        # Process files in filename order
        csv_files = sorted(wf_dir.glob("*.csv"))
        for csv_file in csv_files:
            ingest_lead_file(csv_file, schema, conn)
