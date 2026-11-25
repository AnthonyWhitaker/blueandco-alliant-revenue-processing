"""
Score the member matcher (and, separately, the GPO-key verifier) against the
labelled synthetic Medline file from scripts/make_medline_synthetic.py.

Builds a fresh scratch copy of the source database, loads the synthetic
Account List into it with the app's own load_account_list(), runs the file
through scripts/match_diagnostics.match_files() (the processor's matching
calls, nothing written), and compares each row's result with its label.

    .venv/bin/python scripts/score_medline_synthetic.py --scratch-db /tmp/x/scratch.db --out /tmp/x/report
        [--source-db data/revenue.db] [--samples samples/medline] [--supplier Medline]

Outputs in --out: report.md, unmatched_rows.csv (closest candidate per row),
scored_rows.csv (every row), diagnostics_summary.txt (what match_diagnostics.py prints for this file).
The source database is only read.
"""

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))

import pandas as pd
from thefuzz import fuzz

from engine.db import init_db
from engine.gpo_key import GpoKey, KeyColumns, read_gpo_key
from engine.member_loader import load_account_list
from engine.normalizer import normalize_city, normalize_name, normalize_state, normalize_zip
from match_diagnostics import CAUSES, match_files, open_read_only, summarize

OUTCOMES = ["correct_exact", "correct_entity_other_site", "wrong_member", "missed", "false_match",
            "correct_unmatched"]


def build_scratch_db(source: str, scratch: str, account_list: Path) -> tuple[int, int, int]:
    Path(scratch).parent.mkdir(parents=True, exist_ok=True)
    for suffix in ("", "-wal", "-shm"):
        Path(scratch + suffix).unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
    dst = sqlite3.connect(scratch)
    src.backup(dst)
    src.close()
    dst.close()
    conn = init_db(scratch)            # applies migrations the copied database may not have yet
    result = load_account_list(conn, str(account_list))
    conn.commit()
    conn.close()
    if result[1]:
        sys.exit(f"{result[1]} Account List rows failed to load into the scratch database.")
    return result


def outcome(r, label) -> str:
    expected = label["expected_account_id"]
    if not expected:
        return "false_match" if r.match.matched else "correct_unmatched"
    if not r.match.matched:
        return "missed"
    if r.match.account_id == expected:
        return "correct_exact"
    if (r.match.entity_code or "").upper() == label["expected_entity_code"].upper():
        return "correct_entity_other_site"
    return "wrong_member"


def expected_member_view(matcher, r, label) -> dict:
    """How the expected member looked to the matcher for this row: its name score and whether it shared a bucket."""
    m = next((x for x in matcher._members if x["account_id"] == label["expected_account_id"]), None)
    if not m:
        return {}
    z, s, c = normalize_zip(r.zip_code), normalize_state(r.state), normalize_city(r.city)
    score = fuzz.token_sort_ratio(normalize_name(r.name), m["canonical_key"])
    if (m["norm_zip"], m["norm_state"]) == (z, s):
        tier, needed = "zip+state", matcher.zip_state_threshold
    elif (m["norm_city"], m["norm_state"]) == (c, s):
        tier, needed = "city+state", matcher.city_state_threshold
    elif m["norm_state"] == s:
        tier, needed = "state", matcher.state_threshold
    else:
        tier, needed = "nationwide", matcher.nationwide_threshold
    return {"expected_member_score": score, "expected_member_tier": tier, "expected_member_threshold": needed}


def md_table(df: pd.DataFrame, index: bool = True) -> str:
    if index:
        df = df.reset_index()
    cols = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |", "|" + "|".join("---" for _ in cols) + "|"]
    for row in df.itertuples(index=False):
        lines.append("| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |")
    return "\n".join(lines)


def crosstab(df: pd.DataFrame, by: str, cols: list[str], col: str = "outcome") -> pd.DataFrame:
    t = pd.crosstab(df[by], df[col]).reindex(columns=cols, fill_value=0)
    t = t.loc[:, (t != 0).any()]
    t["total"] = t.sum(axis=1)
    return t.sort_values("total", ascending=False)


def main():
    root = Path(__file__).parent.parent
    parser = argparse.ArgumentParser(description="Score the member matcher on the synthetic Medline file")
    parser.add_argument("--source-db", default=str(root / "data" / "revenue.db"))
    parser.add_argument("--scratch-db", required=True)
    parser.add_argument("--samples", default=str(root / "samples" / "medline"))
    parser.add_argument("--supplier", default="Medline")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    samples, out = Path(args.samples), Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    loaded = build_scratch_db(args.source_db, args.scratch_db, samples / "synthetic_account_list.xlsx")
    conn = open_read_only(args.scratch_db)
    supplier = dict(conn.execute("SELECT * FROM supplier_profile WHERE name = ?", (args.supplier,)).fetchone())
    incoming = samples / "synthetic_incoming.csv"
    matcher, results = match_files(conn, supplier, [str(incoming)])

    key_path = samples / "GPO Key (mock).csv"
    key = GpoKey.from_frame(read_gpo_key(str(key_path), KeyColumns(zip=None)))
    (out / "diagnostics_summary.txt").write_text(
        summarize(supplier, matcher, results, None) + "\n", encoding="utf-8")

    labels = pd.read_csv(samples / "synthetic_incoming_labels.csv", dtype=str).fillna("")
    labels = labels.set_index(labels["source_row"].astype(int))
    acct_list = pd.read_excel(samples / "synthetic_account_list.xlsx", dtype=str).fillna("")
    site_role = dict(zip(acct_list["Account ID"], acct_list["Site Role"]))
    members_by_id = {m["id"]: m for m in matcher._members}

    rows = []
    for r in results:
        lab = labels.loc[r.source_row]
        v = key.verify(r.jde_number, r.name, r.address, r.zip_code)
        matched_member = members_by_id.get(r.match.member_id, {})
        rec = {
            "source_row": r.source_row, "medline_account": r.jde_number,
            "scenario": lab["scenario"], "messiness": lab["messiness"], "gpo_id_state": lab["gpo_id_state"],
            "prior_jde_history": lab["prior_jde_history"],
            "file_name": r.name, "file_street": r.address, "file_city": r.city, "file_state": r.state,
            "file_zip": r.zip_code, "file_gpo_id": r.entity_code_hint,
            "outcome": outcome(r, lab), "matched": r.match.matched, "match_method": r.match.match_method, "match_score": r.match.match_score,
            "matched_name": r.match.account_name or "", "matched_entity_code": r.match.entity_code or "",
            "matched_site_role": site_role.get(r.match.account_id or "", ""),
            "matched_city": matched_member.get("billing_city", ""), "matched_zip": matched_member.get("billing_zip", ""),
            "likely_cause": r.cause or "",
            "closest_name": (r.closest or {}).get("name", ""), "closest_entity_code": (r.closest or {}).get("entity_code", ""),
            "closest_city": (r.closest or {}).get("billing_city", ""), "closest_state": (r.closest or {}).get("billing_state", ""),
            "closest_zip": (r.closest or {}).get("billing_zip", ""), "closest_score": r.closest_score if r.closest else "",
            "closest_is_expected": ("Y" if (r.closest or {}).get("account_id") == lab["expected_account_id"] else "N")
            if r.closest else "",
            "expected_name": lab["expected_name"], "expected_entity_code": lab["expected_entity_code"],
            "expected_site_role": lab["expected_site_role"], "expected_city": lab["expected_city"],
            "expected_state": lab["expected_state"], "expected_zip": lab["expected_zip"],
            "gpo_key_result": "verified" if v.verified else v.reason, "gpo_key_gpo_id": v.gpo_id or "",
        }
        if not r.match.matched and lab["expected_account_id"]:
            rec.update(expected_member_view(matcher, r, lab))
        rows.append(rec)
    df = pd.DataFrame(rows)
    df.to_csv(out / "scored_rows.csv", index=False)
    unmatched = df[~df["matched"]].copy()
    unmatched["cause_sort"] = unmatched["likely_cause"].map({c: i for i, c in enumerate(CAUSES)})
    unmatched.sort_values(["cause_sort", "source_row"]).drop(columns="cause_sort").to_csv(
        out / "unmatched_rows.csv", index=False)

    # ---------------------------------------------------------------- report
    total, n_matched = len(df), int(df["matched"].sum())
    accts = df.drop_duplicates("medline_account")
    fuzzy = df[(df["gpo_id_state"].isin(["blank", "unknown_code"])) & (df["prior_jde_history"] == "N")]
    oc = Counter(df["outcome"])
    should = total - oc["correct_unmatched"] - oc["false_match"]

    md = [
        "# Medline member matching on synthetic data",
        "",
        f"- Incoming file: `{incoming}` ({total} invoice lines, {accts.shape[0]} Medline accounts; "
        "plus a grand-total row the reader drops)",
        f"- Labels: `{samples / 'synthetic_incoming_labels.csv'}`",
        f"- Member data: scratch copy `{args.scratch_db}` of `{args.source_db}` plus "
        f"`synthetic_account_list.xlsx` loaded with `load_account_list` ({loaded[0]} inserted). "
        f"Active members: {len(matcher._members)}.",
        f"- Matcher: `MemberMatcher` as Medline is configured in the DB (identity_mode "
        f"`{supplier.get('identity_mode')}`, thresholds {matcher.zip_state_threshold}/"
        f"{matcher.city_state_threshold}/{matcher.state_threshold}/{matcher.nationwide_threshold}), "
        "same calls and per-account cache as the processor. Nothing was written to either DB.",
        "",
        "## Totals",
        "",
        f"| | rows | Medline accounts |\n|---|---|---|\n"
        f"| total | {total} | {len(accts)} |\n"
        f"| matched | {n_matched} | {int(accts['matched'].sum())} |\n"
        f"| unmatched | {total - n_matched} | {int((~accts['matched']).sum())} |",
        "",
        "## Matched vs. the labels",
        "",
        "Counts are rows. `correct_entity_other_site` = right Premier entity code but a different ship-to row "
        "than labelled.",
        "",
        md_table(pd.DataFrame({"rows": [oc[o] for o in OUTCOMES]}, index=OUTCOMES).rename_axis("outcome")),
        "",
        f"Of the {should} rows that have a correct member, "
        f"{oc['correct_exact'] + oc['correct_entity_other_site']} found the right entity "
        f"({100 * (oc['correct_exact'] + oc['correct_entity_other_site']) / should:.0f}%), "
        f"{oc['wrong_member']} matched the wrong member and {oc['missed']} were missed. "
        f"Of the {oc['correct_unmatched'] + oc['false_match']} rows with no valid member, "
        f"{oc['false_match']} were matched anyway.",
        "",
        "### By match method",
        "",
        md_table(crosstab(df, "match_method", OUTCOMES)),
        "",
        "### By GPO ID in the file",
        "",
        "`GPO ID` is mapped to `entity_code_hint`, which is tried before account-number history and every name tier.",
        "",
        md_table(crosstab(df, "gpo_id_state", OUTCOMES)),
        "",
        "### By scenario",
        "",
        md_table(crosstab(df, "scenario", OUTCOMES)),
        "",
        f"### By messiness, name matching only ({len(fuzzy)} rows: GPO ID blank or unknown, no prior JDE history)",
        "",
        "A row can carry several tags, so it is counted once per tag.",
        "",
        md_table(crosstab(fuzzy.assign(tag=fuzzy["messiness"].str.split(";")).explode("tag").reset_index(drop=True), "tag", OUTCOMES)),
        "",
        "## Unmatched rows by likely cause",
        "",
        "The cause is the same heuristic `scripts/match_diagnostics.py` uses on real data (it can't see labels). "
        "The member matcher doesn't use street addresses, so street format can't cause a member-matcher miss; "
        "`address_format` here means a ZIP or state the matcher can't use.",
        "",
        md_table(crosstab(unmatched, "likely_cause", CAUSES + ["total"], col="likely_cause")[["total"]]
                 .reindex([c for c in CAUSES if c in set(unmatched["likely_cause"])])),
        "",
        "Heuristic cause vs. labelled scenario (checks how far to trust the heuristic on real data):",
        "",
        md_table(pd.crosstab(unmatched["scenario"], unmatched["likely_cause"])),
        "",
    ]
    missed = unmatched[unmatched["outcome"] == "missed"]
    if len(missed):
        md += [
            "Missed rows: how the expected member scored, at the tier where the row could have reached it:",
            "",
            md_table(missed.groupby("expected_member_tier").agg(
                rows=("source_row", "size"), median_score=("expected_member_score", "median"),
                min_score=("expected_member_score", "min"), max_score=("expected_member_score", "max"),
                threshold=("expected_member_threshold", "first"))),
            "",
        ]
    st = df[df["scenario"].isin(["ship_to_on_file", "ship_to_not_on_file"])]
    md += [
        "## Ship-to rows",
        "",
        md_table(pd.crosstab([st["scenario"], st["match_method"]], st["outcome"])),
        "",
        "Which site an exact entity-code match lands on when several member rows share the Premier code "
        "(only rows whose GPO ID was the correct, shared code):",
        "",
        md_table(pd.crosstab(
            df[df["match_method"].str.startswith("entity_code") & df["matched_site_role"].ne("") & (df["gpo_id_state"] == "correct")
               & df["expected_site_role"].ne("single site")]["expected_site_role"].where(
                lambda s: s == "primary", "ship-to").rename("labelled site"),
            df[df["match_method"].str.startswith("entity_code") & df["matched_site_role"].ne("") & (df["gpo_id_state"] == "correct")
               & df["expected_site_role"].ne("single site")]["matched_site_role"].where(
                lambda s: s.isin(["primary", "single site"]), "ship-to").rename("matched site"))),
        "",
    ]
    mk = df[df["scenario"] == "mock_key_account"]
    md += [
        "## GPO-key verifier (separate)",
        "",
        f"`GpoKey.verify` against `{key_path}` (no ZIP column, so street only; exact after normalizing). "
        f"Only the {mk['medline_account'].nunique()} mock-key accounts ({len(mk)} rows) are in that key; "
        "every other row is `account_not_found` by construction.",
        "",
        md_table(pd.crosstab(df["scenario"].where(df["scenario"] == "mock_key_account", "synthetic accounts"),
                             df["gpo_key_result"])),
        "",
        "Mock-key rows by messiness tag:",
        "",
        md_table(pd.crosstab(mk["messiness"], mk["gpo_key_result"])),
        "",
        f"Verified rows whose key GPO ID equals the labelled entity code: "
        f"{int(((mk['gpo_key_result'] == 'verified') & (mk['gpo_key_gpo_id'] == mk['expected_entity_code'])).sum())}"
        f" of {int((mk['gpo_key_result'] == 'verified').sum())}.",
        "",
        "## Files",
        "",
        f"- `{out / 'unmatched_rows.csv'}`: unmatched rows with likely cause, closest candidate, and the expected "
        "member's score",
        f"- `{out / 'scored_rows.csv'}`: every row with label, result and GPO-key result",
        f"- `{out / 'diagnostics_summary.txt'}`: what `scripts/match_diagnostics.py` prints for this file",
    ]
    (out / "report.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print("\n".join(md))


if __name__ == "__main__":
    main()
