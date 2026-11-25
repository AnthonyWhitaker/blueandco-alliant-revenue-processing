"""
Facility-name abbreviations → the full word, applied by engine/normalizer.normalize_name before any
name comparison (member_match fuzzy scoring and aliases, gpo_key name verification), so
'LAKEVIEW MC' and 'Lakeview Medical Center' compare equal.

Whole words only, after punctuation is removed ('St.' -> 'ST' -> 'SAINT'). ST means SAINT here because
these are names; street addresses use engine/address_abbreviations.py, where ST means STREET.
"""

NAME_ABBREV = {
    "HH": "HOME HEALTH",
    "HOSP": "HOSPITAL",
    "MC": "MEDICAL CENTER",
    "MED": "MEDICAL",
    "CTR": "CENTER", "CNTR": "CENTER",
    "HLTH": "HEALTH",
    "REG": "REGIONAL", "REGL": "REGIONAL",
    "MEM": "MEMORIAL", "MEML": "MEMORIAL",
    "UNIV": "UNIVERSITY",
    "CLIN": "CLINIC", "CLNC": "CLINIC",
    "REHAB": "REHABILITATION",
    "CMTY": "COMMUNITY", "COMM": "COMMUNITY",
    "SVCS": "SERVICES", "SVC": "SERVICES",
    "SYS": "SYSTEM",
    "NRSG": "NURSING",
    "PHARM": "PHARMACY",
    "ST": "SAINT", "STE": "SAINTE",
    "MT": "MOUNT",
    "FT": "FORT",
}
