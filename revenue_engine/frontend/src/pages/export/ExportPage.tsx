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
  Table,
  Tabs,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconCircleCheck,
  IconDownload,
  IconFilePlus,
  IconInfoCircle,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router';

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
  jobFilesQuery,
  jobOutputsQuery,
  jobPreviewQuery,
  jobQuery,
  jobsQuery,
  type Job,
  type JobOutput,
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
        Payment ID: <Code>{job.payment_id}</Code> (for reference; the Upload template leaves Payment
        ID blank, to be filled in from the ledger afterwards)
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
            description="Optional, for reference. The Upload template leaves it blank."
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
              { accessor: 'message', title: 'Message', noWrap: false },
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

const OUTPUT_LABELS: Record<JobOutput['artifact_type'], string> = {
  salesforce_upload: 'Processed workbook',
  exception_report: 'Exceptions report',
  validation_report: 'Validation report',
  jde_mapping: 'JDE mapping',
};

function OutputDownload({ job, output }: { job: Job; output: JobOutput }) {
  const download = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.GET(
        '/api/jobs/{job_id}/outputs/{output_id}/content',
        { params: { path: { job_id: job.id, output_id: output.id } }, parseAs: 'blob' },
      );
      if (!response.ok || !data) throw ApiError.fromResponse(response, error);
      return {
        blob: data,
        name: fileNameFromDisposition(
          response.headers.get('Content-Disposition'),
          output.file_name,
        ),
      };
    },
    onSuccess: ({ blob, name }) => {
      saveBlob(blob, name);
    },
  });
  const button = (
    <Button
      size="xs"
      variant="light"
      leftSection={<IconDownload size={14} />}
      loading={download.isPending}
      disabled={!output.available}
      aria-label={`Download ${output.file_name}`}
      onClick={() => {
        download.mutate();
      }}
    >
      Download
    </Button>
  );
  return output.available ? (
    button
  ) : (
    <Tooltip label="No longer on disk (e.g. lost in a server restart). Generate a new version.">
      <span>{button}</span>
    </Tooltip>
  );
}

/** Stored output files. Processing writes the first version; nothing is ever overwritten. */
function Outputs({ job }: { job: Job }) {
  const queryClient = useQueryClient();
  const outputs = useQuery(jobOutputsQuery(job.id));
  const generate = useMutation({
    mutationFn: () =>
      unwrap(api.POST('/api/jobs/{job_id}/outputs', { params: { path: { job_id: job.id } } })),
    onSuccess: async () => {
      notifications.show({ color: 'green', message: 'New version written to the output folder.' });
      await queryClient.invalidateQueries({ queryKey: queryKeys.jobs.outputs(job.id) });
    },
  });
  return (
    <Stack gap="sm">
      <Group>
        <Button
          leftSection={<IconFilePlus size={16} />}
          loading={generate.isPending}
          onClick={() => {
            generate.mutate();
          }}
        >
          Generate new version
        </Button>
        <Text size="sm" c="dimmed">
          Writes a new processed workbook (Upload, Validation, Unmatched, JDE Mapping) and
          exceptions report from the job as it is now, e.g. after review decisions. Earlier versions
          are kept.
        </Text>
      </Group>
      {outputs.error ? (
        <QueryError
          title="Couldn't load the output files"
          error={outputs.error}
          onRetry={() => void outputs.refetch()}
        />
      ) : (
        <DataTable
          label="Output files"
          fetching={outputs.isPending}
          records={outputs.data}
          noRecordsText="No output files yet"
          columns={[
            {
              accessor: 'artifact_type',
              title: 'Type',
              render: (o) => OUTPUT_LABELS[o.artifact_type],
            },
            { accessor: 'file_name', title: 'File', noWrap: false },
            {
              accessor: 'created_at',
              title: 'Written',
              render: (o) => formatDateTime(o.created_at),
            },
            {
              accessor: 'id',
              title: '',
              textAlign: 'right',
              render: (o) => <OutputDownload job={job} output={o} />,
            },
          ]}
        />
      )}
    </Stack>
  );
}

/** Input = Upload tab + exceptions report, per amount. */
function ReconciliationSummary({ job }: { job: Job }) {
  const rec = job.reconciliation;
  if (!rec) return null;
  const rows = [
    {
      amount: 'Revenue (fee)',
      input: rec.input.revenue,
      output: rec.output_revenue,
      exceptions: rec.exceptions_revenue,
      difference: rec.revenue_difference,
    },
    ...(rec.volume_checked
      ? [
          {
            amount: 'Volume (sales)',
            input: rec.input.volume,
            output: rec.output_volume ?? 0,
            exceptions: rec.exceptions_volume,
            difference: rec.volume_difference ?? 0,
          },
        ]
      : []),
  ];
  return (
    <Alert
      color={rec.balanced ? 'green' : 'red'}
      icon={rec.balanced ? <IconCircleCheck /> : <IconAlertTriangle />}
      title={rec.balanced ? 'Totals reconcile' : 'Totals do not reconcile'}
    >
      <Table withRowBorders={false} verticalSpacing={2} fz="sm" className="tabular" maw={640}>
        <Table.Thead>
          <Table.Tr>
            <Table.Th />
            <Table.Th ta="right">Input</Table.Th>
            <Table.Th ta="right">Output</Table.Th>
            <Table.Th ta="right">Exceptions</Table.Th>
            <Table.Th ta="right">Difference</Table.Th>
          </Table.Tr>
        </Table.Thead>
        <Table.Tbody>
          {rows.map((r) => (
            <Table.Tr key={r.amount}>
              <Table.Td>{r.amount}</Table.Td>
              <Table.Td ta="right">{formatCurrency(r.input)}</Table.Td>
              <Table.Td ta="right">{formatCurrency(r.output)}</Table.Td>
              <Table.Td ta="right">{formatCurrency(r.exceptions)}</Table.Td>
              <Table.Td ta="right">{formatCurrency(r.difference)}</Table.Td>
            </Table.Tr>
          ))}
        </Table.Tbody>
      </Table>
      <Text size="xs" mt="xs">
        {formatInteger(rec.input.rows)} input rows: {formatInteger(rec.exception_rows)} on the
        exceptions report, the rest in {pluralize(rec.output_rows, 'Upload row')}.
        {!rec.volume_checked && ' Volume is left blank for this supplier, so it is not checked.'}
        {(rec.input.dropped_rows ?? 0) > 0 &&
          ` ${pluralize(rec.input.dropped_rows ?? 0, 'summary/total row')} left out of the input (listed on the exceptions report).`}
      </Text>
    </Alert>
  );
}

function SourceFiles({ job }: { job: Job }) {
  const files = useQuery({ ...jobFilesQuery(job.id), enabled: job.file_count > 1 });
  if (job.file_count <= 1 || !files.data) return null;
  return (
    <Text size="sm">
      Combined from {pluralize(job.file_count, 'file')}:{' '}
      {files.data
        .map((f) => `${f.original_file_name} (${formatInteger(f.row_count)} rows)`)
        .join(' · ')}
    </Text>
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
        <StatCard label="Rows in output" value={j && formatInteger(j.matched_count)} />
        <StatCard
          label="Exception rows"
          value={j && formatInteger(j.reconciliation?.exception_rows ?? j.unmatched_count)}
        />
        <StatCard label="Total revenue" value={j && formatCurrency(j.total_revenue)} />
      </SimpleGrid>
      {j ? (
        <>
          <SourceFiles job={j} />
          <ReconciliationSummary job={j} />
          <PaymentId job={j} />
          <SignOff job={j} />
          <Exceptions job={j} />
          <Stack gap="sm">
            <Title order={2}>Output preview</Title>
            <OutputPreview job={j} />
          </Stack>
          <Stack gap="sm">
            <Title order={2}>Output files</Title>
            <Outputs job={j} />
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
        description="Check the totals and exceptions, preview the output, and download the workbooks."
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
