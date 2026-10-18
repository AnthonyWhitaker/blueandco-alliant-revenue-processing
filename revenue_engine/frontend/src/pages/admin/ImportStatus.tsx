import { Alert, Button, Group, Loader, Text } from '@mantine/core';
import { IconAlertTriangle, IconCircleCheck } from '@tabler/icons-react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { useEffect, type ReactNode } from 'react';

import { ApiError } from '@/api/client';
import type { Schemas, TaskKind } from '@/api/types';
import { taskQuery } from '@/queries/members';
import { useActiveImport } from '@/stores/hooks';
import { useAppStore } from '@/stores';

type Task = Schemas['Task'];

interface ImportStatusProps {
  kind: TaskKind;
  /** What success means, e.g. "Loaded 1,204 members". */
  describeSuccess: (task: Task) => ReactNode;
  /** Query keys to refresh once the import has succeeded. */
  onSucceeded: () => Promise<unknown>;
}

/** Progress and outcome of the background import the user started (see stores/importsSlice). */
export function ImportStatus({ kind, describeSuccess, onSucceeded }: ImportStatusProps) {
  const taskId = useActiveImport(kind);
  const forgetImport = useAppStore((s) => s.forgetImport);
  const { data: task, error } = useQuery({ ...taskQuery(taskId ?? ''), enabled: taskId !== null });
  const queryClient = useQueryClient();
  const status = task?.status;

  useEffect(() => {
    if (status === 'succeeded') void onSucceeded();
    // Run once per finished task, not on every render of a new callback.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status, task?.id]);

  // The API keeps tasks in memory; after a restart the id is gone for good.
  const gone = error instanceof ApiError && error.status === 404;
  useEffect(() => {
    if (gone) forgetImport(kind);
  }, [gone, forgetImport, kind]);

  if (taskId === null || gone) return null;

  const dismiss = () => {
    queryClient.removeQueries({ queryKey: taskQuery(taskId).queryKey });
    forgetImport(kind);
  };

  if (error) {
    return (
      <Alert color="red" icon={<IconAlertTriangle />} title="Couldn't check the import">
        <Text size="sm">{error.message}</Text>
      </Alert>
    );
  }

  if (!task || status === 'queued' || status === 'running') {
    return (
      <Alert color="ledger" variant="light" role="status">
        <Group gap="sm" wrap="nowrap">
          <Loader size="xs" />
          <Text size="sm">
            {status === 'queued' ? 'Waiting to start…' : 'Importing… you can leave this page.'}
          </Text>
        </Group>
      </Alert>
    );
  }

  const ok = status === 'succeeded';
  return (
    <Alert
      color={ok ? 'green' : 'red'}
      icon={ok ? <IconCircleCheck /> : <IconAlertTriangle />}
      title={ok ? 'Import finished' : 'Import failed'}
      withCloseButton
      closeButtonLabel="Dismiss"
      onClose={dismiss}
      role={ok ? 'status' : 'alert'}
    >
      <Text size="sm">{ok ? describeSuccess(task) : task.error}</Text>
      {!ok && (
        <Button size="xs" variant="light" color="red" mt="xs" onClick={dismiss}>
          Dismiss
        </Button>
      )}
    </Alert>
  );
}
