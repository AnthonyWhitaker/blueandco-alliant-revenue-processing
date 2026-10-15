import { render } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';

import { Providers } from '@/app/Providers';
import { routeTree } from '@/app/routes';
import { createQueryClient } from '@/lib/queryClient';

/** Render the real route tree at `path` inside the real providers. */
export function renderRoute(path: string) {
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routeTree, { initialEntries: [path] });
  return {
    router,
    ...render(
      <Providers queryClient={queryClient}>
        <RouterProvider router={router} />
      </Providers>,
    ),
  };
}

/** Stub fetch with a handler keyed by "METHOD /path". Unhandled requests fail the test. */
export function stubFetch(handlers: Record<string, () => Response>) {
  const fetchMock = vi.fn((input: Request) => {
    const url = new URL(input.url);
    const handler = handlers[`${input.method} ${url.pathname}`];
    if (!handler) throw new Error(`Unexpected request: ${input.method} ${url.pathname}`);
    return Promise.resolve(handler());
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

export const json = (body: unknown, status = 200, contentType = 'application/json') =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': contentType } });
