# AI Usage Disclosure

## Tools used
- Gemini / Claude

## What I used them for
- Generating initial boilerplate and test cases for regexes (date formats, currency symbols, and phone prefixes).
- Reviewing SQLite SQL syntax for window functions.
- Double-checking edge cases in the specification (like verifying day-first date parsing rules).

## What I wrote or decided myself
- The core database schema and table indexing design (`leads`, `interaction_ledger`, `rejections`, `ingested_files`).
- The PII strategy: choosing HMAC-SHA256 blind indexing so events could be joined to leads on phone numbers in SQL without ever storing raw phone numbers.
- The two-step orphan linking algorithm (first linking phone events to leads, then linking phoneless overrides by `call_id`).
- The temporary table strategy in SQLite to speed up report aggregations and avoid slow correlated CTE subqueries.
- The pipeline's CLI design and overall project file layout.

## Where the AI was wrong or unhelpful
- **Correlated CTE performance:** The AI initially drafted the reporting logic using nested CTEs with window functions referenced in subqueries. When running on SQLite, this caused SQLite to re-evaluate the full CTE for every single lead row, making the report query hang. I spotted the issue in the query plan, killed it, and replaced the CTEs with indexed temporary tables, which brought the query time down to under half a second.
- **Field warning counting order:** An initial suggestion from the AI incremented the field warning counter during the validation pass before checking if the row was rejected. The spec states: *"Count warnings only for rows that are not rejected."* I caught this and fixed the logic so warnings are only added to the running total if the row passes all schema checks.

## How I verified AI-generated code
- Wrote 15 automated unit and integration tests (`python test_runner.py`) testing phone normalisation, date edge cases, decimal handling, bucket logic, and effective disposition overrides.
- Checked mathematical consistency across reports (e.g. confirming `total_leads` in `batch_summary.csv` equals the sum of the four buckets, and that `lead_rows_total - lead_rows_rejected - duplicate_lead_rows_merged = total_leads`).
- Ran idempotency checks: ran `drop_1` twice to confirm that file hashes prevented duplicate rows or incremented metrics.
