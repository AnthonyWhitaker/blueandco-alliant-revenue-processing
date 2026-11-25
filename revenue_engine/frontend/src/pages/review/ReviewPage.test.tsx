import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { Schemas } from '@/api/types';
import { useAppStore } from '@/stores';
import { job, member, problem } from '@/test/fixtures';
import { json, renderRoute, stubFetch } from '@/test/render';

afterEach(() => {
  vi.unstubAllGlobals();
});

type ReviewItem = Schemas['ReviewItem'];

const item = (overrides: Partial<ReviewItem> = {}): ReviewItem => ({
  id: 1,
  job_id: 42,
  jde_number: 'J-1',
  customer_name_sample: 'MERCY GEN HOSP',
  city: 'Omaha',
  state: 'NE',
  zip: '68102',
  total_fee: 120,
  total_sales: 4000,
  affected_row_count: 3,
  status: 'pending',
  suggestion: {
    member_id: 10,
    name: 'Mercy General',
    entity_code: 'PEC-10',
    city: 'Omaha',
    state: 'NE',
    score: 86,
  },
  review_reason: null,
  candidates: [
    {
      member_id: 10,
      name: 'Mercy General',
      entity_code: 'PEC-10',
      street: '1 Main St',
      city: 'Omaha',
      state: 'NE',
      zip: '68102',
      score: 86,
      matched: ['name', 'zip'],
      via: 'name',
    },
    {
      member_id: 12,
      name: 'Mercy General - Dock',
      entity_code: 'PEC-10',
      street: '9 Dock Rd',
      city: 'Omaha',
      state: 'NE',
      zip: '68102',
      score: 72,
      matched: ['zip', 'jde'],
      via: 'jde',
    },
  ],
  ...overrides,
});

const pendingJob = job({ status: 'needs_review', pending_review_count: 2 });

function reviewApi(items: ReviewItem[], extra: Parameters<typeof stubFetch>[0] = {}) {
  let queue = items;
  const decide = (id: number) => {
    queue = queue.filter((i) => i.id !== id);
    return json({ updated_rows: 3, job: job({ pending_review_count: queue.length }) });
  };
  const approvals: unknown[] = [];
  const fetchMock = stubFetch({
    'GET /api/jobs': () => json([pendingJob]),
    'GET /api/jobs/42/review-items': () => json(queue),
    'GET /api/dashboard': () =>
      json({ active_members: 1, suppliers: 1, jobs: 1, pending_reviews: queue.length }),
    'GET /api/members': () =>
      json({ items: [member({ id: 11, name: 'Mercy Clinic', entity_code: 'PEC-11' })], total: 1 }),
    'POST /api/review-items/1/approval': async (request) => {
      approvals.push(await request.json());
      return decide(1);
    },
    'POST /api/review-items/2/approval': async (request) => {
      approvals.push(await request.json());
      return decide(2);
    },
    'POST /api/review-items/1/dismissal': () => decide(1),
    ...extra,
  });
  return { fetchMock, approvals };
}

const card = (name: string) => screen.getByRole('article', { name });

describe('Review page', () => {
  it('approves the engine suggestion with the default alias settings', async () => {
    const { approvals } = reviewApi([item()]);
    const user = userEvent.setup();
    const { router } = await renderRoute('/review');

    await waitFor(() => {
      expect(router.state.location.search).toBe('?job=42');
    });
    expect(await screen.findByText('1 unmatched customer, highest fee first')).toBeInTheDocument();
    const mercy = card('MERCY GEN HOSP');
    expect(within(mercy).getByText('Name 86')).toBeInTheDocument();

    await user.click(within(mercy).getByRole('button', { name: /Approve match/ }));
    expect(await screen.findByText(/Nothing left to review for job #42/)).toBeInTheDocument();
    expect(approvals).toEqual([{ member_id: 10, create_alias: true, alias_scope: 'supplier' }]);
  });

  it('approves a searched member with a job-only scope', async () => {
    const { approvals } = reviewApi([item()]);
    const user = userEvent.setup();
    await renderRoute('/review?job=42');

    const mercy = await screen.findByRole('article', { name: 'MERCY GEN HOSP' });
    await user.type(within(mercy).getByRole('combobox', { name: /different member/ }), 'Clinic');
    await user.click(await screen.findByRole('option', { name: /Mercy Clinic/ }));
    await user.click(within(mercy).getByLabelText(/This job only/));
    await user.click(within(mercy).getByRole('button', { name: /Approve match/ }));

    await waitFor(() => {
      expect(approvals).toEqual([{ member_id: 11, create_alias: true, alias_scope: 'job_only' }]);
    });
  });

  it('creates a new facility when nothing matches and approves it', async () => {
    let created: unknown = null;
    const { approvals } = reviewApi([item({ suggestion: null, candidates: [] })], {
      'POST /api/members': async (request) => {
        created = await request.json();
        return json(member({ id: 99, name: 'Mercy Gen Hosp', entity_code: null }), 201);
      },
    });
    const user = userEvent.setup();
    await renderRoute('/review?job=42');

    const mercy = await screen.findByRole('article', { name: 'MERCY GEN HOSP' });
    expect(within(mercy).getByText('No suggestion available')).toBeInTheDocument();
    const approve = within(mercy).getByRole('button', { name: /Approve match/ });
    expect(approve).toBeDisabled();

    // Prefilled from the unmatched row.
    const nameInput = within(mercy).getByLabelText(/Facility name/);
    expect(nameInput).toHaveValue('MERCY GEN HOSP');
    await user.clear(nameInput);
    await user.type(nameInput, 'Mercy Gen Hosp');
    await user.click(within(mercy).getByRole('button', { name: /Create facility/ }));

    await waitFor(() => {
      expect(approve).toBeEnabled();
    });
    expect(created).toEqual({
      name: 'Mercy Gen Hosp',
      premier_entity_code: null,
      city: 'Omaha',
      state: 'NE',
      zip: '68102',
    });
    expect(within(mercy).queryByLabelText(/Facility name/)).not.toBeInTheDocument();

    await user.click(approve);
    await waitFor(() => {
      expect(approvals).toEqual([{ member_id: 99, create_alias: true, alias_scope: 'supplier' }]);
    });
    expect(useAppStore.getState().reviewDrafts).toEqual({});
  });

  it('shows up to three candidates with the matching fields and approves the one picked', async () => {
    const { approvals } = reviewApi([item({ review_reason: 'jde_gpo_conflict' })]);
    const user = userEvent.setup();
    await renderRoute('/review?job=42');

    const mercy = await screen.findByRole('article', { name: 'MERCY GEN HOSP' });
    expect(within(mercy).getByText('JDE # and GPO ID disagree')).toBeInTheDocument();
    const dock = within(mercy).getByRole('listitem', { name: 'Mercy General - Dock' });
    expect(within(dock).getByText('Name 72')).toBeInTheDocument();
    expect(within(dock).getByText('JDE # matches')).toBeInTheDocument();
    expect(within(dock).getByText('ZIP matches')).toBeInTheDocument();
    const general = within(mercy).getByRole('listitem', { name: 'Mercy General' });
    expect(within(general).getByRole('button', { name: /Use Mercy General/ })).toHaveAttribute(
      'aria-pressed',
      'true',
    );

    await user.click(within(dock).getByRole('button', { name: /Use Mercy General - Dock/ }));
    await user.click(within(mercy).getByRole('button', { name: /Approve match/ }));
    await waitFor(() => {
      expect(approvals).toEqual([{ member_id: 12, create_alias: true, alias_scope: 'supplier' }]);
    });
  });

  it("says why a row the GPO key didn't verify is here", async () => {
    reviewApi([item({ review_reason: 'gpo_key_fallback', key_reason: 'account_not_in_key' })]);
    await renderRoute('/review?job=42');
    const mercy = await screen.findByRole('article', { name: 'MERCY GEN HOSP' });
    expect(
      within(mercy).getByText('Not verified by the GPO key: account not in the key'),
    ).toBeInTheDocument();
  });

  it('marks a customer as not a member', async () => {
    let marked = 0;
    reviewApi([item()], {
      'POST /api/review-items/1/not-member': () => {
        marked += 1;
        return json({ updated_rows: 3, job: job({ pending_review_count: 0 }) });
      },
    });
    const user = userEvent.setup();
    await renderRoute('/review?job=42');

    const mercy = await screen.findByRole('article', { name: 'MERCY GEN HOSP' });
    await user.click(within(mercy).getByRole('button', { name: /as not a member/ }));
    expect(await screen.findByText(/won't send it to review/)).toBeInTheDocument();
    expect(marked).toBe(1);
  });

  it('can go back to the suggestion after choosing "none of these"', async () => {
    const { approvals } = reviewApi([item()]);
    const user = userEvent.setup();
    await renderRoute('/review?job=42');

    const mercy = await screen.findByRole('article', { name: 'MERCY GEN HOSP' });
    await user.click(within(mercy).getByRole('button', { name: /None of these/ }));
    expect(within(mercy).getByLabelText(/Facility name/)).toBeInTheDocument();
    expect(within(mercy).getByRole('button', { name: /Approve match/ })).toBeDisabled();

    await user.click(within(mercy).getByRole('button', { name: /Use the suggestion/ }));
    await user.click(within(mercy).getByRole('button', { name: /Approve match/ }));
    await waitFor(() => {
      expect(approvals).toEqual([{ member_id: 10, create_alias: true, alias_scope: 'supplier' }]);
    });
  });

  it('removes a card at once and brings it back if the decision fails', async () => {
    reviewApi([item(), item({ id: 2, customer_name_sample: 'ST LUKE', total_fee: 50 })], {
      'POST /api/review-items/1/dismissal': () =>
        json(problem(409, 'Review item 1 was already decided.'), 409),
    });
    const user = userEvent.setup();
    await renderRoute('/review?job=42');

    const mercy = await screen.findByRole('article', { name: 'MERCY GEN HOSP' });
    await user.click(within(mercy).getByRole('button', { name: /Dismiss/ }));
    // Back after the failed call refetches the queue, with the error toast.
    expect(await screen.findByText('Review item 1 was already decided.')).toBeInTheDocument();
    expect(await screen.findByRole('article', { name: 'MERCY GEN HOSP' })).toBeInTheDocument();
    expect(card('ST LUKE')).toBeInTheDocument();
  });

  it('keeps a draft when leaving the page and coming back', async () => {
    reviewApi([item()]);
    const user = userEvent.setup();
    const { router } = await renderRoute('/review?job=42');

    const mercy = await screen.findByRole('article', { name: 'MERCY GEN HOSP' });
    await user.click(within(mercy).getByLabelText(/All suppliers/));
    await router.navigate('/admin/aliases');
    await router.navigate('/review?job=42');
    const again = await screen.findByRole('article', { name: 'MERCY GEN HOSP' });
    expect(within(again).getByLabelText(/All suppliers/)).toBeChecked();
  });

  it('says so when nothing is pending', async () => {
    stubFetch({ 'GET /api/jobs': () => json([]) });
    await renderRoute('/review');
    expect(await screen.findByText('No pending reviews')).toBeInTheDocument();
  });
});
