import type { Job } from '@/queries/jobs';

/** Badge colour per job status. */
export function statusColor(status: Job['status']): string {
  switch (status) {
    case 'completed':
      return 'green';
    case 'needs_review':
      return 'orange';
    case 'failed':
      return 'red';
    default:
      return 'slate';
  }
}

/** Preview rows have no natural key (one PEC can repeat, see F17); key them by position. */
export const withRowKeys = <T extends object>(rows: T[]) =>
  rows.map((row, index) => ({ ...row, rowKey: index }));
