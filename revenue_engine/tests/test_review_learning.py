"""
Review learning: every match has a confidence; low ones go to review with up to 3 candidates (matched fields
shown); a confirmed match saves an alias (with its source) and the JDE number, so the next file matches the
account at the JDE step; "not a member" is remembered so the account doesn't come back for review.
"""

from fastapi.testclient import TestClient

from api.main import create_app
from core import reviews
from engine.constants import (
    MATCH_JDE_HISTORY, MATCH_LOW_CONFIDENCE, MATCH_NOT_A_MEMBER, MATCH_ZIP_STATE, ROW_NOT_MEMBER,
)
from engine.member_loader import create_member
from engine.member_matcher import MemberMatcher
from engine.processor import process_job
from models.reviews import ReviewApproval, ReviewNotMember

from .fixtures import FILE_NAME, create_job_row, write_supplier_file


def run_file(conn, supplier_id, tmp_path, name="f.xlsx") -> int:
    path = write_supplier_file(tmp_path / name)
    job_id = create_job_row(conn, supplier_id, path, FILE_NAME)
    process_job(conn=conn, job_id=job_id)
    return job_id


def pending(conn, job_id):
    return reviews.list_pending_items(conn, job_id)


def row(conn, job_id, jde):
    return conn.execute("SELECT match_status, match_method, matched_member_id FROM revenue_row "
                        "WHERE job_id = ? AND jde_number = ?", (job_id, jde)).fetchone()


def matcher(conn, supplier_id, **settings):
    supplier = dict(conn.execute("SELECT * FROM supplier_profile WHERE id = ?", (supplier_id,)).fetchone())
    m = MemberMatcher.from_supplier(conn, {**supplier, **settings})
    m.load()
    return m


# --- confidence ---------------------------------------------------------------------------------------

def test_every_match_has_a_method_and_confidence_and_low_ones_go_to_review(conn, seeded):
    # "GENERAL HOSPITAL WEST CAMPUS" scores 73 against "GENERAL HOSPITAL": a ZIP-step match at threshold 70.
    row_ = dict(customer_name="General Hospital West Campus", city="Austin", state="TX", zip_code="78701")
    loose = matcher(conn, seeded["supplier_id"], fuzzy_zip_state_threshold=70, auto_match_min_confidence=70)
    loose = loose.match(**row_)
    assert loose.matched and (loose.match_method, loose.match_score) == (MATCH_ZIP_STATE, 73)
    strict = matcher(conn, seeded["supplier_id"], fuzzy_zip_state_threshold=70).match(**row_)   # default 80
    assert not strict.matched and strict.match_method == MATCH_LOW_CONFIDENCE
    assert strict.candidates[0]["member"]["id"] == seeded["members"]["general"]
    exact = matcher(conn, seeded["supplier_id"]).match("x", account_id_hint="SF-GEN")
    assert (exact.match_method, exact.match_score) == ("account_id_exact", 100)


# --- candidates -----------------------------------------------------------------------------------------

def test_review_candidates_are_top_three_with_matched_fields(conn, seeded):
    for i in range(4):
        create_member(conn, f"Zyxw Clinic {i}", city="Santa Fe", state="NM", zip_code="87501")
    m = matcher(conn, seeded["supplier_id"])
    cands = m.review_candidates("Zyxw Qvbn Clinic", state="NM", zip_code="87501")
    assert len(cands) == 3
    assert all(c["score"] >= 50 for c in cands)
    assert all(set(c["matched"]) >= {"zip"} for c in cands)
    assert [c["score"] for c in cands] == sorted((c["score"] for c in cands), reverse=True)


def test_review_candidates_have_a_minimum_score(conn, seeded):
    assert matcher(conn, seeded["supplier_id"]).review_candidates("Qqqq Wwww", state="NM") == []


def test_review_items_show_candidates(conn, seeded, tmp_path):
    # "Zyxw Family Clinic" scores 71 against the file's "Zyxw Qvbn Clinic": below the ZIP step, a candidate.
    family = create_member(conn, "Zyxw Family Clinic", city="Santa Fe", state="NM", zip_code="87501")
    [item] = pending(conn, run_file(conn, seeded["supplier_id"], tmp_path))
    assert item.suggestion.member_id == item.candidates[0].member_id == family
    assert (item.candidates[0].score, item.candidates[0].matched) == (71, ["zip"])


# --- confirming a match ------------------------------------------------------------------------------------

def test_confirmed_match_saves_alias_with_source_and_jde_so_the_next_file_matches_at_the_jde_step(
        conn, seeded, tmp_path):
    first = run_file(conn, seeded["supplier_id"], tmp_path, "1.xlsx")
    [item] = pending(conn, first)
    assert item.jde_number == "J300"
    reviews.approve(conn, item.id, ReviewApproval(member_id=seeded["members"]["desert"]))

    alias = conn.execute("SELECT raw_name, jde_number, source FROM member_alias").fetchone()
    assert tuple(alias) == ("Zyxw Qvbn Clinic", "J300", "Acme Supply")
    assert conn.execute("SELECT member_id FROM jde_member_history WHERE jde_number = 'J300'").fetchone()[0] \
        == seeded["members"]["desert"]

    second = run_file(conn, seeded["supplier_id"], tmp_path, "2.xlsx")
    r = row(conn, second, "J300")
    assert (r["match_status"], r["match_method"], r["matched_member_id"]) == \
        ("matched", MATCH_JDE_HISTORY, seeded["members"]["desert"])
    assert pending(conn, second) == []


# --- not a member --------------------------------------------------------------------------------------------

def test_not_a_member_is_remembered_and_not_reviewed_again(conn, seeded, tmp_path):
    first = run_file(conn, seeded["supplier_id"], tmp_path, "1.xlsx")
    [item] = pending(conn, first)
    result = reviews.mark_not_member(conn, item.id, ReviewNotMember(notes="vendor's own warehouse"))
    assert result.updated_rows == 1 and result.job.status == "completed"
    assert row(conn, first, "J300")["match_status"] == ROW_NOT_MEMBER
    saved = conn.execute("SELECT jde_number, source FROM non_member").fetchone()
    assert tuple(saved) == ("J300", "Acme Supply")

    second = run_file(conn, seeded["supplier_id"], tmp_path, "2.xlsx")
    r = row(conn, second, "J300")
    assert (r["match_status"], r["match_method"]) == (ROW_NOT_MEMBER, MATCH_NOT_A_MEMBER)
    assert pending(conn, second) == []
    job = conn.execute("SELECT status, unmatched_count FROM revenue_job WHERE id = ?", (second,)).fetchone()
    assert tuple(job) == ("completed", 0)


def test_api_review_item_candidates_and_not_member(conn, seeded, tmp_path, monkeypatch):
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REVENUE_OUTPUT_DIR", str(tmp_path / "outputs"))
    create_member(conn, "Zyxw Family Clinic", city="Santa Fe", state="NM", zip_code="87501")
    job_id = run_file(conn, seeded["supplier_id"], tmp_path)
    with TestClient(create_app()) as client:
        [item] = client.get(f"/api/jobs/{job_id}/review-items").json()
        assert item["candidates"][0].keys() >= {"member_id", "name", "score", "matched", "via", "street"}
        r = client.post(f"/api/review-items/{item['id']}/not-member", json={})
        assert r.status_code == 200 and r.json()["updated_rows"] == 1
        assert client.get(f"/api/jobs/{job_id}/review-items").json() == []
