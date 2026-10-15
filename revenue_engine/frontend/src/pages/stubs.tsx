/** Routes not migrated yet (Phase 4 replaces each with a real page). */
import { NotMigrated } from '@/components/NotMigrated';
import { PageHeader } from '@/components/PageHeader';

export function UploadPage() {
  return (
    <>
      <PageHeader
        title="Upload & Process"
        description="Upload a supplier file and run the matching pipeline."
      />
      <NotMigrated feature="Uploading and processing files" />
    </>
  );
}

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

export function ExportPage() {
  return (
    <>
      <PageHeader
        title="Export"
        description="Preview output and download the Salesforce workbook."
      />
      <NotMigrated feature="Export" />
    </>
  );
}

export function AdminTabStub({ feature }: { feature: string }) {
  return <NotMigrated feature={feature} />;
}
