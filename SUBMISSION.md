# Submission

## Candidate
- **Name:** Lakhan
- **Assignment chosen:** A (Data)
- **Time spent (honest estimate):** 6h

## How to run
```bash
# Setup virtual environment
python -m venv .venv
# Linux/macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Generate test data (if not already present)
python assignment-a-data/tools/generate_data.py --seed 42 --scale small --out data

# Ingest drops in sequence
python run.py ingest data/drop_1
python run.py ingest data/drop_2

# Generate output reports
python run.py report --out reports/

# Run tests
python test_runner.py
# (or pytest)
```

## Design
I structured the solution around SQLite with an append-only ledger for calls and a deduplicated canonical store for leads.

1. **Ingestion & Validation:**
   - Files are processed strictly in drop, workflow, and filename order.
   - Headers are normalized (lowercase, trimmed, collapse underscores/spaces) and mapped to schema fields and aliases.
   - Blank rows are ignored completely and don't advance row counters.
   - For valid data rows, fields are evaluated in the exact order declared in the workflow schema. The first required failure or invalid phone rejects the row immediately into `rejections.csv`. Optional field errors (e.g., bad dates or non-enum language codes) are stored as null and increment `field_warnings` only if the row wasn't rejected.
   - Lead identities are tracked by `(workflow_id, canonical_phone)`. The first file stem creates the lead and permanently sets its `batch_id`. Later records update non-null fields (last-write-wins) and increment `duplicate_lead_rows_merged`. Schema defaults kick in after merging for any fields that remain null.

2. **Append-Only Interaction Ledger:**
   - Raw event JSON lines are streamed in. Malformed lines and duplicate `event_id`s are dropped and counted.
   - Valid events are inserted directly into `interaction_ledger`. Supervisor overrides are appended as new `disposition.override` records—prior events are never mutated.
   - Events are linked to leads via `(workflow_id, canonical_phone)`. Overrides don't carry a phone number, so they link through their `call_id` to the lead established by the other events on that call.
   - Events that arrive before their lead are inserted with `is_orphan = 1`. When `drop_2` introduces the missing leads, an orphan-resolution pass relinks those events automatically.

3. **PII Protection:**
   - The database stores zero plaintext phone numbers or names.
   - To join events to leads without exposing phone numbers, I use an HMAC-SHA256 blind index (`phone_hash`) generated with a secret key. This lets SQLite index and join events directly on the hash.
   - For reports, phones are formatted as `+91******` followed by the last 4 digits. Reversible fields (`customer_name`, raw phone) are encrypted at rest using authenticated counter-mode encryption (HMAC-CTR).

4. **Reporting in SQL:**
   - All report derivations run directly in SQLite rather than Python loops.
   - To make it fast, the reporting step materializes three indexed temp tables (`temp_call_summary`, `temp_lead_current_disp`, `temp_lead_facts`) in a single pass. This derives call initiation, connectivity, latest effective dispositions (where supervisor overrides beat agent sets), and lead buckets without re-running window functions over and over.

## Decisions & assumptions

| Decision | Why | Alternative considered |
|---|---|---|
| HMAC-SHA256 blind index for phone lookup | Enables indexed SQL joins between calls and leads without storing or searching plaintext phone numbers. | Storing raw phones in DB (violates PII spec) or decrypting leads in Python memory on every event (too slow). |
| Indexed temp tables for SQL aggregation | Avoids evaluating CTE window functions repeatedly inside correlated subqueries, reducing report generation time from seconds to ~0.3s. | Pure CTEs (chokes SQLite with large datasets) or computing report metrics in Python application code. |
| Strict schema-order field validation | The prompt states: "walk the fields in schema order. The first failure becomes the row's single reason." Checking fields out of order would misclassify errors (e.g. flagging phone when customer_name was already missing). | Validating phone before name or arbitrary dictionary iteration. |
| Warnings counted only on non-rejected rows | Spec explicitly says: "Count warnings only for rows that are not rejected." | Counting warnings on every row including rejected ones. |
| Idempotency via file content hash | Calculating SHA-256 on ingested files allows safe re-runs of `ingest` without duplicating rows or re-counting stats. | Timestamp checking or wiping the database on every run. |
| Last-write-wins for non-null fields on duplicate leads | Keeps the earliest `batch_id` while allowing updated details (like newer premium amounts or policy dates) to overwrite older values. | Rejecting duplicates or overwriting the original `batch_id`. |

## Failure modes considered
- **Out-of-order and early events:** Handled cleanly by flagging unlinked events as orphans and re-running the linking logic when subsequent drops are ingested.
- **Corrupted / truncated JSON lines:** Handled in the event parser; malformed lines don't crash the script and are counted in `malformed_events`.
- **Re-running drops:** Tracked in `ingested_files`. Re-running `drop_1` changes nothing in the tables or report output.
- **Unknown dispositions:** Codes like `RENEWD` that don't match `dispositions.csv` are tracked in `unknown_disposition_events` and ignored when resolving the call's effective disposition.
- **Process crash during ingestion:** Database writes use transactions (`with conn:`), so partial file writes roll back cleanly.
- **Not handled:** Concurrent multi-process write locks on the same SQLite file. In a live system, I'd back this with PostgreSQL.

## Testing
- **What I tested:**
  - Phone normalisation with valid and invalid Indian formats (9-digit, landline prefixes, US country codes, spaces, dashes).
  - Date formats (day-first parsing, impossible dates like 31/02 and month 13).
  - Decimal parsing (currency prefixes `₹`, `Rs.`, `INR`, thousand separators, negative numbers).
  - Effective disposition priority (overrides beating sets, latest timestamp winning, unknown codes ignored).
  - Lead bucket logic (`yet_to_reach_out`, `completed`, `in_progress_telephony_failed`, `in_progress_dialed`).
  - PII encryption round-trips and tamper checks.
  - End-to-end multi-drop ingestion and idempotency verification.
- **What I didn't test:**
  - Multi-threaded database contention, since SQLite is running in single-process mode here.

## Results
- **Small dataset (`--scale small`):**
  - 2,057 lead rows ingested (108 rejected, 57 duplicate merges, 1,892 final unique leads).
  - 8,745 event lines processed (8 malformed, 237 duplicates, 462 orphans, 18 unknown dispositions).
  - Total ingestion runtime: ~2 seconds.
  - Report generation runtime: ~0.3 seconds.
- **Observations:**
  - Orphan resolution worked as intended: drop 1 events that were unlinked were automatically reconciled once drop 2 was ingested.
  - Supervisor overrides without phone numbers resolved properly through `call_id`.

## If I had another day
1. **Streaming event ingestion:** Move from batch JSONL files to a Kafka or RabbitMQ consumer to process call events in real-time.
2. **Postgres backend:** Switch SQLite connection to PostgreSQL with connection pooling to handle high-concurrency writes from telephony webhooks.
3. **Dead-letter queue & alerting:** Add a retry queue for events that remain orphans after a 48-hour window so operations can inspect missing lead batches.
