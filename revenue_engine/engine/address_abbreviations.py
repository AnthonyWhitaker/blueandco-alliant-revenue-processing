"""
Street-address words → one standard spelling (USPS Publication 28 abbreviations), used by
engine/normalizer.normalize_address so '100 Main Street Suite 5' and '100 MAIN ST STE 5' compare equal.

Edit the tables here; every address comparison (member_match ship-to sites, gpo_key verification,
the Account List loader's identity) picks the change up.
"""

# Street suffixes
SUFFIXES = {
    "ALLEY": "ALY", "AVENUE": "AVE", "AV": "AVE", "AVEN": "AVE", "BOULEVARD": "BLVD", "BOUL": "BLVD",
    "CENTER": "CTR", "CENTRE": "CTR", "CIRCLE": "CIR", "COURT": "CT", "CROSSING": "XING", "DRIVE": "DR",
    "DRV": "DR", "EXPRESSWAY": "EXPY", "FREEWAY": "FWY", "HEIGHTS": "HTS", "HIGHWAY": "HWY", "HIWAY": "HWY",
    "LANE": "LN", "PARKWAY": "PKWY", "PKY": "PKWY", "PARKWY": "PKWY", "PLACE": "PL", "PLAZA": "PLZ",
    "POINT": "PT", "ROAD": "RD", "ROUTE": "RTE", "SQUARE": "SQ", "STREET": "ST", "STR": "ST",
    "TERRACE": "TER", "TRAIL": "TRL", "TURNPIKE": "TPKE", "WY": "WAY",
}

# Directions
DIRECTIONS = {
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W",
    "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
}

# Unit words (suite, building, floor, ...). A bare '#' before a number counts as a suite: '#219' = 'STE 219'.
UNITS = {
    "SUITE": "STE", "STE": "STE", "UNIT": "UNIT", "APARTMENT": "APT", "APT": "APT",
    "BUILDING": "BLDG", "BLDG": "BLDG", "FLOOR": "FL", "FL": "FL", "ROOM": "RM", "RM": "RM",
}
HASH_UNIT = "STE"

ADDRESS_ABBREV = {**SUFFIXES, **DIRECTIONS, **UNITS}
