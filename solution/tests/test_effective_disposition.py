import sqlite3
import unittest
from solution.db import init_db
from solution.reports import prepare_reporting_tables, sync_disposition_catalogue
from solution.schema_loader import SchemaRegistry


class TestEffectiveDisposition(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        self.registry = SchemaRegistry()
        sync_disposition_catalogue(self.registry, self.conn)

    def tearDown(self):
        self.conn.close()

    def test_effective_disposition_precedence(self):
        with self.conn:
            # Create lead
            self.conn.execute(
                """
                INSERT INTO leads (lead_id, workflow_id, phone_hash, phone_masked, phone_encrypted, customer_name_encrypted, external_ref, batch_id, attributes_json)
                VALUES (1, 'motor_renewal', 'hash1', '+91******1234', 'enc', 'enc', 'REF1', 'batch_001', '{}')
                """
            )

            # Call 1: has initiated, set 'payment_link_sent', then supervisor override 'renewed'
            self.conn.execute(
                """
                INSERT INTO interaction_ledger (event_id, workflow_id, call_id, event_type, occurred_at_ist, occurred_at_raw, phone_hash, lead_id, payload_json, is_orphan)
                VALUES
                ('e1', 'motor_renewal', 'c1', 'call.initiated', '2026-10-01T10:00:00+05:30', '...', 'hash1', 1, '{}', 0),
                ('e2', 'motor_renewal', 'c1', 'disposition.set', '2026-10-01T10:05:00+05:30', '...', 'hash1', 1, '{"disposition_code": "payment_link_sent"}', 0),
                ('e3', 'motor_renewal', 'c1', 'disposition.override', '2026-10-01T12:00:00+05:30', '...', NULL, 1, '{"disposition_code": "renewed"}', 0)
                """
            )

            # Call 2: has initiated, unknown disposition 'RENEWD', then valid set 'callback_requested'
            self.conn.execute(
                """
                INSERT INTO interaction_ledger (event_id, workflow_id, call_id, event_type, occurred_at_ist, occurred_at_raw, phone_hash, lead_id, payload_json, is_orphan)
                VALUES
                ('e4', 'motor_renewal', 'c2', 'call.initiated', '2026-10-02T10:00:00+05:30', '...', 'hash1', 1, '{}', 0),
                ('e5', 'motor_renewal', 'c2', 'disposition.set', '2026-10-02T10:05:00+05:30', '...', 'hash1', 1, '{"disposition_code": "RENEWD"}', 0),
                ('e6', 'motor_renewal', 'c2', 'disposition.set', '2026-10-02T10:06:00+05:30', '...', 'hash1', 1, '{"disposition_code": "callback_requested"}', 0)
                """
            )

        prepare_reporting_tables(self.conn)
        cursor = self.conn.cursor()

        cursor.execute("SELECT call_id, effective_disposition, is_terminal, is_success FROM temp_call_summary ORDER BY call_id")
        rows = {r["call_id"]: r for r in cursor.fetchall()}

        # Call 1: override took precedence over set
        self.assertEqual(rows["c1"]["effective_disposition"], "renewed")
        self.assertEqual(rows["c1"]["is_terminal"], 1)
        self.assertEqual(rows["c1"]["is_success"], 1)

        # Call 2: ignored unknown disposition, took valid set
        self.assertEqual(rows["c2"]["effective_disposition"], "callback_requested")
        self.assertEqual(rows["c2"]["is_terminal"], 0)
        self.assertEqual(rows["c2"]["is_success"], 0)


if __name__ == "__main__":
    unittest.main()
