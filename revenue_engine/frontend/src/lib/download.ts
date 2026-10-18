/** Save a Blob as a file through a temporary object URL (st.download_button). */
export function saveBlob(blob: Blob, fileName: string): void {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = fileName;
  document.body.append(link);
  link.click();
  link.remove();
  // Revoke on the next tick so the download has started.
  setTimeout(() => {
    URL.revokeObjectURL(url);
  }, 0);
}

/** File name from `Content-Disposition` (RFC 5987 `filename*` first, then `filename`). */
export function fileNameFromDisposition(header: string | null, fallback: string): string {
  if (!header) return fallback;
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(header)?.[1];
  if (encoded) {
    try {
      return decodeURIComponent(encoded);
    } catch {
      // Malformed encoding: fall through to the plain parameter.
    }
  }
  const plain = /filename="?([^";]+)"?/i.exec(header)?.[1];
  return plain ?? fallback;
}
