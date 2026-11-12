import json
import sqlite3
import unittest
import tempfile
from pathlib import Path
from solution.cli import run_ingest, run_report


class TestPipelineE2E(unittest.TestCase):
    def test_pipeline_end_to_end_and_idempotency(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            db_file = tmp_path / "test.db"
            reports_dir = tmp_path / "reports"
            project_root = Path(__file__).resolve().parent.parent.parent
            schemas_dir = project_root / "assignment-a-data" / "schemas"
            drop1 = project_root / "data" / "drop_1"
            drop2 = project_root / "data" / "drop_2"

            if not drop1.exists() or not drop2.exists():
                self.skipTest("Generated data drops not present")

            # 1. Ingest drop 1
            run_ingest(drop1, db_file, schemas_dir)

            # 2. Ingest drop 1 again (Idempotency test: should change nothing)
            run_ingest(drop1, db_file, schemas_dir)

            # 3. Ingest drop 2
            run_ingest(drop2, db_file, schemas_dir)

            # 4. Generate reports
            run_report(reports_dir, db_file, schemas_dir)

            # Verify report files exist
            self.assertTrue((reports_dir / "batch_summary.csv").exists())
            self.assertTrue((reports_dir / "daily_overview.csv").exists())
            self.assertTrue((reports_dir / "lead_report.csv").exists())
            self.assertTrue((reports_dir / "rejections.csv").exists())
            self.assertTrue((reports_dir / "data_quality.json").exists())

            with open(reports_dir / "data_quality.json", "r", encoding="utf-8") as f:
                dq = json.load(f)

            self.assertGreater(dq["lead_rows_total"], 0)
            self.assertGreater(dq["lead_rows_rejected"], 0)
            self.assertGreater(dq["events_total"], 0)
            self.assertIn("invalid_phone", dq["rejections_by_reason"])
            self.assertIn("missing_required:customer_name", dq["rejections_by_reason"])


if __name__ == "__main__":
    unittest.main()
