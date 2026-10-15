import { api, unwrap } from '@/api/client';

export const dashboardQuery = {
  queryKey: ['dashboard'] as const,
  queryFn: () => unwrap(api.GET('/api/dashboard')),
};
