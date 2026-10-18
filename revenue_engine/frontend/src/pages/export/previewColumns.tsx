/** Column definitions for the four workbook tabs, titled exactly as in the exported XLSX. */
import type { DataTableColumn } from 'mantine-datatable';

import type { Schemas } from '@/api/types';
import { formatCurrency, formatDate, formatDecimal, formatText } from '@/lib/format';

type Preview = Schemas['JobPreview'];
type Keyed<T> = T & { rowKey: number };
export type UploadRow = Keyed<Preview['upload'][number]>;
export type ValidationRow = Keyed<Preview['validation'][number]>;
export type UnmatchedRow = Preview['unmatched'][number];
export type JdeMappingRow = Keyed<Preview['jde_mapping'][number]>;

const money = <T,>(get: (r: T) => number | null) => ({
  textAlign: 'right' as const,
  cellsClassName: 'tabular',
  render: (r: T) => formatCurrency(get(r)),
});
const text = <T,>(get: (r: T) => string | null) => ({ render: (r: T) => formatText(get(r)) });
const date = <T,>(get: (r: T) => string | null) => ({ render: (r: T) => formatDate(get(r)) });

export const uploadColumns: DataTableColumn<UploadRow>[] = [
  { accessor: 'pec', title: 'Pec', cellsClassName: 'mono' },
  { accessor: 'facility_id', title: 'Facility ID', ...text((r: UploadRow) => r.facility_id) },
  { accessor: 'contract_id', title: 'Contract ID', ...text((r: UploadRow) => r.contract_id) },
  { accessor: 'payment_id', title: 'Payment ID', ...text((r: UploadRow) => r.payment_id) },
  {
    accessor: 'volume',
    title: 'Volume',
    textAlign: 'right',
    cellsClassName: 'tabular',
    render: (r) => formatDecimal(r.volume),
  },
  {
    accessor: 'total_revenue',
    title: 'Total Revenue',
    ...money((r: UploadRow) => r.total_revenue),
  },
  { accessor: 'revenue_type', title: 'Revenue Type', ...text((r: UploadRow) => r.revenue_type) },
  { accessor: 'keep_return', title: 'Keep/Return?', ...text((r: UploadRow) => r.keep_return) },
  { accessor: 'earned_period', title: 'Earned Period', ...date((r: UploadRow) => r.earned_period) },
  {
    accessor: 'pmt_for_time_frame',
    title: 'Pmt For (Time Frame)',
    ...text((r: UploadRow) => r.pmt_for_time_frame),
  },
  { accessor: 'date_received', title: 'Date Received', ...date((r: UploadRow) => r.date_received) },
  { accessor: 'comments', title: 'Comments', ...text((r: UploadRow) => r.comments) },
  { accessor: 'date_refunded', title: 'Date Refunded', ...date((r: UploadRow) => r.date_refunded) },
  {
    accessor: 'rebate_retained',
    title: 'Rebate Retained',
    ...text((r: UploadRow) => r.rebate_retained),
  },
  {
    accessor: 'amount_retained',
    title: 'Amount Retained',
    ...money((r: UploadRow) => r.amount_retained),
  },
  {
    accessor: 'retained_reason',
    title: 'Retained Reason',
    ...text((r: UploadRow) => r.retained_reason),
  },
];

export const validationColumns: DataTableColumn<ValidationRow>[] = [
  {
    accessor: 'jde_number',
    title: 'JDE #',
    cellsClassName: 'mono',
    ...text((r: ValidationRow) => r.jde_number),
  },
  {
    accessor: 'supplier_customer_name',
    title: 'Supplier Customer Name',
    ...text((r: ValidationRow) => r.supplier_customer_name),
  },
  {
    accessor: 'entity_code',
    title: 'PEC / Entity Code',
    cellsClassName: 'mono',
    ...text((r: ValidationRow) => r.entity_code),
  },
  {
    accessor: 'salesforce_account_id',
    title: 'Salesforce Account ID',
    ...text((r: ValidationRow) => r.salesforce_account_id),
  },
  {
    accessor: 'account_list_name',
    title: 'Account List Name',
    ...text((r: ValidationRow) => r.account_list_name),
  },
  { accessor: 'city', title: 'City', ...text((r: ValidationRow) => r.city) },
  { accessor: 'state', title: 'State', ...text((r: ValidationRow) => r.state) },
  {
    accessor: 'match_method',
    title: 'Match Method',
    ...text((r: ValidationRow) => r.match_method),
  },
  { accessor: 'score', title: 'Score', textAlign: 'right', render: (r) => r.score ?? '—' },
];

export const unmatchedColumns: DataTableColumn<UnmatchedRow>[] = [
  { accessor: 'row_number', title: 'Row #', textAlign: 'right' },
  {
    accessor: 'jde_number',
    title: 'JDE #',
    cellsClassName: 'mono',
    ...text((r: UnmatchedRow) => r.jde_number),
  },
  {
    accessor: 'customer_name',
    title: 'Customer Name',
    ...text((r: UnmatchedRow) => r.customer_name),
  },
  { accessor: 'address', title: 'Address', ...text((r: UnmatchedRow) => r.address) },
  { accessor: 'city', title: 'City', ...text((r: UnmatchedRow) => r.city) },
  { accessor: 'state', title: 'State', ...text((r: UnmatchedRow) => r.state) },
  { accessor: 'zip', title: 'ZIP', ...text((r: UnmatchedRow) => r.zip) },
  { accessor: 'process_date', title: 'Process Date', ...date((r: UnmatchedRow) => r.process_date) },
  {
    accessor: 'sales_amount',
    title: 'Sales Amount',
    ...money((r: UnmatchedRow) => r.sales_amount),
  },
  { accessor: 'fee_amount', title: 'Fee Amount', ...money((r: UnmatchedRow) => r.fee_amount) },
  { accessor: 'product_line', title: 'Product Line', ...text((r: UnmatchedRow) => r.product_line) },
];

export const jdeMappingColumns: DataTableColumn<JdeMappingRow>[] = [
  {
    accessor: 'jde_number',
    title: 'JDE #',
    cellsClassName: 'mono',
    ...text((r: JdeMappingRow) => r.jde_number),
  },
  {
    accessor: 'supplier_name',
    title: 'Supplier Name',
    ...text((r: JdeMappingRow) => r.supplier_name),
  },
  {
    accessor: 'entity_code',
    title: 'PEC / Entity Code',
    cellsClassName: 'mono',
    ...text((r: JdeMappingRow) => r.entity_code),
  },
  {
    accessor: 'account_list_name',
    title: 'Account List Name',
    ...text((r: JdeMappingRow) => r.account_list_name),
  },
];
