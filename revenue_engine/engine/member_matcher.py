"""
Member (facility) matching engine for the Alliant Revenue Processing Engine.

Implements a 5-tier matching strategy adapted from guerbet_processor.py,
now operating against the persistent member + member_alias + jde_member_history
database tables instead of an in-memory account list.

Matching priority:
  0. Salesforce Account ID (exact).
  1. JDE number: jde_member_history, then aliases carrying a JDE number.
     When the GPO ID points elsewhere (and fits the row), the row goes to review
     with both candidates instead (MATCH_JDE_GPO_CONFLICT).
  2. GPO ID (entity code hint). A code can cover several ship-to sites: see
     "Entity codes and ship-to sites" below and _pick_site. The match is used only if the file's name scores at least
     gpo_id_min_name_score against that member, or its street + ZIP match;
     otherwise the hint is ignored and the later steps run. If they find
     nothing, the row goes to review as MATCH_GPO_ID_CONFLICT.
  3. Alias lookup — exact canonical_key match in member_alias
  4. ZIP + State  — fuzzy name within ZIP/State filtered candidates
  5. City + State — fuzzy name within City/State filtered candidates
     In steps 4-5, when both the row and the member have a street:
       - street + ZIP equal: a weaker name score is enough (fuzzy_street_name_threshold),
         and a name contained in a longer one counts (token_set_ratio);
       - streets differ: only a near-exact name auto-matches (fuzzy_strong_name_threshold);
         a name at the step's threshold goes to review as a suggestion.
     Without a street on either side, the step's threshold auto-matches as before.
  6. State only   — fuzzy name within the state: review suggestions only
  7. Nationwide   — fuzzy name across all members: review suggestions only
     (the same name in another state is a different facility as often as not)

Every match has a method (match_method) and a confidence (match_score, 0-100):
100 for the exact steps 0-3, the name score for steps 4-5, at least
FUZZY_STREET_CONFIDENCE when street + ZIP confirm it. Below the supplier's
auto_match_min_confidence the row goes to review instead (MATCH_LOW_CONFIDENCE).
Before any of this, an account a reviewer marked "not a member" (non_member
table) is MATCH_NOT_A_MEMBER: no review item, not in the output.

Entity codes and ship-to sites:
  A member's entity code is its Premier entity code, or its Alliant entity
  code when it has no Premier code (member_loader.load_account_list). One code
  can cover several members: a health system's primary facility and its
  ship-to sites (pharmacy, dock, home health office) are separate Salesforce
  accounts sharing the code, one of them flagged primary
  (member.is_primary_site). A row is assigned to the site whose street + ZIP
  match the row's, otherwise to the primary site; with no single primary
  site it goes to review. Sites are never merged into one member
  (member_loader._identity keys on street + ZIP too).
"""

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
    MATCH_ZIP_STATE_STREET,
    MATCH_CITY_STATE_STREET,
    MATCH_FUZZY_SUGGESTION,
    MATCH_LOW_CONFIDENCE,
    MATCH_NOT_A_MEMBER,
    AUTO_MATCH_MIN_CONFIDENCE,
    FUZZY_STREET_CONFIDENCE,
    REVIEW_MAX_CANDIDATES,
    REVIEW_SUGGESTION_MIN_SCORE,
    FUZZY_STRONG_NAME_THRESHOLD,
    FUZZY_STREET_NAME_THRESHOLD,
    MATCH_CITY_STATE,
    MATCH_STATE,
    MATCH_NATIONWIDE,
    MATCH_UNMATCHED,
    MATCH_ENTITY_CODE,
    MATCH_ENTITY_CODE_NO_PRIMARY,
    MATCH_ENTITY_CODE_UNFLAGGED,
    MATCH_ENTITY_CODE_PRIMARY,
    MATCH_ENTITY_CODE_SITE_ADDRESS,
    MATCH_ACCOUNT_ID,
    MATCH_GPO_ID_CONFLICT,
    MATCH_JDE_GPO_CONFLICT,
    GPO_ID_MIN_NAME_SCORE,
)
from .normalizer import normalize_address, normalize_key, normalize_name, normalize_zip, normalize_state, normalize_city


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
    # For an unmatched result that goes to review: the members to show the reviewer, e.g. both the
    # JDE number's and the GPO ID's member on a conflict. Each: {"member": dict, "score": int, "via": str}.
    candidates:     list            = field(default_factory=list)


def _name_score(norm_name: str, member: dict) -> int:
    return fuzz.token_sort_ratio(norm_name, member.get("canonical_key") or "") if norm_name else 0


def _same_address(member: dict, address: Optional[str], zip_code: Optional[str]) -> bool:
    street, zip5 = normalize_address(address), normalize_zip(zip_code)
    return bool(street and zip5) and normalize_address(member.get("billing_street")) == street \
        and normalize_zip(member.get("billing_zip")) == zip5


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


def _pick_site(sites: list[dict], address: Optional[str], zip_code: Optional[str],
               no_flags_loaded: bool = False) -> tuple[Optional[dict], str]:
    """
    The member for an entity code (Premier code, else Alliant code). One member: that one, as
    MATCH_ENTITY_CODE. Several members are ship-to sites of one facility sharing the code:
      1. the one site whose street + ZIP equal the row's after normalizing (normalizer.normalize_address:
         USPS abbreviations): MATCH_ENTITY_CODE_SITE_ADDRESS;
      2. else the one site flagged primary (member.is_primary_site = 1): MATCH_ENTITY_CODE_PRIMARY;
      3. else none, MATCH_ENTITY_CODE_NO_PRIMARY: the row goes to review rather than to a guessed site.
         Exception while no_flags_loaded (no member in the database has a primary flag yet, i.e. the Account
         List hasn't been reloaded with its primary-site column): the previous behaviour, the last site
         loaded (highest member id), as MATCH_ENTITY_CODE_UNFLAGGED, so nothing that matched before goes
         to review in the meantime.
    The match method records which rule picked the site.
    """
    if len(sites) == 1:
        return sites[0], MATCH_ENTITY_CODE
    street, zip5 = normalize_address(address), normalize_zip(zip_code)
    if street and zip5:
        at_address = [s for s in sites
                      if normalize_address(s.get("billing_street")) == street
                      and normalize_zip(s.get("billing_zip")) == zip5]
        if len(at_address) == 1:
            return at_address[0], MATCH_ENTITY_CODE_SITE_ADDRESS
    primary = [s for s in sites if s.get("is_primary_site") == 1]
    if len(primary) == 1:
        return primary[0], MATCH_ENTITY_CODE_PRIMARY
    if no_flags_loaded:
        return max(sites, key=lambda s: s["id"]), MATCH_ENTITY_CODE_UNFLAGGED
    return None, MATCH_ENTITY_CODE_NO_PRIMARY


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
        conn: object,
        supplier_profile_id: int,
        zip_state_threshold: int = FUZZY_ZIP_STATE_THRESHOLD,
        city_state_threshold: int = FUZZY_CITY_STATE_THRESHOLD,
        state_threshold: int = FUZZY_STATE_THRESHOLD,
        nationwide_threshold: int = FUZZY_NATIONWIDE_THRESHOLD,
        gpo_id_min_name_score: int = GPO_ID_MIN_NAME_SCORE,
        strong_name_threshold: int = FUZZY_STRONG_NAME_THRESHOLD,
        street_name_threshold: int = FUZZY_STREET_NAME_THRESHOLD,
        auto_match_min_confidence: int = AUTO_MATCH_MIN_CONFIDENCE,
    ):
        self.conn = conn
        self.supplier_profile_id = supplier_profile_id
        self.zip_state_threshold = zip_state_threshold
        self.city_state_threshold = city_state_threshold
        self.state_threshold = state_threshold
        self.nationwide_threshold = nationwide_threshold
        self.gpo_id_min_name_score = gpo_id_min_name_score
        self.strong_name_threshold = strong_name_threshold
        self.street_name_threshold = street_name_threshold
        self.auto_match_min_confidence = auto_match_min_confidence

        # In-memory caches (populated by load())
        self._members: list[dict] = []
        self._by_zip_state: dict[tuple, list[dict]] = {}
        self._by_city_state: dict[tuple, list[dict]] = {}
        self._by_state: dict[str, list[dict]] = {}
        self._aliases: dict[str, dict] = {}        # canonical_key → member dict
        self._jde_map: dict[str, dict] = {}        # jde_number → member dict
        # entity_code (upper case) → every member with that code. Several members = ship-to sites of one
        # facility; all are kept (never last-loaded-wins) so _pick_site can choose by address / primary flag.
        self._by_entity_code: dict[str, list[dict]] = {}
        self._by_account_id: dict[str, dict] = {}   # account_id → member dict
        self._by_id: dict[int, dict] = {}
        self.no_primary_flags_loaded = False
        # Accounts a reviewer marked "not a member" for this supplier: JDE numbers, and name keys (rows without one).
        self._non_member_jde: set[str] = set()
        self._non_member_names: set[str] = set()

    @classmethod
    def from_supplier(cls, conn: object, supplier: dict) -> "MemberMatcher":
        """A matcher with the supplier profile's settings (thresholds, GPO ID cutoff); not loaded yet."""
        def setting(name, default):
            value = supplier.get(name)
            return default if value is None else value
        return cls(
            conn=conn,
            supplier_profile_id=supplier["id"],
            zip_state_threshold=setting("fuzzy_zip_state_threshold", FUZZY_ZIP_STATE_THRESHOLD),
            city_state_threshold=setting("fuzzy_city_state_threshold", FUZZY_CITY_STATE_THRESHOLD),
            state_threshold=setting("fuzzy_state_threshold", FUZZY_STATE_THRESHOLD),
            nationwide_threshold=setting("fuzzy_nationwide_threshold", FUZZY_NATIONWIDE_THRESHOLD),
            gpo_id_min_name_score=setting("gpo_id_min_name_score", GPO_ID_MIN_NAME_SCORE),
            strong_name_threshold=setting("fuzzy_strong_name_threshold", FUZZY_STRONG_NAME_THRESHOLD),
            street_name_threshold=setting("fuzzy_street_name_threshold", FUZZY_STREET_NAME_THRESHOLD),
            auto_match_min_confidence=setting("auto_match_min_confidence", AUTO_MATCH_MIN_CONFIDENCE),
        )

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        """Load members, aliases, and JDE history into memory."""
        self._load_members()
        self._load_aliases()
        self._load_jde_history()
        self._load_non_members()

    def _load_members(self) -> None:
        rows = self.conn.execute(
            "SELECT * FROM member WHERE is_active = 1"
        ).fetchall()
        self._members = [dict(r) for r in rows]
        # No primary flags anywhere: the Account List hasn't been reloaded with them yet (see _pick_site).
        self.no_primary_flags_loaded = all(m.get("is_primary_site") is None for m in self._members)
        # Build geographic indexes
        for m in self._members:
            # Normalized from the raw values with today's rules: stored keys may predate a normalizer change.
            m["canonical_key"] = normalize_name(m.get("name"))
            m["norm_zip"] = normalize_zip(m.get("billing_zip")) or normalize_zip(m.get("norm_zip"))
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
                self._by_entity_code.setdefault(ec, []).append(m)
            aid = (m.get("account_id") or "").strip()
            if aid:
                self._by_account_id[aid] = m
            self._by_id[m["id"]] = m

    def _load_aliases(self) -> None:
        rows = self.conn.execute(
            """
            SELECT a.raw_name AS alias_raw_name, a.canonical_key AS alias_key, a.jde_number, m.*
            FROM member_alias a
            JOIN member m ON m.id = a.member_id
            WHERE m.is_active = 1
              AND (a.supplier_profile_id IS NULL OR a.supplier_profile_id = ?)
            """,
            (self.supplier_profile_id,),
        ).fetchall()
        for r in rows:
            d = dict(r)
            key = normalize_key(d.pop("alias_raw_name")) or d.get("alias_key")
            d.pop("alias_key", None)
            d["canonical_key"] = normalize_name(d.get("name"))
            jde = d.get("jde_number")
            self._aliases[key] = d
            if jde:
                self._jde_map[str(jde)] = d

    def _load_non_members(self) -> None:
        for r in self.conn.execute(
            "SELECT jde_number, raw_name FROM non_member WHERE supplier_profile_id = ?", (self.supplier_profile_id,)
        ).fetchall():
            if r["jde_number"]:
                self._non_member_jde.add(str(r["jde_number"]).strip())
            elif normalize_key(r["raw_name"]):
                self._non_member_names.add(normalize_key(r["raw_name"]))

    def is_non_member(self, jde_number: Optional[str], customer_name: Optional[str]) -> bool:
        """A reviewer said this account isn't a member: by JDE number when the row has one, else by name."""
        if jde_number and str(jde_number).strip():
            return str(jde_number).strip() in self._non_member_jde
        return bool(normalize_key(customer_name)) and normalize_key(customer_name) in self._non_member_names

    def _load_jde_history(self) -> None:
        rows = self.conn.execute(
            """
            SELECT h.jde_number, m.*
            FROM jde_member_history h
            JOIN member m ON m.id = h.member_id
            WHERE h.supplier_profile_id = ? AND m.is_active = 1
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
        address: Optional[str] = None,
    ) -> MatchResult:
        """
        Match a row (see the module docstring for the steps). Accounts marked "not a member" come back as
        MATCH_NOT_A_MEMBER unless the row's Salesforce Account ID is a member; a match below
        auto_match_min_confidence comes back unmatched (MATCH_LOW_CONFIDENCE) with that member as the
        review candidate.
        """
        aid = str(account_id_hint).strip() if account_id_hint else ""
        if aid not in self._by_account_id and self.is_non_member(jde_number, customer_name):
            return MatchResult(match_method=MATCH_NOT_A_MEMBER)
        result = self._match(customer_name, jde_number, city, state, zip_code, entity_code_hint,
                             account_id_hint, address)
        if result.matched and result.match_score < self.auto_match_min_confidence:
            return MatchResult(match_method=MATCH_LOW_CONFIDENCE, candidates=[
                {"member": self._by_id[result.member_id], "score": result.match_score, "via": result.match_method}])
        return result

    def _match(
        self,
        customer_name: Optional[str],
        jde_number: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        zip_code: Optional[str] = None,
        entity_code_hint: Optional[str] = None,
        account_id_hint: Optional[str] = None,
        address: Optional[str] = None,
    ) -> MatchResult:
        """
        Run the matching strategy (see the module docstring for the order) and return a MatchResult.
        """
        norm_name = normalize_name(customer_name)
        norm_zip  = normalize_zip(zip_code)
        norm_state = normalize_state(state)
        norm_city  = normalize_city(city)

        # Step 0: Salesforce Account ID exact match (highest confidence)
        if account_id_hint:
            aid = str(account_id_hint).strip()
            if aid in self._by_account_id:
                return _member_row_to_result(self._by_account_id[aid], MATCH_ACCOUNT_ID, 100)

        # The GPO ID's member, worked out up front so the JDE step can compare against it. The code may
        # cover several ship-to sites; _pick_site chooses one (address, else primary, else none).
        ec_sites: list[dict] = []
        ec_site, ec_method = None, None
        if entity_code_hint:
            ec_sites = self._by_entity_code.get(str(entity_code_hint).strip().upper()) or []
            if ec_sites:
                ec_site, ec_method = _pick_site(ec_sites, address, zip_code, self.no_primary_flags_loaded)
        # Sites of the code the row fits: name close enough, or same street + ZIP.
        fitting = [m for m in ec_sites
                   if _name_score(norm_name, m) >= self.gpo_id_min_name_score
                   or _same_address(m, address, zip_code)]

        # Step 1: JDE number (history, or an alias carrying it)
        jde_member = self._jde_map.get(str(jde_number).strip()) if jde_number else None
        if jde_member:
            if fitting and all(m["id"] != jde_member["id"] for m in ec_sites):
                gpo_member = ec_site if ec_site in fitting else fitting[0]
                return MatchResult(match_method=MATCH_JDE_GPO_CONFLICT, candidates=[
                    {"member": jde_member, "score": _name_score(norm_name, jde_member), "via": "jde"},
                    {"member": gpo_member, "score": _name_score(norm_name, gpo_member), "via": "gpo_id"},
                ])
            return _member_row_to_result(jde_member, MATCH_JDE_HISTORY, 100)

        # Step 2: GPO ID (entity code hint), used only when it fits the row
        gpo_rejected: Optional[dict] = None
        if ec_sites:
            if ec_site is None:                      # several sites, none primary: review, don't guess
                return MatchResult(match_method=ec_method)
            if ec_method == MATCH_ENTITY_CODE_SITE_ADDRESS or ec_site in fitting:
                return _member_row_to_result(ec_site, ec_method, 100)
            gpo_rejected = ec_site                   # doesn't fit: ignore it, try the later steps

        result = self._match_by_name(customer_name, norm_name, norm_zip, norm_state, norm_city, address, zip_code)
        if result.matched or gpo_rejected is None:
            return result
        return MatchResult(match_method=MATCH_GPO_ID_CONFLICT, candidates=[
            {"member": gpo_rejected, "score": _name_score(norm_name, gpo_rejected), "via": "gpo_id"},
            *[c for c in result.candidates if c["member"]["id"] != gpo_rejected["id"]]])

    def _match_by_name(self, customer_name, norm_name, norm_zip, norm_state, norm_city,
                       address=None, zip_code=None) -> MatchResult:
        """Steps 3-7: approved alias, then fuzzy name within ZIP, city (may auto-match), state, nationwide
        (review suggestions only). See the module docstring for the street rules."""
        # Step 3: Alias by canonical name key
        name_key = normalize_key(customer_name)
        if name_key and name_key in self._aliases:
            m = self._aliases[name_key]
            return _member_row_to_result(m, MATCH_ALIAS, 100)

        suggestions: dict[int, dict] = {}       # member id → {"member", "score", "via"}: best per member

        def suggest(member: dict, score: int, via: str) -> None:
            if score > suggestions.get(member["id"], {}).get("score", -1):
                suggestions[member["id"]] = {"member": member, "score": score, "via": via}

        # Steps 4-5: ZIP + state, then city + state
        local_steps = []
        if norm_zip and norm_state:
            local_steps.append((self._by_zip_state.get((norm_zip, norm_state), []), self.zip_state_threshold,
                                MATCH_ZIP_STATE, MATCH_ZIP_STATE_STREET, f"zip={norm_zip} state={norm_state}"))
        if norm_city and norm_state:
            local_steps.append((self._by_city_state.get((norm_city, norm_state), []), self.city_state_threshold,
                                MATCH_CITY_STATE, MATCH_CITY_STATE_STREET, f"city={norm_city} state={norm_state}"))
        row_street = normalize_address(address)
        row_zip = normalize_zip(zip_code)
        for candidates, threshold, method, street_method, geo in local_steps:
            best: Optional[tuple] = None        # (street confirmed, score, member)
            for c in candidates:
                score = _name_score(norm_name, c)
                member_street = normalize_address(c.get("billing_street"))
                if not (row_street and member_street):         # can't compare streets: name alone, as before
                    hit = (False, score) if score >= threshold else None
                elif member_street == row_street and row_zip and row_zip == normalize_zip(c.get("billing_zip")):
                    score = max(score, fuzz.token_set_ratio(norm_name, c.get("canonical_key") or ""))
                    hit = (True, score) if score >= self.street_name_threshold else None
                else:                                          # different street: only a near-exact name
                    hit = (False, score) if score >= self.strong_name_threshold else None
                    if hit is None and score >= threshold:
                        suggest(c, score, method)
                if hit and (best is None or hit > best[:2]):
                    best = (*hit, c)
            if best:
                street_ok, score, member = best
                if street_ok:                                  # street + ZIP confirm it: confident
                    return _member_row_to_result(member, street_method, max(score, FUZZY_STREET_CONFIDENCE), geo)
                return _member_row_to_result(member, method, score, geo)

        # Step 6: State only — suggestions for review, never an automatic match
        if norm_state:
            for c in self._by_state.get(norm_state, []):
                score = _name_score(norm_name, c)
                if score >= self.state_threshold:
                    suggest(c, score, MATCH_STATE)

        # Step 7: Nationwide (only names long enough) — suggestions for review, never an automatic match
        if norm_name and len(norm_name) > FUZZY_MIN_NAME_LENGTH:
            for c in self._members:
                score = _name_score(norm_name, c)
                if score >= self.nationwide_threshold:
                    suggest(c, score, MATCH_NATIONWIDE)

        if suggestions:
            ranked = sorted(suggestions.values(), key=lambda c: -c["score"])[:3]
            return MatchResult(match_method=MATCH_FUZZY_SUGGESTION, candidates=ranked)
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
        Persist a JDE number → member mapping so future jobs can use step 1.
        Updates in-memory cache too.
        """
        if not jde_number:
            return
        jde_str = str(jde_number).strip()
        try:
            from .db import execute_upsert
            execute_upsert(
                self.conn,
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

    def review_candidates(
        self,
        customer_name: Optional[str],
        jde_number: Optional[str] = None,
        address: Optional[str] = None,
        city: Optional[str] = None,
        state: Optional[str] = None,
        zip_code: Optional[str] = None,
        entity_code_hint: Optional[str] = None,
        seeded: Optional[list[dict]] = None,
    ) -> list[dict]:
        """
        Up to REVIEW_MAX_CANDIDATES members to show the reviewer of an unmatched row, best first, each
        {"member", "score", "via", "matched"}: score is the name score; matched lists the fields that agree
        with the row (name, street, zip, jde, gpo_id). Candidates come from the match itself (seeded: a
        conflict's or suggestion's members), the JDE number's and the GPO ID's members, and the best names
        in the row's state (everywhere when it has none). A candidate needs a name score of at least
        REVIEW_SUGGESTION_MIN_SCORE unless the JDE number, GPO ID or street + ZIP point to it.
        """
        norm_name = normalize_name(customer_name)
        row_street, row_zip = normalize_address(address), normalize_zip(zip_code)
        jde_member = self._jde_map.get(str(jde_number).strip()) if jde_number else None
        code = str(entity_code_hint).strip().upper() if entity_code_hint else ""

        pool: dict[int, dict] = {}
        for c in seeded or []:
            pool.setdefault(c["member"]["id"], {"member": c["member"], "via": c["via"]})
        if jde_member:
            pool.setdefault(jde_member["id"], {"member": jde_member, "via": "jde"})
        for m in self._by_entity_code.get(code, []) if code else []:
            pool.setdefault(m["id"], {"member": m, "via": "gpo_id"})
        norm_state = normalize_state(state)
        nearby = self._by_state.get(norm_state, []) if norm_state else self._members
        for m in sorted(nearby, key=lambda m: -_name_score(norm_name, m))[:REVIEW_MAX_CANDIDATES * 2]:
            pool.setdefault(m["id"], {"member": m, "via": "name"})

        ranked = []
        for c in pool.values():
            m = c["member"]
            score = _name_score(norm_name, m)
            matched = []
            if score >= self.zip_state_threshold:
                matched.append("name")
            if row_street and row_street == normalize_address(m.get("billing_street")):
                matched.append("street")
            if row_zip and row_zip == normalize_zip(m.get("billing_zip")):
                matched.append("zip")
            if jde_member and jde_member["id"] == m["id"]:
                matched.append("jde")
            if code and code == (m.get("entity_code") or "").strip().upper():
                matched.append("gpo_id")
            pointed = {"jde", "gpo_id"} & set(matched) or {"street", "zip"} <= set(matched)
            if score < REVIEW_SUGGESTION_MIN_SCORE and not pointed:
                continue
            # Order by evidence: the name score plus a bonus for each identifier or a matching address, so a
            # GPO ID whose name doesn't fit doesn't outrank a close name at the row's address.
            rank = score + 25 * ("jde" in matched) + 15 * ("gpo_id" in matched) \
                + 20 * ({"street", "zip"} <= set(matched))
            ranked.append((rank, {**c, "score": score, "matched": matched}))
        ranked.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in ranked[:REVIEW_MAX_CANDIDATES]]
