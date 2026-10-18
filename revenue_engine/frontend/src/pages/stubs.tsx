/** Routes not migrated yet (Phase 4 replaces each with a real page). */
import { NotMigrated } from '@/components/NotMigrated';

export function AdminTabStub({ feature }: { feature: string }) {
  return <NotMigrated feature={feature} />;
}
