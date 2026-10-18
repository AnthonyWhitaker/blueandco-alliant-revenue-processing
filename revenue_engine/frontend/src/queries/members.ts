import { keepPreviousData, queryOptions } from '@tanstack/react-query';

import { api, unwrap } from '@/api/client';
import type { QueryParams, Schemas } from '@/api/types';

import { queryKeys } from './keys';

export type Member = Schemas['Member'];

export const membersQuery = (params: QueryParams<'/api/members'>) =>
  queryOptions({
    queryKey: queryKeys.members.search(params),
    queryFn: () => unwrap(api.GET('/api/members', { params: { query: params } })),
    // Keep the previous results on screen while the next search loads.
    placeholderData: keepPreviousData,
  });

export const ledgerEntriesQuery = (params: QueryParams<'/api/ledger-entries'>) =>
  queryOptions({
    queryKey: queryKeys.ledgerEntries.list(params),
    queryFn: () => unwrap(api.GET('/api/ledger-entries', { params: { query: params } })),
  });

export const aliasesQuery = (params: QueryParams<'/api/aliases'>) =>
  queryOptions({
    queryKey: queryKeys.aliases.list(params),
    queryFn: () => unwrap(api.GET('/api/aliases', { params: { query: params } })),
  });

export const taskQuery = (taskId: string) =>
  queryOptions({
    queryKey: queryKeys.task(taskId),
    queryFn: () =>
      unwrap(api.GET('/api/tasks/{task_id}', { params: { path: { task_id: taskId } } })),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status === 'succeeded' || status === 'failed' ? false : 1000;
    },
    refetchIntervalInBackground: true,
  });
