import type { StateCreator } from 'zustand';

import type { AliasScope } from '@/api/types';

import type { AppState, AppStoreMutators } from './types';

/** A member as the reviewer picked it: the id plus the label they saw in the picker. */
export interface PickedMember {
  id: number;
  label: string;
}

/**
 * What the reviewer has decided so far for one item.
 * - `suggested`: untouched, so the engine's top suggestion applies (if any).
 * - `none`: they rejected every candidate; the card shows the new-facility form.
 * - `picked`: an existing member from search, or one they just created.
 */
export type ReviewSelection =
  | { kind: 'suggested' }
  | { kind: 'none' }
  | { kind: 'picked'; member: PickedMember; created: boolean };

export interface ReviewDraft {
  selection: ReviewSelection;
  createAlias: boolean;
  aliasScope: AliasScope;
}

/** Legacy defaults: alias checkbox on, scope "supplier". */
export const EMPTY_REVIEW_DRAFT: ReviewDraft = {
  selection: { kind: 'suggested' },
  createAlias: true,
  aliasScope: 'supplier',
};

/**
 * Review slice — unsaved decisions on review cards, keyed by review item id.
 *
 * Owns: per-item member selection, the "facility just created" wizard step,
 * and the alias checkbox/scope.
 * Written by: the Review page's item cards.
 * Replaces: `new_member_id_{item_id}`, `choice_{id}`, `alias_{id}`, `scope_{id}`.
 * Lives in the store (not card state) so a decision survives leaving the
 * page, e.g. to check Admin > Members, as `new_member_id_*` did in Streamlit.
 */
export interface ReviewSlice {
  reviewDrafts: Record<number, ReviewDraft>;
  pickReviewMember: (itemId: number, member: PickedMember) => void;
  recordCreatedMember: (itemId: number, member: PickedMember) => void;
  rejectReviewCandidates: (itemId: number) => void;
  setReviewCreateAlias: (itemId: number, createAlias: boolean) => void;
  setReviewAliasScope: (itemId: number, aliasScope: AliasScope) => void;
  /** Call once the item is approved or dismissed. */
  discardReviewDraft: (itemId: number) => void;
}

export const createReviewSlice: StateCreator<AppState, AppStoreMutators, [], ReviewSlice> = (
  set,
) => {
  const patch = (itemId: number, change: Partial<ReviewDraft>, action: string) => {
    set(
      (state) => ({
        reviewDrafts: {
          ...state.reviewDrafts,
          [itemId]: { ...(state.reviewDrafts[itemId] ?? EMPTY_REVIEW_DRAFT), ...change },
        },
      }),
      undefined,
      `review/${action}`,
    );
  };

  return {
    reviewDrafts: {},
    pickReviewMember: (itemId, member) => {
      patch(itemId, { selection: { kind: 'picked', member, created: false } }, 'pickMember');
    },
    recordCreatedMember: (itemId, member) => {
      patch(
        itemId,
        { selection: { kind: 'picked', member, created: true } },
        'recordCreatedMember',
      );
    },
    rejectReviewCandidates: (itemId) => {
      patch(itemId, { selection: { kind: 'none' } }, 'rejectCandidates');
    },
    setReviewCreateAlias: (itemId, createAlias) => {
      patch(itemId, { createAlias }, 'setCreateAlias');
    },
    setReviewAliasScope: (itemId, aliasScope) => {
      patch(itemId, { aliasScope }, 'setAliasScope');
    },
    discardReviewDraft: (itemId) => {
      set(
        (state) => {
          const { [itemId]: _discarded, ...rest } = state.reviewDrafts;
          return { reviewDrafts: rest };
        },
        undefined,
        'review/discardDraft',
      );
    },
  };
};

/** The member an approval would send, or null when Approve must stay disabled. */
export function resolveReviewMemberId(
  draft: ReviewDraft,
  suggestion: { member_id: number } | null | undefined,
): number | null {
  switch (draft.selection.kind) {
    case 'suggested':
      return suggestion?.member_id ?? null;
    case 'none':
      return null;
    case 'picked':
      return draft.selection.member.id;
  }
}
