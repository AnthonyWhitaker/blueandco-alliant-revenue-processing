import { renderHook } from '@testing-library/react';
import { act } from 'react';

import { useActiveImport, useReviewDraft } from './hooks';
import { EMPTY_REVIEW_DRAFT, resolveReviewMemberId } from './reviewSlice';

import { migratePersistedState, PERSIST_KEY, PERSIST_VERSION, useAppStore } from '.';

const store = () => useAppStore.getState();
const suggestion = { member_id: 7 };

describe('review slice', () => {
  it('starts from the legacy defaults and uses the engine suggestion', () => {
    expect(store().reviewDrafts[1]).toBeUndefined();
    expect(resolveReviewMemberId(EMPTY_REVIEW_DRAFT, suggestion)).toBe(7);
    expect(resolveReviewMemberId(EMPTY_REVIEW_DRAFT, null)).toBeNull();
  });

  it('keeps alias choices when the member selection changes', () => {
    store().setReviewAliasScope(1, 'global');
    store().setReviewCreateAlias(1, false);
    store().pickReviewMember(1, { id: 42, label: 'Mercy General' });
    const draft = store().reviewDrafts[1];
    expect(draft).toMatchObject({ createAlias: false, aliasScope: 'global' });
    expect(draft && resolveReviewMemberId(draft, suggestion)).toBe(42);
  });

  it('disables approval after "none of these" until a facility is created', () => {
    store().rejectReviewCandidates(1);
    const rejected = store().reviewDrafts[1];
    expect(rejected && resolveReviewMemberId(rejected, suggestion)).toBeNull();

    store().recordCreatedMember(1, { id: 99, label: 'New Clinic' });
    expect(store().reviewDrafts[1]?.selection).toEqual({
      kind: 'picked',
      member: { id: 99, label: 'New Clinic' },
      created: true,
    });
  });

  it('keeps drafts per item and discards one without touching the others', () => {
    store().pickReviewMember(1, { id: 42, label: 'A' });
    store().pickReviewMember(2, { id: 43, label: 'B' });
    store().discardReviewDraft(1);
    expect(Object.keys(store().reviewDrafts)).toEqual(['2']);
  });

  it('returns a stable default snapshot for items without a draft', () => {
    const { result, rerender } = renderHook(() => useReviewDraft(5));
    const first = result.current;
    rerender();
    expect(result.current).toBe(first);
    act(() => {
      store().setReviewCreateAlias(5, false);
    });
    expect(result.current.createAlias).toBe(false);
  });
});

describe('column-map draft slice', () => {
  it('holds inferred mappings per supplier until discarded', () => {
    const mappings = [{ source_column: 'Cust Name', canonical_field: 'customer_name' as const }];
    store().setInferredMappings(3, mappings);
    store().setInferredMappings(4, []);
    store().discardInferredMappings(4);
    expect(store().inferredMappings).toEqual({ 3: mappings });
  });
});

describe('imports slice and persistence', () => {
  it('persists only the active import ids, to sessionStorage', () => {
    store().trackImport('ledger_import', 'task-1');
    store().pickReviewMember(1, { id: 42, label: 'A' });
    const saved = JSON.parse(sessionStorage.getItem(PERSIST_KEY) ?? 'null') as unknown;
    expect(saved).toEqual({
      state: { activeImports: { ledger_import: 'task-1' } },
      version: PERSIST_VERSION,
    });
  });

  it('forgets an import', () => {
    const { result } = renderHook(() => useActiveImport('member_import'));
    act(() => {
      store().trackImport('member_import', 'task-2');
    });
    expect(result.current).toBe('task-2');
    act(() => {
      store().forgetImport('member_import');
    });
    expect(result.current).toBeNull();
  });

  it('drops persisted state from an unknown version or with the wrong shape', () => {
    const valid = { activeImports: { member_import: 'task-3' } };
    expect(migratePersistedState(valid, PERSIST_VERSION)).toEqual(valid);
    expect(migratePersistedState(valid, 0)).toEqual({ activeImports: {} });
    expect(migratePersistedState({ activeImports: { member_import: 5 } }, 1)).toEqual({
      activeImports: {},
    });
    expect(migratePersistedState(null, 1)).toEqual({ activeImports: {} });
  });
});
