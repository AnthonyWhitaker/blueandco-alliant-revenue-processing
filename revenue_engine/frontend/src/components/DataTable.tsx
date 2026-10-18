import { DataTable as MantineDataTable, type DataTableColumn } from 'mantine-datatable';

interface DataTableProps<T> {
  records: T[] | undefined;
  columns: DataTableColumn<T>[];
  /** Accessible name for the table. */
  label: string;
  fetching?: boolean;
  noRecordsText?: string;
  /** Defaults to the record's `id`. */
  idAccessor?: keyof T | ((record: T) => React.Key);
  /** Scroll inside the table past this height (long previews). */
  maxHeight?: number;
  /** Keep the last column (row actions) visible while the table scrolls sideways. */
  pinLastColumn?: boolean;
}

/**
 * The st.dataframe equivalent, with the app's defaults: bordered, compact,
 * scrolls horizontally on narrow screens, and a loader over the rows while
 * fetching (so the layout doesn't jump).
 */
export function DataTable<T>({
  records,
  columns,
  label,
  fetching = false,
  noRecordsText = 'No records',
  idAccessor,
  maxHeight,
  pinLastColumn = false,
}: DataTableProps<T>) {
  const empty = !records || records.length === 0;
  return (
    <MantineDataTable<T>
      withTableBorder
      borderRadius="md"
      striped
      highlightOnHover
      aria-label={label}
      minHeight={empty ? 150 : 0}
      {...(maxHeight === undefined ? {} : { maxHeight })}
      pinLastColumn={pinLastColumn}
      fetching={fetching}
      records={records ?? []}
      columns={columns}
      noRecordsText={noRecordsText}
      idAccessor={idAccessor ?? 'id'}
      // Cells stay on one line and the table scrolls sideways; a column opts
      // out with `noWrap: false` (long messages).
      defaultColumnProps={{ noWrap: true }}
    />
  );
}
