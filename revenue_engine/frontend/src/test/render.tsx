import { mergeThemeOverrides } from '@mantine/core';
import { render } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';

import { Providers } from '@/app/Providers';
import { routeTree } from '@/app/routes';
import { createQueryClient } from '@/lib/queryClient';
import { theme } from '@/theme';

/**
 * jsdom has no layout, so Floating UI thinks every popover's target is off-screen
 * and Mantine hides the dropdown (`hideDetached`). Keep dropdowns visible in tests.
 */
const testTheme = mergeThemeOverrides(theme, {
  components: { Popover: { defaultProps: { hideDetached: false } } },
});

/** Render the real route tree at `path` inside the real providers. */
export function renderRoute(path: string) {
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routeTree, { initialEntries: [path] });
  return {
    router,
    queryClient,
    ...render(
      <Providers queryClient={queryClient} theme={testTheme}>
        <RouterProvider router={router} />
      </Providers>,
    ),
  };
}

type Handler = (request: Request) => Response | Promise<Response>;

/**
 * Stub fetch with a handler keyed by "METHOD /path" (query string ignored).
 * Handlers get the Request, to assert on query params or bodies.
 * Unhandled requests fail the test.
 */
export function stubFetch(handlers: Record<string, Handler>) {
  const fetchMock = vi.fn((input: Request) => {
    const url = new URL(input.url);
    const handler = handlers[`${input.method} ${url.pathname}`];
    if (!handler) throw new Error(`Unexpected request: ${input.method} ${url.pathname}`);
    return Promise.resolve(handler(input));
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

export const json = (body: unknown, status = 200, contentType = 'application/json') =>
  new Response(JSON.stringify(body), { status, headers: { 'Content-Type': contentType } });
