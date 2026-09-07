"""Field normalization. Pure functions -- no database, no network, no clock.

Every function here is deterministic and independently testable, which is what
makes the test suite cheap to write. Nothing in this module reaches out to
anything.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Timestamps
# ---------------------------------------------------------------------------

def parse_ts(value) -> datetime | None:
    """Parse a Socrata timestamp into an aware UTC datetime.

    Socrata floating timestamps arrive as '2026-09-03T14:22:11.000' with no
    offset. They are New York local time in intent, but the dataset does not
    say so, and treating them as UTC consistently is the only interpretation
    that round-trips. The `:updated_at` system field arrives as a Unix epoch
    integer instead, so both shapes are handled.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)

    text = str(value).strip()
    if not text:
        return None
    # Bare epoch seconds as a string
    if text.isdigit() and len(text) == 10:
        return datetime.fromtimestamp(int(text), tz=timezone.utc)

    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_socrata_ts(dt: datetime) -> str:
    """Format a datetime the way Socrata's SoQL $where clause expects.

    Floating timestamp literals must have no offset and no trailing 'Z'.
    """
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

def parse_float(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# ZIP
# ---------------------------------------------------------------------------

_ZIP_RE = re.compile(r"^\s*(\d{5})(?:\s*-?\s*\d{4})?\s*$")


def normalize_zip(value) -> str | None:
    """Reduce a ZIP field to five digits, or None if it isn't one.

    Handles the three shapes actually present in the 311 feed: '11215',
    '11215-3401' (ZIP+4), and whitespace-padded variants. Anything else --
    'N/A', 'UNKNOWN', '0', a borough name typed into the ZIP box -- returns
    None, and the validation layer decides what that means.
    """
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    match = _ZIP_RE.match(text)
    if not match:
        return None
    zip5 = match.group(1)
    if zip5 == "00000":
        return None
    return zip5


# ---------------------------------------------------------------------------
# Complaint type
# ---------------------------------------------------------------------------

# Upstream renames and splits categories over time. Mapping the known drift to
# a canonical form keeps a chart from showing one trend line ending and an
# identical one starting on the same day. Unrecognized types are NOT dropped --
# see validate.check_complaint_type_drift.
COMPLAINT_TYPE_ALIASES: dict[str, str] = {
    "NOISE - RESIDENTIAL": "NOISE - RESIDENTIAL",
    "NOISE RESIDENTIAL": "NOISE - RESIDENTIAL",
    "NOISE - COMMERCIAL": "NOISE - COMMERCIAL",
    "NOISE COMMERCIAL": "NOISE - COMMERCIAL",
    "NOISE - STREET/SIDEWALK": "NOISE - STREET/SIDEWALK",
    "NOISE STREET/SIDEWALK": "NOISE - STREET/SIDEWALK",
    "HEATING": "HEAT/HOT WATER",
    "HEAT/HOT WATER": "HEAT/HOT WATER",
    "GENERAL CONSTRUCTION/PLUMBING": "GENERAL CONSTRUCTION",
    "GENERAL CONSTRUCTION": "GENERAL CONSTRUCTION",
    "NONCONST": "NON-CONSTRUCTION",
    "NON-CONSTRUCTION": "NON-CONSTRUCTION",
    "STREET CONDITION": "STREET CONDITION",
    "ILLEGAL PARKING": "ILLEGAL PARKING",
    "BLOCKED DRIVEWAY": "BLOCKED DRIVEWAY",
    "WATER SYSTEM": "WATER SYSTEM",
    "PLUMBING": "PLUMBING",
    "PAINT/PLASTER": "PAINT - PLASTER",
    "PAINT - PLASTER": "PAINT - PLASTER",
    "UNSANITARY CONDITION": "UNSANITARY CONDITION",
    "DIRTY CONDITIONS": "DIRTY CONDITION",
    "DIRTY CONDITION": "DIRTY CONDITION",
    "SANITATION CONDITION": "SANITATION CONDITION",
    "DAMAGED TREE": "DAMAGED TREE",
    "ROOT/SEWER/SIDEWALK CONDITION": "ROOT/SEWER/SIDEWALK CONDITION",
    "DERELICT VEHICLE": "DERELICT VEHICLE",
    "DERELICT VEHICLES": "DERELICT VEHICLE",
    "ABANDONED VEHICLE": "DERELICT VEHICLE",
    "REQUEST LARGE BULKY ITEM COLLECTION": "BULKY ITEM COLLECTION",
    "ELECTRIC": "ELECTRIC",
    "DOOR/WINDOW": "DOOR/WINDOW",
    "FLOORING/STAIRS": "FLOORING/STAIRS",
    "APPLIANCE": "APPLIANCE",
    "SAFETY": "SAFETY",
    "OUTSIDE BUILDING": "OUTSIDE BUILDING",
    "RODENT": "RODENT",
    "AIR QUALITY": "AIR QUALITY",
    "SEWER": "SEWER",
    "TRAFFIC SIGNAL CONDITION": "TRAFFIC SIGNAL CONDITION",
    "STREET LIGHT CONDITION": "STREET LIGHT CONDITION",
    "GRAFFITI": "GRAFFITI",
    "HOMELESS PERSON ASSISTANCE": "HOMELESS PERSON ASSISTANCE",
    "ENCAMPMENT": "ENCAMPMENT",
    "CONSUMER COMPLAINT": "CONSUMER COMPLAINT",
    "FOOD ESTABLISHMENT": "FOOD ESTABLISHMENT",
    "INDOOR AIR QUALITY": "INDOOR AIR QUALITY",
    "MISSED COLLECTION": "MISSED COLLECTION",
    "MISSED COLLECTION (ALL MATERIALS)": "MISSED COLLECTION",
    "ILLEGAL DUMPING": "ILLEGAL DUMPING",
    "ILLEGAL FIREWORKS": "ILLEGAL FIREWORKS",
    "ELEVATOR": "ELEVATOR",
    "WATER QUALITY": "WATER QUALITY",
    "SIDEWALK CONDITION": "SIDEWALK CONDITION",
    "BUILDING/USE": "BUILDING/USE",
    "MAINTENANCE OR FACILITY": "MAINTENANCE OR FACILITY",
    "NEW TREE REQUEST": "NEW TREE REQUEST",
    "TAXI COMPLAINT": "TAXI COMPLAINT",
    "FOR HIRE VEHICLE COMPLAINT": "FOR HIRE VEHICLE COMPLAINT",
    "ANIMAL ABUSE": "ANIMAL ABUSE",
    "ANIMAL-ABUSE": "ANIMAL ABUSE",
    "DEAD/DYING TREE": "DEAD/DYING TREE",
    "DEAD TREE": "DEAD/DYING TREE",
}


def normalize_complaint_type(value) -> str | None:
    """Canonicalize a complaint type. Returns None only for an empty field.

    An unknown value is uppercased and whitespace-collapsed but otherwise
    passed through unchanged -- it is still a real complaint, and dropping it
    because a city agency renamed a category would be the wrong call.
    """
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().upper()
    if not text:
        return None
    return COMPLAINT_TYPE_ALIASES.get(text, text)


def is_recognized_complaint_type(value) -> bool:
    if value is None:
        return False
    text = re.sub(r"\s+", " ", str(value)).strip().upper()
    return text in COMPLAINT_TYPE_ALIASES


# ---------------------------------------------------------------------------
# Borough
# ---------------------------------------------------------------------------

BOROUGHS = ("MANHATTAN", "BRONX", "BROOKLYN", "QUEENS", "STATEN ISLAND")

_BOROUGH_ALIASES = {
    "MANHATTAN": "MANHATTAN",
    "NEW YORK": "MANHATTAN",
    "NY": "MANHATTAN",
    "BRONX": "BRONX",
    "THE BRONX": "BRONX",
    "BRONX COUNTY": "BRONX",
    "BROOKLYN": "BROOKLYN",
    "KINGS": "BROOKLYN",
    "QUEENS": "QUEENS",
    "STATEN ISLAND": "STATEN ISLAND",
    "STATEN IS": "STATEN ISLAND",
    "RICHMOND": "STATEN ISLAND",
}


def normalize_borough(value) -> str | None:
    """Canonicalize a borough name. 'Unspecified' and blanks return None.

    'Unspecified' is a real and common value in this feed -- roughly one row in
    twenty -- and it is a missing value, not a sixth borough.
    """
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip().upper()
    if not text or text.startswith("UNSPECIFIED"):
        return None
    return _BOROUGH_ALIASES.get(text)


# ---------------------------------------------------------------------------
# Address
# ---------------------------------------------------------------------------

_STREET_SUFFIXES = {
    "STREET": "ST", "ST.": "ST", "ST": "ST",
    "AVENUE": "AVE", "AVE.": "AVE", "AVE": "AVE", "AV": "AVE",
    "ROAD": "RD", "RD.": "RD", "RD": "RD",
    "BOULEVARD": "BLVD", "BLVD.": "BLVD", "BLVD": "BLVD",
    "DRIVE": "DR", "DR.": "DR", "DR": "DR",
    "PLACE": "PL", "PL.": "PL", "PL": "PL",
    "COURT": "CT", "CT.": "CT", "CT": "CT",
    "LANE": "LN", "LN.": "LN", "LN": "LN",
    "TERRACE": "TER", "TER.": "TER", "TER": "TER",
    "PARKWAY": "PKWY", "PKWY.": "PKWY", "PKWY": "PKWY",
    "HIGHWAY": "HWY", "HWY.": "HWY", "HWY": "HWY",
    "SQUARE": "SQ", "SQ.": "SQ", "SQ": "SQ",
    "CIRCLE": "CIR", "CIR.": "CIR", "CIR": "CIR",
    "EXPRESSWAY": "EXPY", "EXPY": "EXPY",
    "TURNPIKE": "TPKE", "TPKE": "TPKE",
    "PLAZA": "PLZ", "PLZ": "PLZ",
}

_DIRECTIONALS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW",
    "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}

_ORDINAL_RE = re.compile(r"\b(\d+)(ST|ND|RD|TH)\b")


def normalize_address(value) -> str | None:
    """Normalize a street address into a stable dedup key.

    '123 East 14th Street' and '123 E 14 ST' are the same doorway, and the 311
    feed contains both spellings for the same building on the same day. Without
    this, the deduplication pass finds almost nothing.

    Deliberately conservative: it standardizes suffixes, directionals and
    ordinals, and stops there. It does not attempt to geocode or to resolve
    'BROADWAY' vs 'BROADWAY AVE', because a normalization that is too eager
    collapses genuinely distinct addresses, which is worse than missing a few
    duplicates.
    """
    if value is None:
        return None
    text = str(value).upper()
    text = re.sub(r"[^A-Z0-9\s]", " ", text)      # drop punctuation
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return None

    text = _ORDINAL_RE.sub(r"\1", text)           # 14TH -> 14

    tokens = []
    for token in text.split(" "):
        token = _DIRECTIONALS.get(token, token)
        token = _STREET_SUFFIXES.get(token, token)
        tokens.append(token)

    result = " ".join(tokens).strip()
    return result or None


def clean_text(value, max_len: int = 2000) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    return text[:max_len]
