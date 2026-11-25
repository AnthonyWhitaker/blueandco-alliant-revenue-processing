"""
Entity codes shared by several members (ship-to sites): the Account List's street and primary flag
(engine/member_loader.py) and how MemberMatcher picks a site (engine/member_matcher._pick_site).
"""

from datetime import date

import openpyxl
import pytest

from core import jobs
from engine.constants import (
    MATCH_ENTITY_CODE, MATCH_ENTITY_CODE_NO_PRIMARY, MATCH_ENTITY_CODE_PRIMARY, MATCH_ENTITY_CODE_SITE_ADDRESS,
    MATCH_UNMATCHED,
)
from engine.member_loader import load_account_list
from engine.member_matcher import MemberMatcher
from engine.supplier_presets import MEDLINE, apply_preset

from . import medline_mock as mock

ACCOUNT_LIST = [
    # account id, name, code, street, city, zip, primary
    ("SF-P", "Lakeside Medical Center", "EC900", "100 Main Street", "Austin", "78701", "Y"),
    ("SF-A", "Lakeside Medical Center - Pharmacy", "EC900", "200 Oak Avenue Suite 5", "Austin", "78702", "N"),
    ("SF-B", "Lakeside Home Health - Location 1", "EC900", "300 Elm St.", "Austin", "78703", "N"),
    ("SF-C", "Hilltop Clinic", "EC901", "1 First St", "Austin", "78704", ""),       # no site flagged primary
    ("SF-D", "Hilltop Clinic - Dock", "EC901", "2 Second St", "Austin", "78704", ""),
    ("SF-E", "Solo Hospital", "EC902", "9 Lone Rd", "Austin", "78705", ""),          # the code's only member
    ("SF-F", "Twin Peaks Hospital", "EC903", "1 Peak Rd", "Austin", "78706", "Y"),   # two primaries: don't guess
    ("SF-G", "Twin Peaks Hospital East", "EC903", "2 Peak Rd", "Austin", "78706", "Yes"),
]


def write_account_list(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Account List"
    ws.append(["Account ID", "Account Name", "Premier Entity Code", "Billing Street", "Billing City",
               "Billing State/Province", "Billing Zip/Postal Code", "Is Primary"])
    for acct, name, code, street, city, zip_code, primary in ACCOUNT_LIST:
        ws.append([acct, name, code, street, city, "TX", zip_code, primary])
    wb.save(path)
    return path


@pytest.fixture
def sites(conn, tmp_path):
    load_account_list(conn, str(write_account_list(tmp_path / "accounts.xlsx")))
    conn.commit()


@pytest.fixture
def matcher(conn, sites):
    m = MemberMatcher(conn, supplier_profile_id=apply_preset(conn, MEDLINE))
    m.load()
    return m


def test_account_list_street_and_primary_flag_are_loaded(conn, sites):
    got = {r["account_id"]: (r["billing_street"], r["is_primary_site"]) for r in conn.execute(
        "SELECT account_id, billing_street, is_primary_site FROM member").fetchall()}
    assert got["SF-P"] == ("100 Main Street", 1)
    assert got["SF-A"] == ("200 Oak Avenue Suite 5", 0)
    assert got["SF-C"] == ("1 First St", None)        # blank: the list doesn't say
    assert got["SF-G"][1] == 1


@pytest.mark.parametrize("code, street, zip_code, account_id, method", [
    # 1. street + ZIP equal one site's after normalizing (case, punctuation, ZIP+4, USPS abbreviations)
    ("EC900", "300 ELM ST", "78703-1234", "SF-B", MATCH_ENTITY_CODE_SITE_ADDRESS),
    ("ec900", "200 oak avenue, suite 5", "78702", "SF-A", MATCH_ENTITY_CODE_SITE_ADDRESS),
    ("EC900", "300 Elm Street", "78703", "SF-B", MATCH_ENTITY_CODE_SITE_ADDRESS),   # STREET = ST
    ("EC900", "200 OAK AVE #5", "78702", "SF-A", MATCH_ENTITY_CODE_SITE_ADDRESS),   # AVE #5 = Avenue Suite 5
    ("EC901", "1 FIRST ST", "78704", "SF-C", MATCH_ENTITY_CODE_SITE_ADDRESS),   # no primary needed
    # 2. otherwise the primary site
    ("EC900", "301 Elm St", "78703", "SF-P", MATCH_ENTITY_CODE_PRIMARY),       # house number differs
    ("EC900", "200 Oak Ave Suite 6", "78702", "SF-P", MATCH_ENTITY_CODE_PRIMARY),   # suite differs
    ("EC900", "300 Elm St", "78799", "SF-P", MATCH_ENTITY_CODE_PRIMARY),       # ZIP differs
    ("EC900", "300 Elm St", None, "SF-P", MATCH_ENTITY_CODE_PRIMARY),          # no ZIP in the row
    ("EC900", None, None, "SF-P", MATCH_ENTITY_CODE_PRIMARY),
    # a code with one member is unchanged
    ("EC902", "somewhere else", "11111", "SF-E", MATCH_ENTITY_CODE),
])
def test_site_is_picked_by_address_then_primary(matcher, code, street, zip_code, account_id, method):
    name = {"EC902": "Solo Hospital"}.get(code.upper(), "Lakeside Medical Center")   # a name that fits the code
    r = matcher.match(name, entity_code_hint=code, address=street, zip_code=zip_code, city="Austin", state="TX")
    assert (r.matched, r.account_id, r.match_method, r.match_score) == (True, account_id, method, 100)


@pytest.mark.parametrize("code", ["EC901", "EC903"])   # no primary / two primaries
def test_no_single_primary_goes_to_review_without_falling_through_to_name_matching(matcher, code):
    # "Hilltop Clinic" in 78704 would match SF-C by name; the shared code must not guess.
    r = matcher.match("Hilltop Clinic", entity_code_hint=code, address="5 Other Rd", zip_code="78704",
                      city="Austin", state="TX")
    assert not r.matched and r.member_id is None and r.match_method == MATCH_ENTITY_CODE_NO_PRIMARY


def test_pipeline_records_the_rule_and_reviews_the_unpicked_code(conn, sites, tmp_path, monkeypatch):
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REVENUE_OUTPUT_DIR", str(tmp_path / "outputs"))
    sid = apply_preset(conn, MEDLINE)                       # member_match
    rows = [
        ["INV1", "2026-09-03", "5001", "LAKESIDE HH LOC 1", "300 ELM ST", "AUSTIN", "TX", "78703", "EC900", 100.0, 0.03, 3.0],
        ["INV2", "2026-09-04", "5002", "LAKESIDE MC", "999 DOCK RD", "AUSTIN", "TX", "78701", "EC900", 50.0, 0.03, 1.5],
        ["INV3", "2026-09-05", "5003", "HILLTOP CLINIC", "5 OTHER RD", "AUSTIN", "TX", "78704", "EC901", 20.0, 0.03, 0.6],
    ]
    content = mock.write_medline_xlsx(tmp_path / "medline.xlsx", rows).read_bytes()
    job = jobs.create_job(conn, sid, [("Medline 2026.10.15 5.10.xlsx", content)], date(2026, 10, 15),
                          timeframe="3Q26")
    job = jobs.run_job(conn, job.id)

    got = {r["customer_name"]: (r["match_method"], r["match_status"]) for r in conn.execute(
        "SELECT customer_name, match_method, match_status FROM revenue_row WHERE job_id = ?", (job.id,)).fetchall()}
    assert got["LAKESIDE HH LOC 1"][0] == MATCH_ENTITY_CODE_SITE_ADDRESS
    assert got["LAKESIDE MC"][0] == MATCH_ENTITY_CODE_PRIMARY
    assert got["HILLTOP CLINIC"] == (MATCH_ENTITY_CODE_NO_PRIMARY, "unmatched")
    assert MATCH_UNMATCHED not in {m for m, _ in got.values()}
    review = conn.execute("SELECT customer_name_sample FROM member_review_item WHERE job_id = ?",
                          (job.id,)).fetchall()
    assert [r[0] for r in review] == ["HILLTOP CLINIC"]


# --- sites sharing an entity code are never merged (member_loader._identity) --------------------------

def write_sites_without_account_id(path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Account List"
    ws.append(["Account Name", "Premier Entity Code", "Billing Street", "Billing City", "Billing State/Province",
               "Billing Zip/Postal Code", "Is Primary"])
    for r in rows:
        ws.append(list(r))
    wb.save(path)
    return str(path)


SAME_NAME_SITES = [   # no Account IDs; same name, code and state, different street
    ("Lakeside Medical Center", "EC950", "100 Main Street", "Austin", "TX", "78701", "Y"),
    ("Lakeside Medical Center", "EC950", "200 Oak Avenue", "Austin", "TX", "78701", "N"),
]


def ec950(conn):
    return sorted(r[0] for r in conn.execute(
        "SELECT billing_street FROM member WHERE entity_code = 'EC950' AND is_active = 1").fetchall())


def test_sites_sharing_code_name_and_state_stay_separate_members_across_reloads(conn, tmp_path):
    path = write_sites_without_account_id(tmp_path / "a.xlsx", SAME_NAME_SITES)
    load_account_list(conn, path)
    load_account_list(conn, path)          # a reload updates, never adds or merges
    assert ec950(conn) == ["100 Main Street", "200 Oak Avenue"]
    # Abbreviations are the same street: still two members, not three.
    load_account_list(conn, write_sites_without_account_id(tmp_path / "b.xlsx", [
        ("Lakeside Medical Center", "EC950", "100 MAIN ST", "Austin", "TX", "78701", "Y"),
        ("Lakeside Medical Center", "EC950", "200 OAK AVE", "Austin", "TX", "78701", "N")]))
    assert ec950(conn) == ["100 MAIN ST", "200 OAK AVE"]


def test_member_stored_without_a_street_is_claimed_by_one_site_only(conn, tmp_path):
    # Loaded before the street column was read: one member, no street.
    load_account_list(conn, write_sites_without_account_id(tmp_path / "old.xlsx", [
        ("Lakeside Medical Center", "EC950", None, "Austin", "TX", "78701", None)]))
    assert ec950(conn) == [None]
    load_account_list(conn, write_sites_without_account_id(tmp_path / "new.xlsx", SAME_NAME_SITES))
    assert ec950(conn) == ["100 Main Street", "200 Oak Avenue"]
    assert conn.execute("SELECT COUNT(*) FROM member WHERE entity_code = 'EC950'").fetchone()[0] == 2


# --- before the Account List is reloaded with primary flags ---------------------------------------------

def test_until_any_primary_flag_is_loaded_shared_codes_keep_the_previous_site(conn):
    from engine.constants import MATCH_ENTITY_CODE_UNFLAGGED
    from engine.member_loader import create_member
    first = create_member(conn, "Harbor Clinic", premier_entity_code="EC970", state="TX", zip_code="78701")
    last = create_member(conn, "Harbor Clinic Dock", premier_entity_code="EC970", state="TX", zip_code="78701")
    conn.commit()
    sid = apply_preset(conn, MEDLINE)

    def match():
        m = MemberMatcher(conn, supplier_profile_id=sid)
        m.load()
        return m.match("Harbor Clinic", entity_code_hint="EC970", state="TX")

    r = match()           # no member has a primary flag: the last site loaded, as before this change
    assert (r.matched, r.member_id, r.match_method) == (True, last, MATCH_ENTITY_CODE_UNFLAGGED)

    conn.execute("UPDATE member SET is_primary_site = 0 WHERE id = ?", (first,))    # flags arrive (reload)
    conn.commit()
    r = match()
    assert (r.matched, r.match_method) == (False, MATCH_ENTITY_CODE_NO_PRIMARY)
