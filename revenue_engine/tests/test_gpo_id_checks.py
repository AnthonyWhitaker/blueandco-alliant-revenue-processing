"""
member_match order (engine/member_matcher.MemberMatcher.match): Salesforce account ID → JDE number →
GPO ID (entity code hint), used only when it fits the row → approved alias → fuzzy name; and the
review items for a GPO ID that doesn't fit or disagrees with the JDE number.
"""

import json

import openpyxl
import pytest

from engine.constants import (
    MATCH_ACCOUNT_ID, MATCH_ENTITY_CODE, MATCH_GPO_ID_CONFLICT, MATCH_JDE_GPO_CONFLICT, MATCH_JDE_HISTORY,
    MATCH_ZIP_STATE,
)
from engine.member_loader import create_member
from engine.member_matcher import MemberMatcher
from engine.processor import process_job

from .fixtures import COLUMN_MAP, create_job_row, make_supplier, write_supplier_file


@pytest.fixture
def members(conn):
    ids = {
        "general": create_member(conn, "General Hospital", account_id="SF-GEN", premier_entity_code="EC001",
                                 city="Austin", state="TX", zip_code="78701"),
        "mercy": create_member(conn, "Mercy Medical Center", account_id="SF-MER", premier_entity_code="EC002",
                               city="Austin", state="TX", zip_code="78702"),
        "zephyr": create_member(conn, "Zephyr Rehab Institute", account_id="SF-ZEP", premier_entity_code="EC003",
                                city="Santa Fe", state="NM", zip_code="87501"),
    }
    conn.execute("UPDATE member SET billing_street = '9 Sand Rd' WHERE id = ?", (ids["zephyr"],))
    conn.commit()
    return ids


@pytest.fixture
def supplier(conn):
    sid = make_supplier(conn, column_map=COLUMN_MAP)
    return dict(conn.execute("SELECT * FROM supplier_profile WHERE id = ?", (sid,)).fetchone())


def matcher_for(conn, supplier, jde: dict = None, **settings) -> MemberMatcher:
    for number, member_id in (jde or {}).items():
        conn.execute("INSERT INTO jde_member_history (supplier_profile_id, jde_number, member_id) VALUES (?, ?, ?)",
                     (supplier["id"], number, member_id))
    m = MemberMatcher.from_supplier(conn, {**supplier, **settings})
    m.load()
    return m


def ids(result):
    return [c["member"]["id"] for c in result.candidates]


def test_account_id_comes_first(conn, members, supplier):
    m = matcher_for(conn, supplier, jde={"J1": members["mercy"]})
    r = m.match("General Hospital", jde_number="J1", entity_code_hint="EC002", account_id_hint="SF-GEN")
    assert (r.member_id, r.match_method) == (members["general"], MATCH_ACCOUNT_ID)


def test_gpo_id_that_fits_by_name_is_used(conn, members, supplier):
    r = matcher_for(conn, supplier).match("GENERAL HOSP", entity_code_hint="EC001", zip_code="99999", state="TX")
    assert (r.member_id, r.match_method) == (members["general"], MATCH_ENTITY_CODE)


def test_gpo_id_that_fits_by_street_and_zip_is_used(conn, members, supplier):
    # The name doesn't resemble the member, but the street + ZIP are the member's.
    r = matcher_for(conn, supplier).match("NM CAMPUS RECEIVING", entity_code_hint="EC003",
                                          address="9 SAND ROAD", zip_code="87501", state="NM")
    assert (r.member_id, r.match_method) == (members["zephyr"], MATCH_ENTITY_CODE)


def test_wrong_gpo_id_is_ignored_and_the_later_steps_match(conn, members, supplier):
    # EC003 is a rehab institute in NM; the row is Mercy in Austin.
    r = matcher_for(conn, supplier).match("Mercy Medical Center", entity_code_hint="EC003",
                                          city="Austin", state="TX", zip_code="78702")
    assert (r.member_id, r.match_method) == (members["mercy"], MATCH_ZIP_STATE)


def test_wrong_gpo_id_with_no_other_match_goes_to_review_as_gpo_id_conflict(conn, members, supplier):
    r = matcher_for(conn, supplier).match("Qqqq Xxxx Clinic", entity_code_hint="EC003", state="TX")
    assert not r.matched and r.match_method == MATCH_GPO_ID_CONFLICT
    assert ids(r) == [members["zephyr"]]


def test_cutoff_is_a_supplier_setting(conn, members, supplier):
    # "GENERAL HEALTH" scores about 73 against "GENERAL HOSPITAL": used at 50, not at 95.
    row = dict(customer_name="GENERAL HEALTH", entity_code_hint="EC001", state="NM")
    assert matcher_for(conn, supplier).match(**row).match_method == MATCH_ENTITY_CODE
    assert matcher_for(conn, supplier, gpo_id_min_name_score=95).match(**row).match_method != MATCH_ENTITY_CODE
    assert matcher_for(conn, supplier).gpo_id_min_name_score == 50     # schema default


def test_jde_number_beats_a_gpo_id_that_does_not_fit(conn, members, supplier):
    m = matcher_for(conn, supplier, jde={"J1": members["mercy"]})
    r = m.match("Mercy Medical Center", jde_number="J1", entity_code_hint="EC003")
    assert (r.member_id, r.match_method) == (members["mercy"], MATCH_JDE_HISTORY)


def test_jde_number_and_a_fitting_gpo_id_disagree_goes_to_review_with_both(conn, members, supplier):
    m = matcher_for(conn, supplier, jde={"J1": members["mercy"]})
    r = m.match("General Hospital", jde_number="J1", entity_code_hint="EC001")
    assert not r.matched and r.match_method == MATCH_JDE_GPO_CONFLICT
    assert ids(r) == [members["mercy"], members["general"]]
    assert [c["via"] for c in r.candidates] == ["jde", "gpo_id"]


def test_jde_member_that_is_a_site_of_the_gpo_code_is_not_a_conflict(conn, members, supplier):
    dock = create_member(conn, "General Hospital - Dock", account_id="SF-GEN-D", premier_entity_code="EC001",
                         city="Austin", state="TX", zip_code="78701")
    m = matcher_for(conn, supplier, jde={"J1": dock})
    r = m.match("General Hospital", jde_number="J1", entity_code_hint="EC001")
    assert (r.member_id, r.match_method) == (dock, MATCH_JDE_HISTORY)


def test_conflicts_become_review_items_with_reason_and_candidates(conn, members, supplier, tmp_path):
    conn.execute("INSERT INTO supplier_column_map (supplier_profile_id, source_column, canonical_field) "
                 "VALUES (?, 'GPO', 'entity_code_hint')", (supplier["id"],))
    conn.execute("INSERT INTO jde_member_history (supplier_profile_id, jde_number, member_id) VALUES (?, 'J1', ?)",
                 (supplier["id"], members["mercy"]))
    conn.commit()
    rows = [  # Customer, JDE, City, State, Zip, Fee, Sales, Process Date, GPO
        ("General Hospital", "J1", "Austin", "TX", "78701", 10.0, 100.0, "2025-06-15", "EC001"),
        ("Qqqq Xxxx Clinic", "J9", "Austin", "TX", "78799", 5.0, 50.0, "2025-06-15", "EC003"),
    ]
    path = tmp_path / "f.xlsx"
    write_supplier_file(path, rows)
    wb = openpyxl.load_workbook(path)
    wb.active.cell(row=1, column=9, value="GPO")
    wb.save(path)
    job_id = create_job_row(conn, supplier["id"], path)
    process_job(conn=conn, job_id=job_id)
    items = {r["customer_name_sample"]: r for r in conn.execute(
        "SELECT * FROM member_review_item WHERE job_id = ?", (job_id,)).fetchall()}
    jde_item = items["General Hospital"]
    assert jde_item["review_reason"] == MATCH_JDE_GPO_CONFLICT
    shown = {c["member_id"]: c["matched"] for c in json.loads(jde_item["candidates_json"])}
    assert shown[members["mercy"]] == ["jde"]                         # the JDE number's member
    assert shown[members["general"]] == ["name", "zip", "gpo_id"]     # the GPO ID's member, whose name fits
    assert jde_item["suggested_member_id"] == members["general"]      # more evidence: name + GPO ID
    gpo_item = items["Qqqq Xxxx Clinic"]
    assert gpo_item["review_reason"] == MATCH_GPO_ID_CONFLICT
    first = json.loads(gpo_item["candidates_json"])[0]
    assert (first["member_id"], first["via"], first["matched"]) == (members["zephyr"], "gpo_id", ["gpo_id"])
