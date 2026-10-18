import {
  Alert,
  Anchor,
  Badge,
  Button,
  Checkbox,
  Code,
  Group,
  Paper,
  Select,
  SimpleGrid,
  Skeleton,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { schemaResolver, useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconWand } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { Link } from 'react-router-dom';
import { z } from 'zod';

import { api, formData, unwrap } from '@/api/client';
import type { Schemas } from '@/api/types';
import { DataTable } from '@/components/DataTable';
import { EmptyState } from '@/components/EmptyState';
import { FileDropzone } from '@/components/FileDropzone';
import { QueryError } from '@/components/QueryError';
import { SupplierSelect } from '@/components/SupplierSelect';
import { useSelectedSupplier } from '@/hooks/useSelectedSupplier';
import { applyServerErrors } from '@/lib/forms';
import { pluralize } from '@/lib/format';
import { routes } from '@/lib/routes';
import { queryKeys } from '@/queries/keys';
import { metaQuery } from '@/queries/reference';
import { columnMappingsQuery, suppliersQuery } from '@/queries/suppliers';
import { useAppStore } from '@/stores';
import { useInferredMappings } from '@/stores/hooks';

type CanonicalField = Schemas['ColumnMappingInput']['canonical_field'];

function InferFromSample({ supplierId }: { supplierId: number }) {
  const queryClient = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const draft = useInferredMappings(supplierId);
  const setInferred = useAppStore((s) => s.setInferredMappings);
  const discard = useAppStore((s) => s.discardInferredMappings);

  const infer = useMutation({
    mutationFn: (sample: File) =>
      unwrap(
        api.POST('/api/suppliers/{supplier_id}/column-mappings/inference', {
          params: { path: { supplier_id: supplierId } },
          body: { file: sample },
          bodySerializer: formData,
        }),
      ),
    onSuccess: (mappings) => {
      setInferred(supplierId, mappings);
      setFile(null);
    },
  });

  const save = useMutation({
    mutationFn: (mappings: Schemas['InferredMapping'][]) =>
      unwrap(
        api.POST('/api/suppliers/{supplier_id}/column-mappings/bulk', {
          params: { path: { supplier_id: supplierId } },
          body: mappings.map((m) => ({
            source_column: m.source_column,
            canonical_field: m.canonical_field,
          })),
        }),
      ),
    onSuccess: async (saved) => {
      discard(supplierId);
      notifications.show({
        color: 'green',
        message: `Saved ${pluralize(saved.length, 'mapping')}.`,
      });
      await queryClient.invalidateQueries({
        queryKey: queryKeys.suppliers.columnMappings(supplierId),
      });
    },
  });

  return (
    <Paper p="lg">
      <Stack gap="md">
        <div>
          <Title order={3}>Auto-infer from a sample file</Title>
          <Text size="sm" c="dimmed" mt={4}>
            Suggests mappings from the file&apos;s headers. Nothing is saved until you save the
            suggestions.
          </Text>
        </div>
        <FileDropzone
          label="Sample supplier file"
          extensions={['.xlsx', '.xls', '.csv']}
          file={file}
          onChange={setFile}
          disabled={infer.isPending}
        />
        <Group>
          <Button
            variant="light"
            leftSection={<IconWand size={16} />}
            disabled={!file}
            loading={infer.isPending}
            onClick={() => {
              if (file) infer.mutate(file);
            }}
          >
            Infer column map
          </Button>
        </Group>
        {draft && (
          <Stack gap="sm">
            <Text fw={600} size="sm">
              Suggested mappings (unsaved)
            </Text>
            {draft.length === 0 ? (
              <Text size="sm" c="dimmed">
                No headers in the sample matched a known field.
              </Text>
            ) : (
              <DataTable
                label="Suggested mappings"
                records={draft}
                idAccessor="source_column"
                columns={[
                  { accessor: 'source_column', title: 'Source column', cellsClassName: 'mono' },
                  { accessor: 'canonical_field', title: 'Canonical field' },
                ]}
              />
            )}
            <Group>
              <Button
                disabled={draft.length === 0}
                loading={save.isPending}
                onClick={() => {
                  save.mutate(draft);
                }}
              >
                Save inferred mappings
              </Button>
              <Button
                variant="default"
                disabled={save.isPending}
                onClick={() => {
                  discard(supplierId);
                }}
              >
                Discard
              </Button>
            </Group>
          </Stack>
        )}
      </Stack>
    </Paper>
  );
}

const mappingSchema = z.object({
  source_column: z.string().trim().min(1, 'Enter the column header (or contains:<text>)'),
  canonical_field: z.string().min(1, 'Pick the field'),
  is_required: z.boolean(),
});
type MappingValues = z.infer<typeof mappingSchema>;

function SingleMappingForm({
  supplierId,
  canonicalFields,
}: {
  supplierId: number;
  canonicalFields: CanonicalField[];
}) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<MappingValues>({
    mode: 'controlled',
    initialValues: {
      source_column: '',
      canonical_field: canonicalFields[0] ?? '',
      is_required: false,
    },
    validate: schemaResolver(mappingSchema, { sync: true }),
  });

  const save = useMutation({
    meta: { handlesErrors: true },
    mutationFn: (body: Schemas['ColumnMappingInput']) =>
      unwrap(
        api.POST('/api/suppliers/{supplier_id}/column-mappings', {
          params: { path: { supplier_id: supplierId } },
          body,
        }),
      ),
    onSuccess: async (saved) => {
      notifications.show({
        color: 'green',
        message: `Saved: “${saved.source_column}” → ${saved.canonical_field}`,
      });
      form.reset();
      await queryClient.invalidateQueries({
        queryKey: queryKeys.suppliers.columnMappings(supplierId),
      });
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
          // The select only offers the server's fields; find() narrows the string to that union.
          const canonical = canonicalFields.find((f) => f === v.canonical_field);
          if (!canonical) return;
          setFormError(null);
          save.mutate({
            source_column: v.source_column.trim(),
            canonical_field: canonical,
            is_required: v.is_required,
          });
        })}
      >
        <Stack gap="md">
          <div>
            <Title order={3}>Add or update a single mapping</Title>
            <Text size="sm" c="dimmed" mt={4}>
              Use the exact header text from the supplier file, or{' '}
              <Code>contains:&lt;text&gt;</Code> to match any header containing that text. Saving an
              existing source column replaces its mapping.
            </Text>
          </div>
          {formError && (
            <Alert color="red" title="Couldn't save the mapping">
              {formError}
            </Alert>
          )}
          <SimpleGrid cols={{ base: 1, sm: 2 }}>
            <TextInput
              label="Source column"
              withAsterisk
              {...form.getInputProps('source_column')}
            />
            <Select
              label="Canonical field"
              withAsterisk
              data={canonicalFields}
              searchable
              allowDeselect={false}
              {...form.getInputProps('canonical_field')}
            />
          </SimpleGrid>
          <Checkbox label="Required" {...form.getInputProps('is_required', { type: 'checkbox' })} />
          <Group>
            <Button type="submit" loading={save.isPending}>
              Save mapping
            </Button>
          </Group>
        </Stack>
      </form>
    </Paper>
  );
}

function SupplierMappings({ supplierId }: { supplierId: number }) {
  const mappings = useQuery(columnMappingsQuery(supplierId));
  const meta = useQuery(metaQuery);

  return (
    <Stack gap="lg">
      {mappings.error ? (
        <QueryError
          title="Couldn't load column mappings"
          error={mappings.error}
          onRetry={() => void mappings.refetch()}
        />
      ) : mappings.data?.length === 0 ? (
        <EmptyState title="No column mappings yet">
          Add them below, one at a time or inferred from a sample file.
        </EmptyState>
      ) : (
        <DataTable
          label="Column mappings"
          fetching={mappings.isPending}
          records={mappings.data}
          columns={[
            { accessor: 'source_column', title: 'Source column', cellsClassName: 'mono' },
            { accessor: 'canonical_field', title: 'Canonical field' },
            {
              accessor: 'is_required',
              title: 'Required',
              render: (m) => (m.is_required ? <Badge color="ledger">Required</Badge> : 'No'),
            },
          ]}
        />
      )}
      <InferFromSample supplierId={supplierId} />
      {meta.data ? (
        <SingleMappingForm
          key={supplierId}
          supplierId={supplierId}
          canonicalFields={meta.data.canonical_fields}
        />
      ) : meta.error ? (
        <QueryError title="Couldn't load field options" error={meta.error} />
      ) : (
        <Skeleton height={200} />
      )}
    </Stack>
  );
}

export function ColumnMapsTab() {
  const suppliers = useQuery(suppliersQuery());
  const [supplierId, selectSupplier] = useSelectedSupplier(suppliers.data);

  if (suppliers.error) {
    return (
      <QueryError
        title="Couldn't load suppliers"
        error={suppliers.error}
        onRetry={() => void suppliers.refetch()}
      />
    );
  }
  if (suppliers.isPending) return <Skeleton height={60} maw={360} />;
  if (suppliers.data.length === 0) {
    return (
      <EmptyState title="No suppliers yet">
        <Anchor component={Link} to={routes.admin.suppliers} inherit>
          Add a supplier
        </Anchor>{' '}
        first.
      </EmptyState>
    );
  }
  return (
    <Stack gap="lg">
      <div>
        <Title order={3}>Supplier column mappings</Title>
        <Text size="sm" c="dimmed">
          Maps each supplier&apos;s raw file headers to the engine&apos;s canonical fields.
        </Text>
      </div>
      <SupplierSelect suppliers={suppliers.data} value={supplierId} onChange={selectSupplier} />
      {supplierId !== null && <SupplierMappings key={supplierId} supplierId={supplierId} />}
    </Stack>
  );
}
