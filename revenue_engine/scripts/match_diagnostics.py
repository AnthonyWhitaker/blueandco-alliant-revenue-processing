"""
Aggregate-only member-matching diagnostics for a supplier's files.

Reads the files with the supplier's column map and runs every row through
MemberMatcher exactly as processor._run_pipeline does in member_match mode
(same arguments, same per-identity cache), then prints COUNTS ONLY: total
rows, matched/unmatched, matches by method, likely cause of each miss, and
the abbreviation/suffix/ZIP patterns seen in unmatched rows. It never prints
a name, address, account number or entity code, so the output can be pasted
anywhere.

Nothing is written. No job is created, JDE history is not recorded (within
one run that changes nothing: repeat account numbers hit the same cache entry
the processor uses) and the connection refuses anything but SELECT.

    python scripts/match_diagnostics.py --supplier Medline --file "Medline 2026.10.xlsx" [--file ...]
        [--db-path data/revenue.db]
        [--gpo-key "Medline GPO Key.xlsx" [--gpo-key-zip-column "Medline Zip"] [--gpo-key-sheet Key]]

Database: Azure SQL when AZURE_SQL_CONNECTION_STRING is set, otherwise SQLite
at --db-path / REVENUE_DB_PATH / data/revenue.db (the app's rules). The
supplier's current identity_mode is reported but ignored: the member matcher
always runs. --gpo-key adds a separate GPO-key verification section.
Run from the revenue_engine/ directory.
"""

import argparse
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from thefuzz import fuzz

from engine.combine import SourceFile, read_files
from engine.constants import (
    CANONICAL_ACCOUNT_ID_HINT, CANONICAL_ADDRESS, CANONICAL_CITY, CANONICAL_CUSTOMER_NAME,
    CANONICAL_ENTITY_CODE_HINT, CANONICAL_JDE_NUMBER, CANONICAL_STATE, CANONICAL_ZIP,
    LEGAL_SUFFIXES, MATCH_ENTITY_CODE_NO_PRIMARY, STATE_FULL_TO_ABBREV,
)
from engine.db import get_connection, is_mssql
from engine.file_reader import SOURCE_ROW_COL
from engine.member_matcher import MatchResult, MemberMatcher
from engine.normalizer import normalize_city, normalize_name, normalize_state, normalize_zip
from engine.processor import _iter_records, _text

# Likely cause of a miss (heuristic: the file says nothing about the truth).
CAUSE_NO_NAME = "no_name"
CAUSE_SHARED_CODE = "shared_code_no_primary"   # entity code shared by sites, none picked: sent to review
CAUSE_ADDRESS_FORMAT = "address_format"   # ZIP not 5 digits after cleanup, or state unrecognised
CAUSE_ZIP_MISMATCH = "zip_mismatch"       # the name scores well against a member at another ZIP/city
CAUSE_NAME_MISMATCH = "name_mismatch"     # members at this ZIP/city, none scores past the threshold
CAUSE_NO_CANDIDATE = "no_candidate"
CAUSES = [CAUSE_NAME_MISMATCH, CAUSE_ADDRESS_FORMAT, CAUSE_ZIP_MISMATCH, CAUSE_NO_CANDIDATE, CAUSE_NO_NAME,
          CAUSE_SHARED_CODE]

NAME_LOOKS_RIGHT = 70   # best name score elsewhere at/above this: the location is what differs
NAME_NEAR_MISS = 50     # best name score at this location at/above this: it's the name that differs


# ---------------------------------------------------------------------------
# Read-only connection
# ---------------------------------------------------------------------------

class ReadOnlyConnection:
    """Passes SELECTs through and refuses everything else, so a run can't change the database."""

    def __init__(self, conn):
        self._conn = conn

    def execute(self, sql, params=None):
        if not re.match(r"\s*(SELECT|WITH)\b", sql, re.IGNORECASE):
            raise RuntimeError("match_diagnostics is read-only; refused a non-SELECT statement")
        return self._conn.execute(sql, params) if params is not None else self._conn.execute(sql)

    def commit(self):
        pass

    def close(self):
        self._conn.close()


def open_read_only(db_path: Optional[str]) -> ReadOnlyConnection:
    if is_mssql():
        return ReadOnlyConnection(get_connection())
    path = db_path or get_default_sqlite_path()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return ReadOnlyConnection(conn)


def get_default_sqlite_path() -> str:
    import os
    return os.environ.get("REVENUE_DB_PATH", str(Path(__file__).parent.parent / "data" / "revenue.db"))


# ---------------------------------------------------------------------------
# Matching (mirrors processor._run_pipeline, member_match branch)
# ---------------------------------------------------------------------------

@dataclass
class RowResult:
    """One file row. Held in memory only; the CLI prints aggregates of these, never the fields."""
    source_file: str
    source_row: Optional[int]
    cache_key: tuple
    name: str
    jde_number: str
    address: str
    city: str
    state: str
    zip_code: str
    entity_code_hint: str
    match: MatchResult
    cause: Optional[str] = None                   # unmatched rows only
    closest: Optional[dict] = None                # unmatched rows: best-scoring member anywhere
    closest_score: int = 0
    matched_name_score: Optional[int] = None      # matched rows: row name vs matched member name
    extra: dict = field(default_factory=dict)


def _best(norm_name: str, candidates: list[dict]) -> tuple[Optional[dict], int]:
    best, best_score = None, 0
    for c in candidates:
        score = fuzz.token_sort_ratio(norm_name, c["canonical_key"])
        if score > best_score:
            best, best_score = c, score
    return best, best_score


def classify_unmatched(matcher: MemberMatcher, name: str, city: str, state: str,
                       zip_code: str) -> tuple[str, Optional[dict], int]:
    """(likely cause, closest member anywhere, its score). Uses the matcher's in-memory indexes."""
    norm_name = normalize_name(name)
    if not norm_name:
        return CAUSE_NO_NAME, None, 0
    z, s, c = normalize_zip(zip_code), normalize_state(state), normalize_city(city)

    local = matcher._by_zip_state.get((z, s), []) + matcher._by_city_state.get((c, s), [])
    local_ids = {m["id"] for m in local}
    best_local, local_score = _best(norm_name, local)
    elsewhere = [m for m in (matcher._by_state.get(s, []) if s else matcher._members) if m["id"] not in local_ids]
    best_else, else_score = _best(norm_name, elsewhere)
    best_any, any_score = _best(norm_name, matcher._members)
    if local_score >= any_score and best_local is not None:
        best_any, any_score = best_local, local_score

    if len(z) != 5 or s not in STATE_FULL_TO_ABBREV:
        cause = CAUSE_ADDRESS_FORMAT
    elif else_score >= NAME_LOOKS_RIGHT and else_score > local_score:
        cause = CAUSE_ZIP_MISMATCH
    elif local and local_score >= NAME_NEAR_MISS:
        cause = CAUSE_NAME_MISMATCH
    elif any_score >= NAME_LOOKS_RIGHT:
        cause = CAUSE_ZIP_MISMATCH   # name fits a member in another state
    else:
        cause = CAUSE_NO_CANDIDATE
    return cause, best_any, any_score


def match_files(conn, supplier: dict, paths: list[str]) -> tuple[MemberMatcher, list[RowResult]]:
    matcher = MemberMatcher.from_supplier(conn, supplier)
    matcher.load()
    members_by_id = {m["id"]: m for m in matcher._members}

    cache: dict[str, MatchResult] = {}
    results: list[RowResult] = []
    files = [SourceFile(Path(p).name, p) for p in paths]
    for source, read in read_files(files, supplier, conn):
        jde_col = CANONICAL_JDE_NUMBER if CANONICAL_JDE_NUMBER in read.df.columns else None
        for row in _iter_records(read.df):
            jde = _text(row.get(CANONICAL_JDE_NUMBER)) if jde_col else ""
            name = _text(row.get(CANONICAL_CUSTOMER_NAME))
            ec_hint = _text(row.get(CANONICAL_ENTITY_CODE_HINT))
            aid_hint = _text(row.get(CANONICAL_ACCOUNT_ID_HINT))
            if not name:
                name = ec_hint or aid_hint or ""
            city, state = _text(row.get(CANONICAL_CITY)), _text(row.get(CANONICAL_STATE))
            zip_code, address = _text(row.get(CANONICAL_ZIP)), _text(row.get(CANONICAL_ADDRESS))

            # Every matcher input, as processor._run_pipeline caches on.
            key = (jde or None, name, city, state, zip_code, ec_hint or None, aid_hint or None, address)
            match = cache.get(key)
            if match is None:
                match = matcher.match(
                    customer_name=name, jde_number=jde or None, city=city, state=state, zip_code=zip_code,
                    entity_code_hint=ec_hint or None, account_id_hint=aid_hint or None, address=address,
                )
                cache[key] = match

            src_row = row.get(SOURCE_ROW_COL)
            r = RowResult(source.name, int(src_row) if pd.notna(src_row) else None, key, name, jde,
                          address, city, state, zip_code, ec_hint, match)
            if match.matched:
                m = members_by_id.get(match.member_id)
                if m:
                    r.matched_name_score = fuzz.token_sort_ratio(normalize_name(name), m["canonical_key"])
            else:
                r.cause, r.closest, r.closest_score = classify_unmatched(matcher, name, city, state, zip_code)
                if match.match_method == MATCH_ENTITY_CODE_NO_PRIMARY:
                    r.cause = CAUSE_SHARED_CODE
            results.append(r)
    return matcher, results


# ---------------------------------------------------------------------------
# Patterns (counted, never shown)
# ---------------------------------------------------------------------------

NAME_PATTERNS = {
    "HH (home health)": r"\bHH\b",
    "HOSP": r"\bHOSP\b",
    "CTR": r"\bCTR\b",
    "MED (abbrev)": r"\bMED\b",
    "MC / RMC": r"\bR?MC\b",
    "REG": r"\bREG\b",
    "COMM": r"\bCOMM\b",
    "MEM": r"\bMEM\b",
    "HLTH": r"\bHLTH\b",
    "SURG / ASC": r"\b(SURG|ASC)\b",
    "REHAB": r"\bREHAB\b",
    "ST / ST. (Saint)": r"(^|\s)ST\.?(\s|$)",
    "SAINT spelled out": r"\bSAINT\b",
    "MT (Mount)": r"\bMT\.?(\s|$)",
    "UNIV": r"\bUNIV\b",
    "LOC / LOCATION n": r"\bLOC(ATION)?\.?\s*#?\s*\d+",
    "ship-to word (DOCK, RECEIVING, PHARMACY, ...)": r"\b(DOCK|RECEIVING|PHARMACY|ATTN|WAREHOUSE|STOREROOM|MATERIALS|LAB)\b",
    "legal suffix (INC, LLC, ...)": "|".join(LEGAL_SUFFIXES),
    "& or AND": r"&|\bAND\b",
    "punctuation . , ' - /": r"[.,'\-/]",
    "repeated spaces": r"\s{2,}",
    "digits": r"\d",
    "lower case letters": None,   # checked on the raw text
}
STREET_SUFFIX_ABBR = r"\b(ST|AVE|AV|RD|DR|BLVD|LN|PKWY|HWY|CT|PL|TRL|CIR|WAY)\b\.?"
STREET_SUFFIX_FULL = r"\b(STREET|AVENUE|ROAD|DRIVE|BOULEVARD|LANE|PARKWAY|HIGHWAY|COURT|PLACE|TRAIL|CIRCLE)\b"
STREET_PATTERNS = {
    "suffix abbreviated (ST, AVE, RD, ...)": STREET_SUFFIX_ABBR,
    "suffix spelled out (STREET, AVENUE, ...)": STREET_SUFFIX_FULL,
    "STE": r"\bSTE\b",
    "SUITE": r"\bSUITE\b",
    "# / UNIT / BLDG": r"#|\b(UNIT|BLDG|BUILDING|FL|FLOOR)\b",
    "directional abbreviated (N, S, E, W, NE, ...)": r"\b(N|S|E|W|NE|NW|SE|SW)\b\.?",
    "directional spelled out": r"\b(NORTH|SOUTH|EAST|WEST)\b",
    "PO BOX": r"\bP\.?\s*O\.?\s*BOX\b",
    "periods": r"\.",
    "blank": None,
}
CITY_PATTERNS = {
    "FT / ST / MT / PT abbreviation": r"(^|\s)(FT|ST|MT|PT)\.?\s",
    "blank": None,
}


def zip_shape(z: str) -> str:
    s = re.sub(r"\.0+$", "", z.strip())
    if not s:
        return "blank"
    if re.fullmatch(r"\d{5}", s):
        return "5 digits"
    if re.fullmatch(r"\d{5}-\d{4}", s):
        return "ZIP+4 with dash"
    if re.fullmatch(r"\d{9}", s):
        return "9 digits, no dash"
    if re.fullmatch(r"\d{3,4}", s):
        return "3-4 digits (leading zero lost)"
    if re.fullmatch(r"\d{3,4}-\d{4}", s):
        return "ZIP+4, leading zero lost"
    return "other"


def state_shape(st: str) -> str:
    s = st.strip().upper()
    if not s:
        return "blank"
    if len(s) == 2 and normalize_state(s) in STATE_FULL_TO_ABBREV:
        return "2-letter code"
    if s in STATE_FULL_TO_ABBREV:
        return "full name"
    return "unrecognised"


def _has(pattern: Optional[str], text: str) -> bool:
    if pattern is None:
        return False
    return re.search(pattern, text) is not None


def pattern_counts(rows: list[RowResult]) -> dict[str, Counter]:
    out = {"name": Counter(), "street": Counter(), "city": Counter(), "zip": Counter(), "state": Counter()}
    for r in rows:
        name_u = r.name.upper()
        for label, pat in NAME_PATTERNS.items():
            if label == "lower case letters":
                if re.search(r"[a-z]", r.name):
                    out["name"][label] += 1
            elif _has(pat, name_u):
                out["name"][label] += 1
        street_u = r.address.upper()
        for label, pat in STREET_PATTERNS.items():
            if label == "blank":
                if not street_u.strip():
                    out["street"][label] += 1
            elif _has(pat, street_u):
                out["street"][label] += 1
        city_u = r.city.upper()
        for label, pat in CITY_PATTERNS.items():
            if label == "blank":
                if not city_u.strip():
                    out["city"][label] += 1
            elif _has(pat, city_u + " "):
                out["city"][label] += 1
        out["zip"][zip_shape(r.zip_code)] += 1
        out["state"][state_shape(r.state)] += 1
    return out


# ---------------------------------------------------------------------------
# GPO key (optional, separate section)
# ---------------------------------------------------------------------------

def gpo_key_counts(results: list[RowResult], key_path: str, zip_column: Optional[str],
                   sheet: Optional[str]) -> tuple[Counter, Counter]:
    from engine.gpo_key import GpoKey, KeyColumns, read_gpo_key
    key = GpoKey.from_frame(read_gpo_key(key_path, KeyColumns(zip=zip_column or None, sheet_name=sheet)))
    reasons, agreement = Counter(), Counter()
    seen: dict[tuple, object] = {}
    for r in results:
        k = (r.jde_number, r.name, r.address, r.zip_code)
        v = seen.get(k) or key.verify(r.jde_number, r.name, r.address, r.zip_code)
        seen[k] = v
        reasons["verified" if v.verified else v.reason] += 1
        if v.verified:
            if not r.match.matched:
                agreement["key verified, member matcher unmatched"] += 1
            elif (r.match.entity_code or "").strip().upper() == (v.gpo_id or "").strip().upper():
                agreement["key verified, member matcher same entity code"] += 1
            else:
                agreement["key verified, member matcher DIFFERENT entity code"] += 1
        elif r.match.matched:
            agreement["key exception, member matcher matched"] += 1
        else:
            agreement["key exception, member matcher unmatched"] += 1
    return reasons, agreement


# ---------------------------------------------------------------------------
# Report (aggregates only)
# ---------------------------------------------------------------------------

def _pct(n: int, d: int) -> str:
    return f"{100 * n / d:5.1f}%" if d else "    -"


def _table(title: str, counts: Counter, denom: int, order: Optional[list[str]] = None,
           compare: Optional[Counter] = None, compare_denom: int = 0) -> list[str]:
    lines = [title]
    keys = order or [k for k, _ in counts.most_common()]
    if compare is not None:
        keys += [k for k in compare if k not in keys]
    for k in keys:
        n = counts.get(k, 0)
        line = f"  {k:<48} {n:>8} {_pct(n, denom)}"
        if compare is not None:
            m = compare.get(k, 0)
            line += f"   | all rows {m:>8} {_pct(m, compare_denom)}"
        lines.append(line)
    if not keys:
        lines.append("  (none)")
    return lines


def _score_bucket(s: int) -> str:
    for lo, hi in ((85, 101), (80, 85), (75, 80), (70, 75), (60, 70), (50, 60)):
        if lo <= s < hi:
            return f"{lo}-{hi - 1 if hi <= 100 else 100}"
    return "<50"


def summarize(supplier: dict, matcher: MemberMatcher, results: list[RowResult],
              gpo: Optional[tuple[Counter, Counter]] = None) -> str:
    total = len(results)
    matched = [r for r in results if r.match.matched]
    unmatched = [r for r in results if not r.match.matched]
    identities = {r.cache_key: r for r in results}
    id_matched = sum(1 for r in identities.values() if r.match.matched)

    out = [
        "MEMBER MATCH DIAGNOSTICS (aggregate only)",
        f"supplier identity_mode in DB: {supplier.get('identity_mode')} (member matcher run regardless)",
        f"thresholds zip+state/city+state/state/nationwide: {matcher.zip_state_threshold}/"
        f"{matcher.city_state_threshold}/{matcher.state_threshold}/{matcher.nationwide_threshold}   "
        f"GPO ID min name score: {matcher.gpo_id_min_name_score}",
        f"active members: {len(matcher._members)}   aliases: {len(matcher._aliases)}   "
        f"JDE numbers known: {len(matcher._jde_map)}",
        "",
        f"rows:        total {total}   matched {len(matched)} {_pct(len(matched), total)}   "
        f"unmatched {len(unmatched)} {_pct(len(unmatched), total)}",
        f"identities (distinct match inputs):  total {len(identities)}   matched {id_matched} {_pct(id_matched, len(identities))}   "
        f"unmatched {len(identities) - id_matched} {_pct(len(identities) - id_matched, len(identities))}",
        "  (an identity is what the processor caches on: account number, else ID/code hint, else name)",
        "",
    ]
    out += _table("Rows by match method:", Counter(r.match.match_method for r in results), total)
    out.append("")

    hints = Counter()
    by_code = matcher._by_entity_code
    for r in results:
        if not r.entity_code_hint:
            hints["blank"] += 1
        elif r.entity_code_hint.strip().upper() in by_code:
            hints["present, on member file"] += 1
        else:
            hints["present, NOT on member file"] += 1
    out += _table("Entity-code hint column (e.g. GPO ID):", hints, total)
    out.append("")

    low = Counter(r.match.match_method for r in matched
                  if r.matched_name_score is not None and r.matched_name_score < NAME_NEAR_MISS)
    out += _table(f"Matched rows whose name scores < {NAME_NEAR_MISS} against the matched member "
                  "(worth a look; expected for some JDE/alias matches):", low, len(matched))
    out.append("")

    out += _table("Unmatched rows by likely cause:", Counter(r.cause for r in unmatched), len(unmatched), CAUSES)
    out += [
        "  name_mismatch:  members exist at the row's ZIP/city, best name score there is below the threshold",
        "  address_format: ZIP isn't 5 digits after cleanup, or the state isn't recognised",
        f"  zip_mismatch:   the name scores >= {NAME_LOOKS_RIGHT} against a member at another ZIP/city/state",
        "  no_candidate:   nothing scores well enough anywhere",
        "  shared_code_no_primary: the entity code belongs to several sites, the street + ZIP picked none and "
        "none is flagged primary",
        "  (heuristic; street addresses are not used by the member matcher at all)",
        "",
    ]
    out += _table("Unmatched rows by best score against any member:",
                  Counter(_score_bucket(r.closest_score) for r in unmatched), len(unmatched),
                  ["85-100", "80-84", "75-79", "70-74", "60-69", "50-59", "<50"])
    out.append("")

    pu, pa = pattern_counts(unmatched), pattern_counts(results)
    for part, title in (("name", "Name patterns"), ("street", "Street patterns"), ("city", "City patterns"),
                        ("zip", "ZIP shapes"), ("state", "State shapes")):
        out += _table(f"{title} in unmatched rows:", pu[part], len(unmatched),
                      [k for k, _ in pu[part].most_common()], pa[part], total)
        out.append("")

    if gpo:
        reasons, agreement = gpo
        out.append("GPO KEY VERIFICATION (separate from the member matcher)")
        out += _table("Rows by key result:", reasons, total)
        out += _table("Key vs member matcher:", agreement, total)
        out.append("")
    return "\n".join(out)


def main():
    parser = argparse.ArgumentParser(description="Aggregate-only member-matching diagnostics")
    parser.add_argument("--supplier", required=True, help="Supplier profile name, e.g. Medline")
    parser.add_argument("--file", action="append", required=True, help="Supplier file; repeat for several")
    parser.add_argument("--db-path", default=None, help="SQLite path (ignored when Azure SQL is configured)")
    parser.add_argument("--gpo-key", default=None, help="Optional GPO key file for a side-by-side section")
    parser.add_argument("--gpo-key-zip-column", default="Medline Zip", help="'' when the key has no ZIP column")
    parser.add_argument("--gpo-key-sheet", default=None)
    args = parser.parse_args()

    conn = open_read_only(args.db_path)
    row = conn.execute("SELECT * FROM supplier_profile WHERE name = ?", (args.supplier,)).fetchone()
    if not row:
        sys.exit("No supplier with that name.")
    supplier = dict(row)
    matcher, results = match_files(conn, supplier, args.file)
    gpo = gpo_key_counts(results, args.gpo_key, args.gpo_key_zip_column, args.gpo_key_sheet) if args.gpo_key else None
    print(summarize(supplier, matcher, results, gpo))


if __name__ == "__main__":
    main()
