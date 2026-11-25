"""scripts/member_quality_diagnostics.py: each count, no identifying values in the output, synthetic-only rows."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
_SPEC = importlib.util.spec_from_file_location("member_quality_diagnostics", SCRIPTS / "member_quality_diagnostics.py")
mq = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mq)

SYN = mq.SYNTHETIC_ACCOUNT_PREFIX
MEMBERS = [  # account, name, premier, alliant, street, state, zip, primary
    (f"{SYN}1", "Lakeside Clinic East", "EC1", None, "1 A St", "TX", "78701", None),     # EC1: no primary flag
    (f"{SYN}2", "Lakeside Clinic West", "EC1", None, "2 B St", "TX", "78701", None),
    (f"{SYN}3", "Twin Peaks One", "EC2", None, "3 C St", "TX", "78701", 1),              # EC2: two primaries
    (f"{SYN}4", "Twin Peaks Two", "EC2", None, "4 D St", "TX", "78701", 1),
    (f"{SYN}5", "Dup Hospital", "EC3", None, "5 Elm Street", "TX", "78702", None),       # same as 6: true duplicate
    (f"{SYN}6", "Dup Hosp.", "EC4", None, "5 ELM ST", "TX", "78702", None),
    (f"{SYN}7", "Near Medical Center", "EC5", None, "10 Oak Avenue", "TX", "78703", None),   # near-duplicates
    (f"{SYN}8", "Near Medical Centre", "EC6", None, "10 Oaks Ave", "TX", "78703", None),
    (f"{SYN}9", "No Street Clinic", "EC7", None, None, "TX", "N/A", None),               # missing street, bad ZIP
    (f"{SYN}10", "Zero Zip Clinic", "EC8", None, "10 G St", "MA", "2134", None),          # leading zero restored
    (f"{SYN}11", "Alliant Only Clinic", None, "AL1", "11 H St", None, "78701", None),     # Alliant code, no state
    (f"{SYN}12", "Bad State Clinic", "EC9", None, "12 I St", "XX", "78701", None),
    (f"{SYN}13", "Main Site", "EC10", None, "13 J St", "TX", "78701", 1),                 # EC10: one primary
    (f"{SYN}14", "Main Site Dock", "EC10", None, "14 K St", "TX", "78701", 0),
    ("001REAL0001", "Not Synthetic Clinic", None, None, None, "TX", "78701", None),      # counted, never written
]


@pytest.fixture
def members(conn):
    for acct, name, pec, alliant, street, state, zip_code, primary in MEMBERS:
        conn.execute(
            "INSERT INTO member (account_id, name, canonical_key, premier_entity_code, alliant_entity_code, "
            "entity_code, billing_street, billing_state, billing_zip, is_primary_site) "
            "VALUES (?, ?, '', ?, ?, ?, ?, ?, ?, ?)",
            (acct, name, pec, alliant, pec or alliant, street, state, zip_code, primary))
    conn.commit()
    return mq.load_members(conn)


def test_counts(members):
    shared, _ = mq.shared_codes(members)
    assert (shared["shared codes"], shared["exactly one primary"], shared["more than one primary"],
            shared["no primary: flag missing on every site"]) == (3, 1, 1, 1)
    dups, _ = mq.true_duplicates(members)
    assert dups["groups with different entity codes"] == 1
    near, _ = mq.near_duplicates(members, 90, 85)
    assert near["pairs, different entity codes"] == 1
    bad, _ = mq.bad_data(members)
    assert (bad["missing street"], bad["ZIP can't be read"], bad["missing state"], bad["state not recognised"],
            bad["ZIP missing leading zeros (read with them put back)"]) == (2, 1, 1, 1, 1)
    codes, _ = mq.entity_code_sources(members)
    assert (codes["no Premier code: Alliant code used"], codes["no entity code at all"]) == (1, 1)


def test_summary_prints_no_names_addresses_accounts_or_codes(members):
    text = mq.summarize(members, 90, 85)
    for acct, name, pec, alliant, street, _, zip_code, _ in MEMBERS:
        for value in (acct, name, pec, alliant, street):
            if value:
                assert value not in text


def test_rows_out_writes_synthetic_members_only(members, tmp_path):
    written = mq.write_rows(members, 90, 85, tmp_path)
    everything = "".join(p.read_text() for p in tmp_path.glob("*.csv"))
    assert "Not Synthetic Clinic" not in everything and "001REAL0001" not in everything
    assert written["held back (not synthetic)"] == 2      # its missing street and its missing code
    assert written["true_duplicates.csv"] == 2 and written["near_duplicates.csv"] == 2


def test_connection_is_read_only(conn, tmp_path):
    ro = mq.open_read_only(str(tmp_path / "revenue.db"))
    with pytest.raises(RuntimeError):
        ro.execute("DELETE FROM member")
