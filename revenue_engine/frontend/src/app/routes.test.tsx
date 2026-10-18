import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { json, renderRoute, stubFetch } from '@/test/render';

afterEach(() => {
  vi.unstubAllGlobals();
});

const stats = { active_members: 1234, suppliers: 16, jobs: 7, pending_reviews: 3 };

describe('app shell and routing', () => {
  it('renders the dashboard with stats from the API', async () => {
    stubFetch({ 'GET /api/dashboard': () => json(stats) });
    await renderRoute('/');
    expect(await screen.findByText('1,234')).toBeInTheDocument();
    expect(screen.getByText('16')).toBeInTheDocument();
    // Pending reviews link to the review queue.
    expect(screen.getByRole('link', { name: '3' })).toHaveAttribute('href', '/review');
  });

  it('shows an error state with retry when the API fails', async () => {
    const fetchMock = stubFetch({
      'GET /api/dashboard': () =>
        json(
          {
            type: 'about:blank',
            title: 'Internal Server Error',
            status: 500,
            detail: 'An unexpected error occurred.',
          },
          500,
        ),
    });
    await renderRoute('/');
    expect(await screen.findByText("Couldn't load the overview")).toBeInTheDocument();
    expect(screen.getByText('An unexpected error occurred.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Try again' }));
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('has a navigation entry for every legacy page and marks the active one', async () => {
    stubFetch({});
    await renderRoute('/upload');
    const nav = screen.getByRole('navigation', { name: 'Main navigation' });
    for (const label of ['Dashboard', 'Upload', 'Review', 'Export', 'Admin']) {
      expect(within(nav).getByRole('link', { name: new RegExp(label) })).toBeInTheDocument();
    }
    expect(within(nav).getByRole('link', { name: /Upload/ })).toHaveAttribute(
      'data-active',
      'true',
    );
  });

  it('renders a 404 inside the shell for unknown paths', async () => {
    stubFetch({});
    await renderRoute('/no/such/page');
    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: 'Main navigation' })).toBeInTheDocument();
  });

  it('redirects /admin to the Members tab and switches tabs via the URL', async () => {
    stubFetch({});
    const { router } = await renderRoute('/admin');
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/admin/members');
    });
    await userEvent.click(screen.getByRole('tab', { name: 'Tier C Rules' }));
    // The tab's page loads lazily before the navigation completes.
    await waitFor(() => {
      expect(router.state.location.pathname).toBe('/admin/tier-c');
    });
    expect(screen.getByRole('tab', { name: 'Tier C Rules' })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });
});
