"""GPO key for suppliers whose GPO IDs are verified against a key file (identity_mode = 'gpo_key')."""

from engine.gpo_key import KeyColumns, KeyFileError, load_gpo_key
from models.suppliers import GpoKeyImportResult, GpoKeySummary

from .errors import InvalidInputError
from .suppliers import get_supplier


def get_summary(conn, supplier_id: int) -> GpoKeySummary:
    get_supplier(conn, supplier_id)
    r = conn.execute(
        """
        SELECT COUNT(*) AS entry_count, COUNT(DISTINCT account_number_norm) AS account_count,
               MAX(source_file_name) AS source_file_name, MAX(loaded_at) AS loaded_at
        FROM gpo_key_entry WHERE supplier_profile_id = ?
        """,
        (supplier_id,),
    ).fetchone()
    return GpoKeySummary.model_validate(dict(zip(r.keys(), tuple(r))))


def import_key(conn, supplier_id: int, path: str, file_name: str, columns: KeyColumns) -> GpoKeyImportResult:
    """Replace the supplier's GPO key with the contents of a CSV/XLSX key file."""
    get_supplier(conn, supplier_id)
    try:
        result = load_gpo_key(conn, supplier_id, path, columns, source_file_name=file_name)
    except KeyFileError as e:
        conn.rollback()
        raise InvalidInputError(str(e)) from e
    return GpoKeyImportResult(
        loaded=result.loaded,
        skipped_blank_account=result.skipped_blank_account,
        accounts_with_several_entries=result.accounts_with_several_entries,
        key=get_summary(conn, supplier_id),
    )
