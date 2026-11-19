import { Button } from '@mantine/core';
import { IconDownload } from '@tabler/icons-react';
import { useMutation } from '@tanstack/react-query';

import { api, ApiError } from '@/api/client';
import { fileNameFromDisposition, saveBlob } from '@/lib/download';

type TemplatePath = '/api/member-imports/template' | '/api/ledger-imports/template';

/** Downloads a blank import workbook: the columns the import reads, with a "How to fill in" sheet. */
export function TemplateDownload({ path, fileName }: { path: TemplatePath; fileName: string }) {
  const download = useMutation({
    mutationFn: async () => {
      const { data, error, response } = await api.GET(path, { parseAs: 'blob' });
      if (!response.ok || !data) throw ApiError.fromResponse(response, error);
      return {
        blob: data,
        name: fileNameFromDisposition(response.headers.get('Content-Disposition'), fileName),
      };
    },
    onSuccess: ({ blob, name }) => {
      saveBlob(blob, name);
    },
  });
  return (
    <Button
      variant="default"
      size="xs"
      leftSection={<IconDownload size={14} />}
      loading={download.isPending}
      onClick={() => {
        download.mutate();
      }}
    >
      Download template
    </Button>
  );
}
