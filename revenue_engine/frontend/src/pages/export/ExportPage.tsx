import {
  Accordion,
  Alert,
  Anchor,
  Badge,
  Button,
  Code,
  Group,
  Paper,
  SimpleGrid,
  Skeleton,
  Stack,
  Tabs,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconDownload,
  IconInfoCircle,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';

import { api, ApiError, unwrap } from '@/api/client';
import { DataTable } from '@/components/DataTable';
import { EmptyState } from '@/components/EmptyState';
import { JobSelect } from '@/components/JobSelect';
import { PageHeader } from '@/components/PageHeader';
import { QueryError } from '@/components/QueryError';
import { StatCard } from '@/components/StatCard';
import { useSelectedJob } from '@/hooks/useSelectedJob';
import { fileNameFromDisposition, saveBlob } from '@/lib/download';
import { statusColor, withRowKeys } from '@/lib/jobs';
import { formatCurrency, formatDateTime, formatInteger, pluralize } from '@/lib/format';
import { routes, searchParamNames } from '@/lib/routes';
import {
  EXPORTABLE_STATUSES,
  jobExceptionsQuery,
  jobPreviewQuery,
  jobQuery,
  jobsQuery,
  type Job,
} from '@/queries/jobs';
import { queryKeys } from '@/queries/keys';

import {
  jdeMappingColumns,
  unmatchedColumns,
  uploadColumns,
  validationColumns,
} from './previewColumns';

const PREVIEW_HEIGHT = 480;

const describeJob = (j: Job) =>
  `Job #${j.id} — ${j.supplier_name} — ${j.original_file_name} [${j.status}]`;

function PaymentId({ job }: { job: Job }) {
  const queryClient = useQueryClient();
  const [value, setValue] = useState('');
  const save = useMutation({
    mutationFn: (paymentId: string) =>
      unwrap(
        api.PATCH('/api/jobs/{job_id}', {
          params: { path: { job_id: job.id } },
          body: { payment_id: paymentId },
        }),
      ),
    onSuccess: async (updated) => {
      queryClient.setQueryData(queryKeys.jobs.detail(updated.id), updated);
      notifications.show({ color: 'green', message: 'Payment ID saved.' });
      setValue('');
      await queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
    },
  });

  if (job.payment_id) {
    return (
      <Alert color="ledger" variant="light" icon={<IconInfoCircle />}>
        Payment ID: <Code>{job.payment_id}</Code>
      </Alert>
    );
  }
  const trimmed = value.trim();
  return (
    <Paper p="md">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          if (trimmed) save.mutate(trimmed);
        }}
      >
        <Group align="flex-end">
          <TextInput
            label="Payment ID"
            description="Not set yet. Enter it before downloading."
            value={value}
            onChange={(e) => {
              setValue(e.currentTarget.value);
            }}
            w={320}
          />
          <Button type="submit" disabled={!trimmed} loading={save.isPending}>
            Save Payment ID
          </Button>
        </Group>
      </form>
    </Paper>
  );
}

function SignOff({ job }: { job: Job }) {
  const queryClient = useQueryClient();
  const confirm = useMutation({
    mutationFn: () =>
      unwrap(api.POST('/api/jobs/{job_id}/confirmation', { params: { path: { job_id: job.id } } })),
    onSuccess: async (updated) => {
      queryClient.setQueryData(queryKeys.jobs.detail(updated.id), updated);
      notifications.show({ color: 'green', message: `Job #${updated.id} marked complete.` });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all }),
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }),
      ]);
    },
  });
  // A needs_review job with nothing left to review (e.g. Tier C) is signed off here (F8).
  if (job.status !== 'needs_review' || job.pending_review_count > 0) return null;
  return (
    <Alert color="orange" icon={<IconAlertTriangle />} title="This job needs sign-off">
      <Stack gap="xs" align="flex-start">
        <Text size="sm">
          There is nothing left to review, but the job was flagged for confirmation. Check the
          exceptions below, then confirm.
        </Text>
        <Button
          size="xs"
          color="orange"
          leftSection={<IconCircleCheck size={16} />}
          loading={confirm.isPending}
          onClick={() => {
            confirm.mutate();
          }}
        >
          Confirm job as complete
        </Button>
      </Stack>
    </Alert>
  );
}

function Exceptions({ job }: { job: Job }) {
  const exceptions = useQuery(jobExceptionsQuery(job.id));
  if (exceptions.error) {
    return (
      <QueryError
        title="Couldn't load exceptions"
        error={exceptions.error}
        onRetry={() => void exceptions.refetch()}
      />
    );
  }
  const rows = exceptions.data ?? [];
  if (rows.length === 0) return null;
  const errors = rows.filter((e) => e.severity === 'error').length;
  const warnings = rows.filter((e) => e.severity === 'warning').length;
  const parts = [pluralize(rows.length, 'exception') + ' logged for this job'];
  if (errors) parts.push(pluralize(errors, 'error'));
  if (warnings) parts.push(pluralize(warnings, 'warning'));

  return (
    <Accordion
      variant="contained"
      defaultValue={job.status === 'needs_review' ? 'exceptions' : null}
      key={job.id}
    >
      <Accordion.Item value="exceptions">
        <Accordion.Control
          icon={<IconAlertTriangle size={18} color="var(--mantine-color-orange-6)" />}
        >
          {parts.join(' — ')}
        </Accordion.Control>
        <Accordion.Panel>
          <Text size="sm" c="dimmed" mb="sm">
            Review these before downloading, especially for aggregate/pivot-workpaper (Tier C)
            suppliers: a warning can mean an extracted total or allocation couldn&apos;t be fully
            verified.
          </Text>
          <DataTable
            label="Job exceptions"
            records={rows}
            columns={[
              { accessor: 'exception_type', title: 'Type' },
              { accessor: 'message', title: 'Message' },
              {
                accessor: 'severity',
                title: 'Severity',
                render: (e) => (
                  <Badge
                    color={
                      e.severity === 'error' ? 'red' : e.severity === 'warning' ? 'orange' : 'slate'
                    }
                  >
                    {e.severity}
                  </Badge>
                ),
              },
              { accessor: 'status', title: 'Status' },
              {
                accessor: 'created_at',
                title: 'Logged',
                render: (e) => formatDateTime(e.created_at),
              },
            ]}
          />
        </Accordion.Panel>
      </Accordion.Item>
    </Accordion>
  );
}

function OutputPreview({ job }: { job: Job }) {
  const preview = useQuery(jobPreviewQuery(job.id));
  if (preview.error) {
    return (
      <QueryError
        title="Couldn't build the preview"
        error={preview.error}
        onRetry={() => void preview.refetch()}
      />
    );
  }
  const data = preview.data;
  const count = (n: number | undefined) => (n === undefined ? '' : ` (${formatInteger(n)})`);
  return (
    <Tabs defaultValue="upload" keepMounted={false}>
      <Tabs.List>
        <Tabs.Tab value="upload">Upload{count(data?.upload.length)}</Tabs.Tab>
        <Tabs.Tab value="validation">Validation{count(data?.validation.length)}</Tabs.Tab>
        <Tabs.Tab value="unmatched">Unmatched{count(data?.unmatched.length)}</Tabs.Tab>
        <Tabs.Tab value="jde">JDE Mapping{count(data?.jde_mapping.length)}</Tabs.Tab>
      </Tabs.List>
      <Tabs.Panel value="upload" pt="sm">
        <Text size="xs" c="dimmed" mb="xs">
          Salesforce Facility Revenue upload format.
        </Text>
        <DataTable
          label="Upload tab preview"
          fetching={preview.isPending}
          records={data && withRowKeys(data.upload)}
          columns={uploadColumns}
          idAccessor="rowKey"
          maxHeight={PREVIEW_HEIGHT}
          noRecordsText="No output rows"
        />
      </Tabs.Panel>
      <Tabs.Panel value="validation" pt="sm">
        <Text size="xs" c="dimmed" mb="xs">
          Unique matched customers.
        </Text>
        <DataTable
          label="Validation tab preview"
          fetching={preview.isPending}
          records={data && withRowKeys(data.validation)}
          columns={validationColumns}
          idAccessor="rowKey"
          maxHeight={PREVIEW_HEIGHT}
          noRecordsText="No matched customers"
        />
      </Tabs.Panel>
      <Tabs.Panel value="unmatched" pt="sm">
        {data && data.unmatched.length > 0 && (
          <Alert color="orange" mb="sm" icon={<IconAlertTriangle />}>
            {pluralize(data.unmatched.length, 'unmatched row')}.{' '}
            {job.pending_review_count > 0 && (
              <Anchor
                component={Link}
                to={`${routes.review}?${searchParamNames.job}=${job.id}`}
                inherit
              >
                Resolve them on the Review page
              </Anchor>
            )}
          </Alert>
        )}
        <DataTable
          label="Unmatched tab preview"
          fetching={preview.isPending}
          records={data?.unmatched}
          columns={unmatchedColumns}
          idAccessor="row_number"
          maxHeight={PREVIEW_HEIGHT}
          noRecordsText="Every row matched"
        />
      </Tabs.Panel>
      <Tabs.Panel value="jde" pt="sm">
        <Text size="xs" c="dimmed" mb="xs">
          JDE number to entity code mappings.
        </Text>
        <DataTable
          label="JDE Mapping tab preview"
          fetching={preview.isPending}
          records={data && withRowKeys(data.jde_mapping)}
          columns={jdeMappingColumns}
          idAccessor="rowKey"
          maxHeight={PREVIEW_HEIGHT}
          noRecordsText="No JDE mappings"
        />
      </Tabs.Panel>
    </Tabs>
  );
}

function Download({ job }: { job: Job }) {
  const download = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.GET('/api/jobs/{job_id}/export', {
        params: { path: { job_id: job.id } },
        parseAs: 'blob',
      });
      if (!response.ok || !data) throw ApiError.fromResponse(response, error);
      return {
        blob: data,
        name: fileNameFromDisposition(
          response.headers.get('Content-Disposition'),
          `Job ${job.id} Processed.xlsx`,
        ),
      };
    },
    onSuccess: ({ blob, name }) => {
      saveBlob(blob, name);
    },
  });
  return (
    <Group>
      <Button
        leftSection={<IconDownload size={16} />}
        loading={download.isPending}
        onClick={() => {
          download.mutate();
        }}
      >
        Download workbook
      </Button>
      <Text size="sm" c="dimmed">
        4-tab XLSX: Upload, Validation, Unmatched, JDE Mapping.
      </Text>
    </Group>
  );
}

function JobExport({ jobId }: { jobId: number }) {
  const job = useQuery(jobQuery(jobId));
  if (job.error) {
    return (
      <QueryError
        title={`Couldn't load job #${jobId}`}
        error={job.error}
        onRetry={() => void job.refetch()}
      />
    );
  }
  const j = job.data;
  if (j && !EXPORTABLE_STATUSES.includes(j.status)) {
    return (
      <EmptyState title={`Job #${j.id} isn't ready to export`}>
        Its status is “{j.status}”. Only completed or needs-review jobs can be exported.
        {j.error_message && (
          <Code block mt="xs">
            {j.error_message}
          </Code>
        )}
      </EmptyState>
    );
  }
  return (
    <Stack gap="lg">
      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <StatCard
          label="Status"
          value={
            j && (
              <Badge size="lg" color={statusColor(j.status)} variant="light">
                {j.status.replace('_', ' ')}
              </Badge>
            )
          }
        />
        <StatCard label="Matched rows" value={j && formatInteger(j.matched_count)} />
        <StatCard label="Unmatched" value={j && formatInteger(j.unmatched_count)} />
        <StatCard label="Total revenue" value={j && formatCurrency(j.total_revenue)} />
      </SimpleGrid>
      {j ? (
        <>
          <PaymentId job={j} />
          <SignOff job={j} />
          <Exceptions job={j} />
          <Stack gap="sm">
            <Title order={2}>Output preview</Title>
            <OutputPreview job={j} />
          </Stack>
          <Stack gap="sm">
            <Title order={2}>Download</Title>
            <Download job={j} />
          </Stack>
        </>
      ) : (
        <Skeleton height={240} />
      )}
    </Stack>
  );
}

export function ExportPage() {
  const jobs = useQuery(jobsQuery({ status: EXPORTABLE_STATUSES }));
  const [jobId, setJobId] = useSelectedJob(jobs.data);

  return (
    <>
      <PageHeader
        title="Export"
        description="Preview the output and download the Salesforce workbook."
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
        <EmptyState title="No processed jobs yet">
          <Anchor component={Link} to={routes.upload} inherit>
            Upload a supplier file
          </Anchor>{' '}
          to process it.
        </EmptyState>
      ) : (
        <Stack gap="lg">
          <JobSelect
            label="Job"
            jobs={jobs.data}
            value={jobId}
            onChange={(id) => {
              setJobId(id);
            }}
            describe={describeJob}
          />
          {jobId !== null && <JobExport key={jobId} jobId={jobId} />}
        </Stack>
      )}
    </>
  );
}
