import { Alert } from '@mantine/core';
import { IconInfoCircle } from '@tabler/icons-react';

/**
 * Shown on routes whose page hasn't been migrated yet (Phase 4 builds them one
 * at a time). Says so plainly rather than rendering a mock-up.
 */
export function NotMigrated({ feature }: { feature: string }) {
  return (
    <Alert icon={<IconInfoCircle />} title="Not migrated yet" color="slate" maw={640}>
      {feature} hasn&apos;t been rebuilt in the new interface yet. Use the existing Streamlit app
      for this for now.
    </Alert>
  );
}
