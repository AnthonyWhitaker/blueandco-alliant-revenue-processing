/** Routes not migrated yet (Phase 4 replaces each with a real page). */
import { NotMigrated } from '@/components/NotMigrated';
import { PageHeader } from '@/components/PageHeader';

export function ReviewPage() {
  return (
    <>
      <PageHeader
        title="Review"
        description="Resolve unmatched customers, highest revenue impact first."
      />
      <NotMigrated feature="The review queue" />
    </>
  );
}

export function AdminTabStub({ feature }: { feature: string }) {
  return <NotMigrated feature={feature} />;
}
