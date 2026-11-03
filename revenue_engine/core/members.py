"""Member master (member), payment ledger (payment_ledger), and their spreadsheet imports."""

from typing import Optional

from engine.db import execute_select
from engine.member_loader import create_member as engine_create_member
from engine.member_loader import load_account_list
from engine.payment_resolver import load_ledger_from_excel
from models.common import Page
from models.members import LedgerEntry, LedgerImportResult, Member, MemberImportResult, MemberInput

from .errors import ConflictError, InvalidInputError, NotFoundError
from .rows import row_dict, rows_dicts

_MEMBER_COLS = ("id, name, entity_code, premier_entity_code, alliant_entity_code, account_id, "
                "billing_city, billing_state, billing_zip, is_active")


def get_member(conn, member_id: int) -> Member:
    row = row_dict(conn.execute(f"SELECT {_MEMBER_COLS} FROM member WHERE id = ?", (member_id,)).fetchone())
    if not row:
        raise NotFoundError(f"Member {member_id} not found.")
    return Member.model_validate(row)


def search_members(conn, term: Optional[str] = None, active_only: bool = True, limit: int = 20) -> Page[Member]:
    """Case-insensitive substring search on name or entity code. No term = everything (paged)."""
    where: list[str] = []
    params: list = []
    if active_only:
        where.append("is_active = 1")
    if term:
        where.append("(lower(name) LIKE lower(?) OR lower(entity_code) LIKE lower(?))")
        params += [f"%{term}%", f"%{term}%"]
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT COUNT(*) FROM member{clause}", tuple(params)).fetchone()[0]
    rows = execute_select(
        conn, f"SELECT {_MEMBER_COLS} FROM member{clause} ORDER BY name LIMIT {int(limit)}", params or None
    ).fetchall()
    return Page[Member](items=[Member.model_validate(r) for r in rows_dicts(rows)], total=total)


def create_member(conn, data: MemberInput) -> Member:
    if data.account_id and conn.execute(
        "SELECT 1 FROM member WHERE account_id = ?", (data.account_id,)
    ).fetchone():
        raise ConflictError(f"A member with Salesforce Account ID '{data.account_id}' already exists.")
    member_id = engine_create_member(
        conn=conn,
        name=data.name,
        account_id=data.account_id or None,
        premier_entity_code=data.premier_entity_code or None,
        city=data.city or None,
        state=data.state or None,
        zip_code=data.zip or None,
    )
    return get_member(conn, member_id)


def import_account_list(conn, file_path: str, replace_all: bool) -> MemberImportResult:
    try:
        inserted, skipped, deactivated = load_account_list(conn, file_path, replace_all=replace_all)
    except (ValueError, KeyError) as e:
        raise InvalidInputError(f"Could not read the Account List: {e}") from e
    return MemberImportResult(inserted=inserted, skipped=skipped, deactivated=deactivated)


def list_ledger_entries(conn, limit: int = 30) -> Page[LedgerEntry]:
    total = conn.execute("SELECT COUNT(*) FROM payment_ledger").fetchone()[0]
    rows = execute_select(
        conn,
        "SELECT id, payor, payment_received_date, deposit_amount, sf_payment_id, contract_id "
        f"FROM payment_ledger ORDER BY payment_received_date DESC, id DESC LIMIT {int(limit)}",
    ).fetchall()
    return Page[LedgerEntry](items=[LedgerEntry.model_validate(r) for r in rows_dicts(rows)], total=total)


def import_ledger(conn, file_path: str, sheet_name: str) -> LedgerImportResult:
    try:
        inserted = load_ledger_from_excel(conn, file_path, sheet_name=sheet_name)
    except (ValueError, KeyError) as e:
        raise InvalidInputError(f"Could not read the ledger: {e}") from e
    return LedgerImportResult(inserted=inserted)
