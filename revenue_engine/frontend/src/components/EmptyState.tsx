import { Paper, Stack, Text } from '@mantine/core';
import type { ReactNode } from 'react';

/** "Nothing here yet", with an optional next step. Replaces the legacy `st.info` + `st.stop()`. */
export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <Paper p="lg" bg="var(--mantine-color-default-hover)" withBorder={false}>
      <Stack gap={4}>
        <Text fw={600}>{title}</Text>
        {children && (
          <Text size="sm" c="dimmed" component="div">
            {children}
          </Text>
        )}
      </Stack>
    </Paper>
  );
}
