"""
identity_mode = 'gpo_key_then_member' (engine/processor._run_pipeline): GPO key verification first; rows
whose account, name or address doesn't agree with the key go through member matching to the Review queue
(never auto-matched, the key's reason kept); ambiguous / no-GPO-ID key failures stay exceptions.
"""

import json
from datetime import date

import pytest

from core import jobs, reviews
from engine.constants import (
    KEY_ACCOUNT_NOT_FOUND, KEY_ADDRESS_MISMATCH, KEY_AMBIGUOUS, KEY_MISSING_ACCOUNT, KEY_NAME_MISMATCH,
    KEY_NO_GPO_ID, MATCH_GPO_KEY, MATCH_KEY_FALLBACK, ROW_KEY_EXCEPTION,
)
from engine.gpo_key import load_gpo_key, suppliers_without_key
from engine.supplier_presets import MEDLINE, apply_preset
from models.reviews import ReviewApproval

from . import medline_mock as mock
from .conftest import make_members

THEN_MEMBER = {**MEDLINE, "profile": {**MEDLINE["profile"], "identity_mode": "gpo_key_then_member"}}

KEY_ROWS = mock.KEY_ROWS + [
    ("5001", "TWIN CLINIC", "5 TWIN RD", "EC001", "78701"),      # same account twice, different GPO IDs
    ("5001", "TWIN CLINIC", "5 TWIN RD", "EC002", "78701"),
    ("6001", "BLANK CLINIC", "6 BLANK RD", None, "78701"),       # in the key without a GPO ID
]
ROWS = mock.ROWS + [
    mock.line("INV11", "2026-09-12", "5001", "TWIN CLINIC", "5 TWIN RD", "EC001", 40.00, 1.20),
    mock.line("INV12", "2026-09-13", "6001", "BLANK CLINIC", "6 BLANK RD", "EC001", 50.00, 1.50),
]


@pytest.fixture(autouse=True)
def dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("REVENUE_UPLOAD_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REVENUE_OUTPUT_DIR", str(tmp_path / "outputs"))


def run(conn, tmp_path, preset):
    members = make_members(conn)
    sid = apply_preset(conn, preset)
    load_gpo_key(conn, sid, str(mock.write_key_csv(tmp_path / "key.csv", rows=KEY_ROWS)))
    content = mock.write_medline_xlsx(tmp_path / "m.xlsx", ROWS).read_bytes()
    job = jobs.create_job(conn, sid, [("Medline 2026.10.15.xlsx", content)], date(2026, 10, 15), timeframe="3Q26")
    job = jobs.run_job(conn, job.id)
    rows = {json.loads(r["raw_row_json"])["InvoiceNumber"]: dict(r) for r in conn.execute(
        "SELECT * FROM revenue_row WHERE job_id = ?", (job.id,)).fetchall()}
    return job, rows, members


def test_key_failures_on_account_name_or_address_go_to_review_never_auto_matched(conn, tmp_path):
    job, rows, members = run(conn, tmp_path, THEN_MEMBER)
    for inv in ("INV1", "INV2", "INV3", "INV4", "INV5", "INV6"):
        assert rows[inv]["match_method"] == MATCH_GPO_KEY and rows[inv]["match_status"] == "matched"
    expected = {"INV7": KEY_ADDRESS_MISMATCH, "INV8": KEY_ACCOUNT_NOT_FOUND, "INV9": KEY_MISSING_ACCOUNT,
                "INV10": KEY_NAME_MISMATCH}
    for inv, reason in expected.items():
        r = rows[inv]
        assert (r["match_status"], r["match_method"], r["exception_reason"]) == \
            ("unmatched", MATCH_KEY_FALLBACK, reason), inv
        assert r["matched_member_id"] is None

    items = {i.customer_name_sample: i for i in reviews.list_pending_items(conn, job.id)}
    # INV9 has no account number, but its name is the member's exactly: still a review candidate only.
    general = items["GENERAL HOSPITAL"]
    assert (general.review_reason, general.key_reason) == (MATCH_KEY_FALLBACK, KEY_MISSING_ACCOUNT)
    assert general.candidates[0].member_id == members["general"]
    assert items["DESERT REGIONAL HEALTH"].key_reason == KEY_ADDRESS_MISMATCH
    assert items["UNKNOWN CLINIC"].key_reason == KEY_ACCOUNT_NOT_FOUND


def test_ambiguous_and_no_gpo_id_stay_exceptions(conn, tmp_path):
    job, rows, _ = run(conn, tmp_path, THEN_MEMBER)
    assert (rows["INV11"]["match_status"], rows["INV11"]["exception_reason"]) == (ROW_KEY_EXCEPTION, KEY_AMBIGUOUS)
    assert (rows["INV12"]["match_status"], rows["INV12"]["exception_reason"]) == (ROW_KEY_EXCEPTION, KEY_NO_GPO_ID)
    names = {i.customer_name_sample for i in reviews.list_pending_items(conn, job.id)}
    assert not names & {"TWIN CLINIC", "BLANK CLINIC"}


def test_plain_gpo_key_mode_is_unchanged(conn, tmp_path):
    job, rows, _ = run(conn, tmp_path, mock.MEDLINE_GPO_KEY)
    for inv in ("INV7", "INV8", "INV9", "INV10", "INV11", "INV12"):
        assert rows[inv]["match_status"] == ROW_KEY_EXCEPTION, inv
    assert reviews.list_pending_items(conn, job.id) == []


def test_approving_a_fallback_row_puts_it_in_the_output(conn, tmp_path):
    job, _, members = run(conn, tmp_path, THEN_MEMBER)
    item = next(i for i in reviews.list_pending_items(conn, job.id) if i.customer_name_sample == "GENERAL HOSPITAL")
    reviews.approve(conn, item.id, ReviewApproval(member_id=members["general"]))
    out = conn.execute("SELECT SUM(total_revenue) FROM revenue_output_row WHERE job_id = ? AND pec = 'EC001'",
                       (job.id,)).fetchone()[0]
    assert out == pytest.approx(10.50 + 0.30 + 0.45)     # verified EC001 rows + INV9


def test_startup_check_covers_the_new_mode(conn):
    apply_preset(conn, THEN_MEMBER)
    assert suppliers_without_key(conn) == ["Medline"]
