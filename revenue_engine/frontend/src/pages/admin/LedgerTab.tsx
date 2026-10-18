import { Button, Group, Paper, SimpleGrid, Stack, Text, TextInput, Title } from '@mantine/core';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';

import { api, formData, unwrap } from '@/api/client';
import { DataTable } from '@/components/DataTable';
import { FileDropzone } from '@/components/FileDropzone';
import { QueryError } from '@/components/QueryError';
import { StatCard } from '@/components/StatCard';
import { formatCurrency, formatDate, formatInteger } from '@/lib/format';
import { queryKeys } from '@/queries/keys';
import { ledgerEntriesQuery } from '@/queries/members';
import { useAppStore } from '@/stores';

import { ImportStatus } from './ImportStatus';

const RECENT_LIMIT = 30;

export function LedgerTab() {
  const queryClient = useQueryClient();
  const count = useQuery(ledgerEntriesQuery({ limit: 1 }));
  const [file, setFile] = useState<File | null>(null);
  const [sheetName, setSheetName] = useState('Ledger');
  const [showRecent, setShowRecent] = useState(false);
  const recent = useQuery({ ...ledgerEntriesQuery({ limit: RECENT_LIMIT }), enabled: showRecent });
  const trackImport = useAppStore((s) => s.trackImport);

  const upload = useMutation({
    mutationFn: (vars: { file: File; sheetName: string }) =>
      unwrap(
        api.POST('/api/ledger-imports', {
          body: { file: vars.file, sheet_name: vars.sheetName },
          bodySerializer: formData,
        }),
      ),
    onSuccess: (task) => {
      trackImport('ledger_import', task.id);
      setFile(null);
    },
  });

  const sheet = sheetName.trim();

  return (
    <Stack gap="lg">
      <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }}>
        <StatCard
          label="Ledger entries"
          value={count.isError ? '—' : count.data && formatInteger(count.data.total)}
        />
      </SimpleGrid>

      <Paper p="lg">
        <Stack gap="md">
          <div>
            <Title order={3}>Import the payment ledger</Title>
            <Text size="sm" c="dimmed" mt={4}>
              Upload Ledger.xlsx to import the Salesforce Payment IDs for deposit amounts.
            </Text>
          </div>
          <FileDropzone
            label="Ledger.xlsx"
            extensions={['.xlsx', '.xls']}
            file={file}
            onChange={setFile}
            disabled={upload.isPending}
          />
          <Group align="flex-end">
            <TextInput
              label="Sheet name"
              value={sheetName}
              onChange={(e) => {
                setSheetName(e.currentTarget.value);
              }}
              error={sheet ? null : 'Enter the sheet to read'}
              w={240}
            />
            <Button
              disabled={!file || !sheet}
              loading={upload.isPending}
              onClick={() => {
                if (file) upload.mutate({ file, sheetName: sheet });
              }}
            >
              Load ledger
            </Button>
          </Group>
          <ImportStatus
            kind="ledger_import"
            describeSuccess={(task) =>
              `Loaded ${formatInteger(task.ledger_import?.inserted ?? 0)} ledger entries.`
            }
            onSucceeded={() =>
              queryClient.invalidateQueries({ queryKey: queryKeys.ledgerEntries.all })
            }
          />
        </Stack>
      </Paper>

      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={3}>Recent entries</Title>
          <Button
            variant="default"
            onClick={() => {
              setShowRecent((v) => !v);
            }}
            aria-expanded={showRecent}
          >
            {showRecent ? 'Hide recent entries' : `Show the ${RECENT_LIMIT} most recent`}
          </Button>
        </Group>
        {showRecent &&
          (recent.error ? (
            <QueryError
              title="Couldn't load ledger entries"
              error={recent.error}
              onRetry={() => void recent.refetch()}
            />
          ) : (
            <DataTable
              label="Recent ledger entries"
              fetching={recent.isFetching}
              records={recent.data?.items}
              noRecordsText="The ledger is empty"
              columns={[
                { accessor: 'payor', title: 'Payor' },
                {
                  accessor: 'payment_received_date',
                  title: 'Received',
                  render: (r) => formatDate(r.payment_received_date),
                },
                {
                  accessor: 'deposit_amount',
                  title: 'Deposit',
                  textAlign: 'right',
                  cellsClassName: 'tabular',
                  render: (r) => formatCurrency(r.deposit_amount),
                },
                { accessor: 'sf_payment_id', title: 'Payment ID', cellsClassName: 'mono' },
              ]}
            />
          ))}
      </Stack>
    </Stack>
  );
}
