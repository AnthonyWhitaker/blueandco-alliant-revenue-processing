"""
Declarative supplier configurations for admin-fee suppliers.

Adding a supplier is a config change: a profile (supplier_profile columns)
plus a column map (raw header -> canonical field). apply_preset() writes one
to the database; scripts/seed_admin_fee_suppliers.py applies them all. The
same settings can be edited afterwards in Admin > Suppliers / Column Maps.

Profile keys not given fall back to the supplier_profile column defaults.
"""

from dataclasses import dataclass, field
from typing import Optional

from .constants import IDENTITY_GPO_KEY, IDENTITY_MEMBER_MATCH
from .db import execute_insert_ignore, execute_upsert
from .gpo_key import KeyFileError, load_gpo_key, without_missing_zip

# Every supplier_profile column a preset may set.
_PROFILE_FIELDS = [
    "name", "description", "contract_id", "revenue_type", "keep_return_default",
    "header_row", "sheet_name", "skip_rows_where_col_null", "max_read_columns", "fee_rate",
    "payment_id_source", "payor_filter_name", "job_type", "requires_manual_confirmation",
    "identity_mode", "volume_mode",
]
_DEFAULTS = {
    "revenue_type": "Admin Fee", "keep_return_default": "Keep", "header_row": 0,
    "payment_id_source": "ledger", "job_type": "row_level", "requires_manual_confirmation": 0,
    "identity_mode": "member_match", "volume_mode": "sum",
}

MEDLINE = {
    "profile": dict(
        name="Medline",
        description="Medline — item-level invoice lines (one row per invoice line). Facilities are matched to "
                    "members by GPO ID, Medline account number history and name/location (member matching); "
                    "lines roll up to one row per facility per month (Volume = extended price, Total Revenue = "
                    "GPO fee). Switch identity_mode to gpo_key to verify GPO IDs against a loaded GPO key instead.",
        contract_id="AL-DS-001",
        payor_filter_name="Medline",
        # Only the grand-total row at the bottom has no invoice number. (Not the account number: an
        # invoice line missing its account number must reach the exceptions report, not be stripped.)
        skip_rows_where_col_null="InvoiceNumber",
        # What Medline runs today. gpo_key verification is built and tested, but not switched on yet.
        identity_mode="member_match",
        volume_mode="sum",
    ),
    # Headers of the mock file (scripts/make_medline_mock.py), modelled on Medline's 22-column file.
    # TODO(real data): confirm against a real Medline file.
    "column_map": [
        ("Medline Account #", "jde_number"),
        ("Account Name", "customer_name"),
        ("Account Street", "address"),
        ("Account City", "city"),
        ("Account State", "state"),
        ("Account Zip", "zip"),
        ("GPO ID", "entity_code_hint"),
        ("InvoiceDate", "process_date"),
        ("Extended Price Amount", "sales_amount"),
        ("GPO Fee Amount", "fee_amount"),
        ("ProductDivisionCode", "product_line"),
    ],
}

# TODO(real data): second supplier whose file is already rolled up by facility.
# Name, column headers and how it identifies a facility are placeholders until
# a sample arrives. It populates Total Revenue only; Volume stays blank.
ROLLED_UP_PLACEHOLDER = {
    "profile": dict(
        name="Rolled-up Supplier (placeholder)",
        description="PLACEHOLDER — file already rolled up by facility; populates Total Revenue only "
                    "(Volume blank). Replace the column map with the real headers when a sample arrives.",
        contract_id=None,                      # TODO(real data)
        payor_filter_name=None,                # TODO(real data): payor name as it appears on the ledger
        skip_rows_where_col_null="TODO Facility Name",
        identity_mode="member_match",
        volume_mode="blank",
    ),
    "column_map": [
        ("TODO Facility Name", "customer_name"),
        ("TODO Premier Entity Code", "entity_code_hint"),   # or account_id_hint for a Salesforce ID
        ("TODO City", "city"),
        ("TODO State", "state"),
        ("TODO Period", "process_date"),
        ("TODO Admin Fee", "fee_amount"),
    ],
}

PRESETS = [MEDLINE]
PLACEHOLDER_PRESETS = [ROLLED_UP_PLACEHOLDER]


def apply_preset(conn: object, preset: dict) -> int:
    """Create or update the supplier and add its column map entries. Returns supplier_profile_id."""
    profile = {**_DEFAULTS, **preset["profile"]}
    values = [profile.get(f) for f in _PROFILE_FIELDS]
    updates = ",\n            ".join(f"{f} = excluded.{f}" for f in _PROFILE_FIELDS if f != "name")
    execute_upsert(
        conn,
        f"""
        INSERT INTO supplier_profile ({", ".join(_PROFILE_FIELDS)})
        VALUES ({", ".join("?" for _ in _PROFILE_FIELDS)})
        ON CONFLICT(name) DO UPDATE SET
            {updates},
            updated_at = CURRENT_TIMESTAMP
        """,
        values,
    )
    conn.commit()
    supplier_id = conn.execute("SELECT id FROM supplier_profile WHERE name = ?", (profile["name"],)).fetchone()["id"]
    for source_col, canonical in preset["column_map"]:
        execute_insert_ignore(
            conn,
            "INSERT OR IGNORE INTO supplier_column_map (supplier_profile_id, source_column, canonical_field) "
            "VALUES (?, ?, ?)",
            (supplier_id, source_col, canonical),
        )
    conn.commit()
    return supplier_id


@dataclass
class SeedResult:
    supplier_ids: dict[str, int]
    key_loaded: Optional[int] = None      # entries loaded; None when no key file was given or it failed
    key_error: Optional[str] = None       # why the key file didn't load
    key_without_zip: bool = False         # the key has no ZIP column: the street alone is compared
    held_back: list[str] = field(default_factory=list)   # gpo_key presets left in their previous mode (no key)


def seed_presets(conn: object, presets: list[dict], gpo_key_path: Optional[str] = None,
                 key_supplier: str = "Medline") -> SeedResult:
    """
    Apply *presets* and optionally load *gpo_key_path* as *key_supplier*'s GPO key.

    A preset in gpo_key mode is applied in the supplier's previous mode (member_match for a new
    supplier) and switched to gpo_key only once it has a key, so a key that fails to load never
    leaves a supplier verifying every row against an empty key.
    """
    previous = {r["name"]: r["identity_mode"] for r in conn.execute(
        "SELECT name, identity_mode FROM supplier_profile").fetchall()}
    result = SeedResult(supplier_ids={})
    deferred: list[str] = []
    for preset in presets:
        name = preset["profile"]["name"]
        if {**_DEFAULTS, **preset["profile"]}["identity_mode"] == IDENTITY_GPO_KEY:
            deferred.append(name)
            hold = previous.get(name) or IDENTITY_MEMBER_MATCH
            preset = {**preset, "profile": {**preset["profile"], "identity_mode": hold}}
        result.supplier_ids[name] = apply_preset(conn, preset)

    if gpo_key_path:
        try:
            columns = without_missing_zip(gpo_key_path)
            result.key_without_zip = columns.zip is None
            result.key_loaded = load_gpo_key(conn, result.supplier_ids[key_supplier], gpo_key_path, columns).loaded
        except KeyFileError as e:
            conn.rollback()
            result.key_error = str(e)

    for name in deferred:
        sid = result.supplier_ids[name]
        entries = conn.execute(
            "SELECT COUNT(*) AS n FROM gpo_key_entry WHERE supplier_profile_id = ?", (sid,)).fetchone()["n"]
        if entries and not (result.key_error and name == key_supplier):
            conn.execute("UPDATE supplier_profile SET identity_mode = ? WHERE id = ?", (IDENTITY_GPO_KEY, sid))
        else:
            result.held_back.append(name)
    conn.commit()
    return result
