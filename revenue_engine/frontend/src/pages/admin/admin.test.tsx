import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { useAppStore } from '@/stores';
import { member, meta, problem, supplier, task } from '@/test/fixtures';
import { json, renderRoute, stubFetch } from '@/test/render';

afterEach(() => {
  vi.unstubAllGlobals();
});

const xlsx = (name: string) =>
  new File(['x'], name, {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });

/** The Dropzone's hidden file input. */
const fileInput = () => {
  const input = document.querySelector('input[type="file"]');
  if (!(input instanceof HTMLInputElement)) throw new Error('No file input');
  return input;
};

describe('Admin › Payment Ledger', () => {
  it('uploads the ledger in the background and reports the result', async () => {
    let uploaded: FormData | null = null;
    let polls = 0;
    stubFetch({
      'GET /api/ledger-entries': () => json({ items: [], total: 1204 }),
      'POST /api/ledger-imports': async (request) => {
        uploaded = await request.formData();
        return json(task({ id: 'task-9', status: 'queued' }), 202);
      },
      'GET /api/tasks/task-9': () => {
        polls += 1;
        return json(
          polls < 2
            ? task({ id: 'task-9', status: 'running' })
            : task({ id: 'task-9', status: 'succeeded', ledger_import: { inserted: 37 } }),
        );
      },
    });
    const user = userEvent.setup();
    renderRoute('/admin/ledger');

    expect(await screen.findByText('1,204')).toBeInTheDocument();
    const load = screen.getByRole('button', { name: 'Load ledger' });
    expect(load).toBeDisabled();

    await user.upload(fileInput(), xlsx('Ledger.xlsx'));
    await user.clear(screen.getByLabelText('Sheet name'));
    await user.type(screen.getByLabelText('Sheet name'), '2026');
    await user.click(load);

    expect(await screen.findByText(/Importing/)).toBeInTheDocument();
    expect(useAppStore.getState().activeImports).toEqual({ ledger_import: 'task-9' });
    expect(
      await screen.findByText('Loaded 37 ledger entries.', {}, { timeout: 4000 }),
    ).toBeInTheDocument();

    const body = uploaded as FormData | null;
    expect(body?.get('sheet_name')).toBe('2026');
    // jsdom's File loses its name crossing into Node's FormData; check the content arrived.
    expect(body?.get('file')).toMatchObject({ size: 1 });

    await user.click(screen.getByRole('button', { name: 'Dismiss' }));
    expect(useAppStore.getState().activeImports).toEqual({});
  });

  it('forgets an import the API no longer knows about (restart)', async () => {
    useAppStore.getState().trackImport('ledger_import', 'lost');
    stubFetch({
      'GET /api/ledger-entries': () => json({ items: [], total: 0 }),
      'GET /api/tasks/lost': () => json(problem(404, 'Task lost not found'), 404),
    });
    renderRoute('/admin/ledger');
    await waitFor(() => {
      expect(useAppStore.getState().activeImports).toEqual({});
    });
  });
});

describe('Admin › Members', () => {
  it('asks before a full refresh', async () => {
    const posted = vi.fn(() => json(task({ kind: 'member_import' }), 202));
    stubFetch({
      'GET /api/members': () => json({ items: [], total: 0 }),
      'POST /api/member-imports': posted,
      'GET /api/tasks/task-1': () => json(task({ kind: 'member_import' })),
    });
    const user = userEvent.setup();
    renderRoute('/admin/members');

    await user.upload(fileInput(), xlsx('Account List.xlsx'));
    await user.click(screen.getByLabelText('Replace all existing members (full refresh)'));
    await user.click(screen.getByRole('button', { name: 'Load Account List' }));

    const dialog = await screen.findByRole('dialog', { name: 'Replace all members?' });
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    expect(posted).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: 'Load Account List' }));
    await user.click(await screen.findByRole('button', { name: 'Delete and reload' }));
    await waitFor(() => {
      expect(posted).toHaveBeenCalledTimes(1);
    });
  });

  it('validates the add-member form and shows server conflicts', async () => {
    const bodies: unknown[] = [];
    stubFetch({
      'GET /api/members': () => json({ items: [], total: 0 }),
      'POST /api/members': async (request) => {
        bodies.push(await request.json());
        return json(
          problem(409, "A member with Salesforce Account ID '001A' already exists."),
          409,
        );
      },
    });
    const user = userEvent.setup();
    renderRoute('/admin/members');

    await user.click(screen.getByRole('button', { name: 'Add member' }));
    expect(await screen.findByText('Enter the facility name')).toBeInTheDocument();
    expect(bodies).toHaveLength(0);

    await user.type(screen.getByLabelText(/Facility name/), '  New Clinic ');
    await user.type(screen.getByLabelText(/Salesforce Account ID/), '001A');
    await user.click(screen.getByRole('button', { name: 'Add member' }));

    expect(await screen.findByText(/already exists/)).toBeInTheDocument();
    expect(bodies[0]).toEqual({
      name: 'New Clinic',
      premier_entity_code: null,
      account_id: '001A',
      city: null,
      state: null,
      zip: null,
    });
  });
});

describe('Admin › Suppliers', () => {
  it('edits a supplier starting from its saved values and keeps the fields it did not touch (F22)', async () => {
    let body: unknown = null;
    stubFetch({
      'GET /api/suppliers': () => json([supplier()]),
      'GET /api/meta': () => json(meta),
      'PUT /api/suppliers/1': async (request) => {
        body = await request.json();
        return json(supplier({ contract_id: 'C-200' }));
      },
    });
    const user = userEvent.setup();
    renderRoute('/admin/suppliers');

    const edit = await screen.findByRole('button', { name: 'Edit Acme Imaging' });
    // Enabled once the select options (GET /meta) have loaded.
    await waitFor(() => {
      expect(edit).toBeEnabled();
    });
    await user.click(edit);
    const dialog = await screen.findByRole('dialog', { name: 'Edit Acme Imaging' });
    const contract = within(dialog).getByLabelText('Salesforce contract ID');
    expect(contract).toHaveValue('C-100');
    expect(within(dialog).getByLabelText(/Header row/)).toHaveValue('2');

    await user.clear(contract);
    await user.type(contract, 'C-200');
    await user.click(within(dialog).getByRole('button', { name: 'Save supplier' }));

    await waitFor(() => {
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });
    expect(body).toMatchObject({
      name: 'Acme Imaging',
      contract_id: 'C-200',
      sheet_name: 'Detail',
      header_row: 2,
      max_read_columns: 12,
      fee_rate: 0.03,
      fuzzy_nationwide_threshold: 90,
    });
  });

  it('shows an empty state with no suppliers', async () => {
    stubFetch({ 'GET /api/suppliers': () => json([]), 'GET /api/meta': () => json(meta) });
    renderRoute('/admin/suppliers');
    expect(await screen.findByText('No suppliers configured')).toBeInTheDocument();
  });
});

describe('Admin › Aliases', () => {
  it('asks for the supplier only for a supplier-specific alias (F21)', async () => {
    let body: unknown = null;
    stubFetch({
      'GET /api/aliases': () => json([]),
      'GET /api/suppliers': () => json([supplier(), supplier({ id: 2, name: 'Beta Pharma' })]),
      'GET /api/members': () => json({ items: [member()], total: 1 }),
      'POST /api/aliases': async (request) => {
        body = await request.json();
        return json(
          {
            id: 5,
            member_id: 10,
            member_name: 'Mercy General',
            entity_code: 'PEC-10',
            raw_name: 'Mercy Gen Hosp',
            jde_number: null,
            supplier_profile_id: 2,
            supplier_name: 'Beta Pharma',
            approved_by: 'admin',
            approved_at: '2026-03-02T09:30:00',
          },
          201,
        );
      },
    });
    const user = userEvent.setup();
    renderRoute('/admin/aliases');

    expect(await screen.findByText('No aliases yet')).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: /^Supplier/ })).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save alias' }));
    expect(await screen.findByText('Pick a member')).toBeInTheDocument();

    await user.type(screen.getByRole('combobox', { name: /Member/ }), 'Mer');
    await user.click(await screen.findByRole('option', { name: /Mercy General/ }));
    await user.type(screen.getByLabelText(/Alias \(alternate name\)/), 'Mercy Gen Hosp');
    await user.click(screen.getByText('Supplier-specific'));

    await user.click(screen.getByRole('button', { name: 'Save alias' }));
    expect(await screen.findByText('Pick the supplier')).toBeInTheDocument();

    await user.click(screen.getByRole('combobox', { name: /^Supplier/ }));
    await user.click(await screen.findByRole('option', { name: 'Beta Pharma' }));
    await user.click(screen.getByRole('button', { name: 'Save alias' }));

    await waitFor(() => {
      expect(body).toEqual({
        member_id: 10,
        raw_name: 'Mercy Gen Hosp',
        jde_number: null,
        supplier_profile_id: 2,
      });
    });
  });
});
