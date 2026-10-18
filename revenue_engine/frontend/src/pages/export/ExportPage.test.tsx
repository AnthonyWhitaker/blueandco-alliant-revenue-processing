import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { job } from '@/test/fixtures';
import { json, renderRoute, stubFetch } from '@/test/render';

afterEach(() => {
  vi.unstubAllGlobals();
});

const emptyPreview = { upload: [], validation: [], unmatched: [], jde_mapping: [] };
const preview = {
  ...emptyPreview,
  upload: [
    {
      pec: 'PEC-10',
      facility_id: '001A',
      contract_id: 'C-100',
      payment_id: 'PAY-1',
      volume: 1000,
      total_revenue: 30,
      revenue_type: 'Admin Fee',
      keep_return: 'Keep',
      earned_period: '2026-01-01',
      pmt_for_time_frame: null,
      date_received: '2026-03-01',
      comments: null,
      date_refunded: null,
      rebate_retained: null,
      amount_retained: null,
      retained_reason: null,
    },
  ],
};

type Job = ReturnType<typeof job>;

/** A small stateful fake of the jobs API: `extra` handlers can update `byId`. */
function api(
  jobs: Job[],
  extra: (byId: Map<number, Job>) => Parameters<typeof stubFetch>[0] = () => ({}),
) {
  const byId = new Map(jobs.map((j) => [j.id, j]));
  return stubFetch({
    'GET /api/jobs': () => json(jobs),
    ...Object.fromEntries(
      jobs.flatMap((j) => [
        [`GET /api/jobs/${j.id}`, () => json(byId.get(j.id))],
        [`GET /api/jobs/${j.id}/exceptions`, () => json([])],
        [`GET /api/jobs/${j.id}/preview`, () => json(preview)],
      ]),
    ),
    ...extra(byId),
  });
}

describe('Export page', () => {
  it('opens the newest exportable job by default and puts it in the URL', async () => {
    const fetchMock = api([job({ id: 42 }), job({ id: 41 })]);
    const { router } = renderRoute('/export');

    await waitFor(() => {
      expect(router.state.location.search).toBe('?job=42');
    });
    expect(router.state.historyAction).toBe('REPLACE');
    expect(await screen.findByText('$1,234.50')).toBeInTheDocument();
    expect(screen.getByText('PAY-1')).toBeInTheDocument();
    expect(await screen.findByRole('table', { name: 'Upload tab preview' })).toBeInTheDocument();
    expect(screen.getByText('PEC-10')).toBeInTheDocument();

    const listCall = fetchMock.mock.calls.find(([r]) => new URL(r.url).pathname === '/api/jobs');
    expect(new URL(listCall?.[0].url ?? '').searchParams.getAll('status')).toEqual([
      'completed',
      'needs_review',
    ]);
  });

  it('saves a missing Payment ID explicitly (F10)', async () => {
    let body: unknown = null;
    api([job({ payment_id: null })], (byId) => ({
      'PATCH /api/jobs/42': async (request) => {
        body = await request.json();
        byId.set(42, job({ payment_id: 'PAY-9' }));
        return json(byId.get(42));
      },
    }));
    const user = userEvent.setup();
    renderRoute('/export?job=42');

    const input = await screen.findByLabelText('Payment ID');
    const save = screen.getByRole('button', { name: 'Save Payment ID' });
    expect(save).toBeDisabled();
    await user.type(input, ' PAY-9 ');
    await user.click(save);

    expect(await screen.findByText('PAY-9')).toBeInTheDocument();
    expect(body).toEqual({ payment_id: 'PAY-9' });
  });

  it('lets a needs_review job with nothing pending be signed off (F8)', async () => {
    const confirmed = vi.fn();
    api([job({ status: 'needs_review', pending_review_count: 0 })], (byId) => ({
      'POST /api/jobs/42/confirmation': () => {
        confirmed();
        byId.set(42, job({ status: 'completed' }));
        return json(byId.get(42));
      },
    }));
    const user = userEvent.setup();
    renderRoute('/export?job=42');

    await user.click(await screen.findByRole('button', { name: 'Confirm job as complete' }));
    await waitFor(() => {
      expect(confirmed).toHaveBeenCalledTimes(1);
    });
    await waitFor(() => {
      expect(
        screen.queryByRole('button', { name: 'Confirm job as complete' }),
      ).not.toBeInTheDocument();
    });
  });

  it('explains when a linked job cannot be exported', async () => {
    stubFetch({
      'GET /api/jobs': () => json([]),
      'GET /api/jobs/7': () =>
        json(job({ id: 7, status: 'failed', error_message: 'Payment ID lookup failed' })),
    });
    renderRoute('/export?job=7');
    expect(await screen.findByText("Job #7 isn't ready to export")).toBeInTheDocument();
    expect(screen.getByText('Payment ID lookup failed')).toBeInTheDocument();
  });

  it('downloads the workbook under the server-provided name', async () => {
    // jsdom has no object URLs.
    const createObjectURL = vi.fn(() => 'blob:workbook');
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      /* jsdom can't navigate to a blob URL */
    });
    api([job()], () => ({
      'GET /api/jobs/42/export': () =>
        new Response('xlsx', {
          headers: {
            'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'Content-Disposition': "attachment; filename*=UTF-8''Acme%202026%20Processed.xlsx",
          },
        }),
    }));
    const user = userEvent.setup();
    renderRoute('/export?job=42');

    await user.click(await screen.findByRole('button', { name: 'Download workbook' }));
    await waitFor(() => {
      expect(clicked).toHaveBeenCalled();
    });
    const anchor = clicked.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe('Acme 2026 Processed.xlsx');
    expect(createObjectURL).toHaveBeenCalled();
    clicked.mockRestore();
  });

  it('shows an empty state when nothing has been processed', async () => {
    stubFetch({ 'GET /api/jobs': () => json([]) });
    renderRoute('/export');
    expect(await screen.findByText('No processed jobs yet')).toBeInTheDocument();
  });
});
