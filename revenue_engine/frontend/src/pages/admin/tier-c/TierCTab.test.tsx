import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Schemas } from '@/api/types';
import { meta, problem, supplier } from '@/test/fixtures';
import { json, renderRoute, stubFetch } from '@/test/render';

afterEach(() => {
  vi.unstubAllGlobals();
});

type ExtractionRule = Schemas['ExtractionRule'];
type AllocationRule = Schemas['AllocationRule'];

const extraction = (overrides: Partial<ExtractionRule> = {}): ExtractionRule => ({
  id: 5,
  supplier_profile_id: 3,
  rule_name: 'Gross',
  sheet_name: 'Summary',
  extraction_method: 'named_cell',
  cell_address: 'E8',
  label_text: null,
  label_match_mode: null,
  label_match_threshold: null,
  value_row_offset: null,
  value_col_offset: null,
  range_axis: null,
  column_index: null,
  column_letter: null,
  header_row: null,
  stop_label_text: null,
  formula_expression: null,
  is_sales_amount: true,
  sort_order: 2,
  ...overrides,
});

const allocation = (overrides: Partial<AllocationRule> = {}): AllocationRule => ({
  id: 8,
  supplier_profile_id: 3,
  extraction_rule_name: 'Gross',
  member_id: 10,
  member_name: 'Mercy General',
  entity_code: 'PEC-10',
  allocation_type: 'percent',
  allocation_value: 60,
  contract_number: null,
  product_line: null,
  is_active: true,
  ...overrides,
});

const pivotSuppliers = [
  supplier({ id: 3, name: 'Pivot Co', job_type: 'pivot_allocation' }),
  supplier({ id: 4, name: 'Zeta Pivot', job_type: 'pivot_allocation' }),
];

function tierCApi(extra: Parameters<typeof stubFetch>[0] = {}) {
  return stubFetch({
    'GET /api/suppliers': () => json(pivotSuppliers),
    'GET /api/meta': () => json(meta),
    'GET /api/suppliers/3/extraction-rules': () =>
      json([extraction(), extraction({ id: 6, rule_name: 'Net', sort_order: 3 })]),
    'GET /api/suppliers/3/allocation-rules': () => json([allocation()]),
    'GET /api/suppliers/4/extraction-rules': () => json([]),
    'GET /api/suppliers/4/allocation-rules': () => json([]),
    'POST /api/suppliers/3/allocation-rules/validation': () => json([]),
    ...extra,
  });
}

describe('Admin › Tier C Rules', () => {
  it('only lists pivot_allocation suppliers', async () => {
    const fetchMock = tierCApi();
    renderRoute('/admin/tier-c');
    expect(await screen.findByRole('table', { name: 'Extraction rules' })).toBeInTheDocument();
    const call = fetchMock.mock.calls.find(([r]) => new URL(r.url).pathname === '/api/suppliers');
    expect(new URL(call?.[0].url ?? '').searchParams.get('job_type')).toBe('pivot_allocation');
  });

  it('edits a rule from its saved values and keeps untouched fields (F13)', async () => {
    let body: unknown = null;
    tierCApi({
      'PUT /api/suppliers/3/extraction-rules/5': async (request) => {
        body = await request.json();
        return json({
          rule: extraction({ cell_address: 'F9' }),
          renamed_allocation_refs: 0,
          notices: [],
        });
      },
    });
    const user = userEvent.setup();
    const { router } = renderRoute('/admin/tier-c?supplier=3');

    await user.click(await screen.findByRole('button', { name: 'Edit extraction rule Gross' }));
    expect(router.state.location.search).toBe('?supplier=3&extractionRule=5');

    const editor = screen.getByRole('textbox', { name: /Cell address/ });
    expect(editor).toHaveValue('E8');
    expect(screen.getByLabelText(/Sheet name/)).toHaveValue('Summary');
    expect(screen.getByLabelText(/Is sales amount/)).toBeChecked();

    await user.clear(editor);
    await user.type(editor, 'F9');
    await user.click(screen.getByRole('button', { name: 'Save extraction rule' }));

    await waitFor(() => {
      expect(body).toEqual({
        extraction_method: 'named_cell',
        rule_name: 'Gross',
        sheet_name: 'Summary',
        is_sales_amount: true,
        sort_order: 2,
        cell_address: 'F9',
      });
    });
  });

  it('switching rules loads that rule, and switching supplier clears the rule', async () => {
    tierCApi();
    const user = userEvent.setup();
    const { router } = renderRoute('/admin/tier-c?supplier=3&extractionRule=5');

    expect(await screen.findByRole('textbox', { name: /Cell address/ })).toHaveValue('E8');
    await user.click(screen.getByRole('combobox', { name: 'Rule to edit' }));
    await user.click(await screen.findByRole('option', { name: 'Net' }));
    expect(router.state.location.search).toBe('?supplier=3&extractionRule=6');
    expect(screen.getByLabelText(/Sort order/)).toHaveValue('3');

    await user.click(screen.getByRole('combobox', { name: 'Supplier' }));
    await user.click(await screen.findByRole('option', { name: 'Zeta Pivot' }));
    expect(router.state.location.search).toBe('?supplier=4');
    expect(await screen.findByText('No extraction rules yet')).toBeInTheDocument();
    expect(screen.getByText(/Add an extraction rule first/)).toBeInTheDocument();
  });

  it('shows the fields for the chosen method and validates them', async () => {
    const posted = vi.fn();
    tierCApi({ 'POST /api/suppliers/3/extraction-rules': posted });
    const user = userEvent.setup();
    renderRoute('/admin/tier-c?supplier=3&extractionRule=new');

    await user.type(await screen.findByLabelText(/Rule name/), 'Fees');
    await user.click(screen.getByRole('combobox', { name: 'Extraction method' }));
    await user.click(await screen.findByRole('option', { name: 'column_sum' }));
    expect(screen.queryByRole('textbox', { name: /Cell address/ })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/Column index/)).toBeInTheDocument();

    await user.click(screen.getByText('Letter'));
    await user.type(screen.getByLabelText(/Column letter/), 'F1');
    await user.click(screen.getByRole('button', { name: 'Save extraction rule' }));
    expect(await screen.findByText('Enter a column letter (A–ZZZ)')).toBeInTheDocument();
    expect(posted).not.toHaveBeenCalled();
  });

  it('warns live about allocation totals and saves the rule', async () => {
    let body: unknown = null;
    tierCApi({
      'POST /api/suppliers/3/allocation-rules/validation': async (request) => {
        const draft = (await request.json()) as { allocation_value: number | null };
        return json(
          (draft.allocation_value ?? 0) > 40
            ? [
                {
                  level: 'warning',
                  message: "Active percent allocations for 'Gross' now sum to 110.0% (>100%).",
                },
              ]
            : [],
        );
      },
      'POST /api/suppliers/3/allocation-rules': async (request) => {
        body = await request.json();
        return json(allocation({ id: 9, allocation_value: 50 }), 201);
      },
      'GET /api/members': () =>
        json({
          items: [
            {
              id: 12,
              name: 'St Luke',
              entity_code: 'PEC-12',
              premier_entity_code: 'PEC-12',
              alliant_entity_code: null,
              account_id: null,
              billing_city: null,
              billing_state: null,
              billing_zip: null,
              is_active: false,
            },
          ],
          total: 1,
        }),
    });
    const user = userEvent.setup();
    const { router } = renderRoute('/admin/tier-c?supplier=3&allocationRule=new');

    const form = (await screen.findByRole('button', { name: 'Save allocation rule' })).closest(
      'form',
    );
    if (!form) throw new Error('No allocation form');
    const scope = within(form);
    await user.type(scope.getByRole('combobox', { name: /Member/ }), 'Luke');
    await user.click(await screen.findByRole('option', { name: /St Luke/ }));
    await user.type(scope.getByLabelText(/Allocation \(%\)/), '50');
    expect(await scope.findByText(/now sum to 110.0%/)).toBeInTheDocument();

    await user.click(scope.getByRole('button', { name: 'Save allocation rule' }));
    await waitFor(() => {
      expect(body).toEqual({
        extraction_rule_name: 'Gross',
        member_id: 12,
        allocation_type: 'percent',
        allocation_value: 50,
        contract_number: null,
        product_line: null,
        is_active: true,
      });
    });
    await waitFor(() => {
      expect(router.state.location.search).toBe('?supplier=3&allocationRule=9');
    });
  });

  it('explains why a referenced extraction rule cannot be deleted', async () => {
    tierCApi({
      'DELETE /api/suppliers/3/extraction-rules/5': () =>
        json(
          problem(
            409,
            "Cannot delete: 1 allocation rule(s) reference 'Gross'. Delete those first.",
          ),
          409,
        ),
    });
    const user = userEvent.setup();
    renderRoute('/admin/tier-c?supplier=3&extractionRule=5');

    await user.click(await screen.findByRole('button', { name: 'Delete extraction rule' }));
    await user.click(await screen.findByRole('button', { name: 'Delete' }));
    expect(await screen.findByText(/Cannot delete: 1 allocation rule/)).toBeInTheDocument();
  });
});
