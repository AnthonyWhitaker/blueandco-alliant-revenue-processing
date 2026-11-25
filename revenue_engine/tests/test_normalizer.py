import pytest

from engine.normalizer import (
    normalize_address, normalize_name, normalize_zip, parse_amount, parse_date_from_filename, parse_deposit_amount_from_filename, quarter_start,
)


def test_parse_date_from_filename_dotted():
    assert parse_date_from_filename("Guerbet 2025.07.02 4231.37 Ledger.xlsx") == "2025-07-02"


def test_parse_date_from_filename_zero_pads():
    assert parse_date_from_filename("Carolon 2026.1.9 1219.79 Ledger.xlsx") == "2026-01-09"


def test_parse_date_from_filename_dashed():
    assert parse_date_from_filename("Zier 2025-11-14 88.10.xlsx") == "2025-11-14"


def test_parse_date_from_filename_rejects_impossible_date():
    # Previously returned "2025-02-30", which crashed the Upload page's strptime.
    assert parse_date_from_filename("Guerbet 2025.02.30 10.00 Ledger.xlsx") is None


def test_parse_date_from_filename_missing():
    assert parse_date_from_filename("no date here.xlsx") is None


def test_parse_deposit_amount_ignores_date_tokens():
    assert parse_deposit_amount_from_filename("Guerbet 2025.07.02 4231.37 Ledger.xlsx") == 4231.37


# --- normalize_address: one rule per case ------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("1 main st", "1 MAIN ST"),                                  # upper case
    ("1 Main St.,  Rm. 4", "1 MAIN ST RM 4"),                    # punctuation -> spaces, collapsed
    ("Oak & Elm Plaza", "OAK AND ELM PLZ"),                      # & -> AND
    ("1 Main Street", "1 MAIN ST"), ("1 Mill Road", "1 MILL RD"), ("1 Oak Avenue", "1 OAK AVE"),
    ("1 Oak Av", "1 OAK AVE"), ("1 Oak Boulevard", "1 OAK BLVD"), ("1 Oak Parkway", "1 OAK PKWY"),
    ("1 Oak Highway", "1 OAK HWY"), ("1 Oak Court", "1 OAK CT"), ("1 Oak Drive", "1 OAK DR"),
    ("1 Oak Lane", "1 OAK LN"), ("1 Oak Trail", "1 OAK TRL"),     # street suffixes
    ("1 Oak Dr Suite 5", "1 OAK DR STE 5"),                      # SUITE -> STE
    ("1 Oak Dr #5", "1 OAK DR STE 5"),                           # bare # -> STE
    ("1 Oak Dr Ste #5", "1 OAK DR STE 5"),                       # no double STE
    ("1 Oak Dr Apartment 5", "1 OAK DR APT 5"),
    ("1 Oak Dr Building 2 Floor 3", "1 OAK DR BLDG 2 FL 3"),     # BLDG, FL
    ("1 North Oak Dr", "1 N OAK DR"), ("1 Southwest Oak Dr", "1 SW OAK DR"),   # directions
    ("1 Café Way", "1 CAFE WAY"),                                # ASCII
])
def test_normalize_address_rules(raw, expected):
    assert normalize_address(raw) == expected


@pytest.mark.parametrize("a, b", [
    ("8033 MAIN PKWY.", "8033 Main Parkway"),
    ("3825 E LAKEVIEW HWY STE. 502", "3825 East Lakeview Highway Suite 502"),
    ("1142 Park Dr #219", "1142 Park Drive Suite 219"),
    ("9692 Franklin Ct.", "9692 Franklin Court"),
    ("8352 LAKEVIEW AVE", "8352 Lakeview Avenue"),
    ("12 OAK BLVD APT 3", "12 Oak Boulevard Apartment 3"),
])
def test_normalize_address_treats_usps_abbreviations_as_equal(a, b):
    assert normalize_address(a) == normalize_address(b)


@pytest.mark.parametrize("a, b", [
    ("300 Elm St", "301 Elm St"),                      # house number
    ("1142 Park Dr #219", "1142 Park Dr #220"),        # suite number
    ("10 N Main St", "10 S Main St"),                  # directional
    ("5 Oak Ct", "5 Oak Cir"),                         # suffix
    ("5 Oak Ct Ste 1", "5 Oak Ct Bldg 1"),             # unit word
])
def test_normalize_address_keeps_real_differences(a, b):
    assert normalize_address(a) != normalize_address(b)


def test_normalize_address_blank():
    assert normalize_address(None) == normalize_address(float("nan")) == normalize_address("  ") == ""


# --- normalize_zip ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("78701", "78701"), ("78701-1234", "78701"), ("787011234", "78701"), (78701.0, "78701"), ("78701.0", "78701"),
    (" 78701 ", "78701"),
    (2134, "02134"), ("2134", "02134"), ("2134.0", "02134"), ("2134-5678", "02134"), ("21345678", "02134"),  # zeros back
    ("02134", "02134"),
    (None, ""), ("", ""), ("N/A", ""), (float("nan"), ""),
])
def test_normalize_zip(raw, expected):
    assert normalize_zip(raw) == expected


# --- normalize_name ---------------------------------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("General Hospital", "GENERAL HOSPITAL"),
    ("Mary's Hospital & Clinic", "MARY S HOSPITAL AND CLINIC"),           # punctuation, & -> AND
    ("St. Elias HH", "SAINT ELIAS HOME HEALTH"),                           # abbreviations (name_abbreviations.py)
    ("Mercy Med Ctr", "MERCY MEDICAL CENTER"), ("Lakeview MC", "LAKEVIEW MEDICAL CENTER"),
    ("Acme Hosp", "ACME HOSPITAL"), ("Main St Clinic", "MAIN SAINT CLINIC"),   # ST is SAINT in names
    ("Stmary Hospital", "STMARY HOSPITAL"),                                # whole words only
    ("Acme Health, Inc.", "ACME HEALTH"), ("Acme Health LLC", "ACME HEALTH"),   # legal suffixes
    ("Clínica Ñandú", "CLINICA NANDU"),
    (None, ""), ("", ""),
])
def test_normalize_name(raw, expected):
    assert normalize_name(raw) == expected


@pytest.mark.parametrize("value, expected", [
    (26.0, 26.0), ("26", 26.0), ("$26.00", 26.0), (" $ 1,234.50 ", 1234.5), ("-$26.00", -26.0),
    ("$-26.00", -26.0), ("($1,234.50)", -1234.5), ("26.00-", -26.0), ("0", 0.0), ("$0.00", 0.0),
    (None, None), ("", None), ("  ", None), (float("nan"), None), ("N/A", None), ("$", None),
])
def test_parse_amount(value, expected):
    assert parse_amount(value) == expected


@pytest.mark.parametrize("timeframe, expected", [
    ("1Q26", "2026-01-01"), ("3q26", "2026-07-01"), (" 4Q25 ", "2025-10-01"), (None, None), ("", None), ("Q3", None),
])
def test_quarter_start(timeframe, expected):
    assert quarter_start(timeframe) == expected
