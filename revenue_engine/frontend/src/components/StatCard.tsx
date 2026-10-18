import { Card, Skeleton, Text } from '@mantine/core';
import type { ReactNode } from 'react';

interface StatCardProps {
  label: string;
  /** `undefined` while loading (renders a skeleton). */
  value: ReactNode;
  hint?: ReactNode;
}

/** The st.metric equivalent: a small labelled figure. */
export function StatCard({ label, value, hint }: StatCardProps) {
  return (
    <Card padding="md">
      <Text size="xs" c="dimmed" tt="uppercase" fw={600}>
        {label}
      </Text>
      {value === undefined ? (
        <Skeleton height={30} width="50%" mt={6} />
      ) : (
        <Text fz={26} fw={650} mt={4} className="tabular" lh={1.25}>
          {value}
        </Text>
      )}
      {hint && (
        <Text size="xs" c="dimmed" mt={4}>
          {hint}
        </Text>
      )}
    </Card>
  );
}
