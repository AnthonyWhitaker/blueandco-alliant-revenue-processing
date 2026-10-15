import {
  Badge,
  Button,
  Card,
  createTheme,
  NavLink,
  Paper,
  Table,
  Tooltip,
  type MantineColorsTuple,
} from '@mantine/core';

/**
 * Deep teal: reads as "finance/ledger" without being bank-blue, and keeps
 * enough contrast against the red/orange used for review and error states.
 */
const ledger: MantineColorsTuple = [
  '#e7f6f3',
  '#d0ebe6',
  '#a1d6cc',
  '#6dbfb0',
  '#45ac99',
  '#2b9f8b',
  '#1a9883',
  '#078470',
  '#007563',
  '#006454',
];

/** Cool slate for chrome and neutral surfaces (replaces Mantine's default gray). */
const slate: MantineColorsTuple = [
  '#f4f6f8',
  '#e7eaee',
  '#cdd3da',
  '#b0b9c4',
  '#97a3b1',
  '#8795a5',
  '#7e8ea0',
  '#6b7b8c',
  '#5e6d7e',
  '#4d5d6f',
];

export const theme = createTheme({
  primaryColor: 'ledger',
  primaryShade: { light: 7, dark: 5 },
  colors: { ledger, slate },
  autoContrast: true,
  cursorType: 'pointer',

  fontFamily: "'Inter Variable', system-ui, -apple-system, 'Segoe UI', sans-serif",
  fontFamilyMonospace:
    "'JetBrains Mono Variable', ui-monospace, 'SFMono-Regular', Menlo, monospace",
  headings: {
    fontFamily: "'Inter Variable', system-ui, sans-serif",
    fontWeight: '650',
    sizes: {
      h1: { fontSize: '1.625rem', lineHeight: '1.25' },
      h2: { fontSize: '1.25rem', lineHeight: '1.3' },
      h3: { fontSize: '1.0625rem', lineHeight: '1.35' },
      h4: { fontSize: '0.9375rem', lineHeight: '1.4' },
    },
  },

  // A dense, data-heavy tool: body text at 14px, tables at 13px.
  fontSizes: {
    xs: '0.75rem',
    sm: '0.8125rem',
    md: '0.875rem',
    lg: '1rem',
    xl: '1.125rem',
  },
  lineHeights: { xs: '1.4', sm: '1.45', md: '1.5', lg: '1.55', xl: '1.6' },
  spacing: {
    xs: '0.5rem',
    sm: '0.75rem',
    md: '1rem',
    lg: '1.5rem',
    xl: '2rem',
  },
  defaultRadius: 'sm',
  radius: { xs: '2px', sm: '4px', md: '6px', lg: '10px', xl: '16px' },

  components: {
    Button: Button.extend({ defaultProps: { size: 'sm' } }),
    Card: Card.extend({ defaultProps: { withBorder: true, padding: 'lg', radius: 'md' } }),
    Paper: Paper.extend({ defaultProps: { withBorder: true, radius: 'md' } }),
    Table: Table.extend({
      defaultProps: {
        verticalSpacing: 'xs',
        horizontalSpacing: 'sm',
        fz: 'sm',
        highlightOnHover: true,
      },
    }),
    Badge: Badge.extend({ defaultProps: { variant: 'light', radius: 'sm' } }),
    Tooltip: Tooltip.extend({ defaultProps: { withArrow: true, openDelay: 300 } }),
    NavLink: NavLink.extend({ defaultProps: { variant: 'light' } }),
  },
});
