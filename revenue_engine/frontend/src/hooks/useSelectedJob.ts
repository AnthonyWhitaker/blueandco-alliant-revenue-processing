import { useEffect } from 'react';

import { searchParamNames } from '@/lib/routes';
import { idCodec, useSearchParamState } from '@/lib/searchParams';
import type { Job } from '@/queries/jobs';

/**
 * The selected job lives in `?job=`. With no param, the first job in the list
 * is selected (the legacy selectbox default), replacing the history entry so
 * Back still leaves the page.
 */
export function useSelectedJob(jobs: Job[] | undefined) {
  const [jobId, setJobId] = useSearchParamState(searchParamNames.job, idCodec);
  const first = jobs?.[0]?.id;
  useEffect(() => {
    if (jobId === null && first !== undefined) setJobId(first, { replace: true });
  }, [jobId, first, setJobId]);
  return [jobId, setJobId] as const;
}
