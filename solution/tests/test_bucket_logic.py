import sqlite3
import unittest
from solution.db import init_db
from solution.reports import prepare_reporting_tables, sync_disposition_catalogue
from solution.schema_loader import SchemaRegistry


class TestBucketLogic(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        self.registry = SchemaRegistry()
        sync_disposition_catalogue(self.registry, self.conn)

    def tearDown(self):
        self.conn.close()

    def test_bucket_derivations(self):
        with self.conn:
            # Lead 1: No calls -> yet_to_reach_out
            self.conn.execute(
                """
                INSERT INTO leads (lead_id, workflow_id, phone_hash, phone_masked, phone_encrypted, customer_name_encrypted, external_ref, batch_id, attributes_json)
                VALUES (1, 'motor_renewal', 'h1', '+91******1111', 'e', 'e', 'R1', 'b1', '{}')
                """
            )

            # Lead 2: Has a terminal call -> completed
            self.conn.execute(
                """
                INSERT INTO leads (lead_id, workflow_id, phone_hash, phone_masked, phone_encrypted, customer_name_encrypted, external_ref, batch_id, attributes_json)
                VALUES (2, 'motor_renewal', 'h2', '+91******2222', 'e', 'e', 'R2', 'b1', '{}')
                """
            )
            self.conn.execute(
                """
                INSERT INTO interaction_ledger (event_id, workflow_id, call_id, event_type, occurred_at_ist, occurred_at_raw, phone_hash, lead_id, payload_json, is_orphan)
                VALUES
                ('e1', 'motor_renewal', 'c2_1', 'call.initiated', '2026-10-01T10:00:00+05:30', '...', 'h2', 2, '{}', 0),
                ('e2', 'motor_renewal', 'c2_1', 'disposition.set', '2026-10-01T10:05:00+05:30', '...', 'h2', 2, '{"disposition_code": "renewed"}', 0)
                """
            )

            # Lead 3: All calls failed telephony -> in_progress_telephony_failed
            self.conn.execute(
                """
                INSERT INTO leads (lead_id, workflow_id, phone_hash, phone_masked, phone_encrypted, customer_name_encrypted, external_ref, batch_id, attributes_json)
                VALUES (3, 'motor_renewal', 'h3', '+91******3333', 'e', 'e', 'R3', 'b1', '{}')
                """
            )
            self.conn.execute(
                """
                INSERT INTO interaction_ledger (event_id, workflow_id, call_id, event_type, occurred_at_ist, occurred_at_raw, phone_hash, lead_id, payload_json, is_orphan)
                VALUES
                ('e3', 'motor_renewal', 'c3_1', 'call.initiated', '2026-10-01T10:00:00+05:30', '...', 'h3', 3, '{}', 0),
                ('e4', 'motor_renewal', 'c3_1', 'call.telephony_failed', '2026-10-01T10:00:05+05:30', '...', 'h3', 3, '{"reason": "sip_503"}', 0),
                ('e5', 'motor_renewal', 'c3_2', 'call.initiated', '2026-10-02T10:00:00+05:30', '...', 'h3', 3, '{}', 0),
                ('e6', 'motor_renewal', 'c3_2', 'call.telephony_failed', '2026-10-02T10:00:05+05:30', '...', 'h3', 3, '{"reason": "carrier_rejected"}', 0)
                """
            )

            # Lead 4: Dialed and non-terminal -> in_progress_dialed
            self.conn.execute(
                """
                INSERT INTO leads (lead_id, workflow_id, phone_hash, phone_masked, phone_encrypted, customer_name_encrypted, external_ref, batch_id, attributes_json)
                VALUES (4, 'motor_renewal', 'h4', '+91******4444', 'e', 'e', 'R4', 'b1', '{}')
                """
            )
            self.conn.execute(
                """
                INSERT INTO interaction_ledger (event_id, workflow_id, call_id, event_type, occurred_at_ist, occurred_at_raw, phone_hash, lead_id, payload_json, is_orphan)
                VALUES
                ('e7', 'motor_renewal', 'c4_1', 'call.initiated', '2026-10-01T10:00:00+05:30', '...', 'h4', 4, '{}', 0),
                ('e8', 'motor_renewal', 'c4_1', 'call.connected', '2026-10-01T10:00:10+05:30', '...', 'h4', 4, '{}', 0),
                ('e9', 'motor_renewal', 'c4_1', 'disposition.set', '2026-10-01T10:05:00+05:30', '...', 'h4', 4, '{"disposition_code": "payment_link_sent"}', 0)
                """
            )

        prepare_reporting_tables(self.conn)
        cursor = self.conn.cursor()
        cursor.execute("SELECT lead_id, bucket FROM temp_lead_facts ORDER BY lead_id")
        buckets = {r["lead_id"]: r["bucket"] for r in cursor.fetchall()}

        self.assertEqual(buckets[1], "yet_to_reach_out")
        self.assertEqual(buckets[2], "completed")
        self.assertEqual(buckets[3], "in_progress_telephony_failed")
        self.assertEqual(buckets[4], "in_progress_dialed")


if __name__ == "__main__":
    unittest.main()
