import {
  Alert,
  Button,
  Checkbox,
  Group,
  NumberInput,
  Select,
  SimpleGrid,
  Stack,
  Text,
  TextInput,
} from '@mantine/core';
import { schemaResolver, useForm } from '@mantine/form';
import { useDebouncedValue } from '@mantine/hooks';
import { modals } from '@mantine/modals';
import { notifications } from '@mantine/notifications';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { z } from 'zod';

import { api, unwrap } from '@/api/client';
import type { Schemas } from '@/api/types';
import { MemberPicker } from '@/components/MemberPicker';
import { applyServerErrors, blankToNull } from '@/lib/forms';
import { formatText } from '@/lib/format';
import { queryKeys } from '@/queries/keys';
import { allocationDraftNoticesQuery } from '@/queries/suppliers';

import { Notices } from './Notices';

type Rule = Schemas['AllocationRule'];
type RuleInput = Schemas['AllocationRuleInput'];
type Meta = Schemas['Meta'];

const schema = z
  .object({
    extraction_rule_name: z.string().min(1, 'Pick the extraction rule'),
    member: z.object({ id: z.number(), label: z.string() }).nullable(),
    allocation_type: z.enum(['percent', 'fixed_amount', 'remainder']),
    allocation_value: z.union([z.number(), z.literal('')]),
    contract_number: z.string(),
    product_line: z.string(),
    is_active: z.boolean(),
  })
  .superRefine((v, ctx) => {
    if (!v.member) ctx.addIssue({ code: 'custom', path: ['member'], message: 'Select a member' });
    if (v.allocation_type !== 'remainder' && !v.allocation_value) {
      ctx.addIssue({
        code: 'custom',
        path: ['allocation_value'],
        message: 'Allocation Value is required for percent/fixed_amount.',
      });
    }
  });
type Values = z.infer<typeof schema>;

const memberOf = (rule: Rule) => ({
  id: rule.member_id,
  label: `${rule.member_name} (${formatText(rule.entity_code)})`,
});

function initialValues(rule: Rule | null, extractionRuleNames: string[]): Values {
  const linked =
    rule && extractionRuleNames.includes(rule.extraction_rule_name)
      ? rule.extraction_rule_name
      : (extractionRuleNames[0] ?? '');
  return {
    extraction_rule_name: linked,
    member: rule ? memberOf(rule) : null,
    allocation_type: rule?.allocation_type ?? 'percent',
    allocation_value: rule?.allocation_value ?? '',
    contract_number: rule?.contract_number ?? '',
    product_line: rule?.product_line ?? '',
    is_active: rule?.is_active ?? true,
  };
}

interface AllocationRuleFormProps {
  supplierId: number;
  meta: Meta;
  rule: Rule | null;
  extractionRuleNames: string[];
  onSaved: (rule: Rule) => void;
  onDeleted: () => void;
}

export function AllocationRuleForm({
  supplierId,
  meta,
  rule,
  extractionRuleNames,
  onSaved,
  onDeleted,
}: AllocationRuleFormProps) {
  const queryClient = useQueryClient();
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<Values>({
    mode: 'controlled',
    initialValues: initialValues(rule, extractionRuleNames),
    validate: schemaResolver(schema, { sync: true }),
  });
  const v = form.getValues();

  // Live checks against the saved rules (percent total, duplicate remainder).
  const [draft] = useDebouncedValue(
    {
      extraction_rule_name: v.extraction_rule_name,
      allocation_type: v.allocation_type,
      allocation_value:
        v.allocation_type === 'remainder' || v.allocation_value === '' ? null : v.allocation_value,
      rule_id: rule?.id ?? null,
    },
    300,
  );
  const notices = useQuery({
    ...allocationDraftNoticesQuery(supplierId, draft),
    enabled: draft.extraction_rule_name !== '',
  });

  const refresh = () =>
    queryClient.invalidateQueries({ queryKey: queryKeys.suppliers.allocationRules(supplierId) });

  const save = useMutation({
    meta: { handlesErrors: true },
    mutationFn: (body: RuleInput) =>
      rule
        ? unwrap(
            api.PUT('/api/suppliers/{supplier_id}/allocation-rules/{rule_id}', {
              params: { path: { supplier_id: supplierId, rule_id: rule.id } },
              body,
            }),
          )
        : unwrap(
            api.POST('/api/suppliers/{supplier_id}/allocation-rules', {
              params: { path: { supplier_id: supplierId } },
              body,
            }),
          ),
    onSuccess: async (saved) => {
      notifications.show({ color: 'green', message: 'Allocation rule saved.' });
      await refresh();
      onSaved(saved);
    },
    onError: (error) => {
      setFormError(applyServerErrors(form, error));
    },
  });

  const remove = useMutation({
    mutationFn: (target: Rule) =>
      unwrap(
        api.DELETE('/api/suppliers/{supplier_id}/allocation-rules/{rule_id}', {
          params: { path: { supplier_id: supplierId, rule_id: target.id } },
        }),
      ),
    onSuccess: async () => {
      notifications.show({ color: 'green', message: 'Deleted allocation rule.' });
      await refresh();
      onDeleted();
    },
  });

  return (
    <form
      noValidate
      onSubmit={form.onSubmit((values) => {
        // The schema guarantees a member; this narrows the type.
        if (!values.member) return;
        setFormError(null);
        save.mutate({
          extraction_rule_name: values.extraction_rule_name,
          member_id: values.member.id,
          allocation_type: values.allocation_type,
          allocation_value:
            values.allocation_type === 'remainder' || values.allocation_value === ''
              ? null
              : values.allocation_value,
          contract_number: blankToNull(values.contract_number),
          product_line: blankToNull(values.product_line),
          is_active: values.is_active,
        });
      })}
    >
      <Stack gap="md">
        {formError && (
          <Alert color="red" title="Couldn't save the allocation rule">
            {formError}
          </Alert>
        )}
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Select
            label="Extraction rule"
            withAsterisk
            data={extractionRuleNames}
            allowDeselect={false}
            {...form.getInputProps('extraction_rule_name')}
          />
          <MemberPicker
            label="Member"
            withAsterisk
            activeOnly={false}
            value={v.member}
            onChange={(member) => {
              form.setFieldValue('member', member);
            }}
            error={typeof form.errors['member'] === 'string' ? form.errors['member'] : null}
          />
        </SimpleGrid>
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <Select
            label="Allocation type"
            data={meta.allocation_types}
            allowDeselect={false}
            {...form.getInputProps('allocation_type')}
          />
          {v.allocation_type === 'remainder' ? (
            <Text size="sm" c="dimmed" mt={28}>
              Gets whatever is left after the other rules for this extraction rule are applied.
            </Text>
          ) : (
            <NumberInput
              label={v.allocation_type === 'percent' ? 'Allocation (%)' : 'Allocation ($)'}
              withAsterisk
              decimalScale={4}
              {...(v.allocation_type === 'percent' ? { suffix: '%' } : { prefix: '$' })}
              {...form.getInputProps('allocation_value')}
            />
          )}
        </SimpleGrid>
        <Notices notices={notices.data ?? []} />
        <SimpleGrid cols={{ base: 1, sm: 2 }}>
          <TextInput
            label="Contract number"
            description="Optional override"
            {...form.getInputProps('contract_number')}
          />
          <TextInput
            label="Product line"
            description="Optional"
            {...form.getInputProps('product_line')}
          />
        </SimpleGrid>
        <Checkbox label="Active" {...form.getInputProps('is_active', { type: 'checkbox' })} />
        <Group>
          <Button type="submit" loading={save.isPending}>
            Save allocation rule
          </Button>
          {rule && (
            <Button
              variant="default"
              color="red"
              loading={remove.isPending}
              onClick={() => {
                modals.openConfirmModal({
                  title: 'Delete this allocation rule?',
                  children: (
                    <Text size="sm">
                      {rule.extraction_rule_name} → {rule.member_name}. This can&apos;t be undone.
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
              Delete allocation rule
            </Button>
          )}
        </Group>
      </Stack>
    </form>
  );
}
