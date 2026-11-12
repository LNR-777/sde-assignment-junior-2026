from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

# Base paths
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMAS_DIR = PROJECT_ROOT / "assignment-a-data" / "schemas"
DEFAULT_DB_PATH = PROJECT_ROOT / "solution.db"

# Timezone
IST = timezone(timedelta(hours=5, minutes=30))

# Cryptographic secret for PII blind indexing & encryption
# Stable default ensures reproducible tests and restarts; overridable via env var.
PII_SECRET_KEY = os.environ.get(
    "PII_SECRET_KEY", "agentic-universe-assignment-a-secret-key-2026"
).encode("utf-8")
