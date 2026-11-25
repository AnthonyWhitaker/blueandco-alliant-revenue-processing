"""
GPO ID verification against a supplier key file (supplier_profile.identity_mode = 'gpo_key').

The GPO ID printed in some supplier files (Medline) can be wrong. The supplier
key lists, per supplier account, the account name, address and the correct GPO
ID. A file row is verified when its account number is in the key AND its
account name AND address agree with that key entry; the row then takes the
key's GPO ID. The address is the street plus the ZIP code (first five digits)
when the key has a ZIP column. Anything else is an exception with a reason. Nothing is guessed:
there is no fallback to the GPO ID in the file or to fuzzy member matching.

Comparison is on values normalized by the same functions member_match uses
(engine/normalizer.py): names by normalize_name (upper case, '&' -> 'AND',
punctuation and legal suffixes like INC/LLC removed), streets by
normalize_address (USPS abbreviations: STREET = ST, SUITE = STE, EAST = E),
ZIPs by normalize_zip (five digits, leading zeros restored). Account numbers
lose a trailing '.0' and leading zeros. Similarity tolerance for name/address
is configurable via VerifyOptions but defaults to exact (100).

Public API:
    KeyColumns            — which columns of the key file hold which field
    read_gpo_key()        — read a CSV/XLSX key file into a normalized DataFrame
    without_missing_zip() — drop the ZIP column from KeyColumns when the key file has none
    load_gpo_key()        — replace a supplier's key in gpo_key_entry
    suppliers_without_key() — suppliers in gpo_key mode with an empty key
    GpoKey                — in-memory key with verify()
"""

import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

import pandas as pd

from .constants import (
    KEY_IDENTITY_MODES, KEY_ACCOUNT_NOT_FOUND, KEY_ADDRESS_MISMATCH, KEY_AMBIGUOUS, KEY_MISSING_ACCOUNT,
    KEY_NAME_AND_ADDRESS_MISMATCH, KEY_NAME_MISMATCH, KEY_NO_GPO_ID,
)
from .file_reader import _normalize_col
from .normalizer import normalize_address, normalize_name, normalize_zip


class KeyFileError(ValueError):
    """The key file can't be read or lacks a configured column."""


@dataclass(frozen=True)
class KeyColumns:
    """Column names in the key file. Matched case- and whitespace-insensitively.

    Defaults are the headers of the Medline -> Alliant / Salesforce Account Key
    (Key sheet). "Final GPO ID" is the resolved ID; "GPO ID (Medline)" is what
    Medline has on file and is not used. The address is the street plus the ZIP
    code; set zip to None (or "") to compare the street only.
    """
    account_number: str = "Medline Account Number"
    account_name: str = "Medline Account Name"
    address: str = "Medline Street"
    gpo_id: str = "Final GPO ID"
    zip: Optional[str] = "Medline Zip"
    sheet_name: Optional[str] = None   # XLSX only; None = the first sheet that has all four columns
    header_row: int = 0                # 0-based row holding the headers


@dataclass(frozen=True)
class VerifyOptions:
    """Minimum similarity (0-100) for name and address to count as agreeing. 100 = exact after normalizing."""
    name_min_score: int = 100
    address_min_score: int = 100


@dataclass(frozen=True)
class Verification:
    gpo_id: Optional[str]        # the key's GPO ID when verified, else None
    reason: Optional[str]        # KEY_* reason when not verified, else None
    detail: str = ""             # human-readable explanation for the exceptions report

    @property
    def verified(self) -> bool:
        return self.reason is None


@dataclass(frozen=True)
class KeyLoadResult:
    loaded: int
    skipped_blank_account: int
    accounts_with_several_entries: int


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

def normalize_account(value) -> str:
    """'0006433012' / '6433012.0' / 6433012 -> '6433012'. Non-numeric values are upper-cased and stripped."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    s = str(value).strip().upper()
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    if s.isdigit():
        s = s.lstrip("0") or "0"
    return s


def _clean(value) -> Optional[str]:
    """Stripped text, or None for a blank/NaN cell."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    s = str(value).strip()
    return s or None


# ---------------------------------------------------------------------------
# Reading and loading the key file
# ---------------------------------------------------------------------------

def _wanted(columns: KeyColumns) -> dict[str, str]:
    wanted = {
        "account_number": columns.account_number,
        "account_name": columns.account_name,
        "address": columns.address,
        "gpo_id": columns.gpo_id,
    }
    if columns.zip:
        wanted["zip"] = columns.zip
    return wanted


def _first_sheet_with_columns(sheets: dict[str, pd.DataFrame], columns: KeyColumns) -> pd.DataFrame:
    """The key workbook also has summary and review sheets: use the first sheet with every key column."""
    wanted = {_normalize_col(n) for n in _wanted(columns).values()}
    for df in sheets.values():
        if wanted <= {_normalize_col(str(c)) for c in df.columns}:
            return df
    return next(iter(sheets.values()))   # none fits: the missing-column error names the first sheet's columns


def _read_raw(path: str, columns: KeyColumns) -> pd.DataFrame:
    """The key file's sheet with the configured columns, as text, headers unchanged."""
    suffix = Path(path).suffix.lower()
    try:
        if suffix == ".csv":
            raw = pd.read_csv(path, header=columns.header_row, dtype=str)
        elif suffix in (".xlsx", ".xls", ".xlsm"):
            if columns.sheet_name:
                raw = pd.read_excel(path, sheet_name=columns.sheet_name, header=columns.header_row, dtype=str)
            else:
                raw = _first_sheet_with_columns(
                    pd.read_excel(path, sheet_name=None, header=columns.header_row, dtype=str), columns)
        else:
            raise KeyFileError(f"Unsupported key file type '{suffix}'. Use CSV or Excel.")
    except KeyFileError:
        raise
    except Exception as e:
        raise KeyFileError(f"Cannot read key file: {e}") from e
    return raw


def without_missing_zip(path: str, columns: KeyColumns = KeyColumns()) -> KeyColumns:
    """*columns*, with zip=None when the key file has no such ZIP column (the street alone is then compared)."""
    if not columns.zip:
        return columns
    headers = {_normalize_col(str(c)) for c in _read_raw(path, replace(columns, zip=None)).columns}
    return columns if _normalize_col(columns.zip) in headers else replace(columns, zip=None)


def read_gpo_key(path: str, columns: KeyColumns = KeyColumns()) -> pd.DataFrame:
    """
    Read a key file (.csv, .xlsx, .xls, .xlsm) and return a DataFrame with
    columns account_number, account_name, address, gpo_id, zip (None when no
    ZIP column is configured) and their *_norm versions. Rows without an account number are dropped (see load_gpo_key
    for the count).
    """
    raw = _read_raw(path, columns)
    by_norm = {_normalize_col(str(c)): c for c in raw.columns}
    wanted = _wanted(columns)
    missing = [f"{field} ('{name}')" for field, name in wanted.items() if _normalize_col(name) not in by_norm]
    if missing:
        raise KeyFileError(
            f"Key file is missing configured column(s): {', '.join(missing)}. "
            f"Columns in the file: {[str(c) for c in raw.columns]}"
        )

    # Built as object Series of str/None: pandas would otherwise turn None back into NaN.
    df = pd.DataFrame({
        field: pd.Series([_clean(v) for v in raw[by_norm[_normalize_col(name)]]], dtype=object)
        for field, name in wanted.items()
    })
    if "zip" not in df:
        df["zip"] = pd.Series([None] * len(df), dtype=object)
    df["account_number_norm"] = df["account_number"].map(normalize_account)
    df["account_name_norm"] = df["account_name"].map(normalize_name)
    df["address_norm"] = df["address"].map(normalize_address)
    df["zip_norm"] = df["zip"].map(normalize_zip)
    df.attrs["skipped_blank_account"] = int((df["account_number_norm"] == "").sum())
    return df[df["account_number_norm"] != ""].reset_index(drop=True)


def load_gpo_key(
    conn: object,
    supplier_profile_id: int,
    path: str,
    columns: KeyColumns = KeyColumns(),
    source_file_name: Optional[str] = None,
) -> KeyLoadResult:
    """Replace the supplier's key in gpo_key_entry with the contents of *path*. The key is a full snapshot."""
    df = read_gpo_key(path, columns)
    conn.execute("DELETE FROM gpo_key_entry WHERE supplier_profile_id = ?", (supplier_profile_id,))
    name = source_file_name or Path(path).name
    params = [
        (supplier_profile_id, r.account_number, r.account_name, r.address, r.zip, r.gpo_id,
         r.account_number_norm, r.account_name_norm, r.address_norm, r.zip_norm, name)
        for r in df.itertuples(index=False)
    ]
    if params:
        conn.executemany(
            """
            INSERT INTO gpo_key_entry (
                supplier_profile_id, account_number, account_name, address, zip, gpo_id,
                account_number_norm, account_name_norm, address_norm, zip_norm, source_file_name
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            params,
        )
    conn.commit()
    counts = df["account_number_norm"].value_counts()
    return KeyLoadResult(
        loaded=len(df),
        skipped_blank_account=df.attrs.get("skipped_blank_account", 0),
        accounts_with_several_entries=int((counts > 1).sum()),
    )


def suppliers_without_key(conn: object) -> list[str]:
    """Names of suppliers in gpo_key mode with no key loaded: every row of theirs would be an exception."""
    rows = conn.execute(
        """
        SELECT s.name FROM supplier_profile s
        WHERE s.identity_mode IN (?, ?)
          AND NOT EXISTS (SELECT 1 FROM gpo_key_entry e WHERE e.supplier_profile_id = s.id)
        ORDER BY s.name
        """,
        KEY_IDENTITY_MODES,
    ).fetchall()
    return [r["name"] for r in rows]


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Entry:
    account_number: str
    account_name: Optional[str]
    address: Optional[str]
    gpo_id: Optional[str]
    name_norm: str
    address_norm: str
    zip: Optional[str] = None
    zip_norm: str = ""


class GpoKey:
    def __init__(self, entries: list[_Entry], options: VerifyOptions = VerifyOptions()):
        self.options = options
        # ZIP is compared when the key was loaded with a ZIP column (some entry has one). Then an
        # entry or row without a ZIP doesn't verify.
        self.match_zip = any(e.zip_norm for e in entries)
        self._by_account: dict[str, list[_Entry]] = {}
        for e in entries:
            self._by_account.setdefault(normalize_account(e.account_number), []).append(e)

    def __len__(self) -> int:
        return sum(len(v) for v in self._by_account.values())

    @classmethod
    def from_frame(cls, df: pd.DataFrame, options: VerifyOptions = VerifyOptions()) -> "GpoKey":
        """From read_gpo_key() output."""
        return cls([
            _Entry(_clean(r.account_number), _clean(r.account_name), _clean(r.address), _clean(r.gpo_id),
                   r.account_name_norm or "", r.address_norm or "", _clean(r.zip), r.zip_norm or "")
            for r in df.itertuples(index=False)
        ], options)

    @classmethod
    def from_db(cls, conn: object, supplier_profile_id: int, options: VerifyOptions = VerifyOptions()) -> "GpoKey":
        rows = conn.execute(
            "SELECT account_number, account_name, address, zip, gpo_id, account_name_norm, address_norm, zip_norm "
            "FROM gpo_key_entry WHERE supplier_profile_id = ?",
            (supplier_profile_id,),
        ).fetchall()
        # Normalized again from the stored raw values: the *_norm columns may predate a normalizer change.
        return cls([
            _Entry(r["account_number"], r["account_name"], r["address"], _clean(r["gpo_id"]),
                   normalize_name(r["account_name"]), normalize_address(r["address"]), r["zip"],
                   normalize_zip(r["zip"]))
            for r in rows
        ], options)

    def _agrees(self, file_value: str, key_value: str, min_score: int) -> bool:
        if not file_value or not key_value:
            return False
        if min_score >= 100:
            return file_value == key_value
        from thefuzz import fuzz
        return fuzz.ratio(file_value, key_value) >= min_score

    def _address_agrees(self, street: str, zip_norm: str, e: _Entry) -> bool:
        if not self._agrees(street, e.address_norm, self.options.address_min_score):
            return False
        return not self.match_zip or (bool(zip_norm) and zip_norm == e.zip_norm)

    def verify(self, account_number, account_name, address, zip_code=None) -> Verification:
        acct = normalize_account(account_number)
        if not acct:
            return Verification(None, KEY_MISSING_ACCOUNT, "The row has no account number.")
        entries = self._by_account.get(acct)
        if not entries:
            return Verification(None, KEY_ACCOUNT_NOT_FOUND, f"Account {acct} is not in the key.")

        name, addr, zip_norm = normalize_name(account_name), normalize_address(address), normalize_zip(zip_code)
        name_ok = [self._agrees(name, e.name_norm, self.options.name_min_score) for e in entries]
        addr_ok = [self._address_agrees(addr, zip_norm, e) for e in entries]
        hits = [e for e, n, a in zip(entries, name_ok, addr_ok) if n and a]

        if not hits:
            key_desc = "; ".join(
                f"'{e.account_name or ''}', '{e.address or ''}'" + (f" {e.zip or '(no ZIP)'}" if self.match_zip else "")
                for e in entries)
            if any(name_ok):
                reason = KEY_ADDRESS_MISMATCH
            elif any(addr_ok):
                reason = KEY_NAME_MISMATCH
            else:
                reason = KEY_NAME_AND_ADDRESS_MISMATCH
            return Verification(None, reason, f"Account {acct} in the key: {key_desc}.")

        gpo_ids = sorted({e.gpo_id for e in hits if e.gpo_id})
        if len(gpo_ids) > 1:
            return Verification(None, KEY_AMBIGUOUS,
                                f"Account {acct} matches key entries with different GPO IDs: {', '.join(gpo_ids)}.")
        if not gpo_ids:
            return Verification(None, KEY_NO_GPO_ID, f"Account {acct} is in the key without a GPO ID.")
        return Verification(gpo_ids[0], None)
