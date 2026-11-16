import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { job, jobOutput, reconciliation } from '@/test/fixtures';
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
        [`GET /api/jobs/${j.id}/outputs`, () => json([])],
      ]),
    ),
    ...extra(byId),
  });
}

describe('Export page', () => {
  it('opens the newest exportable job by default and puts it in the URL', async () => {
    const fetchMock = api([job({ id: 42 }), job({ id: 41 })]);
    const { router } = await renderRoute('/export');

    await waitFor(() => {
      expect(router.state.location.search).toBe('?job=42');
    });
    expect(router.state.historyAction).toBe('REPLACE');
    expect((await screen.findAllByText('$1,234.50')).length).toBeGreaterThan(0);
    expect(screen.getByText('PAY-1')).toBeInTheDocument();
    expect(screen.getByText('Totals reconcile')).toBeInTheDocument();
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
    await renderRoute('/export?job=42');

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
    await renderRoute('/export?job=42');

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
    await renderRoute('/export?job=7');
    expect(await screen.findByText("Job #7 isn't ready to export")).toBeInTheDocument();
    expect(screen.getByText('Payment ID lookup failed')).toBeInTheDocument();
  });

  it('downloads a stored output file under the server-provided name', async () => {
    // jsdom has no object URLs.
    const createObjectURL = vi.fn(() => 'blob:workbook');
    Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
    const clicked = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {
      /* jsdom can't navigate to a blob URL */
    });
    const output = jobOutput({ id: 7, file_name: 'Acme 2026 Processed 20260302-093100.xlsx' });
    api([job()], () => ({
      'GET /api/jobs/42/outputs': () => json([output]),
      'GET /api/jobs/42/outputs/7/content': () =>
        new Response('xlsx', {
          headers: {
            'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'Content-Disposition':
              "attachment; filename*=UTF-8''Acme%202026%20Processed%2020260302-093100.xlsx",
          },
        }),
    }));
    const user = userEvent.setup();
    await renderRoute('/export?job=42');

    await user.click(await screen.findByRole('button', { name: `Download ${output.file_name}` }));
    await waitFor(() => {
      expect(clicked).toHaveBeenCalled();
    });
    const anchor = clicked.mock.instances[0] as HTMLAnchorElement;
    expect(anchor.download).toBe('Acme 2026 Processed 20260302-093100.xlsx');
    expect(createObjectURL).toHaveBeenCalled();
    clicked.mockRestore();
  });

  it('generates a new version without replacing the old ones', async () => {
    const outputs = [jobOutput({ id: 1, file_name: 'v1 Processed.xlsx' })];
    let posted = 0;
    api([job()], () => ({
      'GET /api/jobs/42/outputs': () => json(outputs),
      'POST /api/jobs/42/outputs': () => {
        posted += 1;
        const created = [
          jobOutput({ id: 2, file_name: 'v2 Processed.xlsx' }),
          jobOutput({ id: 3, artifact_type: 'exception_report', file_name: 'v2 Exceptions.xlsx' }),
        ];
        outputs.unshift(...created);
        return json(created, 201);
      },
    }));
    const user = userEvent.setup();
    await renderRoute('/export?job=42');

    expect(await screen.findByText('v1 Processed.xlsx')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Generate new version' }));
    expect(await screen.findByText('v2 Exceptions.xlsx')).toBeInTheDocument();
    expect(screen.getByText('v1 Processed.xlsx')).toBeInTheDocument();
    expect(posted).toBe(1);
  });

  it('disables downloading a file that is no longer on disk', async () => {
    api([job()], () => ({
      'GET /api/jobs/42/outputs': () =>
        json([jobOutput({ file_name: 'gone.xlsx', available: false })]),
    }));
    await renderRoute('/export?job=42');
    expect(await screen.findByRole('button', { name: 'Download gone.xlsx' })).toBeDisabled();
  });

  it('flags totals that do not reconcile', async () => {
    api([
      job({
        status: 'needs_review',
        reconciliation: reconciliation({ balanced: false, revenue_difference: 12.5 }),
      }),
    ]);
    await renderRoute('/export?job=42');
    expect(await screen.findByText('Totals do not reconcile')).toBeInTheDocument();
    expect(screen.getByText('$12.50')).toBeInTheDocument();
  });

  it('shows an empty state when nothing has been processed', async () => {
    stubFetch({ 'GET /api/jobs': () => json([]) });
    await renderRoute('/export');
    expect(await screen.findByText('No processed jobs yet')).toBeInTheDocument();
  });
});
