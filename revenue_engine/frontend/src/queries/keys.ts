import type { QueryParams } from '@/api/types';

/**
 * Every Query key, in one place so mutations invalidate by prefix
 * (invalidating `jobs.all` refreshes every job list, detail and sub-resource).
 * The invalidation map is in src/stores/README.md.
 */
export const queryKeys = {
  meta: ['meta'] as const,
  dashboard: ['dashboard'] as const,
  fileNameInference: (fileName: string) => ['file-name-inference', fileName] as const,

  jobs: {
    all: ['jobs'] as const,
    list: (filters: QueryParams<'/api/jobs'>) => ['jobs', 'list', filters] as const,
    detail: (jobId: number) => ['jobs', jobId] as const,
    exceptions: (jobId: number) => ['jobs', jobId, 'exceptions'] as const,
    preview: (jobId: number) => ['jobs', jobId, 'preview'] as const,
    reviewItems: (jobId: number) => ['jobs', jobId, 'review-items'] as const,
  },

  suppliers: {
    all: ['suppliers'] as const,
    list: (filters: QueryParams<'/api/suppliers'>) => ['suppliers', 'list', filters] as const,
    detail: (supplierId: number) => ['suppliers', supplierId] as const,
    columnMappings: (supplierId: number) => ['suppliers', supplierId, 'column-mappings'] as const,
    extractionRules: (supplierId: number) => ['suppliers', supplierId, 'extraction-rules'] as const,
    allocationRules: (supplierId: number) => ['suppliers', supplierId, 'allocation-rules'] as const,
  },

  members: {
    all: ['members'] as const,
    search: (params: QueryParams<'/api/members'>) => ['members', 'search', params] as const,
  },

  ledgerEntries: {
    all: ['ledger-entries'] as const,
    list: (params: QueryParams<'/api/ledger-entries'>) => ['ledger-entries', params] as const,
  },

  aliases: {
    all: ['aliases'] as const,
    list: (params: QueryParams<'/api/aliases'>) => ['aliases', params] as const,
  },

  task: (taskId: string) => ['tasks', taskId] as const,
};
