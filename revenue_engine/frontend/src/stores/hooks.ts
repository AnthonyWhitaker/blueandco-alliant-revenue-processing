/** Narrow selector hooks, so pages subscribe to exactly what they render. */
import type { TaskKind } from '@/api/types';

import { useAppStore } from '.';
import { EMPTY_REVIEW_DRAFT, type ReviewDraft } from './reviewSlice';

export function useReviewDraft(itemId: number): ReviewDraft {
  // The shared default keeps the snapshot stable when there is no draft yet.
  return useAppStore((s) => s.reviewDrafts[itemId] ?? EMPTY_REVIEW_DRAFT);
}

export function useInferredMappings(supplierId: number) {
  return useAppStore((s) => s.inferredMappings[supplierId] ?? null);
}

export function useActiveImport(kind: TaskKind): string | null {
  return useAppStore((s) => s.activeImports[kind] ?? null);
}
