import { Alert, Stack } from '@mantine/core';
import { IconAlertTriangle, IconInfoCircle } from '@tabler/icons-react';

import type { Schemas } from '@/api/types';

/** Advisory notices from the Tier C checks (the legacy st.warning / st.info calls). */
export function Notices({ notices }: { notices: Schemas['Notice'][] }) {
  if (notices.length === 0) return null;
  return (
    <Stack gap="xs" aria-live="polite">
      {notices.map((n) => (
        <Alert
          key={n.message}
          color={n.level === 'warning' ? 'orange' : 'ledger'}
          variant="light"
          icon={n.level === 'warning' ? <IconAlertTriangle /> : <IconInfoCircle />}
          py="xs"
        >
          {n.message}
        </Alert>
      ))}
    </Stack>
  );
}
