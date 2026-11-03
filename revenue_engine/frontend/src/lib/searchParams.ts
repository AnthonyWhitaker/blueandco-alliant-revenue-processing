import { useCallback } from 'react';
import { useSearchParams } from 'react-router';

/**
 * Selections and filters live in the address bar (shareable, survive refresh,
 * work with Back). See src/stores/README.md for which params each page owns.
 */

/** Converts a param between its URL text and a typed value; `parse` returns null if invalid. */
export interface ParamCodec<T> {
  parse: (raw: string) => T | null;
  serialize: (value: T) => string;
}

/** Positive integer ids (job, supplier, rule). */
export const idCodec: ParamCodec<number> = {
  parse: (raw) => (/^[1-9]\d{0,15}$/.test(raw) ? Number(raw) : null),
  serialize: String,
};

/** A rule editor's target: an existing rule id, or "new" for the add form. */
export type RuleSelection = number | 'new';
export const ruleCodec: ParamCodec<RuleSelection> = {
  parse: (raw) => (raw === 'new' ? 'new' : idCodec.parse(raw)),
  serialize: String,
};

export interface NavigateOptions {
  /** Replace the history entry instead of pushing one (defaults, corrections). */
  replace?: boolean;
}

/**
 * Set or clear several params in one navigation, keeping the rest.
 * Changing a parent selection must clear its children in the same call,
 * e.g. `{ supplier: '12', extractionRule: null }`.
 */
export function usePatchSearchParams() {
  const [, setSearchParams] = useSearchParams();
  return useCallback(
    (patch: Record<string, string | null>, options: NavigateOptions = {}) => {
      setSearchParams(
        (current) => {
          const next = new URLSearchParams(current);
          for (const [name, value] of Object.entries(patch)) {
            if (value === null) next.delete(name);
            else next.set(name, value);
          }
          return next;
        },
        { replace: options.replace ?? false },
      );
    },
    [setSearchParams],
  );
}

/** One typed param. An absent or malformed value reads as null. */
export function useSearchParamState<T>(
  name: string,
  codec: ParamCodec<T>,
): [T | null, (value: T | null, options?: NavigateOptions) => void] {
  const [searchParams] = useSearchParams();
  const patch = usePatchSearchParams();
  const raw = searchParams.get(name);
  const value = raw === null ? null : codec.parse(raw);
  const setValue = useCallback(
    (next: T | null, options?: NavigateOptions) => {
      patch({ [name]: next === null ? null : codec.serialize(next) }, options);
    },
    [patch, name, codec],
  );
  return [value, setValue];
}
