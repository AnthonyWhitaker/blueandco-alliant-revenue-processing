import { notifications } from '@mantine/notifications';
import { MutationCache, QueryClient } from '@tanstack/react-query';

import { ApiError } from '@/api/client';

declare module '@tanstack/react-query' {
  interface Register {
    defaultError: Error;
    mutationMeta: {
      /** Set when the calling component shows the error itself (e.g. inline form errors). */
      handlesErrors?: boolean;
    };
  }
}

/** Client errors (4xx) won't succeed on retry; network blips and 5xx might. */
function shouldRetry(failureCount: number, error: Error): boolean {
  if (error instanceof ApiError && error.status >= 400 && error.status < 500) return false;
  return failureCount < 2;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: shouldRetry,
        refetchOnWindowFocus: true,
      },
      mutations: { retry: false },
    },
    // Queries render their own error states in place; mutations get a toast
    // unless the caller opts out because it renders the error itself.
    mutationCache: new MutationCache({
      onError: (error, _variables, _context, mutation) => {
        if (mutation.meta?.handlesErrors) return;
        notifications.show({ color: 'red', title: 'Something went wrong', message: error.message });
      },
    }),
  });
}
