import pytest

from engine.normalizer import normalize_street, parse_date_from_filename, parse_deposit_amount_from_filename


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


@pytest.mark.parametrize("a, b", [
    ("8033 MAIN PKWY.", "8033 Main Parkway"),
    ("3825 E LAKEVIEW HWY STE. 502", "3825 East Lakeview Highway Suite 502"),
    ("1142 Park Dr #219", "1142 Park Drive Suite 219"),
    ("9692 Franklin Ct.", "9692 Franklin Court"),
    ("8352 LAKEVIEW AVE", "8352 Lakeview Avenue"),
    ("12 OAK BLVD UNIT 3", "12 Oak Boulevard Apt 3"),
])
def test_normalize_street_treats_usps_abbreviations_as_equal(a, b):
    assert normalize_street(a) == normalize_street(b)


@pytest.mark.parametrize("a, b", [
    ("300 Elm St", "301 Elm St"),                      # house number
    ("1142 Park Dr #219", "1142 Park Dr #220"),        # suite number
    ("10 N Main St", "10 S Main St"),                  # directional
    ("5 Oak Ct", "5 Oak Cir"),                         # suffix
])
def test_normalize_street_keeps_real_differences(a, b):
    assert normalize_street(a) != normalize_street(b)


def test_normalize_street_blank():
    assert normalize_street(None) == normalize_street(float("nan")) == normalize_street("  ") == ""
