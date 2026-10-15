import pytest
from pydantic import ValidationError

from core import tier_c
from core.errors import ConflictError
from models.tier_c import AllocationRuleDraft, AllocationRuleInput, ExtractionRuleInput

from .conftest import make_members, make_supplier


@pytest.fixture
def pivot(conn):
    return {"supplier_id": make_supplier(conn, name="Komet", job_type="pivot_allocation"),
            "members": make_members(conn)}


def _named_cell(name="Total", cell="E8", **kw):
    return ExtractionRuleInput(extraction_method="named_cell", rule_name=name, cell_address=cell, **kw)


def test_required_fields_per_method():
    with pytest.raises(ValidationError, match="Cell Address is required"):
        ExtractionRuleInput(extraction_method="named_cell", rule_name="x")
    with pytest.raises(ValidationError, match="Label Text is required"):
        ExtractionRuleInput(extraction_method="label_range_sum", rule_name="x")
    with pytest.raises(ValidationError, match="Column Index or Column Letter"):
        ExtractionRuleInput(extraction_method="column_sum", rule_name="x")
    with pytest.raises(ValidationError, match="Formula Expression is required"):
        ExtractionRuleInput(extraction_method="formula", rule_name="x")
    ExtractionRuleInput(extraction_method="column_sum", rule_name="x", column_letter="F")
    ExtractionRuleInput(extraction_method="manual_entry", rule_name="x")


def test_irrelevant_fields_are_reset_to_defaults(conn, pivot):
    saved = tier_c.save_extraction_rule(conn, pivot["supplier_id"], ExtractionRuleInput(
        extraction_method="named_cell", rule_name="Total", cell_address="E8",
        label_text="ignored", formula_expression="ignored", value_row_offset=7,
    )).rule
    assert (saved.label_text, saved.formula_expression, saved.value_row_offset) == (None, None, 1)


def test_edit_keeps_fields_it_is_given(conn, pivot):
    """The legacy editor wiped untouched fields on edit (FINDINGS F13); the API sends the whole rule."""
    sid = pivot["supplier_id"]
    rule = tier_c.save_extraction_rule(conn, sid, _named_cell("Beta", "B2", sheet_name="Summary",
                                                              is_sales_amount=True, sort_order=5)).rule
    edited = tier_c.save_extraction_rule(conn, sid, _named_cell("Beta", "B9", sheet_name="Summary",
                                                                is_sales_amount=True, sort_order=5),
                                         rule_id=rule.id).rule
    assert (edited.cell_address, edited.sheet_name, edited.is_sales_amount, edited.sort_order) == \
        ("B9", "Summary", True, 5)


def test_duplicate_rule_name_conflicts(conn, pivot):
    tier_c.save_extraction_rule(conn, pivot["supplier_id"], _named_cell("Total"))
    with pytest.raises(ConflictError):
        tier_c.save_extraction_rule(conn, pivot["supplier_id"], _named_cell("Total", "F1"))


def test_rules_only_for_pivot_suppliers(conn):
    row_level = make_supplier(conn, name="RowCo")
    with pytest.raises(ConflictError):
        tier_c.save_extraction_rule(conn, row_level, _named_cell())


def test_rename_cascades_and_delete_is_guarded(conn, pivot):
    sid, members = pivot["supplier_id"], pivot["members"]
    rule = tier_c.save_extraction_rule(conn, sid, _named_cell("Total")).rule
    tier_c.save_allocation_rule(conn, sid, AllocationRuleInput(
        extraction_rule_name="Total", member_id=members["general"], allocation_type="remainder"))

    result = tier_c.save_extraction_rule(conn, sid, _named_cell("Grand Total"), rule_id=rule.id)
    assert result.renamed_allocation_refs == 1
    assert [a.extraction_rule_name for a in tier_c.list_allocation_rules(conn, sid)] == ["Grand Total"]

    with pytest.raises(ConflictError, match="Delete those first"):
        tier_c.delete_extraction_rule(conn, rule.id)
    tier_c.delete_allocation_rule(conn, tier_c.list_allocation_rules(conn, sid)[0].id)
    tier_c.delete_extraction_rule(conn, rule.id)
    assert tier_c.list_extraction_rules(conn, sid) == []


def test_formula_substring_notice(conn, pivot):
    sid = pivot["supplier_id"]
    tier_c.save_extraction_rule(conn, sid, _named_cell("Gross"))
    tier_c.save_extraction_rule(conn, sid, _named_cell("Gross Fees", "F2"))
    result = tier_c.save_extraction_rule(conn, sid, ExtractionRuleInput(
        extraction_method="formula", rule_name="Net", formula_expression="Gross - Gross Fees"))
    assert any("'Gross' is a substring of 'Gross Fees'" in n.message for n in result.notices)


def test_allocation_value_required_unless_remainder():
    with pytest.raises(ValidationError, match="Allocation Value is required"):
        AllocationRuleInput(extraction_rule_name="T", member_id=1, allocation_type="percent")
    assert AllocationRuleInput(extraction_rule_name="T", member_id=1, allocation_type="remainder",
                               allocation_value=5).allocation_value is None


def test_allocation_conflicts(conn, pivot):
    sid, members = pivot["supplier_id"], pivot["members"]
    with pytest.raises(ConflictError, match="No extraction rule"):
        tier_c.save_allocation_rule(conn, sid, AllocationRuleInput(
            extraction_rule_name="Missing", member_id=members["general"], allocation_type="remainder"))
    tier_c.save_extraction_rule(conn, sid, _named_cell("Total"))
    data = AllocationRuleInput(extraction_rule_name="Total", member_id=members["general"],
                               allocation_type="percent", allocation_value=40)
    tier_c.save_allocation_rule(conn, sid, data)
    with pytest.raises(ConflictError, match="already has an allocation row"):
        tier_c.save_allocation_rule(conn, sid, data)


def test_allocation_notices(conn, pivot):
    sid, members = pivot["supplier_id"], pivot["members"]
    tier_c.save_extraction_rule(conn, sid, _named_cell("Total"))

    first = AllocationRuleDraft(extraction_rule_name="Total", allocation_type="percent", allocation_value=40)
    assert [n.level for n in tier_c.validate_allocation_draft(conn, sid, first)] == ["info"]

    tier_c.save_allocation_rule(conn, sid, AllocationRuleInput(
        extraction_rule_name="Total", member_id=members["general"], allocation_type="percent", allocation_value=70))
    tier_c.save_allocation_rule(conn, sid, AllocationRuleInput(
        extraction_rule_name="Total", member_id=members["mercy"], allocation_type="remainder"))

    over = AllocationRuleDraft(extraction_rule_name="Total", allocation_type="percent", allocation_value=40)
    assert "sum to 110.0%" in tier_c.validate_allocation_draft(conn, sid, over)[0].message

    second_remainder = AllocationRuleDraft(extraction_rule_name="Total", allocation_type="remainder")
    assert "already has a remainder row" in tier_c.validate_allocation_draft(conn, sid, second_remainder)[0].message

    # Editing the existing remainder row itself is not a duplicate.
    rem_id = next(r.id for r in tier_c.list_allocation_rules(conn, sid) if r.allocation_type == "remainder")
    editing = AllocationRuleDraft(extraction_rule_name="Total", allocation_type="remainder", rule_id=rem_id)
    assert tier_c.validate_allocation_draft(conn, sid, editing) == []
