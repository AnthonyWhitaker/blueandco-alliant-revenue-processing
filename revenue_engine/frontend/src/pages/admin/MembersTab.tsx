import {
  Alert,
  Button,
  Checkbox,
  Group,
  Paper,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { schemaResolver, useForm } from '@mantine/form';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { z } from 'zod';

import { api, formData, unwrap } from '@/api/client';
import { DataTable } from '@/components/DataTable';
import { FileDropzone } from '@/components/FileDropzone';
import { QueryError } from '@/components/QueryError';
import { StatCard } from '@/components/StatCard';
import { applyServerErrors, blankToNull } from '@/lib/forms';
import { formatInteger, formatText } from '@/lib/format';
import { queryKeys } from '@/queries/keys';
import { membersQuery } from '@/queries/members';
import { useAppStore } from '@/stores';

import { ImportStatus } from './ImportStatus';

const SAMPLE_LIMIT = 50;

function AccountListImport() {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [replaceAll, setReplaceAll] = useState(false);
  const trackImport = useAppStore((s) => s.trackImport);

  const upload = useMutation({
    mutationFn: (vars: { file: File; replaceAll: boolean }) =>
      unwrap(
        api.POST('/api/member-imports', {
          body: { file: vars.file, replace_all: vars.replaceAll },
          bodySerializer: formData,
        }),
      ),
    onSuccess: (task) => {
      trackImport('member_import', task.id);
      setFile(null);
      setReplaceAll(false);
    },
  });

  const start = () => {
    if (!file) return;
    if (!replaceAll) {
      upload.mutate({ file, replaceAll });
      return;
    }
    // A full refresh can deactivate many members at once, so ask.
    modals.openConfirmModal({
      title: 'Replace all members?',
      children: (
        <Text size="sm">
          Members from an earlier Account List that are not in this file are deactivated: they are
          no longer matched or offered on the Review page. Nothing is deleted, so past jobs, aliases
          and Tier C rules keep working, and a member is reactivated if a later file includes it
          again. Facilities added by hand on the Review page are not affected.
        </Text>
      ),
      labels: { confirm: 'Deactivate missing and reload', cancel: 'Cancel' },
      confirmProps: { color: 'orange' },
      onConfirm: () => {
        upload.mutate({ file, replaceAll });
      },
    });
  };

  return (
    <Paper p="lg">
      <Stack gap="md">
        <div>
          <Title order={2} size="h3">
            Load the Account List
          </Title>
          <Text size="sm" c="dimmed" mt={4}>
            Upload Account List.xlsx to load or refresh the member database. Existing members are
            updated by Salesforce Account ID.
          </Text>
        </div>
        <FileDropzone
          label="Account List.xlsx"
          extensions={['.xlsx', '.xls']}
          file={file}
          onChange={setFile}
          disabled={upload.isPending}
        />
        <Checkbox
          label="Replace all existing members (full refresh)"
          description="Deactivates members that are not in this file."
          checked={replaceAll}
          onChange={(e) => {
            setReplaceAll(e.currentTarget.checked);
          }}
        />
        <Group>
          <Button disabled={!file} loading={upload.isPending} onClick={start}>
            Load Account List
          </Button>
        </Group>
        <ImportStatus
          kind="member_import"
          describeSuccess={(task) => {
            const result = task.member_import;
            const deactivated = result?.deactivated ?? 0;
            return `Loaded ${formatInteger(result?.inserted ?? 0)} members (inserted or updated), ${formatInteger(result?.skipped ?? 0)} skipped${deactivated ? `, ${formatInteger(deactivated)} deactivated (not in the file)` : ''}.`;
          }}
          onSucceeded={() =>
            Promise.all([
              queryClient.invalidateQueries({ queryKey: queryKeys.members.all }),
              queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }),
            ])
          }
        />
      </Stack>
    </Paper>
  );
}

const memberSchema = z.object({
  name: z.string().trim().min(1, 'Enter the facility name'),
  premier_entity_code: z.string(),
  account_id: z.string(),
  city: z.string(),
  state: z.string(),
  zip: z.string(),
});
type MemberFormValues = z.infer<typeof memberSchema>;

const EMPTY_MEMBER: MemberFormValues = {
  name: '',
  premier_entity_code: '',
  account_id: '',
  city: '',
  state: '',
  zip: '',
};

function AddMemberForm() {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<MemberFormValues>({
    mode: 'controlled',
    initialValues: EMPTY_MEMBER,
    validate: schemaResolver(memberSchema, { sync: true }),
  });

  const create = useMutation({
    meta: { handlesErrors: true },
    mutationFn: (values: MemberFormValues) =>
      unwrap(
        api.POST('/api/members', {
          body: {
            name: values.name.trim(),
            premier_entity_code: blankToNull(values.premier_entity_code),
            account_id: blankToNull(values.account_id),
            city: blankToNull(values.city),
            state: blankToNull(values.state),
            zip: blankToNull(values.zip),
          },
        }),
      ),
    onSuccess: async (member) => {
      notifications.show({
        color: 'green',
        title: 'Member added',
        message: `Added “${member.name}” as member id ${member.id}.`,
      });
      form.reset();
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.members.all }),
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }),
      ]);
    },
    onError: (error) => {
      setFormError(applyServerErrors(form, error));
    },
  });

  return (
    <Paper p="lg">
      <form
        onSubmit={form.onSubmit((values) => {
          setFormError(null);
          create.mutate(values);
        })}
        noValidate
      >
        <Stack gap="md">
          <div>
            <Title order={2} size="h3">
              Add a single member
            </Title>
            <Text size="sm" c="dimmed" mt={4}>
              For a real facility that&apos;s missing from the Account List. A later full refresh
              still overrides this row&apos;s city, state and zip if it appears there. Without an
              entity code, the facility&apos;s matched revenue still counts toward job totals, but
              its output row uses a placeholder PEC until a real one is added.
            </Text>
          </div>
          {formError && (
            <Alert color="red" title="Couldn't add the member">
              {formError}
            </Alert>
          )}
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput label="Facility name" withAsterisk {...form.getInputProps('name')} />
            <TextInput
              label="Premier/Alliant entity code"
              description="Optional"
              {...form.getInputProps('premier_entity_code')}
            />
            <TextInput
              label="Salesforce Account ID"
              description="Optional"
              {...form.getInputProps('account_id')}
            />
          </SimpleGrid>
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <TextInput label="City" {...form.getInputProps('city')} />
            <TextInput label="State" {...form.getInputProps('state')} />
            <TextInput label="Zip" {...form.getInputProps('zip')} />
          </SimpleGrid>
          <Group>
            <Button type="submit" loading={create.isPending}>
              Add member
            </Button>
          </Group>
        </Stack>
      </form>
    </Paper>
  );
}

export function MembersTab() {
  const count = useQuery(membersQuery({ active_only: false, limit: 1 }));
  const [showSample, setShowSample] = useState(false);
  const sample = useQuery({
    ...membersQuery({ active_only: false, limit: SAMPLE_LIMIT }),
    enabled: showSample,
  });

  return (
    <Stack gap="lg">
      <SimpleGrid cols={{ base: 1, xs: 2, md: 4 }}>
        <StatCard
          label="Members in database"
          value={count.isError ? '—' : count.data && formatInteger(count.data.total)}
        />
      </SimpleGrid>

      <AccountListImport />

      <Stack gap="sm">
        <Group justify="space-between">
          <Title order={2} size="h3">
            Sample members
          </Title>
          <Button
            variant="default"
            aria-expanded={showSample}
            onClick={() => {
              setShowSample((v) => !v);
            }}
          >
            {showSample ? 'Hide sample' : 'Show sample members'}
          </Button>
        </Group>
        {showSample &&
          (sample.error ? (
            <QueryError
              title="Couldn't load members"
              error={sample.error}
              onRetry={() => void sample.refetch()}
            />
          ) : (
            <DataTable
              label="Sample members"
              fetching={sample.isFetching}
              records={sample.data?.items}
              noRecordsText="No members loaded yet"
              columns={[
                {
                  accessor: 'entity_code',
                  title: 'Entity code',
                  cellsClassName: 'mono',
                  render: (m) => formatText(m.entity_code),
                },
                { accessor: 'name', title: 'Name' },
                {
                  accessor: 'billing_city',
                  title: 'City',
                  render: (m) => formatText(m.billing_city),
                },
                {
                  accessor: 'billing_state',
                  title: 'State',
                  render: (m) => formatText(m.billing_state),
                },
              ]}
            />
          ))}
      </Stack>

      <AddMemberForm />
    </Stack>
  );
}
