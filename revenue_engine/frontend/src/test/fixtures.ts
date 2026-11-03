/** Synthetic API payloads for component tests (test-only; never imported by app code). */
import type { Schemas } from '@/api/types';

export const meta: Schemas['Meta'] = {
  canonical_fields: [
    'customer_name',
    'jde_number',
    'process_date',
    'city',
    'state',
    'zip',
    'address',
    'sales_amount',
    'fee_amount',
  ],
  revenue_types: ['Admin Fee', 'Rebate'],
  keep_return_options: ['Keep', 'Return'],
  payment_id_sources: ['ledger', 'manual', 'column'],
  job_types: ['row_level', 'pivot_allocation'],
  extraction_methods: [
    'named_cell',
    'label_lookup',
    'label_range_sum',
    'column_sum',
    'formula',
    'manual_entry',
  ],
  label_match_modes: ['exact', 'contains', 'startswith', 'fuzzy'],
  range_axes: ['column', 'row'],
  allocation_types: ['percent', 'fixed_amount', 'remainder'],
  alias_scopes: ['supplier', 'global', 'job_only'],
  upload_extensions: ['.xlsx', '.xls', '.xlsm', '.csv'],
};

export function supplier(overrides: Partial<Schemas['Supplier']> = {}): Schemas['Supplier'] {
  return {
    id: 1,
    name: 'Acme Imaging',
    description: null,
    contract_id: 'C-100',
    revenue_type: 'Admin Fee',
    keep_return_default: 'Keep',
    payor_filter_name: 'Acme',
    payment_id_source: 'ledger',
    skip_rows_where_col_null: 'JDE Customer Number',
    sheet_name: 'Detail',
    header_row: 2,
    max_read_columns: 12,
    fee_rate: 0.03,
    job_type: 'row_level',
    requires_manual_confirmation: false,
    fuzzy_zip_state_threshold: 70,
    fuzzy_city_state_threshold: 72,
    fuzzy_state_threshold: 81,
    fuzzy_nationwide_threshold: 90,
    created_at: '2026-01-01T00:00:00',
    updated_at: '2026-01-01T00:00:00',
    ...overrides,
  };
}

export function member(overrides: Partial<Schemas['Member']> = {}): Schemas['Member'] {
  return {
    id: 10,
    name: 'Mercy General',
    entity_code: 'PEC-10',
    premier_entity_code: 'PEC-10',
    alliant_entity_code: null,
    account_id: '001A',
    billing_city: 'Omaha',
    billing_state: 'NE',
    billing_zip: '68102',
    is_active: true,
    ...overrides,
  };
}

export function job(overrides: Partial<Schemas['Job']> = {}): Schemas['Job'] {
  return {
    id: 42,
    supplier_profile_id: 1,
    supplier_name: 'Acme Imaging',
    job_type: 'row_level',
    original_file_name: 'Acme 2026-03-01 $1,234.50.xlsx',
    status: 'completed',
    payment_id: 'PAY-1',
    payment_id_source: 'ledger_auto',
    deposit_amount: 1234.5,
    date_received: '2026-03-01',
    earned_period_start: '2026-01-01',
    earned_period_end: '2026-01-31',
    contract_id: 'C-100',
    row_count: 10,
    matched_count: 9,
    unmatched_count: 1,
    total_volume: 50000,
    total_revenue: 1234.5,
    error_message: null,
    pending_review_count: 0,
    created_at: '2026-03-02T09:30:00',
    completed_at: '2026-03-02T09:31:00',
    ...overrides,
  };
}

export function extractionRule(
  overrides: Partial<Schemas['ExtractionRule']> = {},
): Schemas['ExtractionRule'] {
  return {
    id: 5,
    supplier_profile_id: 3,
    rule_name: 'Gross',
    sheet_name: 'Summary',
    extraction_method: 'named_cell',
    cell_address: 'E8',
    label_text: null,
    label_match_mode: null,
    label_match_threshold: null,
    value_row_offset: null,
    value_col_offset: null,
    range_axis: null,
    column_index: null,
    column_letter: null,
    header_row: null,
    stop_label_text: null,
    formula_expression: null,
    is_sales_amount: true,
    sort_order: 2,
    ...overrides,
  };
}

export function task(overrides: Partial<Schemas['Task']> = {}): Schemas['Task'] {
  return {
    id: 'task-1',
    kind: 'ledger_import',
    status: 'running',
    created_at: '2026-03-02T09:30:00',
    finished_at: null,
    error: null,
    member_import: null,
    ledger_import: null,
    ...overrides,
  };
}

export const problem = (status: number, detail: string, errors?: Schemas['FieldError'][]) => ({
  type: 'about:blank',
  title: 'Error',
  status,
  detail,
  ...(errors ? { errors } : {}),
});
