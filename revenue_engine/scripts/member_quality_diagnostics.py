"""
Aggregate-only quality report on the member master (the active members loaded from the Account List).

Prints COUNTS ONLY: never a name, address, account number or entity code, so the output can be pasted
anywhere. Same rules as match_diagnostics.py: the connection refuses anything but SELECT, and the database
is Azure SQL when AZURE_SQL_CONNECTION_STRING is set, otherwise SQLite at --db-path / REVENUE_DB_PATH /
data/revenue.db.

    python scripts/member_quality_diagnostics.py [--db-path data/revenue.db]
        [--near-name 90] [--near-street 85] [--rows-out DIR]

Sections:
  - Entity codes shared by several members (ship-to sites): how many have exactly one primary site,
    none, or more than one.
  - True duplicates: same normalized name + street + ZIP under different entity codes.
  - Near-duplicates: in the same ZIP, names scoring at least --near-name and streets at least --near-street.
  - Missing or bad data: blank name, missing street, ZIP that can't be read, missing or unknown state.
  - Members with no Premier code (their entity code is the Alliant code), and with no code at all.

--rows-out writes the rows behind each count as CSVs, for testing on synthetic data only: it refuses to run
against Azure SQL, and writes only synthetic members (account IDs starting SYNTHETIC_ACCOUNT_PREFIX, as made
by scripts/make_medline_synthetic.py). Any other member is counted but never written.
Run from the revenue_engine/ directory.
"""

import argparse
import csv
import sys
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

from thefuzz import fuzz

from engine.constants import STATE_FULL_TO_ABBREV
from engine.db import is_mssql
from engine.normalizer import normalize_address, normalize_name, normalize_state, normalize_zip
from match_diagnostics import open_read_only

SYNTHETIC_ACCOUNT_PREFIX = "001SYN"


def _blank(value) -> bool:
    return value is None or not str(value).strip()


def load_members(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT id, account_id, name, premier_entity_code, alliant_entity_code, entity_code, billing_street, "
        "billing_city, billing_state, billing_zip, is_primary_site FROM member WHERE is_active = 1"
    ).fetchall()
    members = []
    for r in rows:
        m = dict(r)
        m["norm_name"] = normalize_name(m["name"])
        m["norm_street"] = normalize_address(m["billing_street"])
        m["norm_zip"] = normalize_zip(m["billing_zip"])
        m["norm_state"] = normalize_state(m["billing_state"])
        m["code"] = (m["entity_code"] or "").strip().upper()
        members.append(m)
    return members


def shared_codes(members: list[dict]) -> tuple[Counter, list[tuple[str, list[dict]]]]:
    """Codes with several members, by how many of them are flagged primary."""
    by_code: dict[str, list[dict]] = defaultdict(list)
    for m in members:
        if m["code"]:
            by_code[m["code"]].append(m)
    shared = [(code, ms) for code, ms in by_code.items() if len(ms) > 1]
    counts = Counter()
    for _, ms in shared:
        flags = [m["is_primary_site"] for m in ms]
        primaries = sum(1 for f in flags if f == 1)
        if primaries == 1:
            counts["exactly one primary"] += 1
        elif primaries > 1:
            counts["more than one primary"] += 1
        elif all(f is None for f in flags):
            counts["no primary: flag missing on every site"] += 1
        else:
            counts["no primary: sites flagged, none primary"] += 1
        counts["members in shared codes"] += len(ms)
    counts["shared codes"] = len(shared)
    return counts, shared


def true_duplicates(members: list[dict]) -> tuple[Counter, list[list[dict]]]:
    """Same normalized name + street + ZIP: under different entity codes (and, separately, the same code)."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for m in members:
        if m["norm_name"] and m["norm_street"] and m["norm_zip"]:
            groups[(m["norm_name"], m["norm_street"], m["norm_zip"])].append(m)
    counts = Counter()
    different = []
    for ms in groups.values():
        if len(ms) < 2:
            continue
        if len({m["code"] for m in ms}) > 1:
            counts["groups with different entity codes"] += 1
            counts["members in those groups"] += len(ms)
            different.append(ms)
        else:
            counts["groups with the same entity code"] += 1
    return counts, different


def near_duplicates(members: list[dict], near_name: int, near_street: int) -> tuple[Counter, list[tuple]]:
    """Pairs in the same ZIP whose names and streets are close but not both identical."""
    by_zip: dict[str, list[dict]] = defaultdict(list)
    for m in members:
        if m["norm_zip"] and m["norm_name"] and m["norm_street"]:
            by_zip[m["norm_zip"]].append(m)
    counts = Counter()
    pairs = []
    for ms in by_zip.values():
        for a, b in combinations(ms, 2):
            if a["norm_name"] == b["norm_name"] and a["norm_street"] == b["norm_street"]:
                continue                                       # a true duplicate: counted above
            name = fuzz.token_sort_ratio(a["norm_name"], b["norm_name"])
            street = fuzz.ratio(a["norm_street"], b["norm_street"])
            if name >= near_name and street >= near_street:
                same_code = a["code"] == b["code"] and a["code"]
                counts["pairs, same entity code" if same_code else "pairs, different entity codes"] += 1
                pairs.append((a, b, name, street))
    counts["pairs"] = len(pairs)
    return counts, pairs


def bad_data(members: list[dict]) -> tuple[Counter, list[tuple[str, dict]]]:
    counts = Counter()
    rows = []

    def flag(problem: str, m: dict) -> None:
        counts[problem] += 1
        rows.append((problem, m))

    for m in members:
        if not m["norm_name"]:
            flag("blank name", m)
        if not m["norm_street"]:
            flag("missing street", m)
        if _blank(m["billing_zip"]):
            flag("missing ZIP", m)
        elif not m["norm_zip"]:
            flag("ZIP can't be read", m)
        elif len(str(m["billing_zip"]).strip().split("-")[0].split(".")[0]) < 5:
            counts["ZIP missing leading zeros (read with them put back)"] += 1
        if _blank(m["billing_state"]):
            flag("missing state", m)
        elif m["norm_state"] not in STATE_FULL_TO_ABBREV:
            flag("state not recognised", m)
    return counts, rows


def entity_code_sources(members: list[dict]) -> tuple[Counter, list[tuple[str, dict]]]:
    counts = Counter()
    rows = []
    for m in members:
        if not _blank(m["premier_entity_code"]):
            counts["Premier code"] += 1
        elif not _blank(m["alliant_entity_code"]):
            counts["no Premier code: Alliant code used"] += 1
            rows.append(("alliant_fallback", m))
        else:
            counts["no entity code at all"] += 1
            rows.append(("no_code", m))
    return counts, rows


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def _pct(n: int, total: int) -> str:
    return f"({100 * n / total:.1f}%)" if total else ""


def _section(title: str, counts: Counter, order: list[str], total: Optional[int] = None) -> list[str]:
    out = ["", title]
    for key in order:
        n = counts.get(key, 0)
        out.append(f"  {key:<52} {n:>6} {_pct(n, total) if total else ''}".rstrip())
    return out


def summarize(members: list[dict], near_name: int, near_street: int) -> str:
    total = len(members)
    shared, _ = shared_codes(members)
    dups, _ = true_duplicates(members)
    near, _ = near_duplicates(members, near_name, near_street)
    bad, _ = bad_data(members)
    codes, _ = entity_code_sources(members)
    out = ["MEMBER MASTER QUALITY (aggregate only)", f"active members: {total}"]
    out += _section("Entity codes shared by several members (ship-to sites)", shared, [
        "shared codes", "members in shared codes", "exactly one primary", "more than one primary",
        "no primary: flag missing on every site", "no primary: sites flagged, none primary"])
    out += _section("True duplicates (same normalized name + street + ZIP)", dups, [
        "groups with different entity codes", "members in those groups", "groups with the same entity code"])
    out += _section(f"Near-duplicates (same ZIP, name >= {near_name}, street >= {near_street})", near, [
        "pairs", "pairs, different entity codes", "pairs, same entity code"])
    out += _section("Missing or bad data", bad, [
        "blank name", "missing street", "missing ZIP", "ZIP can't be read",
        "ZIP missing leading zeros (read with them put back)", "missing state", "state not recognised"], total)
    out += _section("Entity code source", codes, [
        "Premier code", "no Premier code: Alliant code used", "no entity code at all"], total)
    return "\n".join(out)


def _synthetic(m: dict) -> bool:
    return str(m.get("account_id") or "").startswith(SYNTHETIC_ACCOUNT_PREFIX)


def write_rows(members: list[dict], near_name: int, near_street: int, out_dir: Path) -> Counter:
    """CSV rows behind each count, synthetic members only. Returns how many rows each file got / held back."""
    out_dir.mkdir(parents=True, exist_ok=True)
    fields = ["account_id", "name", "entity_code", "premier_entity_code", "alliant_entity_code", "billing_street",
              "billing_city", "billing_state", "billing_zip", "is_primary_site"]
    written = Counter()

    def write(name: str, header: list[str], rows: list[list]) -> None:
        with open(out_dir / name, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(header)
            w.writerows(rows)
        written[name] = len(rows)

    def ok(*ms: dict) -> bool:
        if all(_synthetic(m) for m in ms):
            return True
        written["held back (not synthetic)"] += 1
        return False

    _, shared = shared_codes(members)
    write("shared_codes.csv", ["group"] + fields,
          [[code] + [m[f] for f in fields] for code, ms in shared for m in ms if ok(m)])
    _, dups = true_duplicates(members)
    write("true_duplicates.csv", ["group"] + fields,
          [[i] + [m[f] for f in fields] for i, ms in enumerate(dups, 1) if ok(*ms) for m in ms])
    _, pairs = near_duplicates(members, near_name, near_street)
    write("near_duplicates.csv", ["pair", "name_score", "street_score"] + fields,
          [[i, n, s] + [m[f] for f in fields] for i, (a, b, n, s) in enumerate(pairs, 1) if ok(a, b) for m in (a, b)])
    _, bad = bad_data(members)
    write("bad_data.csv", ["problem"] + fields, [[p] + [m[f] for f in fields] for p, m in bad if ok(m)])
    _, codes = entity_code_sources(members)
    write("entity_code_source.csv", ["source"] + fields, [[p] + [m[f] for f in fields] for p, m in codes if ok(m)])
    return written


def main():
    parser = argparse.ArgumentParser(description="Aggregate-only member master quality report")
    parser.add_argument("--db-path", default=None, help="SQLite path (ignored when Azure SQL is configured)")
    parser.add_argument("--near-name", type=int, default=90, help="Near-duplicates: minimum name score")
    parser.add_argument("--near-street", type=int, default=85, help="Near-duplicates: minimum street score")
    parser.add_argument("--rows-out", default=None,
                        help="Synthetic data only: write the rows behind each count as CSVs to this directory")
    args = parser.parse_args()

    if args.rows_out and is_mssql():
        sys.exit("--rows-out is for synthetic SQLite databases only; refusing to write rows from Azure SQL.")
    conn = open_read_only(args.db_path)
    members = load_members(conn)
    print(summarize(members, args.near_name, args.near_street))
    if args.rows_out:
        written = write_rows(members, args.near_name, args.near_street, Path(args.rows_out))
        print("\nrows written (synthetic members only): " + ", ".join(f"{k} {v}" for k, v in sorted(written.items())))


if __name__ == "__main__":
    main()
