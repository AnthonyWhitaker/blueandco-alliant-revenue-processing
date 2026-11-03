/** Display formatting shared by every page (en-US, as the legacy app). */

const integer = new Intl.NumberFormat('en-US');
const currency = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' });
const decimal = new Intl.NumberFormat('en-US', { maximumFractionDigits: 4 });

export const EMPTY = '—';

export const formatInteger = (value: number | null | undefined) =>
  value === null || value === undefined ? EMPTY : integer.format(value);

/** "$1,234.50", or an em dash when there is no amount. */
export const formatCurrency = (value: number | null | undefined) =>
  value === null || value === undefined ? EMPTY : currency.format(value);

export const formatDecimal = (value: number | null | undefined) =>
  value === null || value === undefined ? EMPTY : decimal.format(value);

export const formatText = (value: string | null | undefined) =>
  value === null || value === undefined || value === '' ? EMPTY : value;

/** API dates are ISO `YYYY-MM-DD`; show them as-is (unambiguous, sortable, as Streamlit did). */
export const formatDate = (value: string | null | undefined) =>
  value ? value.slice(0, 10) : EMPTY;

/** API datetimes are naive server-local timestamps; show `YYYY-MM-DD HH:MM`. */
export const formatDateTime = (value: string | null | undefined) =>
  value ? value.replace('T', ' ').slice(0, 16) : EMPTY;

export const pluralize = (count: number, singular: string, plural = `${singular}s`) =>
  `${integer.format(count)} ${count === 1 ? singular : plural}`;
