import '@fontsource-variable/inter';
import '@fontsource-variable/jetbrains-mono';
import '@mantine/core/styles.css';
import '@mantine/notifications/styles.css';
import './styles/global.css';

import { lazy, StrictMode, Suspense } from 'react';
import { createRoot } from 'react-dom/client';
import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { Providers } from '@/app/Providers';
import { routeTree } from '@/app/routes';
import { createQueryClient } from '@/lib/queryClient';

// Dev-only and lazy, so it never reaches the production bundle.
const ReactQueryDevtools = import.meta.env.DEV
  ? lazy(() =>
      import('@tanstack/react-query-devtools').then((m) => ({ default: m.ReactQueryDevtools })),
    )
  : null;

const queryClient = createQueryClient();
const router = createBrowserRouter(routeTree);

const rootElement = document.getElementById('root');
if (!rootElement) throw new Error('index.html is missing <div id="root">');

createRoot(rootElement).render(
  <StrictMode>
    <Providers queryClient={queryClient}>
      <RouterProvider router={router} />
      {ReactQueryDevtools && (
        <Suspense>
          <ReactQueryDevtools buttonPosition="bottom-left" />
        </Suspense>
      )}
    </Providers>
  </StrictMode>,
);
