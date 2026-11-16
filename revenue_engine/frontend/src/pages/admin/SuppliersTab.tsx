import {
  Accordion,
  Alert,
  Badge,
  Button,
  Checkbox,
  Fieldset,
  Group,
  Modal,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
  Title,
} from '@mantine/core';
import { schemaResolver, useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { IconPencil, IconPlus, IconUpload } from '@tabler/icons-react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { z } from 'zod';

import { api, formData, unwrap } from '@/api/client';
import type { Schemas } from '@/api/types';
import { DataTable } from '@/components/DataTable';
import { EmptyState } from '@/components/EmptyState';
import { FileDropzone } from '@/components/FileDropzone';
import { QueryError } from '@/components/QueryError';
import { applyServerErrors, blankToNull } from '@/lib/forms';
import { formatDateTime, formatInteger, formatText } from '@/lib/format';
import { queryKeys } from '@/queries/keys';
import { metaQuery } from '@/queries/reference';
import { gpoKeyQuery, suppliersQuery, type Supplier } from '@/queries/suppliers';

type SupplierInput = Schemas['SupplierInput'];
type Meta = Schemas['Meta'];

const threshold = z.number({ error: 'Enter 0–100' }).int().min(0).max(100);

const supplierSchema = z.object({
  name: z.string().trim().min(1, 'Enter the supplier name'),
  description: z.string(),
  contract_id: z.string(),
  revenue_type: z.string().min(1),
  keep_return_default: z.string().min(1),
  payor_filter_name: z.string(),
  payment_id_source: z.enum(['ledger', 'manual', 'column']),
  skip_rows_where_col_null: z.string(),
  sheet_name: z.string(),
  header_row: z.number({ error: 'Enter a row number' }).int().min(0),
  /** 0 = no limit (stored as null), as in the legacy form. */
  max_read_columns: z.number({ error: 'Enter 0 for no limit' }).int().min(0),
  job_type: z.enum(['row_level', 'pivot_allocation']),
  requires_manual_confirmation: z.boolean(),
  identity_mode: z.enum(['member_match', 'gpo_key']),
  volume_mode: z.enum(['sum', 'blank']),
  /** Empty string = no fee rate. */
  fee_rate: z.union([z.number().min(0, 'Must be 0 or more'), z.literal('')]),
  fuzzy_zip_state_threshold: threshold,
  fuzzy_city_state_threshold: threshold,
  fuzzy_state_threshold: threshold,
  fuzzy_nationwide_threshold: threshold,
});
type SupplierFormValues = z.infer<typeof supplierSchema>;

/** New-supplier defaults: the first option of each select, as the legacy form; thresholds from the API model. */
function newSupplierValues(meta: Meta): SupplierFormValues {
  return {
    name: '',
    description: '',
    contract_id: '',
    revenue_type: meta.revenue_types[0] ?? 'Admin Fee',
    keep_return_default: meta.keep_return_options[0] ?? 'Keep',
    payor_filter_name: '',
    payment_id_source: meta.payment_id_sources[0] ?? 'ledger',
    skip_rows_where_col_null: '',
    sheet_name: '',
    header_row: 0,
    max_read_columns: 0,
    job_type: meta.job_types[0] ?? 'row_level',
    requires_manual_confirmation: false,
    identity_mode: 'member_match',
    volume_mode: 'sum',
    fee_rate: '',
    fuzzy_zip_state_threshold: 75,
    fuzzy_city_state_threshold: 75,
    fuzzy_state_threshold: 80,
    fuzzy_nationwide_threshold: 85,
  };
}

function supplierValues(s: Supplier): SupplierFormValues {
  return {
    name: s.name,
    description: s.description ?? '',
    contract_id: s.contract_id ?? '',
    revenue_type: s.revenue_type,
    keep_return_default: s.keep_return_default,
    payor_filter_name: s.payor_filter_name ?? '',
    payment_id_source: s.payment_id_source,
    skip_rows_where_col_null: s.skip_rows_where_col_null ?? '',
    sheet_name: s.sheet_name ?? '',
    header_row: s.header_row,
    max_read_columns: s.max_read_columns ?? 0,
    job_type: s.job_type,
    requires_manual_confirmation: s.requires_manual_confirmation,
    identity_mode: s.identity_mode,
    volume_mode: s.volume_mode,
    fee_rate: s.fee_rate ?? '',
    fuzzy_zip_state_threshold: s.fuzzy_zip_state_threshold,
    fuzzy_city_state_threshold: s.fuzzy_city_state_threshold,
    fuzzy_state_threshold: s.fuzzy_state_threshold,
    fuzzy_nationwide_threshold: s.fuzzy_nationwide_threshold,
  };
}

function toInput(v: SupplierFormValues): SupplierInput {
  return {
    name: v.name.trim(),
    description: blankToNull(v.description),
    contract_id: blankToNull(v.contract_id),
    revenue_type: v.revenue_type,
    keep_return_default: v.keep_return_default,
    payor_filter_name: blankToNull(v.payor_filter_name),
    payment_id_source: v.payment_id_source,
    skip_rows_where_col_null: blankToNull(v.skip_rows_where_col_null),
    sheet_name: blankToNull(v.sheet_name),
    header_row: v.header_row,
    max_read_columns: v.max_read_columns === 0 ? null : v.max_read_columns,
    job_type: v.job_type,
    requires_manual_confirmation: v.requires_manual_confirmation,
    identity_mode: v.identity_mode,
    volume_mode: v.volume_mode,
    fee_rate: v.fee_rate === '' ? null : v.fee_rate,
    fuzzy_zip_state_threshold: v.fuzzy_zip_state_threshold,
    fuzzy_city_state_threshold: v.fuzzy_city_state_threshold,
    fuzzy_state_threshold: v.fuzzy_state_threshold,
    fuzzy_nationwide_threshold: v.fuzzy_nationwide_threshold,
  };
}

const options = (values: readonly string[]) => values.map((v) => ({ value: v, label: v }));

const IDENTITY_LABELS: Record<string, string> = {
  member_match: 'Match to members (Review queue for unmatched)',
  gpo_key: 'Verify GPO ID against a key file (exceptions report)',
};
const VOLUME_LABELS: Record<string, string> = {
  sum: 'Sum of sales amounts',
  blank: 'Leave blank (file carries revenue only)',
};
const labelled = (values: readonly string[], labels: Record<string, string>) =>
  values.map((v) => ({ value: v, label: labels[v] ?? v }));

/** Key file column names; blank = the API's defaults (engine/gpo_key.KeyColumns: the Medline Account Key headers). */
const KEY_COLUMNS = [
  {
    field: 'account_number_column',
    label: 'Account number column',
    placeholder: 'Medline Account Number',
  },
  {
    field: 'account_name_column',
    label: 'Account name column',
    placeholder: 'Medline Account Name',
  },
  { field: 'address_column', label: 'Address column', placeholder: 'Medline Street' },
  { field: 'gpo_id_column', label: 'GPO ID column', placeholder: 'Final GPO ID' },
] as const;
type KeyColumnField = (typeof KEY_COLUMNS)[number]['field'];

/** The supplier's GPO key: what is loaded, and replacing it from a CSV/XLSX file. */
function GpoKeyPanel({ supplier, extensions }: { supplier: Supplier; extensions: string[] }) {
  const queryClient = useQueryClient();
  const key = useQuery(gpoKeyQuery(supplier.id));
  const [file, setFile] = useState<File | null>(null);
  const [columns, setColumns] = useState<Record<KeyColumnField, string>>({
    account_number_column: '',
    account_name_column: '',
    address_column: '',
    gpo_id_column: '',
  });
  const upload = useMutation({
    mutationFn: (f: File) =>
      unwrap(
        api.PUT('/api/suppliers/{supplier_id}/gpo-key', {
          params: { path: { supplier_id: supplier.id } },
          body: {
            file: f,
            ...Object.fromEntries(
              Object.entries(columns).map(([k, v]) => [k, v.trim() || undefined]),
            ),
          },
          bodySerializer: formData,
        }),
      ),
    onSuccess: async (result) => {
      notifications.show({
        color: 'green',
        title: 'GPO key loaded',
        message: `${formatInteger(result.loaded)} entries loaded${
          result.skipped_blank_account
            ? `; ${formatInteger(result.skipped_blank_account)} rows without an account number skipped`
            : ''
        }.`,
      });
      setFile(null);
      await queryClient.invalidateQueries({ queryKey: queryKeys.suppliers.gpoKey(supplier.id) });
    },
  });
  const k = key.data;
  return (
    <Fieldset legend="GPO key">
      <Stack gap="sm">
        <Text size="sm">
          {k && k.entry_count > 0
            ? `${formatInteger(k.entry_count)} entries for ${formatInteger(k.account_count)} accounts, from ${formatText(k.source_file_name)} (loaded ${formatDateTime(k.loaded_at)}).`
            : 'No key loaded: every row of this supplier’s files will go to the exceptions report.'}
        </Text>
        <Text size="xs" c="dimmed">
          Rows are verified by account number, account name and address; the key’s GPO ID is used.
          Loading a file replaces the whole key.
        </Text>
        <FileDropzone label="Key file" extensions={extensions} file={file} onChange={setFile} />
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          {KEY_COLUMNS.map((c) => (
            <TextInput
              key={c.field}
              label={c.label}
              placeholder={c.placeholder}
              value={columns[c.field]}
              onChange={(e) => {
                const value = e.currentTarget.value;
                setColumns((cols) => ({ ...cols, [c.field]: value }));
              }}
            />
          ))}
        </SimpleGrid>
        {upload.error && (
          <Alert color="red" title="Couldn't load the key">
            {upload.error.message}
          </Alert>
        )}
        <Group>
          <Button
            leftSection={<IconUpload size={16} />}
            disabled={!file}
            loading={upload.isPending}
            onClick={() => {
              if (file) upload.mutate(file);
            }}
          >
            Load key
          </Button>
        </Group>
      </Stack>
    </Fieldset>
  );
}

interface SupplierFormProps {
  meta: Meta;
  /** null = add a new supplier. */
  supplier: Supplier | null;
  onDone: () => void;
}

export function SupplierForm({ meta, supplier, onDone }: SupplierFormProps) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<SupplierFormValues>({
    mode: 'controlled',
    initialValues: supplier ? supplierValues(supplier) : newSupplierValues(meta),
    validate: schemaResolver(supplierSchema, { sync: true }),
  });

  const save = useMutation({
    meta: { handlesErrors: true },
    mutationFn: (body: SupplierInput) =>
      supplier
        ? unwrap(
            api.PUT('/api/suppliers/{supplier_id}', {
              params: { path: { supplier_id: supplier.id } },
              body,
            }),
          )
        : unwrap(api.POST('/api/suppliers', { body })),
    onSuccess: async (saved) => {
      notifications.show({
        color: 'green',
        title: 'Supplier saved',
        message: `Saved “${saved.name}”.`,
      });
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.suppliers.all }),
        queryClient.invalidateQueries({ queryKey: queryKeys.dashboard }),
      ]);
      onDone();
    },
    onError: (error) => {
      setFormError(applyServerErrors(form, error));
    },
  });

  return (
    <form
      noValidate
      onSubmit={form.onSubmit((values) => {
        setFormError(null);
        save.mutate(toInput(values));
      })}
    >
      <Stack gap="md">
        {formError && (
          <Alert color="red" title="Couldn't save the supplier">
            {formError}
          </Alert>
        )}
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput
            label="Supplier name"
            withAsterisk
            placeholder="Guerbet"
            data-autofocus
            {...form.getInputProps('name')}
          />
          <TextInput label="Salesforce contract ID" {...form.getInputProps('contract_id')} />
        </SimpleGrid>
        <TextInput label="Description" {...form.getInputProps('description')} />
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Select
            label="Revenue type"
            data={options(meta.revenue_types)}
            allowDeselect={false}
            {...form.getInputProps('revenue_type')}
          />
          <Select
            label="Default keep/return"
            data={options(meta.keep_return_options)}
            allowDeselect={false}
            {...form.getInputProps('keep_return_default')}
          />
          <TextInput
            label="Payor filter name"
            description="Matches the payor in the payment ledger"
            placeholder="Guerbet"
            {...form.getInputProps('payor_filter_name')}
          />
          <Select
            label="Payment ID source"
            data={options(meta.payment_id_sources)}
            allowDeselect={false}
            {...form.getInputProps('payment_id_source')}
          />
        </SimpleGrid>

        <Title order={4} mt="xs">
          Reading the file
        </Title>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput
            label="Sheet name"
            description="Blank = first sheet"
            {...form.getInputProps('sheet_name')}
          />
          <TextInput
            label="Strip summary rows where this column is blank"
            placeholder="JDE Customer Number"
            {...form.getInputProps('skip_rows_where_col_null')}
          />
          <NumberInput
            label="Header row"
            description="0-indexed"
            min={0}
            allowDecimal={false}
            {...form.getInputProps('header_row')}
          />
          <NumberInput
            label="Max columns to read"
            description="0 = no limit"
            min={0}
            allowDecimal={false}
            {...form.getInputProps('max_read_columns')}
          />
          <Select
            label="Job type"
            description="row_level = facility rows (Tier A/B); pivot_allocation = aggregate workpaper (Tier C)"
            data={options(meta.job_types)}
            allowDeselect={false}
            {...form.getInputProps('job_type')}
          />
          <Select
            label="Facility identity"
            description="Row-level suppliers only"
            data={labelled(meta.identity_modes, IDENTITY_LABELS)}
            allowDeselect={false}
            {...form.getInputProps('identity_mode')}
          />
          <Select
            label="Volume"
            data={labelled(meta.volume_modes, VOLUME_LABELS)}
            allowDeselect={false}
            {...form.getInputProps('volume_mode')}
          />
        </SimpleGrid>
        <Checkbox
          label="Requires manual confirmation before completing (recommended for new Tier C suppliers)"
          {...form.getInputProps('requires_manual_confirmation', { type: 'checkbox' })}
        />

        <Accordion variant="contained">
          <Accordion.Item value="matching">
            <Accordion.Control>Fee rate and fuzzy-match thresholds</Accordion.Control>
            <Accordion.Panel>
              <Text size="xs" c="dimmed" mb="sm">
                Not editable in the legacy app. Saved values are kept unless you change them here.
              </Text>
              <SimpleGrid cols={{ base: 1, sm: 2 }}>
                <NumberInput
                  label="Fee rate"
                  description="Blank = none"
                  min={0}
                  decimalScale={6}
                  {...form.getInputProps('fee_rate')}
                />
                <NumberInput
                  label="Zip + state threshold"
                  min={0}
                  max={100}
                  allowDecimal={false}
                  {...form.getInputProps('fuzzy_zip_state_threshold')}
                />
                <NumberInput
                  label="City + state threshold"
                  min={0}
                  max={100}
                  allowDecimal={false}
                  {...form.getInputProps('fuzzy_city_state_threshold')}
                />
                <NumberInput
                  label="State threshold"
                  min={0}
                  max={100}
                  allowDecimal={false}
                  {...form.getInputProps('fuzzy_state_threshold')}
                />
                <NumberInput
                  label="Nationwide threshold"
                  min={0}
                  max={100}
                  allowDecimal={false}
                  {...form.getInputProps('fuzzy_nationwide_threshold')}
                />
              </SimpleGrid>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>

        {supplier && form.getValues().identity_mode === 'gpo_key' && (
          <GpoKeyPanel supplier={supplier} extensions={meta.upload_extensions} />
        )}

        <Group justify="flex-end">
          <Button variant="default" onClick={onDone} disabled={save.isPending}>
            Cancel
          </Button>
          <Button type="submit" loading={save.isPending}>
            Save supplier
          </Button>
        </Group>
      </Stack>
    </form>
  );
}

type Editing = { mode: 'closed' } | { mode: 'new' } | { mode: 'edit'; supplier: Supplier };

export function SuppliersTab() {
  const suppliers = useQuery(suppliersQuery());
  const meta = useQuery(metaQuery);
  const [editing, setEditing] = useState<Editing>({ mode: 'closed' });
  const close = () => {
    setEditing({ mode: 'closed' });
  };

  return (
    <Stack gap="md">
      <Group justify="space-between">
        <div>
          <Title order={2} size="h3">
            Supplier profiles
          </Title>
          <Text size="sm" c="dimmed">
            For a pivot/aggregate-workpaper supplier (job type pivot_allocation), also set up its
            rules in the Tier C Rules tab.
          </Text>
        </div>
        <Button
          leftSection={<IconPlus size={16} />}
          disabled={!meta.data}
          onClick={() => {
            setEditing({ mode: 'new' });
          }}
        >
          Add supplier
        </Button>
      </Group>

      {suppliers.error ? (
        <QueryError
          title="Couldn't load suppliers"
          error={suppliers.error}
          onRetry={() => void suppliers.refetch()}
        />
      ) : suppliers.data?.length === 0 ? (
        <EmptyState title="No suppliers configured">
          Add one with “Add supplier”, or run <code>scripts/seed_guerbet.py</code>.
        </EmptyState>
      ) : (
        <DataTable
          label="Supplier profiles"
          pinLastColumn
          fetching={suppliers.isPending}
          records={suppliers.data}
          columns={[
            { accessor: 'name', title: 'Name' },
            {
              accessor: 'contract_id',
              title: 'Contract ID',
              cellsClassName: 'mono',
              render: (s) => formatText(s.contract_id),
            },
            { accessor: 'revenue_type', title: 'Revenue type' },
            { accessor: 'keep_return_default', title: 'Keep/return' },
            {
              accessor: 'payor_filter_name',
              title: 'Payor filter',
              render: (s) => formatText(s.payor_filter_name),
            },
            { accessor: 'payment_id_source', title: 'Payment ID source' },
            {
              accessor: 'identity_mode',
              title: 'Identity',
              render: (s) =>
                s.identity_mode === 'gpo_key' ? (
                  <Badge color="teal">GPO key</Badge>
                ) : (
                  'Member match'
                ),
            },
            {
              accessor: 'job_type',
              title: 'Job type',
              render: (s) => (
                <Badge color={s.job_type === 'pivot_allocation' ? 'grape' : 'slate'}>
                  {s.job_type}
                </Badge>
              ),
            },
            { accessor: 'header_row', title: 'Header row', textAlign: 'right' },
            {
              accessor: 'max_read_columns',
              title: 'Max cols',
              textAlign: 'right',
              render: (s) => s.max_read_columns ?? 'No limit',
            },
            {
              accessor: 'requires_manual_confirmation',
              title: 'Manual sign-off',
              render: (s) => (s.requires_manual_confirmation ? 'Yes' : 'No'),
            },
            {
              accessor: 'actions',
              title: <span className="sr-only">Actions</span>,
              textAlign: 'right',
              render: (s) => (
                <Button
                  size="compact-sm"
                  variant="subtle"
                  leftSection={<IconPencil size={14} />}
                  disabled={!meta.data}
                  aria-label={`Edit ${s.name}`}
                  onClick={() => {
                    setEditing({ mode: 'edit', supplier: s });
                  }}
                >
                  Edit
                </Button>
              ),
            },
          ]}
        />
      )}

      <Modal
        opened={editing.mode !== 'closed'}
        onClose={close}
        title={editing.mode === 'edit' ? `Edit ${editing.supplier.name}` : 'Add a supplier'}
        size="xl"
      >
        {meta.data && editing.mode !== 'closed' && (
          <SupplierForm
            key={editing.mode === 'edit' ? editing.supplier.id : 'new'}
            meta={meta.data}
            supplier={editing.mode === 'edit' ? editing.supplier : null}
            onDone={close}
          />
        )}
      </Modal>
    </Stack>
  );
}
