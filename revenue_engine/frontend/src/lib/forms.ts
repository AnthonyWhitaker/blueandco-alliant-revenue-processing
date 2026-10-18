import type { UseFormReturnType } from '@mantine/form';

import { ApiError } from '@/api/client';

/**
 * Show a failed save on the form: 422 field errors go inline on the matching
 * fields; anything else (or a field the form doesn't have) is returned as a
 * message for the form-level alert.
 */
export function applyServerErrors<V extends Record<string, unknown>>(
  form: UseFormReturnType<V>,
  error: Error,
): string | null {
  if (!(error instanceof ApiError)) return error.message;
  const fieldErrors = error.fieldErrors();
  const known: Record<string, string> = {};
  const unknown: string[] = [];
  for (const [field, message] of Object.entries(fieldErrors)) {
    if (field in form.getValues()) known[field] = message;
    else unknown.push(message);
  }
  form.setErrors(known);
  if (unknown.length) return unknown.join(' ');
  return Object.keys(known).length ? null : error.message;
}

/** Turn a blank text field into null for the API (the legacy `x or None`). */
export const blankToNull = (value: string): string | null => {
  const trimmed = value.trim();
  return trimmed === '' ? null : trimmed;
};
