import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { extractionRule, job, meta, supplier } from '@/test/fixtures';
import { json, renderRoute, stubFetch } from '@/test/render';

afterEach(() => {
  vi.unstubAllGlobals();
});

const file = (name: string) => new File(['a,b\n1,2'], name, { type: 'text/csv' });
const fileInput = () => {
  const input = document.querySelector('input[type="file"]');
  if (!(input instanceof HTMLInputElement)) throw new Error('No file input');
  return input;
};
const suppliers = [supplier(), supplier({ id: 2, name: 'Beta Pharma', contract_id: 'C-200' })];

describe('Upload page', () => {
  it('fills supplier and date from the file name, processes, and follows the job', async () => {
    let form: FormData | null = null;
    let polls = 0;
    const fetchMock = stubFetch({
      'GET /api/suppliers': () => json(suppliers),
      'GET /api/meta': () => json(meta),
      'GET /api/jobs': () => json([]),
      'GET /api/file-name-inference': () =>
        json({
          file_name: 'Beta 2026-03-01.csv',
          supplier_profile_id: 2,
          supplier_name: 'Beta Pharma',
          date_received: '2026-03-01',
          deposit_amount: null,
        }),
      'POST /api/jobs': async (request) => {
        form = await request.formData();
        return json(job({ status: 'uploaded' }), 202);
      },
      'GET /api/jobs/42': () => {
        polls += 1;
        return json(
          polls < 2
            ? job({ status: 'processing' })
            : job({ status: 'needs_review', unmatched_count: 3, pending_review_count: 3 }),
        );
      },
    });
    const user = userEvent.setup();
    const { router } = await renderRoute('/upload');

    await user.upload(fileInput(), file('Beta 2026-03-01.csv'));
    expect(await screen.findByText('Inferred from the file name: Beta Pharma')).toBeInTheDocument();
    expect(screen.getByLabelText(/Date received/)).toHaveValue('2026-03-01');
    expect(screen.getByText('C-200')).toBeInTheDocument();

    await user.type(screen.getByLabelText(/Payment ID override/), ' PAY-7 ');
    await user.click(screen.getByRole('button', { name: 'Process file' }));

    await waitFor(() => {
      expect(router.state.location.search).toBe('?job=42');
    });
    expect(await screen.findByText(/Processing job #42/)).toBeInTheDocument();
    expect(
      await screen.findByText(/3 unmatched customers need review/, {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Review page/ })).toHaveAttribute(
      'href',
      '/review?job=42',
    );

    const sent = form as FormData | null;
    expect(sent?.get('supplier_profile_id')).toBe('2');
    expect(sent?.get('date_received')).toBe('2026-03-01');
    expect(sent?.get('manual_payment_id')).toBe('PAY-7');

    const inferenceCall = fetchMock.mock.calls.find(
      ([r]) => new URL(r.url).pathname === '/api/file-name-inference',
    );
    expect(new URL(inferenceCall?.[0].url ?? '').searchParams.get('file_name')).toBe(
      'Beta 2026-03-01.csv',
    );
    // Recent jobs are refetched once the job has finished.
    await waitFor(() => {
      expect(
        fetchMock.mock.calls.filter(([r]) => new URL(r.url).pathname === '/api/jobs').length,
      ).toBeGreaterThanOrEqual(2);
    });
  });

  it('asks for supplier and date when the file name says nothing', async () => {
    const posted = vi.fn();
    stubFetch({
      'GET /api/suppliers': () => json(suppliers),
      'GET /api/meta': () => json(meta),
      'GET /api/jobs': () => json([]),
      'GET /api/file-name-inference': () =>
        json({
          file_name: 'export.csv',
          supplier_profile_id: null,
          supplier_name: null,
          date_received: null,
          deposit_amount: null,
        }),
      'POST /api/jobs': () => {
        posted();
        return json(job(), 202);
      },
    });
    const user = userEvent.setup();
    await renderRoute('/upload');

    await user.upload(fileInput(), file('export.csv'));
    expect(
      await screen.findByText(/Could not infer the supplier from the file name/),
    ).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Process file' }));
    expect(await screen.findByText('Pick the supplier')).toBeInTheDocument();
    expect(screen.getByText('Enter the date received')).toBeInTheDocument();
    expect(posted).not.toHaveBeenCalled();
  });

  it('asks for the confirmed total of a Tier C manual-entry rule and sends it', async () => {
    let form: FormData | null = null;
    const pivot = supplier({ id: 3, name: 'Pivot Co', job_type: 'pivot_allocation' });
    stubFetch({
      'GET /api/suppliers': () => json([pivot]),
      'GET /api/meta': () => json(meta),
      'GET /api/jobs': () => json([]),
      'GET /api/suppliers/3/extraction-rules': () =>
        json([
          extractionRule({ id: 1, rule_name: 'Gross' }),
          extractionRule({
            id: 2,
            rule_name: 'Rebate',
            extraction_method: 'manual_entry',
            cell_address: null,
            is_sales_amount: false,
          }),
        ]),
      'GET /api/file-name-inference': () =>
        json({
          file_name: 'Pivot Co 2026-03-01.xlsx',
          supplier_profile_id: 3,
          supplier_name: 'Pivot Co',
          date_received: '2026-03-01',
          deposit_amount: null,
        }),
      'POST /api/jobs': async (request) => {
        form = await request.formData();
        return json(job({ status: 'uploaded' }), 202);
      },
      'GET /api/jobs/42': () =>
        json(job({ job_type: 'pivot_allocation', manual_totals: { Rebate: 1500.25 } })),
    });
    const user = userEvent.setup();
    await renderRoute('/upload');

    await user.upload(fileInput(), file('Pivot Co 2026-03-01.xlsx'));
    const rebate = await screen.findByLabelText(/^Rebate/);
    // Only manual_entry rules get a box.
    expect(screen.queryByLabelText(/^Gross/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Process file' }));
    expect(await screen.findByText('Enter the confirmed total for Rebate')).toBeInTheDocument();
    expect(form).toBeNull();

    await user.type(rebate, '1500.25');
    await user.click(screen.getByRole('button', { name: 'Process file' }));
    expect(
      await screen.findByText('Confirmed totals entered at upload: Rebate $1,500.25'),
    ).toBeInTheDocument();
    const sent = form as FormData | null;
    expect(JSON.parse(sent?.get('manual_totals') as string)).toEqual({ Rebate: 1500.25 });
  });

  it('shows why processing failed', async () => {
    stubFetch({
      'GET /api/suppliers': () => json(suppliers),
      'GET /api/meta': () => json(meta),
      'GET /api/jobs': () => json([job({ status: 'failed' })]),
      'GET /api/jobs/42': () =>
        json(job({ status: 'failed', error_message: 'No column mapped to fee_amount' })),
    });
    await renderRoute('/upload?job=42');
    expect(await screen.findByText('Processing failed (job #42)')).toBeInTheDocument();
    expect(screen.getByText('No column mapped to fee_amount')).toBeInTheDocument();
  });

  it('points to Admin when no suppliers exist', async () => {
    stubFetch({
      'GET /api/suppliers': () => json([]),
      'GET /api/meta': () => json(meta),
    });
    await renderRoute('/upload');
    expect(await screen.findByText('No supplier profiles configured')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Admin › Suppliers' })).toHaveAttribute(
      'href',
      '/admin/suppliers',
    );
  });
});
