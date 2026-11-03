import {
  Alert,
  Button,
  Checkbox,
  Group,
  NumberInput,
  SegmentedControl,
  Select,
  SimpleGrid,
  Stack,
  Text,
  Textarea,
  TextInput,
} from '@mantine/core';
import { schemaResolver, useForm } from '@mantine/form';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { z } from 'zod';

import { api, unwrap } from '@/api/client';
import type { Schemas } from '@/api/types';
import { applyServerErrors, blankToNull } from '@/lib/forms';
import { formulaNameNotices } from '@/lib/tierC';
import { queryKeys } from '@/queries/keys';

import { Notices } from './Notices';

type Rule = Schemas['ExtractionRule'];
type RuleInput = Schemas['ExtractionRuleInput'];
type Method = RuleInput['extraction_method'];
type Meta = Schemas['Meta'];

const int = (message: string) => z.number({ error: message }).int(message);

const schema = z
  .object({
    rule_name: z.string().trim().min(1, 'Enter a rule name'),
    sheet_name: z.string(),
    extraction_method: z.enum([
      'named_cell',
      'label_lookup',
      'label_range_sum',
      'column_sum',
      'formula',
      'manual_entry',
    ]),
    cell_address: z.string(),
    label_text: z.string(),
    label_match_mode: z.enum(['exact', 'contains', 'startswith', 'fuzzy']),
    label_match_threshold: int('Enter 0–100').min(0, 'Enter 0–100').max(100, 'Enter 0–100'),
    value_row_offset: int('Enter a whole number'),
    value_col_offset: int('Enter a whole number'),
    range_axis: z.enum(['column', 'row']),
    stop_label_text: z.string(),
    column_spec: z.enum(['index', 'letter']),
    column_index: int('Enter a column index').min(0, 'Must be 0 or more'),
    column_letter: z.string(),
    header_row: int('Enter a row number').min(0, 'Must be 0 or more'),
    formula_expression: z.string(),
    is_sales_amount: z.boolean(),
    sort_order: int('Enter a whole number'),
  })
  .superRefine((v, ctx) => {
    const need = (path: keyof typeof v, message: string) => {
      ctx.addIssue({ code: 'custom', path: [path], message });
    };
    switch (v.extraction_method) {
      case 'named_cell':
        if (!v.cell_address.trim())
          need('cell_address', 'Cell Address is required for named_cell.');
        break;
      case 'label_lookup':
      case 'label_range_sum':
        if (!v.label_text.trim()) need('label_text', 'Label Text is required for this method.');
        break;
      case 'column_sum':
        if (v.column_spec === 'letter' && !/^[A-Za-z]{1,3}$/.test(v.column_letter.trim())) {
          need('column_letter', 'Enter a column letter (A–ZZZ)');
        }
        break;
      case 'formula':
        if (!v.formula_expression.trim()) {
          need('formula_expression', 'Formula Expression is required for formula.');
        }
        break;
      case 'manual_entry':
        break;
    }
  });
type Values = z.infer<typeof schema>;

/** Legacy defaults: a new rule, or the saved rule's values (F13: edit starts from what's saved). */
function initialValues(rule: Rule | null): Values {
  return {
    rule_name: rule?.rule_name ?? '',
    sheet_name: rule?.sheet_name ?? '',
    extraction_method: rule?.extraction_method ?? 'named_cell',
    cell_address: rule?.cell_address ?? '',
    label_text: rule?.label_text ?? '',
    label_match_mode: rule?.label_match_mode ?? 'exact',
    label_match_threshold: rule?.label_match_threshold ?? 90,
    value_row_offset: rule?.value_row_offset ?? 1,
    value_col_offset: rule?.value_col_offset ?? 0,
    range_axis: rule?.range_axis ?? 'column',
    stop_label_text: rule?.stop_label_text ?? '',
    column_spec: rule?.column_letter ? 'letter' : 'index',
    column_index: rule?.column_index ?? 0,
    column_letter: rule?.column_letter ?? '',
    header_row: rule?.header_row ?? 0,
    formula_expression: rule?.formula_expression ?? '',
    is_sales_amount: rule?.is_sales_amount ?? false,
    sort_order: rule?.sort_order ?? 0,
  };
}

/** Only the fields the chosen method uses; the server resets the rest to defaults. */
function toInput(v: Values): RuleInput {
  const base = {
    extraction_method: v.extraction_method,
    rule_name: v.rule_name.trim(),
    sheet_name: blankToNull(v.sheet_name),
    is_sales_amount: v.is_sales_amount,
    sort_order: v.sort_order,
  };
  const label = {
    label_text: v.label_text.trim(),
    label_match_mode: v.label_match_mode,
    ...(v.label_match_mode === 'fuzzy' ? { label_match_threshold: v.label_match_threshold } : {}),
  };
  switch (v.extraction_method) {
    case 'named_cell':
      return { ...base, cell_address: v.cell_address.trim() };
    case 'label_lookup':
      return {
        ...base,
        ...label,
        value_row_offset: v.value_row_offset,
        value_col_offset: v.value_col_offset,
      };
    case 'label_range_sum':
      return {
        ...base,
        ...label,
        range_axis: v.range_axis,
        stop_label_text: blankToNull(v.stop_label_text),
      };
    case 'column_sum':
      return {
        ...base,
        header_row: v.header_row,
        ...(v.column_spec === 'index'
          ? { column_index: v.column_index }
          : { column_letter: v.column_letter.trim().toUpperCase() }),
      };
    case 'formula':
      return { ...base, formula_expression: v.formula_expression.trim() };
    case 'manual_entry':
      return base;
  }
}

interface ExtractionRuleFormProps {
  supplierId: number;
  meta: Meta;
  rule: Rule | null;
  /** Every saved rule name for this supplier. */
  ruleNames: string[];
  onSaved: (rule: Rule) => void;
  onDeleted: () => void;
}

export function ExtractionRuleForm({
  supplierId,
  meta,
  rule,
  ruleNames,
  onSaved,
  onDeleted,
}: ExtractionRuleFormProps) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<Values>({
    mode: 'controlled',
    initialValues: initialValues(rule),
    validate: schemaResolver(schema, { sync: true }),
  });
  const v = form.getValues();
  const method: Method = v.extraction_method;

  // A rename cascades to allocation rules, so both lists are refreshed.
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.suppliers.extractionRules(supplierId) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.suppliers.allocationRules(supplierId) }),
    ]);

  const save = useMutation({
    meta: { handlesErrors: true },
    mutationFn: (body: RuleInput) =>
      rule
        ? unwrap(
            api.PUT('/api/suppliers/{supplier_id}/extraction-rules/{rule_id}', {
              params: { path: { supplier_id: supplierId, rule_id: rule.id } },
              body,
            }),
          )
        : unwrap(
            api.POST('/api/suppliers/{supplier_id}/extraction-rules', {
              params: { path: { supplier_id: supplierId } },
              body,
            }),
          ),
    onSuccess: async (result) => {
      notifications.show({
        color: 'green',
        message: `Extraction rule “${result.rule.rule_name}” saved.`,
      });
      for (const n of result.notices) {
        notifications.show({
          color: n.level === 'warning' ? 'orange' : 'ledger',
          message: n.message,
        });
      }
      await refresh();
      onSaved(result.rule);
    },
    onError: (error) => {
      setFormError(applyServerErrors(form, error));
    },
  });

  const remove = useMutation({
    mutationFn: (target: Rule) =>
      unwrap(
        api.DELETE('/api/suppliers/{supplier_id}/extraction-rules/{rule_id}', {
          params: { path: { supplier_id: supplierId, rule_id: target.id } },
        }),
      ),
    onSuccess: async (_data, target) => {
      notifications.show({
        color: 'green',
        message: `Deleted extraction rule “${target.rule_name}”.`,
      });
      await refresh();
      onDeleted();
    },
  });

  const otherNames = ruleNames.filter((n) => n !== rule?.rule_name);
  const methodOptions = meta.extraction_methods.map((m) => ({ value: m, label: m }));

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
          <Alert color="red" title="Couldn't save the extraction rule">
            {formError}
          </Alert>
        )}
        <SimpleGrid cols={{ base: 1, sm: 3 }}>
          <TextInput label="Rule name" withAsterisk {...form.getInputProps('rule_name')} />
          <TextInput
            label="Sheet name"
            description="Blank = first sheet"
            {...form.getInputProps('sheet_name')}
          />
          <Select
            label="Extraction method"
            data={methodOptions}
            allowDeselect={false}
            {...form.getInputProps('extraction_method')}
          />
        </SimpleGrid>

        {method === 'named_cell' && (
          <TextInput
            label="Cell address"
            withAsterisk
            placeholder="E8"
            maw={200}
            {...form.getInputProps('cell_address')}
          />
        )}

        {(method === 'label_lookup' || method === 'label_range_sum') && (
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <TextInput label="Label text" withAsterisk {...form.getInputProps('label_text')} />
            <Select
              label="Label match mode"
              data={meta.label_match_modes}
              allowDeselect={false}
              {...form.getInputProps('label_match_mode')}
            />
            {v.label_match_mode === 'fuzzy' && (
              <NumberInput
                label="Fuzzy match threshold"
                description="0–100"
                min={0}
                max={100}
                allowDecimal={false}
                {...form.getInputProps('label_match_threshold')}
              />
            )}
          </SimpleGrid>
        )}
        {method === 'label_lookup' && (
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <NumberInput
              label="Value row offset"
              allowDecimal={false}
              {...form.getInputProps('value_row_offset')}
            />
            <NumberInput
              label="Value column offset"
              allowDecimal={false}
              {...form.getInputProps('value_col_offset')}
            />
          </SimpleGrid>
        )}
        {method === 'label_range_sum' && (
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <Select
              label="Range axis"
              data={meta.range_axes}
              allowDeselect={false}
              {...form.getInputProps('range_axis')}
            />
            <TextInput
              label="Stop label text"
              description="Optional boundary"
              {...form.getInputProps('stop_label_text')}
            />
          </SimpleGrid>
        )}

        {method === 'column_sum' && (
          <Stack gap="sm">
            <Stack gap={6}>
              <Text size="sm" fw={500} id="column-spec-label">
                Specify the column by
              </Text>
              <SegmentedControl
                aria-labelledby="column-spec-label"
                w="fit-content"
                data={[
                  { value: 'index', label: 'Index' },
                  { value: 'letter', label: 'Letter' },
                ]}
                {...form.getInputProps('column_spec')}
              />
            </Stack>
            <SimpleGrid cols={{ base: 1, sm: 3 }}>
              {v.column_spec === 'index' ? (
                <NumberInput
                  label="Column index"
                  description="0-based"
                  withAsterisk
                  min={0}
                  allowDecimal={false}
                  {...form.getInputProps('column_index')}
                />
              ) : (
                <TextInput
                  label="Column letter"
                  withAsterisk
                  placeholder="F"
                  {...form.getInputProps('column_letter')}
                />
              )}
              <NumberInput
                label="Header row"
                description="0-indexed; the sum starts below it"
                min={0}
                allowDecimal={false}
                {...form.getInputProps('header_row')}
              />
            </SimpleGrid>
          </Stack>
        )}

        {method === 'formula' && (
          <Stack gap="xs">
            {otherNames.length > 0 && (
              <Text size="xs" c="dimmed">
                Other rules you can reference: {otherNames.join(', ')}
              </Text>
            )}
            <Textarea
              label="Formula expression"
              withAsterisk
              placeholder="Gross - Fees Withheld"
              autosize
              minRows={2}
              {...form.getInputProps('formula_expression')}
            />
            {v.formula_expression.trim() && <Notices notices={formulaNameNotices(ruleNames)} />}
          </Stack>
        )}

        {method === 'manual_entry' && (
          <Alert color="blue" variant="light">
            manual_entry has no automated parsing. The confirmed total is entered on the Upload page
            each time a file for this supplier is processed.
          </Alert>
        )}

        <Group align="flex-end" gap="xl">
          <NumberInput
            label="Sort order"
            allowDecimal={false}
            w={140}
            {...form.getInputProps('sort_order')}
          />
          <Checkbox
            label="Is sales amount (feeds volume instead of fee amount)"
            mb={8}
            {...form.getInputProps('is_sales_amount', { type: 'checkbox' })}
          />
        </Group>

        <Group>
          <Button type="submit" loading={save.isPending}>
            Save extraction rule
          </Button>
          {rule && (
            <Button
              variant="default"
              color="red"
              loading={remove.isPending}
              onClick={() => {
                modals.openConfirmModal({
                  title: `Delete “${rule.rule_name}”?`,
                  children: (
                    <Text size="sm">
                      This can&apos;t be undone. Allocation rules that use it must be deleted first.
                    </Text>
                  ),
                  labels: { confirm: 'Delete', cancel: 'Cancel' },
                  confirmProps: { color: 'red' },
                  onConfirm: () => {
                    remove.mutate(rule);
                  },
                });
              }}
            >
              Delete extraction rule
            </Button>
          )}
        </Group>
      </Stack>
    </form>
  );
}
