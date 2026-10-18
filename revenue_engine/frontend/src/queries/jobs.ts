import { queryOptions } from '@tanstack/react-query';

import { api, unwrap } from '@/api/client';
import type { QueryParams, Schemas } from '@/api/types';

import { queryKeys } from './keys';

export type Job = Schemas['Job'];
export type JobStatus = Job['status'];

/** Statuses the pipeline is still working through; the job is polled until it leaves them. */
export const IN_PROGRESS_STATUSES: readonly JobStatus[] = [
  'uploaded',
  'validating',
  'validated',
  'processing',
];
/** Same list as core.jobs.EXPORTABLE_STATUSES. */
export const EXPORTABLE_STATUSES: JobStatus[] = ['completed', 'needs_review'];

export const isInProgress = (status: JobStatus) => IN_PROGRESS_STATUSES.includes(status);

export const jobsQuery = (filters: QueryParams<'/api/jobs'>) =>
  queryOptions({
    queryKey: queryKeys.jobs.list(filters),
    queryFn: () => unwrap(api.GET('/api/jobs', { params: { query: filters } })),
  });

export const jobQuery = (jobId: number) =>
  queryOptions({
    queryKey: queryKeys.jobs.detail(jobId),
    queryFn: () => unwrap(api.GET('/api/jobs/{job_id}', { params: { path: { job_id: jobId } } })),
    // Poll while the background worker is processing the file.
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && isInProgress(status) ? 1500 : false;
    },
    // Keep following the job while the user is in another tab; polling stops when it finishes.
    refetchIntervalInBackground: true,
  });

export const jobExceptionsQuery = (jobId: number) =>
  queryOptions({
    queryKey: queryKeys.jobs.exceptions(jobId),
    queryFn: () =>
      unwrap(api.GET('/api/jobs/{job_id}/exceptions', { params: { path: { job_id: jobId } } })),
  });

export const jobPreviewQuery = (jobId: number) =>
  queryOptions({
    queryKey: queryKeys.jobs.preview(jobId),
    queryFn: () =>
      unwrap(api.GET('/api/jobs/{job_id}/preview', { params: { path: { job_id: jobId } } })),
  });

export const reviewItemsQuery = (jobId: number) =>
  queryOptions({
    queryKey: queryKeys.jobs.reviewItems(jobId),
    queryFn: () =>
      unwrap(api.GET('/api/jobs/{job_id}/review-items', { params: { path: { job_id: jobId } } })),
  });

export const fileNameInferenceQuery = (fileName: string) =>
  queryOptions({
    queryKey: queryKeys.fileNameInference(fileName),
    queryFn: () =>
      unwrap(api.GET('/api/file-name-inference', { params: { query: { file_name: fileName } } })),
    staleTime: Infinity,
  });
