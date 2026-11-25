import { notifications } from '@mantine/notifications';
import { useMutation, useQueryClient } from '@tanstack/react-query';

import { api, unwrap } from '@/api/client';
import type { AliasScope, Schemas } from '@/api/types';
import { pluralize } from '@/lib/format';
import { queryKeys } from '@/queries/keys';
import { useAppStore } from '@/stores';

type ReviewItem = Schemas['ReviewItem'];

export type ReviewDecision =
  | { kind: 'approve'; memberId: number; createAlias: boolean; aliasScope: AliasScope }
  | { kind: 'dismiss' }
  | { kind: 'not_member' };

/**
 * Approve, dismiss, or mark one review item as not a member. The card leaves the queue immediately
 * (optimistic); on failure the queue is refetched, which brings it back
 * without undoing other cards' decisions.
 */
export function useReviewDecision(item: ReviewItem) {
  const queryClient = useQueryClient();
  const discardReviewDraft = useAppStore((s) => s.discardReviewDraft);
  const itemsKey = queryKeys.jobs.reviewItems(item.job_id);

  return useMutation({
    mutationFn: (decision: ReviewDecision) => {
      const path = { params: { path: { item_id: item.id } } };
      switch (decision.kind) {
        case 'approve':
          return unwrap(
            api.POST('/api/review-items/{item_id}/approval', {
              ...path,
              body: {
                member_id: decision.memberId,
                create_alias: decision.createAlias,
                alias_scope: decision.aliasScope,
              },
            }),
          );
        case 'dismiss':
          return unwrap(api.POST('/api/review-items/{item_id}/dismissal', path));
        case 'not_member':
          return unwrap(api.POST('/api/review-items/{item_id}/not-member', { ...path, body: {} }));
      }
    },
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: itemsKey });
      queryClient.setQueryData<ReviewItem[]>(itemsKey, (items) =>
        items?.filter((i) => i.id !== item.id),
      );
    },
    onSuccess: async (result, decision) => {
      discardReviewDraft(item.id);
      queryClient.setQueryData(queryKeys.jobs.detail(result.job.id), result.job);
      const customer = item.customer_name_sample ?? 'customer';
      const messages: Record<ReviewDecision['kind'], string> = {
        approve: `Approved “${customer}”: updated ${pluralize(result.updated_rows, 'row')}, output re-aggregated.`,
        dismiss: `Dismissed “${customer}”: it stays on the Unmatched tab.`,
        not_member: `Marked “${customer}” as not a member: later files won't send it to review.`,
      };
      notifications.show({ color: 'green', message: messages[decision.kind] });
      const savesAlias =
        decision.kind === 'approve' && decision.createAlias && decision.aliasScope !== 'job_only';
      await Promise.all([
        // Job lists carry pending counts. Not jobs.all: refetching this queue now could
        // briefly bring back cards whose decisions are still in flight.
        queryClient.invalidateQueries({ queryKey: queryKeys.jobs.lists }),
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }),
        savesAlias && queryClient.invalidateQueries({ queryKey: queryKeys.aliases.all }),
      ]);
    },
    onError: () => queryClient.invalidateQueries({ queryKey: itemsKey }),
  });
}
