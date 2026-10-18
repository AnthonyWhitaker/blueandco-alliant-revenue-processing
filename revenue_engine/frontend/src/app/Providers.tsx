import { MantineProvider, type MantineThemeOverride } from '@mantine/core';
import { ModalsProvider } from '@mantine/modals';
import { Notifications } from '@mantine/notifications';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import type { ReactNode } from 'react';

import { theme as appTheme } from '@/theme';

interface ProvidersProps {
  queryClient: QueryClient;
  children: ReactNode;
  /** Tests pass a variant of the app theme (see src/test/render.tsx). */
  theme?: MantineThemeOverride;
}

/** Everything except the router, so tests can wrap a memory router in the same providers. */
export function Providers({ queryClient, children, theme = appTheme }: ProvidersProps) {
  return (
    <MantineProvider theme={theme} defaultColorScheme="auto">
      <QueryClientProvider client={queryClient}>
        <ModalsProvider>
          <Notifications position="top-right" />
          {children}
        </ModalsProvider>
      </QueryClientProvider>
    </MantineProvider>
  );
}
