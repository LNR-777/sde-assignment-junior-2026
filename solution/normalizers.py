from __future__ import annotations

import re
from datetime import datetime, timezone
from solution.config import IST

HEADER_RUN_REGEX = re.compile(r"[\s_]+")
DECIMAL_REGEX = re.compile(r"^(?:₹|rs\.?|inr)?\s*([0-9,]+(?:\.[0-9]+)?)$", re.IGNORECASE)
DATE_YMD_REGEX = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")
DATE_DMY_SLASH_REGEX = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
DATE_DMY_DASH_REGEX = re.compile(r"^(\d{1,2})-(\d{1,2})-(\d{4})$")
DATE_DMON_DASH_REGEX = re.compile(r"^(\d{1,2})-([a-zA-Z]{3,4})-(\d{4})$")


def normalize_header(header: str) -> str:
    """
    Lower-case, trim, and replace every run of '_' or whitespace with a single space.
    """
    return HEADER_RUN_REGEX.sub(" ", header.strip().lower())


def normalize_phone(raw: str) -> tuple[bool, str | None]:
    """
    Strip every non-digit.
    If 12 digits starting with '91', drop '91'.
    If 11 digits starting with '0', drop '0'.
    Valid only if exactly 10 digits and starts with 6, 7, 8 or 9.
    Returns (is_valid, canonical_phone_as_+91XXXXXXXXXX).
    """
    if raw is None:
        return False, None
    raw = raw.strip()
    if not raw:
        return False, None

    digits = re.sub(r"\D", "", raw)
    if len(digits) == 12 and digits.startswith("91"):
        digits = digits[2:]
    elif len(digits) == 11 and digits.startswith("0"):
        digits = digits[1:]

    if len(digits) == 10 and digits[0] in "6789":
        return True, f"+91{digits}"
    return False, None


def normalize_date(raw: str) -> tuple[bool, str | None]:
    """
    Accept YYYY-MM-DD, DD/MM/YYYY, DD-MM-YYYY and DD-Mon-YYYY (case-insensitive).
    Dates are day-first. Impossible dates (like 31/02) are invalid.
    Returns (is_valid, 'YYYY-MM-DD').
    """
    if raw is None:
        return False, None
    raw = raw.strip()
    if not raw:
        return False, None

    # 1. YYYY-MM-DD
    if DATE_YMD_REGEX.match(raw):
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d")
            return True, dt.strftime("%Y-%m-%d")
        except ValueError:
            return False, None

    # 2. DD/MM/YYYY
    if DATE_DMY_SLASH_REGEX.match(raw):
        try:
            dt = datetime.strptime(raw, "%d/%m/%Y")
            return True, dt.strftime("%Y-%m-%d")
        except ValueError:
            return False, None

    # 3. DD-MM-YYYY
    if DATE_DMY_DASH_REGEX.match(raw):
        try:
            dt = datetime.strptime(raw, "%d-%m-%Y")
            return True, dt.strftime("%Y-%m-%d")
        except ValueError:
            return False, None

    # 4. DD-Mon-YYYY (case-insensitive)
    m = DATE_DMON_DASH_REGEX.match(raw)
    if m:
        day, mon, yr = m.groups()
        cand = f"{int(day):02d}-{mon.capitalize()}-{yr}"
        try:
            dt = datetime.strptime(cand, "%d-%b-%Y")
            return True, dt.strftime("%Y-%m-%d")
        except ValueError:
            return False, None

    return False, None


def normalize_decimal(raw: str) -> tuple[bool, float | None]:
    """
    Optionally prefixed by ₹, Rs, Rs. or INR (case-insensitive, optional space).
    Commas are thousands separators. Must be >= 0. Round to 2 places.
    """
    if raw is None:
        return False, None
    raw = raw.strip()
    if not raw:
        return False, None

    m = DECIMAL_REGEX.match(raw)
    if not m:
        return False, None

    clean_num = m.group(1).replace(",", "")
    try:
        val = float(clean_num)
        if val < 0:
            return False, None
        return True, round(val, 2)
    except ValueError:
        return False, None


def normalize_enum(raw: str, allowed_values: list[str]) -> tuple[bool, str | None]:
    """
    Compared case-insensitively against values and stored lower-case.
    """
    if raw is None:
        return False, None
    raw = raw.strip().lower()
    if not raw:
        return False, None
    lowered_allowed = {v.lower(): v.lower() for v in allowed_values}
    if raw in lowered_allowed:
        return True, lowered_allowed[raw]
    return False, None


def parse_event_timestamp(ts_str: str) -> datetime:
    """
    Timestamps come in three forms:
    - UTC ('...Z')
    - Offset ('...+05:30')
    - Naive ('2026-10-03 10:01:00') -> Naive means IST.
    Returns an aware IST datetime.
    """
    ts_str = ts_str.strip()
    if ts_str.endswith("Z"):
        dt = datetime.strptime(ts_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
        return dt.astimezone(IST)
    if "T" in ts_str:
        dt = datetime.fromisoformat(ts_str)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=IST)
        else:
            dt = dt.astimezone(IST)
        return dt
    # Naive date string with space
    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=IST)
    return dt
