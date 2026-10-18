import { queryOptions } from '@tanstack/react-query';

import { api, unwrap } from '@/api/client';
import type { QueryParams, Schemas } from '@/api/types';

import { queryKeys } from './keys';

export type Supplier = Schemas['Supplier'];

export const suppliersQuery = (filters: QueryParams<'/api/suppliers'> = {}) =>
  queryOptions({
    queryKey: queryKeys.suppliers.list(filters),
    queryFn: () => unwrap(api.GET('/api/suppliers', { params: { query: filters } })),
  });

const path = (supplierId: number) => ({ params: { path: { supplier_id: supplierId } } });

export const columnMappingsQuery = (supplierId: number) =>
  queryOptions({
    queryKey: queryKeys.suppliers.columnMappings(supplierId),
    queryFn: () =>
      unwrap(api.GET('/api/suppliers/{supplier_id}/column-mappings', path(supplierId))),
  });

export const extractionRulesQuery = (supplierId: number) =>
  queryOptions({
    queryKey: queryKeys.suppliers.extractionRules(supplierId),
    queryFn: () =>
      unwrap(api.GET('/api/suppliers/{supplier_id}/extraction-rules', path(supplierId))),
  });

export const allocationRulesQuery = (supplierId: number) =>
  queryOptions({
    queryKey: queryKeys.suppliers.allocationRules(supplierId),
    queryFn: () =>
      unwrap(api.GET('/api/suppliers/{supplier_id}/allocation-rules', path(supplierId))),
  });
