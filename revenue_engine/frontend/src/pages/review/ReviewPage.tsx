import { Alert, Anchor, Skeleton, Stack, Text, Title } from '@mantine/core';
import { IconCircleCheck } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';

import { EmptyState } from '@/components/EmptyState';
import { JobSelect } from '@/components/JobSelect';
import { PageHeader } from '@/components/PageHeader';
import { QueryError } from '@/components/QueryError';
import { useSelectedJob } from '@/hooks/useSelectedJob';
import { pluralize } from '@/lib/format';
import { routes, searchParamNames } from '@/lib/routes';
import { jobsQuery, reviewItemsQuery, type Job } from '@/queries/jobs';

import { ReviewCard } from './ReviewCard';

const describeJob = (j: Job) =>
  `Job #${j.id} — ${j.supplier_name} — ${j.original_file_name} (${String(j.pending_review_count)} pending)`;

function ReviewQueue({ jobId }: { jobId: number }) {
  const items = useQuery(reviewItemsQuery(jobId));

  if (items.error) {
    return (
      <QueryError
        title={`Couldn't load the review queue for job #${jobId}`}
        error={items.error}
        onRetry={() => void items.refetch()}
      />
    );
  }
  if (items.isPending) {
    return (
      <Stack gap="md" aria-busy>
        <Skeleton height={28} width={360} />
        <Skeleton height={320} />
        <Skeleton height={320} />
      </Stack>
    );
  }
  if (items.data.length === 0) {
    return (
      <Alert
        color="green"
        icon={<IconCircleCheck />}
        title={`Nothing left to review for job #${jobId}`}
      >
        <Anchor component={Link} to={`${routes.export}?${searchParamNames.job}=${jobId}`} inherit>
          Preview and download it on the Export page
        </Anchor>
        .
      </Alert>
    );
  }
  return (
    <Stack gap="md">
      <div>
        <Title order={2}>
          {pluralize(items.data.length, 'unmatched customer')}, highest fee first
        </Title>
        <Text size="sm" c="dimmed">
          Resolve each one. Approved matches update the output automatically.
        </Text>
      </div>
      {items.data.map((item) => (
        <ReviewCard key={item.id} item={item} />
      ))}
    </Stack>
  );
}

export function ReviewPage() {
  const jobs = useQuery(jobsQuery({ has_pending_reviews: true }));
  const [jobId, setJobId] = useSelectedJob(jobs.data);

  return (
    <>
      <PageHeader
        title="Review"
        description="Resolve unmatched customers, highest revenue impact first."
      />
      {jobs.error ? (
        <QueryError
          title="Couldn't load jobs"
          error={jobs.error}
          onRetry={() => void jobs.refetch()}
        />
      ) : jobs.isPending ? (
        <Skeleton height={60} maw={720} />
      ) : jobs.data.length === 0 && jobId === null ? (
        <EmptyState title="No pending reviews">All customers are matched.</EmptyState>
      ) : (
        <Stack gap="lg">
          <JobSelect
            label="Job to review"
            jobs={jobs.data}
            value={jobId}
            onChange={(id) => {
              setJobId(id);
            }}
            describe={describeJob}
          />
          {jobId !== null && <ReviewQueue key={jobId} jobId={jobId} />}
        </Stack>
      )}
    </>
  );
}
