import { Alert, Button, Group, Paper, SimpleGrid, Stack, Text, TextInput } from '@mantine/core';
import { schemaResolver, useForm } from '@mantine/form';
import { notifications } from '@mantine/notifications';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { z } from 'zod';

import { api, unwrap } from '@/api/client';
import type { Schemas } from '@/api/types';
import { applyServerErrors, blankToNull } from '@/lib/forms';
import { memberLabel } from '@/lib/members';
import { queryKeys } from '@/queries/keys';
import { useAppStore } from '@/stores';

type ReviewItem = Schemas['ReviewItem'];

const facilitySchema = z.object({
  name: z.string().trim().min(1, 'Enter the facility name'),
  premier_entity_code: z.string(),
  city: z.string(),
  state: z.string(),
  zip: z.string(),
});
type FacilityValues = z.infer<typeof facilitySchema>;

/** "Not in the database? Add it as a new facility", prefilled from the unmatched row. */
export function NewFacilityForm({ item }: { item: ReviewItem }) {
  const queryClient = useQueryClient();
  const recordCreatedMember = useAppStore((s) => s.recordCreatedMember);
  const [formError, setFormError] = useState<string | null>(null);
  const form = useForm<FacilityValues>({
    mode: 'controlled',
    initialValues: {
      name: item.customer_name_sample ?? '',
      premier_entity_code: '',
      city: item.city ?? '',
      state: item.state ?? '',
      zip: item.zip ?? '',
    },
    validate: schemaResolver(facilitySchema, { sync: true }),
  });
  const entityCode = form.getValues().premier_entity_code.trim();

  const create = useMutation({
    meta: { handlesErrors: true },
    mutationFn: (v: FacilityValues) =>
      unwrap(
        api.POST('/api/members', {
          body: {
            name: v.name.trim(),
            premier_entity_code: blankToNull(v.premier_entity_code),
            city: blankToNull(v.city),
            state: blankToNull(v.state),
            zip: blankToNull(v.zip),
          },
        }),
      ),
    onSuccess: async (member) => {
      recordCreatedMember(item.id, { id: member.id, label: memberLabel(member) });
      notifications.show({
        color: 'green',
        title: 'Facility created',
        message: `Created “${member.name}” (member id ${member.id}). Approve to match it.`,
      });
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
    <Paper p="md" bg="var(--mantine-color-default-hover)" withBorder={false}>
      <form
        noValidate
        onSubmit={form.onSubmit((v) => {
          setFormError(null);
          create.mutate(v);
        })}
      >
        <Stack gap="sm">
          <Text fw={600} size="sm">
            Not in the database? Add it as a new facility
          </Text>
          {formError && (
            <Alert color="red" title="Couldn't create the facility">
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
          </SimpleGrid>
          {!entityCode && (
            <Text size="xs" c="orange.8">
              Without an entity code, this facility&apos;s revenue still counts toward the job
              total, but its output row uses a placeholder PEC until a real one is added in Admin ›
              Members.
            </Text>
          )}
          <SimpleGrid cols={{ base: 1, sm: 3 }}>
            <TextInput label="City" {...form.getInputProps('city')} />
            <TextInput label="State" {...form.getInputProps('state')} />
            <TextInput label="Zip" {...form.getInputProps('zip')} />
          </SimpleGrid>
          <Group>
            <Button type="submit" variant="default" loading={create.isPending}>
              Create facility &amp; match
            </Button>
          </Group>
        </Stack>
      </form>
    </Paper>
  );
}
