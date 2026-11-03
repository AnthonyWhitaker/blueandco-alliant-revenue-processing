import { api, unwrap } from '@/api/client';

import { queryKeys } from './keys';

export const dashboardQuery = {
  queryKey: queryKeys.dashboard,
  queryFn: () => unwrap(api.GET('/api/dashboard')),
};
