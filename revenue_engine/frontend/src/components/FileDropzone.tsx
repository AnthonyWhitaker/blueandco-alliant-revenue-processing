import '@mantine/dropzone/styles.css';

import { ActionIcon, Group, Paper, Text, Tooltip } from '@mantine/core';
import { Dropzone } from '@mantine/dropzone';
import { IconFileSpreadsheet, IconUpload, IconX } from '@tabler/icons-react';

/** MIME types per spreadsheet extension; the extension alone is enough to accept a file. */
const MIME_BY_EXTENSION: Record<string, string> = {
  '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  '.xlsm': 'application/vnd.ms-excel.sheet.macroEnabled.12',
  '.xls': 'application/vnd.ms-excel',
  '.csv': 'text/csv',
};

interface FileDropzoneProps {
  label: string;
  /** Allowed extensions, e.g. ['.xlsx', '.csv']. */
  extensions: readonly string[];
  file: File | null;
  onChange: (file: File | null) => void;
  disabled?: boolean;
  error?: string | null;
}

/** Single-file picker (st.file_uploader). The file stays in the caller's local state. */
export function FileDropzone({
  label,
  extensions,
  file,
  onChange,
  disabled = false,
  error,
}: FileDropzoneProps) {
  const accept: Record<string, string[]> = {};
  for (const ext of extensions) {
    const mime = MIME_BY_EXTENSION[ext] ?? 'application/octet-stream';
    accept[mime] = [...(accept[mime] ?? []), ext];
  }
  const hint = extensions.map((e) => e.slice(1).toUpperCase()).join(', ');

  if (file) {
    return (
      <Paper p="sm">
        <Group justify="space-between" wrap="nowrap">
          <Group gap="sm" wrap="nowrap" miw={0}>
            <IconFileSpreadsheet size={22} stroke={1.5} />
            <div style={{ minWidth: 0 }}>
              <Text size="sm" fw={500} truncate>
                {file.name}
              </Text>
              <Text size="xs" c="dimmed">
                {label} · {(file.size / 1024).toFixed(0)} KB
              </Text>
            </div>
          </Group>
          <Tooltip label="Remove file">
            <ActionIcon
              variant="subtle"
              color="slate"
              aria-label={`Remove ${file.name}`}
              disabled={disabled}
              onClick={() => {
                onChange(null);
              }}
            >
              <IconX size={16} />
            </ActionIcon>
          </Tooltip>
        </Group>
      </Paper>
    );
  }

  return (
    <div>
      <Dropzone
        onDrop={(files) => {
          onChange(files[0] ?? null);
        }}
        accept={accept}
        multiple={false}
        disabled={disabled}
        aria-label={label}
        p="lg"
      >
        <Group justify="center" gap="md" mih={64} style={{ pointerEvents: 'none' }}>
          <Dropzone.Accept>
            <IconUpload size={28} stroke={1.5} />
          </Dropzone.Accept>
          <Dropzone.Reject>
            <IconX size={28} stroke={1.5} />
          </Dropzone.Reject>
          <Dropzone.Idle>
            <IconFileSpreadsheet size={28} stroke={1.5} />
          </Dropzone.Idle>
          <div>
            <Text size="sm" fw={500}>
              {label}
            </Text>
            <Text size="xs" c="dimmed">
              Drop a file here or click to browse · {hint}
            </Text>
          </div>
        </Group>
      </Dropzone>
      {error && (
        <Text size="xs" c="red" mt={4}>
          {error}
        </Text>
      )}
    </div>
  );
}
