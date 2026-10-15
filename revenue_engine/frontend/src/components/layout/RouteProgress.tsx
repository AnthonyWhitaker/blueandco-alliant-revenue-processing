import { Progress } from '@mantine/core';
import { useIsFetching, useIsMutating } from '@tanstack/react-query';
import { useNavigation } from 'react-router-dom';

/**
 * Global loading indicator: a thin bar along the top of the header while a
 * route is loading or any request is in flight. Page-level loading states
 * (skeletons) are still each page's job; this only signals "busy".
 */
export function RouteProgress() {
  const navigating = useNavigation().state !== 'idle';
  const busy = useIsFetching() + useIsMutating() > 0;
  if (!navigating && !busy) return null;
  return (
    <Progress
      value={100}
      animated
      size={2}
      radius={0}
      pos="absolute"
      top={0}
      left={0}
      right={0}
      aria-label="Loading"
    />
  );
}
