import { Alert, Button, Text } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';

interface QueryErrorProps {
  title: string;
  error: Error;
  onRetry?: () => void;
}

/** In-place error state for a failed query, with a retry button. */
export function QueryError({ title, error, onRetry }: QueryErrorProps) {
  return (
    <Alert color="red" icon={<IconAlertTriangle />} title={title} mb="md">
      <Text size="sm" mb={onRetry ? 'xs' : 0}>
        {error.message}
      </Text>
      {onRetry && (
        <Button size="xs" variant="light" color="red" onClick={onRetry}>
          Try again
        </Button>
      )}
    </Alert>
  );
}
