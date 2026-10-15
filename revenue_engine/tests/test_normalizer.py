from engine.normalizer import parse_date_from_filename, parse_deposit_amount_from_filename


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
