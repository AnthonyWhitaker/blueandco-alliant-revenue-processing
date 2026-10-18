import { queryOptions } from '@tanstack/react-query';

import { api, unwrap } from '@/api/client';

import { queryKeys } from './keys';

/** Option lists for selects. They only change on deploy. */
export const metaQuery = queryOptions({
  queryKey: queryKeys.meta,
  queryFn: () => unwrap(api.GET('/api/meta')),
  staleTime: Infinity,
});
