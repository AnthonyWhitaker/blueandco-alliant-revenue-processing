import {
  Alert,
  Badge,
  Button,
  Group,
  Paper,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { schemaResolver, useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { z } from 'zod';

import { api, unwrap } from '@/api/client';
import { DataTable } from '@/components/DataTable';
import { EmptyState } from '@/components/EmptyState';
import { MemberPicker } from '@/components/MemberPicker';
import { QueryError } from '@/components/QueryError';
import { applyServerErrors, blankToNull } from '@/lib/forms';
import { formatDateTime, formatText } from '@/lib/format';
import { queryKeys } from '@/queries/keys';
import { aliasesQuery } from '@/queries/members';
import { suppliersQuery } from '@/queries/suppliers';

const LIST_LIMIT = 200;

const aliasSchema = z
  .object({
    member: z.object({ id: z.number(), label: z.string() }).nullable(),
    raw_name: z.string().trim().min(1, 'Enter the alternate name'),
    jde_number: z.string(),
    scope: z.enum(['global', 'supplier']),
    supplier_id: z.string().nullable(),
  })
  .superRefine((v, ctx) => {
    if (!v.member) ctx.addIssue({ code: 'custom', path: ['member'], message: 'Pick a member' });
    if (v.scope === 'supplier' && !v.supplier_id) {
      ctx.addIssue({ code: 'custom', path: ['supplier_id'], message: 'Pick the supplier' });
    }
  });
type AliasFormValues = z.infer<typeof aliasSchema>;

const EMPTY_ALIAS: AliasFormValues = {
  member: null,
  raw_name: '',
  jde_number: '',
  scope: 'global',
  supplier_id: null,
};

function AddAliasForm() {
  const queryClient = useQueryClient();
  const suppliers = useQuery(suppliersQuery());
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<AliasFormValues>({
    mode: 'controlled',
    initialValues: EMPTY_ALIAS,
    validate: schemaResolver(aliasSchema, { sync: true }),
  });
  const values = form.getValues();

  const create = useMutation({
    meta: { handlesErrors: true },
    mutationFn: (body: {
      member_id: number;
      raw_name: string;
      jde_number: string | null;
      supplier_profile_id: number | null;
    }) => unwrap(api.POST('/api/aliases', { body })),
    onSuccess: async (alias) => {
      notifications.show({
        color: 'green',
        title: 'Alias saved',
        message: `“${alias.raw_name}” now matches ${alias.member_name}.`,
      });
      form.reset();
      await queryClient.invalidateQueries({ queryKey: queryKeys.aliases.all });
    },
    onError: (error) => {
      setFormError(applyServerErrors(form, error));
    },
  });

  return (
    <Paper p="lg">
      <form
        noValidate
        onSubmit={form.onSubmit((v) => {
          // The schema guarantees a member; this narrows the type.
          if (!v.member) return;
          setFormError(null);
          create.mutate({
            member_id: v.member.id,
            raw_name: v.raw_name.trim(),
            jde_number: blankToNull(v.jde_number),
            supplier_profile_id:
              v.scope === 'supplier' && v.supplier_id ? Number(v.supplier_id) : null,
          });
        })}
      >
        <Stack gap="md">
          <Title order={2} size="h3">
            Add a manual alias
          </Title>
          {formError && (
            <Alert color="red" title="Couldn't save the alias">
              {formError}
            </Alert>
          )}
          <MemberPicker
            label="Member"
            withAsterisk
            activeOnly={false}
            value={values.member}
            onChange={(member) => {
              form.setFieldValue('member', member);
            }}
            error={typeof form.errors['member'] === 'string' ? form.errors['member'] : null}
          />
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput
              label="Alias (alternate name)"
              withAsterisk
              {...form.getInputProps('raw_name')}
            />
            <TextInput
              label="JDE number"
              description="Optional: ties the alias to one JDE number"
              {...form.getInputProps('jde_number')}
            />
          </SimpleGrid>
          <Stack gap={6}>
            <Text size="sm" fw={500} id="alias-scope-label">
              Scope
            </Text>
            <SegmentedControl
              aria-labelledby="alias-scope-label"
              data={[
                { value: 'global', label: 'Global (all suppliers)' },
                { value: 'supplier', label: 'Supplier-specific' },
              ]}
              value={values.scope}
              onChange={(scope) => {
                form.setFieldValue('scope', scope === 'supplier' ? 'supplier' : 'global');
              }}
              w="fit-content"
            />
          </Stack>
          {values.scope === 'supplier' && (
            <Select
              label="Supplier"
              withAsterisk
              placeholder={suppliers.isPending ? 'Loading suppliers…' : 'Pick a supplier'}
              data={(suppliers.data ?? []).map((s) => ({ value: String(s.id), label: s.name }))}
              searchable
              maw={360}
              {...form.getInputProps('supplier_id')}
            />
          )}
          <Group>
            <Button type="submit" loading={create.isPending}>
              Save alias
            </Button>
          </Group>
        </Stack>
      </form>
    </Paper>
  );
}

export function AliasesTab() {
  const aliases = useQuery(aliasesQuery({ limit: LIST_LIMIT }));

  return (
    <Stack gap="lg">
      <Stack gap="sm">
        <div>
          <Title order={2} size="h3">
            Member aliases
          </Title>
          <Text size="sm" c="dimmed">
            Alternate names for members, used in matching before fuzzy search. Review approvals
            create them automatically.
          </Text>
        </div>
        {aliases.error ? (
          <QueryError
            title="Couldn't load aliases"
            error={aliases.error}
            onRetry={() => void aliases.refetch()}
          />
        ) : aliases.data?.length === 0 ? (
          <EmptyState title="No aliases yet">
            They are created automatically when you approve matches on the Review page.
          </EmptyState>
        ) : (
          <DataTable
            label="Member aliases"
            fetching={aliases.isPending}
            records={aliases.data}
            maxHeight={520}
            columns={[
              { accessor: 'raw_name', title: 'Alias' },
              { accessor: 'member_name', title: 'Member' },
              {
                accessor: 'entity_code',
                title: 'Entity code',
                cellsClassName: 'mono',
                render: (a) => formatText(a.entity_code),
              },
              {
                accessor: 'jde_number',
                title: 'JDE #',
                cellsClassName: 'mono',
                render: (a) => formatText(a.jde_number),
              },
              {
                accessor: 'supplier_name',
                title: 'Scope',
                render: (a) =>
                  a.supplier_name ?? (
                    <Badge color="slate" variant="outline">
                      Global
                    </Badge>
                  ),
              },
              {
                accessor: 'approved_by',
                title: 'Approved by',
                render: (a) => formatText(a.approved_by),
              },
              {
                accessor: 'approved_at',
                title: 'Approved',
                render: (a) => formatDateTime(a.approved_at),
              },
            ]}
          />
        )}
        {aliases.data?.length === LIST_LIMIT && (
          <Text size="xs" c="dimmed">
            Showing the first {LIST_LIMIT} aliases, by member name.
          </Text>
        )}
      </Stack>
      <AddAliasForm />
    </Stack>
  );
}
