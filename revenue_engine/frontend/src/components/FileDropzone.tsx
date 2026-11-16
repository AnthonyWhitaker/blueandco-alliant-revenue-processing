import '@mantine/dropzone/styles.css';

import { ActionIcon, Group, Paper, Stack, Text, Tooltip } from '@mantine/core';
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

function acceptFor(extensions: readonly string[]) {
  const accept: Record<string, string[]> = {};
  for (const ext of extensions) {
    const mime = MIME_BY_EXTENSION[ext] ?? 'application/octet-stream';
    accept[mime] = [...(accept[mime] ?? []), ext];
  }
  return { accept, hint: extensions.map((e) => e.slice(1).toUpperCase()).join(', ') };
}

function PickedFile({
  file,
  label,
  disabled,
  onRemove,
}: {
  file: File;
  label: string;
  disabled: boolean;
  onRemove: () => void;
}) {
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
            onClick={onRemove}
          >
            <IconX size={16} />
          </ActionIcon>
        </Tooltip>
      </Group>
    </Paper>
  );
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
  if (file) {
    return (
      <PickedFile
        file={file}
        label={label}
        disabled={disabled}
        onRemove={() => {
          onChange(null);
        }}
      />
    );
  }
  return (
    <DropArea
      label={label}
      extensions={extensions}
      multiple={false}
      disabled={disabled}
      error={error}
      onDrop={(files) => {
        onChange(files[0] ?? null);
      }}
    />
  );
}

interface FileListDropzoneProps {
  label: string;
  extensions: readonly string[];
  files: File[];
  /** Called with the whole new list (dropped files are appended in drop order). */
  onChange: (files: File[]) => void;
  disabled?: boolean;
  error?: string | null;
}

/** Multi-file picker: the picked files are listed, and more can be dropped in. */
export function FileListDropzone({
  label,
  extensions,
  files,
  onChange,
  disabled = false,
  error,
}: FileListDropzoneProps) {
  return (
    <Stack gap="xs">
      {files.map((file, i) => (
        <PickedFile
          key={`${file.name}:${String(file.lastModified)}:${String(i)}`}
          file={file}
          label={files.length > 1 ? `File ${String(i + 1)} of ${String(files.length)}` : label}
          disabled={disabled}
          onRemove={() => {
            onChange(files.filter((_, j) => j !== i));
          }}
        />
      ))}
      <DropArea
        label={files.length ? 'Add more files for the same month' : label}
        extensions={extensions}
        multiple
        disabled={disabled}
        error={error}
        onDrop={(dropped) => {
          onChange([...files, ...dropped]);
        }}
      />
    </Stack>
  );
}

function DropArea({
  label,
  extensions,
  multiple,
  disabled,
  error,
  onDrop,
}: {
  label: string;
  extensions: readonly string[];
  multiple: boolean;
  disabled: boolean;
  error?: string | null | undefined;
  onDrop: (files: File[]) => void;
}) {
  const { accept, hint } = acceptFor(extensions);
  return (
    <div>
      <Dropzone
        onDrop={onDrop}
        accept={accept}
        multiple={multiple}
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
              {multiple ? 'Drop files here' : 'Drop a file here'} or click to browse · {hint}
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
