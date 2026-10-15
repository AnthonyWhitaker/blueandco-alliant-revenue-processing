import { Alert, Button, Center, Code, Group, Stack, Text, Title } from '@mantine/core';
import { IconAlertTriangle } from '@tabler/icons-react';
import { isRouteErrorResponse, Link, useRouteError } from 'react-router-dom';

import { ApiError } from '@/api/client';
import { routes } from '@/lib/routes';

export function NotFoundPage() {
  return (
    <Center mih="50vh">
      <Stack align="center" gap="sm">
        <Text c="dimmed" fw={650} fz={48} lh={1}>
          404
        </Text>
        <Title order={2}>Page not found</Title>
        <Text c="dimmed">That address doesn&apos;t match any page in the app.</Text>
        <Button component={Link} to={routes.dashboard} variant="light">
          Go to the dashboard
        </Button>
      </Stack>
    </Center>
  );
}

function describe(error: unknown): { title: string; message: string } {
  if (isRouteErrorResponse(error)) {
    return { title: `${error.status} ${error.statusText}`, message: String(error.data ?? '') };
  }
  if (error instanceof ApiError)
    return { title: 'The server returned an error', message: error.message };
  if (error instanceof Error) return { title: 'Something went wrong', message: error.message };
  return { title: 'Something went wrong', message: 'An unknown error occurred.' };
}

/** Route-level error boundary: renders inside the app shell so navigation still works. */
export function RouteErrorBoundary() {
  const error = useRouteError();
  if (isRouteErrorResponse(error) && error.status === 404) return <NotFoundPage />;
  const { title, message } = describe(error);
  return (
    <Alert color="red" variant="light" icon={<IconAlertTriangle />} title={title} maw={720}>
      <Stack gap="sm">
        <Text size="sm">This page hit an unexpected error. Reloading usually fixes it.</Text>
        {message && <Code block>{message}</Code>}
        <Group>
          <Button
            size="xs"
            variant="light"
            color="red"
            onClick={() => {
              window.location.reload();
            }}
          >
            Reload
          </Button>
          <Button size="xs" variant="subtle" color="red" component={Link} to={routes.dashboard}>
            Back to dashboard
          </Button>
        </Group>
      </Stack>
    </Alert>
  );
}

/** Last-resort boundary for errors in the shell itself. */
export function RootErrorBoundary() {
  return (
    <Center mih="100vh" p="lg">
      <RouteErrorBoundary />
    </Center>
  );
}
