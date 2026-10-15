/** Route paths, one per legacy Streamlit page (Admin tabs become sub-routes). */
export const routes = {
  dashboard: '/',
  upload: '/upload',
  review: '/review',
  export: '/export',
  admin: {
    root: '/admin',
    members: '/admin/members',
    ledger: '/admin/ledger',
    suppliers: '/admin/suppliers',
    columnMaps: '/admin/column-maps',
    tierC: '/admin/tier-c',
    aliases: '/admin/aliases',
  },
} as const;
