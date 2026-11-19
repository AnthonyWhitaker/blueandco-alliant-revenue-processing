import {
  Alert,
  Badge,
  Button,
  Code,
  CopyButton,
  Group,
  Modal,
  ScrollArea,
  SimpleGrid,
  Skeleton,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconAlertTriangle, IconCheck, IconCopy } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';

import { DataTable } from '@/components/DataTable';
import { QueryError } from '@/components/QueryError';
import { formatCurrency, formatDate, formatDateTime, formatInteger, formatText } from '@/lib/format';
import { statusColor, statusLabel } from '@/lib/jobs';
import { jobExceptionsQuery, jobFilesQuery, jobQuery, type Job } from '@/queries/jobs';

/** Processing log entries shown; a large file can log one warning per row. */
const LOG_LIMIT = 200;

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <Text size="xs" c="dimmed">
        {label}
      </Text>
      <Text size="sm">{children}</Text>
    </div>
  );
}

/** What a failed job reported: the message, and the traceback to send to support. */
function FailureDetails({ job }: { job: Job }) {
  const report = [
    `Job #${String(job.id)} (${job.supplier_name}, ${job.original_file_name})`,
    job.error_message ?? '',
    job.error_detail ?? '',
  ]
    .filter(Boolean)
    .join('\n\n');
  return (
    <Stack gap="xs">
      <Alert color="red" icon={<IconAlertTriangle />} title="Why it failed">
        <Text size="sm" style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {job.error_message ?? 'The job failed without an error message.'}
        </Text>
      </Alert>
      {job.error_detail && (
        <>
          <Group justify="space-between">
            <Text size="sm" fw={600}>
              Technical details
            </Text>
            <CopyButton value={report}>
              {({ copied, copy }) => (
                <Button
                  size="xs"
                  variant="default"
                  leftSection={copied ? <IconCheck size={14} /> : <IconCopy size={14} />}
                  onClick={copy}
                >
                  {copied ? 'Copied' : 'Copy error report'}
                </Button>
              )}
            </CopyButton>
          </Group>
          <ScrollArea.Autosize mah={260} type="auto">
            <Code block>{job.error_detail}</Code>
          </ScrollArea.Autosize>
        </>
      )}
    </Stack>
  );
}

function ProcessingLog({ jobId }: { jobId: number }) {
  const log = useQuery(jobExceptionsQuery(jobId));
  if (log.error) {
    return (
      <QueryError
        title="Couldn't load the processing log"
        error={log.error}
        onRetry={() => void log.refetch()}
      />
    );
  }
  const entries = log.data ?? [];
  return (
    <Stack gap="xs">
      <Title order={3} size="h5">
        Processing log
      </Title>
      <DataTable
        label="Processing log"
        fetching={log.isPending}
        records={entries.slice(0, LOG_LIMIT)}
        noRecordsText="Nothing was logged"
        maxHeight={300}
        columns={[
          {
            accessor: 'severity',
            title: 'Severity',
            render: (e) => (
              <Badge
                variant="light"
                color={e.severity === 'error' ? 'red' : e.severity === 'warning' ? 'orange' : 'slate'}
                style={{ overflow: 'visible' }}
              >
                {e.severity}
              </Badge>
            ),
          },
          { accessor: 'exception_type', title: 'Type' },
          { accessor: 'message', title: 'Message', noWrap: false },
        ]}
      />
      {entries.length > LOG_LIMIT && (
        <Text size="xs" c="dimmed">
          Showing the first {formatInteger(LOG_LIMIT)} of {formatInteger(entries.length)} entries.
          The Export page lists them all.
        </Text>
      )}
    </Stack>
  );
}

function JobDetailsBody({ jobId }: { jobId: number }) {
  const job = useQuery(jobQuery(jobId));
  const files = useQuery(jobFilesQuery(jobId));
  if (job.error) {
    return (
      <QueryError
        title={`Couldn't load job #${String(jobId)}`}
        error={job.error}
        onRetry={() => void job.refetch()}
      />
    );
  }
  const j = job.data;
  if (!j) return <Skeleton height={240} />;
  const fileNames = files.data?.map((f) => f.original_file_name) ?? [j.original_file_name];
  return (
    <Stack gap="md">
      <Group gap="xs">
        <Badge color={statusColor(j.status)}>{statusLabel(j.status)}</Badge>
        {j.archived_at && (
          <Badge color="gray" variant="outline">
            Deleted {formatDateTime(j.archived_at)}
          </Badge>
        )}
      </Group>
      <SimpleGrid cols={{ base: 2, sm: 3 }} spacing="sm">
        <Fact label="Supplier">{j.supplier_name}</Fact>
        <Fact label="Uploaded">{formatDateTime(j.created_at)}</Fact>
        <Fact label="Finished">{formatDateTime(j.completed_at)}</Fact>
        <Fact label="Date received">{formatDate(j.date_received)}</Fact>
        <Fact label="Timeframe">{formatText(j.timeframe)}</Fact>
        <Fact label="Contract ID">{formatText(j.contract_id)}</Fact>
        <Fact label="Rows">{formatInteger(j.row_count)}</Fact>
        <Fact label="Matched / unmatched">
          {formatInteger(j.matched_count)} / {formatInteger(j.unmatched_count)}
        </Fact>
        <Fact label="Total revenue">{formatCurrency(j.total_revenue)}</Fact>
      </SimpleGrid>
      <Fact label={fileNames.length > 1 ? `Files (${String(fileNames.length)})` : 'File'}>
        {fileNames.map((name) => (
          <span key={name} style={{ display: 'block', wordBreak: 'break-all' }}>
            {name}
          </span>
        ))}
      </Fact>
      {j.status === 'failed' && <FailureDetails job={j} />}
      <ProcessingLog jobId={j.id} />
    </Stack>
  );
}

/** Everything recorded about one job, including the full error of a failed one. */
export function JobDetailsModal({ jobId, onClose }: { jobId: number | null; onClose: () => void }) {
  return (
    <Modal
      opened={jobId !== null}
      onClose={onClose}
      title={jobId === null ? '' : `Job #${String(jobId)}`}
      size="xl"
    >
      {jobId !== null && <JobDetailsBody jobId={jobId} />}
    </Modal>
  );
}
