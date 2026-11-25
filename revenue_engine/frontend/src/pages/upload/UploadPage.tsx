import {
  ActionIcon,
  Alert,
  Anchor,
  Badge,
  Button,
  Code,
  Fieldset,
  Group,
  Loader,
  NumberInput,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Skeleton,
  Stack,
  Text,
  TextInput,
  Title,
  Tooltip,
} from '@mantine/core';
import { schemaResolver, useForm } from '@mantine/form';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import {
  IconAlertTriangle,
  IconArrowBackUp,
  IconCircleCheck,
  IconPlayerPlay,
  IconTrash,
} from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, useRef, useState } from 'react';
import { Link } from 'react-router';
import { z } from 'zod';

import { api, formData, unwrap } from '@/api/client';
import type { Schemas } from '@/api/types';
import { DataTable } from '@/components/DataTable';
import { EmptyState } from '@/components/EmptyState';
import { FileListDropzone } from '@/components/FileDropzone';
import { PageHeader } from '@/components/PageHeader';
import { QueryError } from '@/components/QueryError';
import { StatCard } from '@/components/StatCard';
import { applyServerErrors, blankToNull } from '@/lib/forms';
import { formatCurrency, formatDateTime, formatInteger, formatText, pluralize } from '@/lib/format';
import { statusColor, statusLabel } from '@/lib/jobs';
import { routes, searchParamNames } from '@/lib/routes';
import { idCodec, useSearchParamState } from '@/lib/searchParams';
import {
  fileNameInferenceQuery,
  isInProgress,
  jobQuery,
  jobsQuery,
  type Job,
} from '@/queries/jobs';
import { queryKeys } from '@/queries/keys';
import { metaQuery } from '@/queries/reference';
import { extractionRulesQuery, suppliersQuery, type Supplier } from '@/queries/suppliers';

import { JobDetailsModal } from './JobDetails';

const RECENT_LIMIT = 20;
/** Same as core.jobs.UPLOAD_EXTENSIONS; /meta is the source once it has loaded. */
const DEFAULT_EXTENSIONS = ['.xlsx', '.xls', '.xlsm', '.csv'];

const jobLink = (page: string, jobId: number) => `${page}?${searchParamNames.job}=${jobId}`;

const uploadSchema = z.object({
  supplier_id: z.string({ error: 'Pick the supplier' }).min(1, 'Pick the supplier'),
  date_received: z.iso.date({ error: 'Enter the date received' }),
  timeframe: z
    .string()
    .trim()
    .min(1, 'Enter the quarter this payment represents')
    .regex(/^[1-4]Q\d{2}$/i, 'Use XQYY: quarter and 2-digit year, e.g. 1Q26'),
  manual_payment_id: z.string(),
});
type UploadValues = z.infer<typeof uploadSchema>;
/** Typed-in totals for a Tier C supplier's manual_entry rules, by rule name ('' until entered). */
type ManualTotals = Record<string, number | string>;

interface UploadFormProps {
  files: File[];
  suppliers: Supplier[];
  inference: Schemas['FileNameInference'] | null;
  onSubmitted: (job: Job) => void;
}

/** Mounted once per first picked file, so the filename guesses become the form's defaults. */
function UploadForm({ files, suppliers, inference, onSubmitted }: UploadFormProps) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const guessedSupplier = suppliers.find((s) => s.id === inference?.supplier_profile_id);
  const form = useForm<UploadValues>({
    mode: 'controlled',
    initialValues: {
      supplier_id: guessedSupplier ? String(guessedSupplier.id) : '',
      date_received: inference?.date_received ?? '',
      timeframe: '',
      manual_payment_id: '',
    },
    validate: schemaResolver(uploadSchema, { sync: true }),
  });
  const values = form.getValues();
  const selected = suppliers.find((s) => String(s.id) === values.supplier_id);

  // Tier C rules with no automated parsing need their confirmed total typed in here (FINDINGS F9).
  const isPivot = selected?.job_type === 'pivot_allocation';
  const tooManyFiles = isPivot && files.length > 1;
  const extractionRules = useQuery({
    ...extractionRulesQuery(selected?.id ?? 0),
    enabled: isPivot,
  });
  const manualRules = isPivot
    ? (extractionRules.data ?? []).filter((r) => r.extraction_method === 'manual_entry')
    : [];
  const [manualTotals, setManualTotals] = useState<ManualTotals>({});
  const [manualErrors, setManualErrors] = useState<Record<string, string>>({});
  const checkManualTotals = () => {
    const errors: Record<string, string> = {};
    for (const r of manualRules) {
      if (typeof manualTotals[r.rule_name] !== 'number') {
        errors[r.rule_name] = `Enter the confirmed total for ${r.rule_name}`;
      }
    }
    setManualErrors(errors);
    return Object.keys(errors).length === 0;
  };

  const submit = useMutation({
    meta: { handlesErrors: true },
    mutationFn: (v: UploadValues) =>
      unwrap(
        api.POST('/api/jobs', {
          body: {
            files,
            supplier_profile_id: Number(v.supplier_id),
            date_received: v.date_received,
            timeframe: v.timeframe.trim().toUpperCase(),
            manual_payment_id: blankToNull(v.manual_payment_id),
            manual_totals:
              manualRules.length > 0
                ? JSON.stringify(
                    Object.fromEntries(
                      manualRules.map((r) => [r.rule_name, manualTotals[r.rule_name]]),
                    ),
                  )
                : null,
          },
          bodySerializer: formData,
        }),
      ),
    onSuccess: (job) => {
      // The new job appears in Recent jobs straight away, as "uploaded".
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.lists });
      onSubmitted(job);
    },
    onError: (error) => {
      setFormError(applyServerErrors(form, error));
    },
  });

  return (
    <form
      noValidate
      onSubmit={form.onSubmit(
        (v) => {
          setFormError(null);
          if (checkManualTotals()) submit.mutate(v);
        },
        () => {
          checkManualTotals();
        },
      )}
    >
      <Stack gap="md">
        {formError && (
          <Alert color="red" title="Couldn't start processing">
            {formError}
          </Alert>
        )}
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Select
            label="Supplier"
            withAsterisk
            placeholder="Pick a supplier"
            searchable
            data={suppliers.map((s) => ({ value: String(s.id), label: s.name }))}
            description={
              guessedSupplier
                ? `Inferred from the file name: ${guessedSupplier.name}`
                : 'Could not infer the supplier from the file name. Please pick one.'
            }
            {...form.getInputProps('supplier_id')}
          />
          <TextInput
            type="date"
            label="Date received"
            withAsterisk
            description={
              inference?.date_received
                ? `Inferred from the file name: ${inference.date_received}`
                : 'Could not infer the date from the file name. Please enter it.'
            }
            {...form.getInputProps('date_received')}
          />
        </SimpleGrid>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput
            label="Timeframe"
            withAsterisk
            placeholder="e.g. 1Q26"
            description="The quarter this payment represents. Written to “Pmt For (Time Frame)” on every row."
            {...form.getInputProps('timeframe')}
          />
          <TextInput
            label="Payment ID override"
            description="Optional, for reference. The Upload template leaves Payment ID blank; it is filled in from the ledger afterwards."
            {...form.getInputProps('manual_payment_id')}
          />
        </SimpleGrid>
        {tooManyFiles && (
          <Alert color="red" icon={<IconAlertTriangle />}>
            {selected.name} is an aggregate/pivot supplier and takes one file per job. Remove the
            extra files.
          </Alert>
        )}
        {isPivot &&
          (extractionRules.error ? (
            <QueryError
              title="Couldn't load this supplier's extraction rules"
              error={extractionRules.error}
              onRetry={() => void extractionRules.refetch()}
            />
          ) : extractionRules.isPending ? (
            <Skeleton height={60} />
          ) : (
            manualRules.length > 0 && (
              <Fieldset legend="Confirmed totals">
                <Stack gap="sm">
                  <Text size="sm" c="dimmed">
                    {selected.name} has {pluralize(manualRules.length, 'total')} that can't be read
                    from the file. Enter the confirmed{' '}
                    {manualRules.length === 1 ? 'amount' : 'amounts'}; they are allocated like any
                    extracted total.
                  </Text>
                  <SimpleGrid cols={{ base: 1, sm: 2 }}>
                    {manualRules.map((r) => (
                      <NumberInput
                        key={r.id}
                        label={r.rule_name}
                        withAsterisk
                        prefix="$"
                        decimalScale={2}
                        thousandSeparator=","
                        description={
                          r.is_sales_amount
                            ? 'Sales amount (counts toward volume)'
                            : 'Fee amount (counts toward revenue)'
                        }
                        value={manualTotals[r.rule_name] ?? ''}
                        error={manualErrors[r.rule_name]}
                        onChange={(value) => {
                          setManualTotals((t) => ({ ...t, [r.rule_name]: value }));
                          setManualErrors(({ [r.rule_name]: _cleared, ...rest }) => rest);
                        }}
                      />
                    ))}
                  </SimpleGrid>
                </Stack>
              </Fieldset>
            )
          ))}
        {selected && (
          <Text size="sm">
            Contract ID <Code>{selected.contract_id ?? '—'}</Code> · Revenue type{' '}
            <Code>{selected.revenue_type}</Code>
          </Text>
        )}
        <Group>
          <Button
            type="submit"
            leftSection={<IconPlayerPlay size={16} />}
            loading={submit.isPending}
            disabled={tooManyFiles || (isPivot && !extractionRules.isSuccess)}
          >
            {files.length > 1
              ? `Combine and process ${String(files.length)} files`
              : 'Process file'}
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

/** Progress, then the outcome, of the job just submitted (`?job=`). */
function JobResult({ jobId, onDetails }: { jobId: number; onDetails: (jobId: number) => void }) {
  const queryClient = useQueryClient();
  const job = useQuery(jobQuery(jobId));
  const status = job.data?.status;
  const wasRunning = useRef(false);

  // When processing finishes, refresh the lists and counts that depend on it.
  useEffect(() => {
    if (!status) return;
    if (isInProgress(status)) {
      wasRunning.current = true;
    } else if (wasRunning.current) {
      wasRunning.current = false;
      void queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all });
      void queryClient.invalidateQueries({ queryKey: queryKeys.dashboard });
    }
  }, [status, queryClient]);

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
  if (!j) return <Skeleton height={120} />;

  if (isInProgress(j.status)) {
    return (
      <Paper p="lg" role="status">
        <Group gap="sm" wrap="nowrap">
          <Loader size="sm" />
          <div>
            <Text fw={600}>Processing job #{j.id}…</Text>
            <Text size="sm" c="dimmed">
              {j.original_file_name} · {j.status}. You can leave this page; the job keeps running.
            </Text>
          </div>
        </Group>
      </Paper>
    );
  }

  if (j.status === 'failed') {
    return (
      <Alert color="red" icon={<IconAlertTriangle />} title={`Processing failed (job #${j.id})`}>
        <Stack gap="xs" align="flex-start">
          <Text size="sm" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {j.error_message ?? 'The job failed without an error message.'}
          </Text>
          <Button
            size="xs"
            variant="white"
            color="red"
            onClick={() => {
              onDetails(j.id);
            }}
          >
            View details
          </Button>
        </Stack>
      </Alert>
    );
  }

  const complete = j.status === 'completed';
  const rec = j.reconciliation;
  const keyExceptions = (rec?.exception_rows ?? 0) - (j.unmatched_count ?? 0);
  const reasons = [
    j.unmatched_count ? pluralize(j.unmatched_count, 'unmatched row') : null,
    keyExceptions > 0
      ? `${pluralize(keyExceptions, 'row')} not verified against the GPO key`
      : null,
    rec && !rec.balanced ? 'totals do not reconcile' : null,
  ].filter(Boolean);
  return (
    <Stack gap="md">
      <Alert
        color={complete ? 'green' : 'orange'}
        icon={complete ? <IconCircleCheck /> : <IconAlertTriangle />}
        title={
          complete
            ? `Processing complete (job #${j.id})`
            : `Processing complete; needs review${reasons.length ? `: ${reasons.join(', ')}` : ''} (job #${j.id})`
        }
      >
        <Group gap="md">
          {j.pending_review_count > 0 && (
            <Anchor component={Link} to={jobLink(routes.review, j.id)} inherit>
              Resolve unmatched customers on the Review page
            </Anchor>
          )}
          <Anchor component={Link} to={jobLink(routes.export, j.id)} inherit>
            Check exceptions, preview and download on the Export page
          </Anchor>
        </Group>
      </Alert>
      <SimpleGrid cols={{ base: 2, md: 4 }}>
        <StatCard
          label={j.file_count > 1 ? `Total rows (${String(j.file_count)} files)` : 'Total rows'}
          value={formatInteger(j.row_count)}
        />
        <StatCard label="In output" value={formatInteger(j.matched_count)} />
        <StatCard
          label="Exceptions"
          value={formatInteger(rec?.exception_rows ?? j.unmatched_count)}
        />
        <StatCard
          label="Total revenue"
          value={j.total_revenue ? formatCurrency(j.total_revenue) : '—'}
        />
      </SimpleGrid>
      {Object.keys(j.manual_totals ?? {}).length > 0 && (
        <Text size="sm">
          Confirmed totals entered at upload:{' '}
          {Object.entries(j.manual_totals ?? {})
            .map(([name, amount]) => `${name} ${formatCurrency(amount)}`)
            .join(' · ')}
        </Text>
      )}
      {rec && (
        <Text size="sm" {...(rec.balanced ? {} : { c: 'red' })}>
          {rec.balanced ? 'Totals reconcile' : 'Totals do not reconcile'}: input revenue{' '}
          {formatCurrency(rec.input.revenue)} = output {formatCurrency(rec.output_revenue)} +
          exceptions {formatCurrency(rec.exceptions_revenue)}.
        </Text>
      )}
      <Text size="sm" c="dimmed">
        {j.payment_id ? (
          <>
            Payment ID <Code>{j.payment_id}</Code> (source: {formatText(j.payment_id_source)}), for
            reference; the Upload template leaves it blank.
          </>
        ) : (
          'No Payment ID was resolved from the ledger. The Upload template leaves it blank either way.'
        )}
      </Text>
    </Stack>
  );
}

type JobListView = 'active' | 'deleted';

/** Soft delete and restore; both refresh every job list and the dashboard counts. */
function useJobArchive() {
  const queryClient = useQueryClient();
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.jobs.all }),
      queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }),
    ]);
  const remove = useMutation({
    mutationFn: (jobId: number) =>
      unwrap(api.DELETE('/api/jobs/{job_id}', { params: { path: { job_id: jobId } } })),
    onSuccess: async (job) => {
      notifications.show({
        color: 'green',
        title: `Job #${String(job.id)} deleted`,
        message: 'Restore it any time from Deleted jobs.',
      });
      await refresh();
    },
  });
  const restore = useMutation({
    mutationFn: (jobId: number) =>
      unwrap(api.POST('/api/jobs/{job_id}/restoration', { params: { path: { job_id: jobId } } })),
    onSuccess: async (job) => {
      notifications.show({
        color: 'green',
        title: `Job #${String(job.id)} restored`,
        message: 'It is back in Recent jobs.',
      });
      await refresh();
    },
  });
  const confirmDelete = (j: Job) => {
    modals.openConfirmModal({
      title: `Delete job #${String(j.id)}?`,
      children: (
        <Text size="sm">
          {j.original_file_name} ({j.supplier_name}) is removed from Recent jobs, Review and Export.
          Nothing is erased: you can restore it from Deleted jobs.
        </Text>
      ),
      labels: { confirm: 'Delete job', cancel: 'Cancel' },
      confirmProps: { color: 'red' },
      onConfirm: () => {
        remove.mutate(j.id);
      },
    });
  };
  return { confirmDelete, restore };
}

interface RecentJobsProps {
  onOpen: (jobId: number) => void;
  onDetails: (jobId: number) => void;
}

function RecentJobs({ onOpen, onDetails }: RecentJobsProps) {
  const [view, setView] = useState<JobListView>('active');
  const deleted = view === 'deleted';
  const jobs = useQuery(jobsQuery({ limit: RECENT_LIMIT, archived: deleted }));
  const { confirmDelete, restore } = useJobArchive();
  return (
    <Stack gap="sm">
      <Group justify="space-between">
        <Title order={2}>{deleted ? 'Deleted jobs' : 'Recent jobs'}</Title>
        <SegmentedControl
          size="xs"
          aria-label="Which jobs"
          value={view}
          onChange={(v) => {
            setView(v);
          }}
          data={[
            { value: 'active', label: 'Recent' },
            { value: 'deleted', label: 'Deleted' },
          ]}
        />
      </Group>
      {jobs.error ? (
        <QueryError
          title={`Couldn't load ${deleted ? 'deleted' : 'recent'} jobs`}
          error={jobs.error}
          onRetry={() => void jobs.refetch()}
        />
      ) : (
        <DataTable
          label={deleted ? 'Deleted jobs' : 'Recent jobs'}
          fetching={jobs.isPending}
          records={jobs.data}
          noRecordsText={deleted ? 'No deleted jobs' : 'No jobs yet'}
          pinLastColumn
          columns={[
            {
              accessor: 'id',
              title: 'Job #',
              render: (j) =>
                deleted ? (
                  `#${String(j.id)}`
                ) : (
                  <Anchor
                    component="button"
                    type="button"
                    size="sm"
                    onClick={() => {
                      onOpen(j.id);
                    }}
                    aria-label={`Open job ${j.id}`}
                  >
                    #{j.id}
                  </Anchor>
                ),
            },
            { accessor: 'supplier_name', title: 'Supplier' },
            { accessor: 'original_file_name', title: 'File' },
            {
              accessor: 'status',
              title: 'Status',
              render: (j) => (
                <Badge color={statusColor(j.status)} style={{ overflow: 'visible' }}>
                  {statusLabel(j.status)}
                </Badge>
              ),
            },
            {
              accessor: 'row_count',
              title: 'Rows',
              textAlign: 'right',
              render: (j) => formatInteger(j.row_count),
            },
            {
              accessor: 'matched_count',
              title: 'Matched',
              textAlign: 'right',
              render: (j) => formatInteger(j.matched_count),
            },
            {
              accessor: 'unmatched_count',
              title: 'Unmatched',
              textAlign: 'right',
              render: (j) => formatInteger(j.unmatched_count),
            },
            {
              accessor: 'total_revenue',
              title: 'Total revenue',
              textAlign: 'right',
              cellsClassName: 'tabular',
              render: (j) => formatCurrency(j.total_revenue),
            },
            {
              accessor: deleted ? 'archived_at' : 'created_at',
              title: deleted ? 'Deleted' : 'Created',
              render: (j) => formatDateTime(deleted ? j.archived_at : j.created_at),
            },
            {
              accessor: 'actions',
              title: '',
              render: (j) => (
                <Group gap={4} wrap="nowrap">
                  <Button
                    size="compact-xs"
                    variant="default"
                    onClick={() => {
                      onDetails(j.id);
                    }}
                    aria-label={`Details of job ${j.id}`}
                  >
                    Details
                  </Button>
                  {deleted ? (
                    <Button
                      size="compact-xs"
                      variant="default"
                      leftSection={<IconArrowBackUp size={14} />}
                      loading={restore.isPending && restore.variables === j.id}
                      onClick={() => {
                        restore.mutate(j.id);
                      }}
                      aria-label={`Restore job ${j.id}`}
                    >
                      Restore
                    </Button>
                  ) : (
                    <Tooltip label={isInProgress(j.status) ? 'Still processing' : 'Delete job'}>
                      <ActionIcon
                        variant="subtle"
                        color="red"
                        disabled={isInProgress(j.status)}
                        onClick={() => {
                          confirmDelete(j);
                        }}
                        aria-label={`Delete job ${j.id}`}
                      >
                        <IconTrash size={16} />
                      </ActionIcon>
                    </Tooltip>
                  )}
                </Group>
              ),
            },
          ]}
        />
      )}
    </Stack>
  );
}

export function UploadPage() {
  const suppliers = useQuery(suppliersQuery());
  const meta = useQuery(metaQuery);
  const [files, setFiles] = useState<File[]>([]);
  const first = files[0];
  const [jobId, setJobId] = useSearchParamState(searchParamNames.job, idCodec);
  const [detailsJobId, setDetailsJobId] = useState<number | null>(null);
  const inference = useQuery({
    ...fileNameInferenceQuery(first?.name ?? ''),
    enabled: first !== undefined,
  });

  const header = (
    <PageHeader
      title="Upload & process"
      description="Upload a supplier file, or every file for one month; they are combined before processing. The supplier and date received are inferred from the first file name."
    />
  );

  if (suppliers.error) {
    return (
      <>
        {header}
        <QueryError
          title="Couldn't load suppliers"
          error={suppliers.error}
          onRetry={() => void suppliers.refetch()}
        />
      </>
    );
  }
  if (suppliers.data?.length === 0) {
    return (
      <>
        {header}
        <EmptyState title="No supplier profiles configured">
          Add one in{' '}
          <Anchor component={Link} to={routes.admin.suppliers} inherit>
            Admin › Suppliers
          </Anchor>{' '}
          (Guerbet is pre-seeded by <code>scripts/seed_guerbet.py</code>).
        </EmptyState>
      </>
    );
  }

  return (
    <>
      {header}
      <Stack gap="xl">
        <Paper p="lg">
          <Stack gap="md">
            <FileListDropzone
              label="Supplier file(s)"
              extensions={meta.data?.upload_extensions ?? DEFAULT_EXTENSIONS}
              files={files}
              onChange={setFiles}
            />
            {first &&
              (suppliers.data && !inference.isPending ? (
                <UploadForm
                  key={`${first.name}:${String(first.lastModified)}:${String(first.size)}`}
                  files={files}
                  suppliers={suppliers.data}
                  inference={inference.data ?? null}
                  onSubmitted={(job) => {
                    setFiles([]);
                    setJobId(job.id);
                  }}
                />
              ) : (
                <Skeleton height={160} />
              ))}
          </Stack>
        </Paper>
        {jobId !== null && <JobResult key={jobId} jobId={jobId} onDetails={setDetailsJobId} />}
        <RecentJobs
          onOpen={(id) => {
            setJobId(id);
            window.scrollTo({ top: 0, behavior: 'smooth' });
          }}
          onDetails={setDetailsJobId}
        />
      </Stack>
      <JobDetailsModal
        jobId={detailsJobId}
        onClose={() => {
          setDetailsJobId(null);
        }}
      />
    </>
  );
}
