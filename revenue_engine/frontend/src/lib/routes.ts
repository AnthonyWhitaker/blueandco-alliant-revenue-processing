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

/** Search params that hold selections (see src/stores/README.md). */
export const searchParamNames = {
  /** Upload (the job just submitted), Review and Export: the selected job. */
  job: 'job',
  /** Admin > Column Maps and Admin > Tier C Rules: the selected supplier. */
  supplier: 'supplier',
  /** Admin > Tier C Rules: extraction rule being edited, or "new". */
  extractionRule: 'extractionRule',
  /** Admin > Tier C Rules: allocation rule being edited, or "new". */
  allocationRule: 'allocationRule',
} as const;
