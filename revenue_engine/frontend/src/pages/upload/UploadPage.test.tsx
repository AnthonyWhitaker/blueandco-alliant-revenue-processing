import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { extractionRule, job, meta, supplier } from '@/test/fixtures';
import { json, renderRoute, stubFetch } from '@/test/render';

afterEach(() => {
  vi.unstubAllGlobals();
});

const file = (name: string) => new File([`a,b\n1,${name}`], name, { type: 'text/csv' });
/** The test fetch layer drops multipart file names, so files are told apart by content. */
const sentFiles = (form: FormData | null) =>
  Promise.all((form?.getAll('files') as Blob[]).map((f) => f.text()));
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
    await user.type(screen.getByLabelText(/^Timeframe/), '3Q26');
    await user.click(screen.getByRole('button', { name: 'Process file' }));

    await waitFor(() => {
      expect(router.state.location.search).toBe('?job=42');
    });
    expect(await screen.findByText(/Processing job #42/)).toBeInTheDocument();
    expect(
      await screen.findByText(/needs review: 3 unmatched rows/, {}, { timeout: 4000 }),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Review page/ })).toHaveAttribute(
      'href',
      '/review?job=42',
    );

    const sent = form as FormData | null;
    expect(sent?.get('supplier_profile_id')).toBe('2');
    expect(sent?.get('date_received')).toBe('2026-03-01');
    expect(sent?.get('manual_payment_id')).toBe('PAY-7');
    expect(sent?.get('timeframe')).toBe('3Q26');
    expect(await sentFiles(sent)).toEqual(['a,b\n1,Beta 2026-03-01.csv']);

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
    expect(screen.getByText('Enter the quarter this payment represents')).toBeInTheDocument();
    expect(posted).not.toHaveBeenCalled();
  });

  it('combines several files for one month into one job', async () => {
    let form: FormData | null = null;
    stubFetch({
      'GET /api/suppliers': () => json(suppliers),
      'GET /api/meta': () => json(meta),
      'GET /api/jobs': () => json([]),
      'GET /api/file-name-inference': () =>
        json({
          file_name: 'Beta 2026-03-01 part 1.csv',
          supplier_profile_id: 2,
          supplier_name: 'Beta Pharma',
          date_received: '2026-03-01',
          deposit_amount: null,
        }),
      'POST /api/jobs': async (request) => {
        form = await request.formData();
        return json(job({ status: 'uploaded', file_count: 3 }), 202);
      },
      'GET /api/jobs/42': () => json(job({ file_count: 3, row_count: 120000 })),
    });
    const user = userEvent.setup();
    await renderRoute('/upload');

    await user.upload(fileInput(), [file('Beta 2026-03-01 part 1.csv'), file('part 2.csv')]);
    expect(await screen.findByText('Inferred from the file name: Beta Pharma')).toBeInTheDocument();
    await user.upload(fileInput(), file('part 3.csv'));
    expect(screen.getByText('File 3 of 3 · 0 KB')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Remove part 2.csv' }));
    await user.upload(fileInput(), file('part 2.csv'));
    await user.type(screen.getByLabelText(/^Timeframe/), '1Q26');
    await user.click(screen.getByRole('button', { name: 'Combine and process 3 files' }));

    expect(await screen.findByText('Total rows (3 files)')).toBeInTheDocument();
    const sent = form as FormData | null;
    expect(await sentFiles(sent)).toEqual([
      'a,b\n1,Beta 2026-03-01 part 1.csv',
      'a,b\n1,part 3.csv',
      'a,b\n1,part 2.csv',
    ]);
  });

  it('allows one file per job for a Tier C supplier', async () => {
    const pivot = supplier({ id: 3, name: 'Pivot Co', job_type: 'pivot_allocation' });
    stubFetch({
      'GET /api/suppliers': () => json([pivot]),
      'GET /api/meta': () => json(meta),
      'GET /api/jobs': () => json([]),
      'GET /api/suppliers/3/extraction-rules': () => json([extractionRule({ id: 1 })]),
      'GET /api/file-name-inference': () =>
        json({
          file_name: 'Pivot Co 2026-03-01.xlsx',
          supplier_profile_id: 3,
          supplier_name: 'Pivot Co',
          date_received: '2026-03-01',
          deposit_amount: null,
        }),
    });
    const user = userEvent.setup();
    await renderRoute('/upload');
    await user.upload(fileInput(), [file('Pivot Co 2026-03-01.xlsx'), file('b.xlsx')]);
    expect(await screen.findByText(/takes one file per job/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Combine and process 2 files' })).toBeDisabled();
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
    await user.type(screen.getByLabelText(/^Timeframe/), '1Q26');
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
