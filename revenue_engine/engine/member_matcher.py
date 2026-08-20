"""
Member (facility) matching engine for the Alliant Revenue Processing Engine.

Implements a 5-tier matching strategy adapted from guerbet_processor.py,
now operating against the persistent member + member_alias + jde_member_history
database tables instead of an in-memory account list.

Matching priority:
  1. JDE history  — look up JDE Customer Number in jde_member_history
  2. Alias lookup — exact canonical_key match in member_alias
  3. ZIP + State  — fuzzy name within ZIP/State filtered candidates
  4. City + State — fuzzy name within City/State filtered candidates
  5. State only   — fuzzy name within State filtered candidates
  6. Nationwide   — fuzzy name across all members (last resort)
"""

import sqlite3
from dataclasses import dataclass, field
from typing import Optional

from thefuzz import fuzz

from .constants import (
    FUZZY_ZIP_STATE_THRESHOLD,
    FUZZY_CITY_STATE_THRESHOLD,
    FUZZY_STATE_THRESHOLD,
    FUZZY_NATIONWIDE_THRESHOLD,
    FUZZY_MIN_NAME_LENGTH,
    MATCH_JDE_HISTORY,
    MATCH_ALIAS,
    MATCH_ZIP_STATE,
    MATCH_CITY_STATE,
    MATCH_STATE,
    MATCH_NATIONWIDE,
    MATCH_UNMATCHED,
    MATCH_ENTITY_CODE,
    MATCH_ACCOUNT_ID,
)
from .normalizer import normalize_name, normalize_zip, normalize_state, normalize_city


@dataclass
class MatchResult:
    member_id:      Optional[int]   = None
    entity_code:    Optional[str]   = None
    account_id:     Optional[str]   = None
    account_name:   Optional[str]   = None
    match_method:   str             = MATCH_UNMATCHED
    match_score:    int             = 0
    geo_filter:     Optional[str]   = None
    matched:        bool            = False


def _best_fuzzy(
    norm_name: str,
    candidates: list[dict],
    threshold: int,
) -> tuple[Optional[dict], int]:
    """
    Run token_sort_ratio against a list of candidate member dicts.
    Returns (best_candidate_dict, score) or (None, 0) if below threshold.
    """
    best_score = 0
    best_candidate = None
    for c in candidates:
        score = fuzz.token_sort_ratio(norm_name, c["canonical_key"])
        if score > best_score:
            best_score = score
            best_candidate = c
    if best_score >= threshold and best_candidate:
        return best_candidate, best_score
    return None, 0


def _member_row_to_result(row: dict, method: str, score: int, geo: Optional[str] = None) -> MatchResult:
    return MatchResult(
        member_id=row["id"],
        entity_code=row["entity_code"],
        account_id=row["account_id"],
        account_name=row["name"],
        match_method=method,
        match_score=score,
        geo_filter=geo,
        matched=True,
    )


class MemberMatcher:
    """
    Stateful matcher that caches the full member list in memory for the
    duration of a processing job (avoids per-row DB queries).

    Usage:
        matcher = MemberMatcher(conn, supplier_profile_id=1)
        matcher.load()
        result = matcher.match(jde_number="12345", customer_name="Acme Hospital",
                               city="Austin", state="TX", zip="78701")
    """

    def __init__(
        self,
        conn: sqlite3.Connection,
        supplier_profile_id: int,
        zip_state_threshold: int = FUZZY_ZIP_STATE_THRESHOLD,
        city_state_threshold: int = FUZZY_CITY_STATE_THRESHOLD,
        state_threshold: int = FUZZY_STATE_THRESHOLD,
        nationwide_threshold: int = FUZZY_NATIONWIDE_THRESHOLD,
    ):
        self.conn = conn
        self.supplier_profile_id = supplier_profile_id
        self.zip_state_threshold = zip_state_threshold
        self.city_state_threshold = city_state_threshold
        self.state_threshold = state_threshold
        self.nationwide_threshold = nationwide_threshold

        # In-memory caches (populated by load())
        self._members: list[dict] = []
        self._by_zip_state: dict[tuple, list[dict]] = {}
        self._by_city_state: dict[tuple, list[dict]] = {}
        self._by_state: dict[str, list[dict]] = {}
        self._aliases: dict[str, dict] = {}        # canonical_key → member dict
        self._jde_map: dict[str, dict] = {}        # jde_number → member dict
        self._by_entity_code: dict[str, dict] = {}  # entity_code → member dict
        self._by_account_id: dict[str, dict] = {}   # account_id → member dict

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load members, aliases, and JDE history into memory."""
        self._load_members()
        self._load_aliases()
        self._load_jde_history()

    def _load_members(self) -> None:
        rows = self.conn.execute(
            "SELECT * FROM member WHERE is_active = 1"
        ).fetchall()
        self._members = [dict(r) for r in rows]
        # Build geographic indexes
        for m in self._members:
            z = m.get("norm_zip") or ""
            s = m.get("norm_state") or ""
            c = m.get("norm_city") or ""
            if z and s:
                self._by_zip_state.setdefault((z, s), []).append(m)
            if c and s:
                self._by_city_state.setdefault((c, s), []).append(m)
            if s:
                self._by_state.setdefault(s, []).append(m)
            ec = (m.get("entity_code") or "").strip().upper()
            if ec:
                self._by_entity_code[ec] = m
            aid = (m.get("account_id") or "").strip()
            if aid:
                self._by_account_id[aid] = m

    def _load_aliases(self) -> None:
        rows = self.conn.execute(
            """
            SELECT a.canonical_key, a.jde_number, m.*
            FROM member_alias a
            JOIN member m ON m.id = a.member_id
            WHERE a.supplier_profile_id IS NULL
               OR a.supplier_profile_id = ?
            """,
            (self.supplier_profile_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            key = d.pop("canonical_key")
            jde = d.get("jde_number")
            self._aliases[key] = d
            if jde:
                self._jde_map[str(jde)] = d

    def _load_jde_history(self) -> None:
        rows = self.conn.execute(
            """
            SELECT h.jde_number, m.*
            FROM jde_member_history h
            JOIN member m ON m.id = h.member_id
            WHERE h.supplier_profile_id = ?
            """,
            (self.supplier_profile_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            jde = d.pop("jde_number", None)
            if jde:
                self._jde_map[str(jde)] = d

    # ------------------------------------------------------------------
    # Matching
    # ------------------------------------------------------------------

    def match(
        self,
        customer_name: Optional[str],
        jde_number: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        zip_code: Optional[str] = None,
        entity_code_hint: Optional[str] = None,
        account_id_hint: Optional[str] = None,
    ) -> MatchResult:
        """
        Run the matching strategy and return a MatchResult.

        Tier 0a/0b (entity_code_hint / account_id_hint) apply when the
        supplier file already carries a Premier/Alliant Entity Code or a
        Salesforce Account ID per row — an exact match against the member
        master, ahead of JDE history and fuzzy name matching.
        """
        norm_name = normalize_name(customer_name)
        norm_zip  = normalize_zip(zip_code)
        norm_state = normalize_state(state)
        norm_city  = normalize_city(city)

        # Tier 0a: Salesforce Account ID exact match (highest confidence)
        if account_id_hint:
            aid = str(account_id_hint).strip()
            if aid in self._by_account_id:
                return _member_row_to_result(self._by_account_id[aid], MATCH_ACCOUNT_ID, 100)

        # Tier 0b: Premier/Alliant Entity Code exact match
        if entity_code_hint:
            ec = str(entity_code_hint).strip().upper()
            if ec in self._by_entity_code:
                return _member_row_to_result(self._by_entity_code[ec], MATCH_ENTITY_CODE, 100)

        # Tier 1: JDE history / alias by JDE number
        if jde_number:
            jde_str = str(jde_number).strip()
            if jde_str in self._jde_map:
                m = self._jde_map[jde_str]
                return _member_row_to_result(m, MATCH_JDE_HISTORY, 100)

        # Tier 2: Alias by canonical name key
        from .normalizer import normalize_key
        name_key = normalize_key(customer_name)
        if name_key and name_key in self._aliases:
            m = self._aliases[name_key]
            return _member_row_to_result(m, MATCH_ALIAS, 100)

        # Tier 3: ZIP + State fuzzy name match
        if norm_zip and norm_state:
            candidates = self._by_zip_state.get((norm_zip, norm_state), [])
            best, score = _best_fuzzy(norm_name, candidates, self.zip_state_threshold)
            if best:
                return _member_row_to_result(
                    best, MATCH_ZIP_STATE, score, f"zip={norm_zip} state={norm_state}"
                )

        # Tier 4: City + State fuzzy name match
        if norm_city and norm_state:
            candidates = self._by_city_state.get((norm_city, norm_state), [])
            best, score = _best_fuzzy(norm_name, candidates, self.city_state_threshold)
            if best:
                return _member_row_to_result(
                    best, MATCH_CITY_STATE, score, f"city={norm_city} state={norm_state}"
                )

        # Tier 5: State only fuzzy name match
        if norm_state:
            candidates = self._by_state.get(norm_state, [])
            best, score = _best_fuzzy(norm_name, candidates, self.state_threshold)
            if best:
                return _member_row_to_result(
                    best, MATCH_STATE, score, f"state={norm_state}"
                )

        # Tier 6: Nationwide fuzzy (last resort — only for names long enough)
        if norm_name and len(norm_name) > FUZZY_MIN_NAME_LENGTH:
            best, score = _best_fuzzy(norm_name, self._members, self.nationwide_threshold)
            if best:
                return _member_row_to_result(best, MATCH_NATIONWIDE, score, "nationwide")

        return MatchResult(match_method=MATCH_UNMATCHED)

    # ------------------------------------------------------------------
    # Post-match: update JDE history
    # ------------------------------------------------------------------

    def record_jde_match(
        self,
        jde_number: str,
        member_id: int,
        job_id: int,
    ) -> None:
        """
        Persist a JDE number → member mapping so future jobs can use Tier 1.
        Updates in-memory cache too.
        """
        if not jde_number:
            return
        jde_str = str(jde_number).strip()
        try:
            self.conn.execute(
                """
                INSERT INTO jde_member_history
                    (supplier_profile_id, jde_number, member_id, first_seen_job_id, last_seen_job_id, occurrence_count)
                VALUES (?, ?, ?, ?, ?, 1)
                ON CONFLICT(supplier_profile_id, jde_number)
                DO UPDATE SET
                    member_id = excluded.member_id,
                    last_seen_job_id = excluded.last_seen_job_id,
                    occurrence_count = occurrence_count + 1,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (self.supplier_profile_id, jde_str, member_id, job_id, job_id),
            )
            self.conn.commit()
            # Update in-memory map too
            rows = self.conn.execute(
                "SELECT * FROM member WHERE id = ?", (member_id,)
            ).fetchall()
            if rows:
                self._jde_map[jde_str] = dict(rows[0])
        except Exception:
            pass  # Non-fatal — history is an optimization, not a requirement

    def suggest_for_review(
        self,
        customer_name: Optional[str],
        state: Optional[str] = None,
    ) -> tuple[Optional[dict], int]:
        """
        For unmatched rows, find the best suggestion (even below threshold)
        to display in the review UI.
        Returns (member_dict, score) or (None, 0).
        """
        norm_name = normalize_name(customer_name)
        norm_state_val = normalize_state(state)

        candidates = self._by_state.get(norm_state_val, []) if norm_state_val else self._members
        if not candidates:
            candidates = self._members

        best_score = 0
        best_candidate = None
        for c in candidates:
            score = fuzz.token_sort_ratio(norm_name, c["canonical_key"])
            if score > best_score:
                best_score = score
                best_candidate = c

        return best_candidate, best_score
