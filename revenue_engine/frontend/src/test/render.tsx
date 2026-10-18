import { mergeThemeOverrides } from '@mantine/core';
import { render, waitFor } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router-dom';

import { Providers } from '@/app/Providers';
import { providerFuture, routeTree, routerFuture } from '@/app/routes';
import { createQueryClient } from '@/lib/queryClient';
import { theme } from '@/theme';

/**
 * jsdom has no layout, so Floating UI thinks every popover's target is off-screen
 * and Mantine hides the dropdown (`hideDetached`). Keep dropdowns visible in tests.
 */
const testTheme = mergeThemeOverrides(theme, {
  components: { Popover: { defaultProps: { hideDetached: false } } },
});

/**
 * Render the real route tree at `path` inside the real providers, and wait
 * until the route's lazily loaded page module is in (the router is initialized).
 */
export async function renderRoute(path: string) {
  const queryClient = createQueryClient();
  queryClient.setDefaultOptions({ queries: { retry: false } });
  const router = createMemoryRouter(routeTree, { initialEntries: [path], future: routerFuture });
  const view = render(
    <Providers queryClient={queryClient} theme={testTheme}>
      <RouterProvider router={router} future={providerFuture} />
    </Providers>,
  );
  // The page module loads lazily, then (startTransition) commits a tick later.
  await waitFor(() => {
    if (!router.state.initialized) throw new Error('router still loading the page');
    view.getByRole('main');
  });
  return { router, queryClient, ...view };
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
