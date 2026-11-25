"""
Loads the Account List (member/facility master) into the member table.
Can be re-run to refresh the member table from a new Account List export.
"""

from pathlib import Path
from typing import Optional

import pandas as pd

from .normalizer import normalize_address, normalize_name, normalize_key, normalize_zip, normalize_state, normalize_city

# NOTE: member.canonical_key stores the SPACED normalized name (normalize_name output)
# so fuzzy matching (token_sort_ratio) works correctly.
# member_alias.canonical_key stores the NO-SPACES version (normalize_key output)
# for fast exact lookups. These two usages are intentionally different.


# Column names as they appear in the Account List.xlsx
# These are tried in order (case-insensitive) to handle slight name variations
_COL_NAMES = {
    "account_name":           ["account name", "name"],
    "premier_entity_code":    ["premier entity code", "pec", "premier_entity_code"],
    "alliant_entity_code":    ["alliant entity code", "alliant_entity_code", "entity code"],
    "account_id":             ["account id", "salesforce id", "facility id", "account_id"],
    "billing_city":           ["billing city", "city"],
    "billing_state":          ["billing state/province", "billing state", "state"],
    "billing_zip":            ["billing zip/postal code", "billing zip", "zip", "postal code"],
    "billing_street":         ["billing street", "billing address", "street"],
    # TODO(real data): confirm which Account List column marks a facility's primary site.
    "is_primary_site":        ["is primary", "is primary site", "primary site", "primary location", "primary"],
}

_YES = {"Y", "YES", "TRUE", "1", "X", "PRIMARY"}
_NO = {"N", "NO", "FALSE", "0", "SHIP-TO", "SHIP TO"}


def _primary_flag(value: str) -> Optional[int]:
    """1 / 0 for a yes / no cell; None when blank or not recognisable (the list doesn't say)."""
    v = value.strip().upper()
    return 1 if v in _YES else 0 if v in _NO else None


def _find_col(df_cols: list[str], candidates: list[str]) -> Optional[str]:
    """Find the first matching column name from a list of candidates."""
    norm_cols = {c.strip().lower(): c for c in df_cols}
    for candidate in candidates:
        if candidate.lower() in norm_cols:
            return norm_cols[candidate.lower()]
    return None


def _cell(row, col: Optional[str]) -> str:
    """A cell as stripped text; blank and missing cells (NaN) are ''."""
    if not col:
        return ""
    value = row.get(col)
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    return str(value).strip()


def _identity(canonical: str, entity_code: str, norm_state: str, street: Optional[str],
              norm_zip: str) -> tuple[str, str, str, str, str]:
    """
    How a row without a Salesforce Account ID is recognised on a later load: name, entity code, state,
    street and ZIP. Street + ZIP keep apart ship-to sites that share an entity code (and often the name):
    they are separate members and must never be merged into one.
    """
    return canonical, entity_code.upper(), norm_state, normalize_address(street), norm_zip


def _account_list_member_ids(conn: object, active: bool) -> set[int]:
    rows = conn.execute(
        "SELECT id FROM member WHERE account_id IS NOT NULL AND is_active = ?", (1 if active else 0,)
    ).fetchall()
    return {r[0] for r in rows}


def load_account_list(
    conn: object,
    file_path: str,
    sheet_name: Optional[str] = None,
    replace_all: bool = False,
) -> tuple[int, int, int]:
    """
    Load the Account List XLSX into the member table.

    Args:
        conn:        Database connection
        file_path:   Path to Account List.xlsx
        sheet_name:  Sheet name (auto-detects if None)
        replace_all: If True, a full refresh: members from an earlier Account
                     List that are not in this file are deactivated. They are
                     never deleted, because jobs, review items, allocation
                     rules, aliases and JDE history refer to them (FINDINGS F23).

    Rows are matched to existing members by Salesforce Account ID. A row
    without one is matched by name + entity code + state + street + ZIP
    instead (see _identity), so loading the same list again updates it rather
    than adding it again, and ship-to sites sharing an entity code stay
    separate members. A member first loaded without an ID takes the ID when a
    later list has one. A member stored without a street (loaded before the
    street was read) can be claimed by one row per load, so two sites can't
    both collapse into it.

    Returns:
        (inserted_count, skipped_count, deactivated_count)
    """
    xl = pd.ExcelFile(file_path)
    if sheet_name is None:
        # Auto-detect: use first sheet whose name contains "account"
        for s in xl.sheet_names:
            if "account" in s.lower():
                sheet_name = s
                break
        if sheet_name is None:
            sheet_name = xl.sheet_names[0]

    df = pd.read_excel(file_path, sheet_name=sheet_name, dtype=str)
    df.dropna(how="all", inplace=True)

    cols = list(df.columns)

    def gc(key: str) -> Optional[str]:
        return _find_col(cols, _COL_NAMES[key])

    col_name    = gc("account_name")
    col_pec     = gc("premier_entity_code")
    col_alliant = gc("alliant_entity_code")
    col_acct_id = gc("account_id")
    col_city    = gc("billing_city")
    col_state   = gc("billing_state")
    col_zip     = gc("billing_zip")
    col_street  = gc("billing_street")
    col_primary = gc("is_primary_site")

    if not col_name:
        raise ValueError("Cannot find Account Name column in Account List file.")

    if replace_all:
        # Mark and sweep, in one transaction: deactivate every Account List
        # member, then the upserts below reactivate the ones in this file.
        # Members created by hand during Review (no account_id) are left alone.
        active_before = _account_list_member_ids(conn, active=True)
        conn.execute("UPDATE member SET is_active = 0 WHERE account_id IS NOT NULL")

    # Existing members by Account ID, and by identity for matching rows without one.
    by_account_id: dict[str, Optional[int]] = {}       # None: added by this load (id not looked up)
    by_identity: dict[tuple, int] = {}
    by_identity_without_id: dict[tuple, int] = {}
    no_street: set[int] = set()          # members stored without a street: each claimable once per load
    for m in conn.execute(
        "SELECT id, account_id, name, entity_code, norm_state, billing_street, billing_zip, norm_zip "
        "FROM member ORDER BY id"
    ).fetchall():
        # Name and ZIP normalized again from the raw values: stored keys may predate a normalizer change.
        key = _identity(normalize_name(m["name"]), m["entity_code"] or "", m["norm_state"] or "",
                        m["billing_street"], normalize_zip(m["billing_zip"]) or normalize_zip(m["norm_zip"]))
        if m["account_id"]:
            by_account_id[m["account_id"]] = m["id"]
        else:
            by_identity_without_id.setdefault(key, m["id"])
        by_identity.setdefault(key, m["id"])
        if not key[3]:
            no_street.add(m["id"])

    def find(index: dict, key: tuple, claim: bool) -> Optional[int]:
        """The member for key; else, for a row with a street, a member stored without one (once)."""
        found = index.pop(key, None) if claim else index.get(key)
        if found is None and key[3]:
            legacy = index.get(key[:3] + ("", key[4]))
            if legacy in no_street:
                no_street.discard(legacy)
                found = legacy
        return found

    from .db import execute_upsert, get_last_insert_id
    inserted = 0
    skipped = 0

    for _, row in df.iterrows():
        name = _cell(row, col_name)
        if not name:
            skipped += 1
            continue

        pec     = _cell(row, col_pec)
        alliant = _cell(row, col_alliant)
        acct_id = _cell(row, col_acct_id)
        city    = _cell(row, col_city)
        state   = _cell(row, col_state)
        zip_raw = _cell(row, col_zip)
        street  = _cell(row, col_street)
        primary = _primary_flag(_cell(row, col_primary))

        # Effective entity code: the Premier code, else the Alliant code. Several members (ship-to sites
        # of one facility) can share it; see member_matcher._pick_site. They stay separate members (_identity).
        entity_code = pec if pec else alliant

        canonical = normalize_name(name)  # spaced version for fuzzy matching
        norm_city  = normalize_city(city)
        norm_state = normalize_state(state)
        norm_zip   = normalize_zip(zip_raw)
        key = _identity(canonical, entity_code, norm_state or "", street, norm_zip or "")

        fields = (
            pec or None,
            alliant or None,
            entity_code or None,
            name,
            canonical,
            city or None,
            state or None,
            zip_raw or None,
            norm_city or None,
            norm_state or None,
            norm_zip or None,
            street or None,
            primary,
        )
        # The member this row updates without an Account ID upsert: a row without an ID that matches by
        # identity, or a row with a new ID whose facility was loaded earlier without one (it takes the ID).
        if acct_id:
            existing = None if acct_id in by_account_id else find(by_identity_without_id, key, claim=True)
        else:
            existing = find(by_identity, key, claim=False)
            if existing is not None:
                by_identity[key] = existing

        try:
            if existing is not None:
                conn.execute(
                    """
                    UPDATE member SET
                        account_id          = COALESCE(?, account_id),
                        premier_entity_code = ?, alliant_entity_code = ?, entity_code = ?,
                        name = ?, canonical_key = ?,
                        billing_city = ?, billing_state = ?, billing_zip = ?,
                        norm_city = ?, norm_state = ?, norm_zip = ?,
                        billing_street = ?, is_primary_site = ?,
                        is_active = 1, updated_at = CURRENT_TIMESTAMP
                    WHERE id = ?
                    """,
                    (acct_id or None, *fields, existing),
                )
                if acct_id:
                    by_account_id[acct_id] = existing
            elif not acct_id:
                cur = conn.execute(
                    """
                    INSERT INTO member (
                        account_id, premier_entity_code, alliant_entity_code, entity_code,
                        name, canonical_key,
                        billing_city, billing_state, billing_zip,
                        norm_city, norm_state, norm_zip, billing_street, is_primary_site
                    ) VALUES (NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    fields,
                )
                by_identity[key] = get_last_insert_id(conn, cur)
            else:
                execute_upsert(
                    conn,
                    """
                    INSERT INTO member (
                        account_id, premier_entity_code, alliant_entity_code, entity_code,
                        name, canonical_key,
                        billing_city, billing_state, billing_zip,
                        norm_city, norm_state, norm_zip, billing_street, is_primary_site
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(account_id) DO UPDATE SET
                        premier_entity_code = excluded.premier_entity_code,
                        alliant_entity_code = excluded.alliant_entity_code,
                        entity_code         = excluded.entity_code,
                        name                = excluded.name,
                        canonical_key       = excluded.canonical_key,
                        billing_city        = excluded.billing_city,
                        billing_state       = excluded.billing_state,
                        billing_zip         = excluded.billing_zip,
                        norm_city           = excluded.norm_city,
                        norm_state          = excluded.norm_state,
                        norm_zip            = excluded.norm_zip,
                        billing_street      = excluded.billing_street,
                        is_primary_site     = excluded.is_primary_site,
                        is_active           = 1,
                        updated_at          = CURRENT_TIMESTAMP
                    """,
                    (acct_id, *fields),
                )
                by_account_id.setdefault(acct_id, None)
            inserted += 1
        except Exception:
            skipped += 1

    deactivated = 0
    if replace_all:
        deactivated = len(active_before & _account_list_member_ids(conn, active=False))
    conn.commit()
    return inserted, skipped, deactivated


def create_member(
    conn: object,
    name: str,
    account_id: Optional[str] = None,
    premier_entity_code: Optional[str] = None,
    alliant_entity_code: Optional[str] = None,
    city: Optional[str] = None,
    state: Optional[str] = None,
    zip_code: Optional[str] = None,
) -> int:
    """
    Manually create a single new member record — used when a supplier file
    references a real facility that isn't yet in the Account List (e.g. a
    zero-fuzzy-match-candidate resolved during Review).

    Returns the new member.id.
    """
    name = (name or "").strip()
    if not name:
        raise ValueError("Member name is required")

    entity_code = (premier_entity_code or alliant_entity_code or "").strip() or None
    canonical = normalize_name(name)
    norm_city_val = normalize_city(city) if city else None
    norm_state_val = normalize_state(state) if state else None
    norm_zip_val = normalize_zip(zip_code) if zip_code else None

    from .db import get_last_insert_id
    cursor = conn.execute(
        """
        INSERT INTO member (
            account_id, premier_entity_code, alliant_entity_code, entity_code,
            name, canonical_key,
            billing_city, billing_state, billing_zip,
            norm_city, norm_state, norm_zip
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (account_id or "").strip() or None,
            (premier_entity_code or "").strip() or None,
            (alliant_entity_code or "").strip() or None,
            entity_code,
            name,
            canonical,
            (city or "").strip() or None,
            (state or "").strip() or None,
            (zip_code or "").strip() or None,
            norm_city_val,
            norm_state_val,
            norm_zip_val,
        ),
    )
    conn.commit()
    return get_last_insert_id(conn, cursor)
