"""
member_match fuzzy steps (engine/member_matcher.MemberMatcher._match_by_name): street + ZIP confirmation,
names contained in a longer one, name abbreviations, review-only statewide / nationwide results.
"""

import pytest

from engine.constants import (
    MATCH_FUZZY_SUGGESTION, MATCH_NATIONWIDE, MATCH_STATE, MATCH_UNMATCHED, MATCH_ZIP_STATE,
    MATCH_ZIP_STATE_STREET,
)
from engine.member_loader import create_member
from engine.member_matcher import MemberMatcher

from .fixtures import make_supplier


def member(conn, name, street, city="Austin", state="TX", zip_code="78701"):
    mid = create_member(conn, name, city=city, state=state, zip_code=zip_code)
    conn.execute("UPDATE member SET billing_street = ? WHERE id = ?", (street, mid))
    return mid


@pytest.fixture
def m(conn):
    ids = {
        "brannock": member(conn, "Margaret Brannock Memorial Hospital", "500 Hospital Drive"),
        "hilda": member(conn, "Saint Hilda Hospital", "10 Elm Street"),
        "lakeview": member(conn, "Lakeview Home Health", "77 Lake Road Suite 2"),
        "nostreet": member(conn, "Riverside Surgical Center", None, zip_code="78702"),
        "elsewhere": member(conn, "Pinecrest Medical Center", "1 Pine St", city="Denver", state="CO",
                            zip_code="80202"),
    }
    conn.commit()
    return ids


def matcher(conn, **settings):
    sid = make_supplier(conn, name=f"Supplier {conn.execute('SELECT COUNT(*) FROM supplier_profile').fetchone()[0]}")
    supplier = dict(conn.execute("SELECT * FROM supplier_profile WHERE id = ?", (sid,)).fetchone())
    mm = MemberMatcher.from_supplier(conn, {**supplier, **settings})
    mm.load()
    return mm


def run(mm, name, street=None, city="Austin", state="TX", zip_code="78701"):
    return mm.match(name, address=street, city=city, state=state, zip_code=zip_code)


def test_short_name_contained_in_longer_one_matches_when_street_and_zip_are_equal(conn, m):
    r = run(matcher(conn), "BRANNOCK HOSPITAL", "500 HOSPITAL DR")
    assert (r.member_id, r.match_method) == (m["brannock"], MATCH_ZIP_STATE_STREET)


def test_short_name_at_a_different_street_does_not_auto_match(conn, m):
    r = run(matcher(conn), "BRANNOCK HOSPITAL", "900 Other Rd")
    assert not r.matched


def test_name_abbreviations_are_expanded_before_scoring(conn, m):
    r = run(matcher(conn), "LAKEVIEW HH", "77 LAKE RD STE 2")
    assert (r.member_id, r.match_score) == (m["lakeview"], 100)


def test_similar_name_in_same_zip_at_a_different_street_goes_to_review(conn, m):
    # "SAINT ELIAS HOSPITAL" scores in the 80s against "SAINT HILDA HOSPITAL": not the same place.
    r = run(matcher(conn), "St. Elias Hospital", "300 Cedar Ave")
    assert not r.matched and r.match_method == MATCH_FUZZY_SUGGESTION
    assert [c["member"]["id"] for c in r.candidates] == [m["hilda"]]
    assert r.candidates[0]["via"] == MATCH_ZIP_STATE


def test_strong_name_matches_even_at_a_different_street(conn, m):
    r = run(matcher(conn), "SAINT HILDA HOSPITAL", "1 Other Street")
    assert (r.member_id, r.match_method) == (m["hilda"], MATCH_ZIP_STATE)


def test_without_a_street_on_either_side_the_step_threshold_applies_as_before(conn, m):
    assert run(matcher(conn), "RIVERSIDE SURGICAL CTR", "1 Any St", zip_code="78702").match_method == MATCH_ZIP_STATE
    assert run(matcher(conn), "St. Hilda Hosp.", None).member_id == m["hilda"]


def test_same_name_in_another_state_is_only_a_suggestion(conn, m):
    r = run(matcher(conn), "Pinecrest Medical Center", "1 Pine St", city="Phoenix", state="AZ", zip_code="85001")
    assert not r.matched and r.match_method == MATCH_FUZZY_SUGGESTION
    assert (r.candidates[0]["member"]["id"], r.candidates[0]["via"]) == (m["elsewhere"], MATCH_NATIONWIDE)


def test_same_name_elsewhere_in_the_state_is_only_a_suggestion(conn, m):
    r = run(matcher(conn), "Saint Hilda Hospital", "10 Elm Street", city="Waco", zip_code="76701")
    assert not r.matched
    assert (r.candidates[0]["member"]["id"], r.candidates[0]["via"]) == (m["hilda"], MATCH_STATE)


def test_thresholds_are_supplier_settings(conn, m):
    # Street threshold: "BRANNOCK HOSPITAL" scores 100 by containment; a stricter setting doesn't change that,
    # but a name that only shares one word does depend on it.
    assert run(matcher(conn, fuzzy_street_name_threshold=101), "BRANNOCK HOSPITAL", "500 Hospital Dr").matched is False
    # Strong threshold: "SAINT HILDA HOSPITAL" (100) is strong at 95, not at 101.
    assert not run(matcher(conn, fuzzy_strong_name_threshold=101), "SAINT HILDA HOSPITAL", "1 Other St").matched
    # Step threshold still sets the floor for suggestions.
    r = run(matcher(conn, fuzzy_zip_state_threshold=99, fuzzy_city_state_threshold=99, fuzzy_state_threshold=99,
                    fuzzy_nationwide_threshold=99),
            "St. Elias Hospital", "300 Cedar Ave")
    assert r.match_method == MATCH_UNMATCHED and r.candidates == []
