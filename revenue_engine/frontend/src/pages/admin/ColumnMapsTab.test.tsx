import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import { useAppStore } from '@/stores';
import { meta, supplier } from '@/test/fixtures';
import { json, renderRoute, stubFetch } from '@/test/render';

afterEach(() => {
  vi.unstubAllGlobals();
});

const suppliers = [supplier(), supplier({ id: 2, name: 'Beta Pharma' })];
const mapping = (id: number, supplierId: number, source: string) => ({
  id,
  supplier_profile_id: supplierId,
  source_column: source,
  canonical_field: 'customer_name',
  is_required: true,
});

describe('Admin › Column Maps', () => {
  it('selects the first supplier, switches via the URL, and keeps inferred drafts per supplier', async () => {
    let bulk: unknown = null;
    stubFetch({
      'GET /api/suppliers': () => json(suppliers),
      'GET /api/meta': () => json(meta),
      'GET /api/suppliers/1/column-mappings': () => json([mapping(1, 1, 'Customer')]),
      'GET /api/suppliers/2/column-mappings': () => json([]),
      'POST /api/suppliers/1/column-mappings/inference': () =>
        json([{ source_column: 'Cust Name', canonical_field: 'customer_name' }]),
      'POST /api/suppliers/1/column-mappings/bulk': async (request) => {
        bulk = await request.json();
        return json([mapping(2, 1, 'Cust Name')]);
      },
    });
    const user = userEvent.setup();
    const { router } = renderRoute('/admin/column-maps');

    await waitFor(() => {
      expect(router.state.location.search).toBe('?supplier=1');
    });
    expect(await screen.findByText('Customer')).toBeInTheDocument();

    const input = document.querySelector('input[type="file"]');
    if (!(input instanceof HTMLInputElement)) throw new Error('No file input');
    await user.upload(input, new File(['Cust Name\nX'], 'sample.csv', { type: 'text/csv' }));
    await user.click(screen.getByRole('button', { name: 'Infer column map' }));
    expect(await screen.findByText('Cust Name')).toBeInTheDocument();

    // Switching supplier and back keeps supplier 1's unsaved suggestions.
    await user.click(screen.getByRole('combobox', { name: 'Supplier' }));
    await user.click(await screen.findByRole('option', { name: 'Beta Pharma' }));
    expect(router.state.location.search).toBe('?supplier=2');
    expect(await screen.findByText('No column mappings yet')).toBeInTheDocument();
    expect(screen.queryByText('Cust Name')).not.toBeInTheDocument();
    await router.navigate(-1);
    expect(await screen.findByText('Cust Name')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: 'Save inferred mappings' }));
    await waitFor(() => {
      expect(useAppStore.getState().inferredMappings).toEqual({});
    });
    expect(bulk).toEqual([{ source_column: 'Cust Name', canonical_field: 'customer_name' }]);
  });

  it('validates and saves a single mapping', async () => {
    let body: unknown = null;
    stubFetch({
      'GET /api/suppliers': () => json(suppliers),
      'GET /api/meta': () => json(meta),
      'GET /api/suppliers/2/column-mappings': () => json([]),
      'POST /api/suppliers/2/column-mappings': async (request) => {
        body = await request.json();
        return json({ ...mapping(3, 2, 'contains:Fee'), canonical_field: 'fee_amount' });
      },
    });
    const user = userEvent.setup();
    renderRoute('/admin/column-maps?supplier=2');

    await user.click(await screen.findByRole('button', { name: 'Save mapping' }));
    expect(await screen.findByText(/Enter the column header/)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/Source column/), 'contains:Fee');
    await user.click(screen.getByRole('combobox', { name: /Canonical field/ }));
    await user.click(await screen.findByRole('option', { name: 'fee_amount' }));
    await user.click(screen.getByLabelText('Required'));
    await user.click(screen.getByRole('button', { name: 'Save mapping' }));

    await waitFor(() => {
      expect(body).toEqual({
        source_column: 'contains:Fee',
        canonical_field: 'fee_amount',
        is_required: true,
      });
    });
  });
});
