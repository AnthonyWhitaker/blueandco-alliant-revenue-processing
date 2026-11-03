import { create } from 'zustand';
import { createJSONStorage, devtools, persist } from 'zustand/middleware';

import { createColumnMapDraftSlice } from './columnMapDraftSlice';
import { createImportsSlice, type ImportsSlice } from './importsSlice';
import { createReviewSlice } from './reviewSlice';
import type { AppState } from './types';

export type { AppState } from './types';

/** Only this subset is persisted; everything else resets on refresh, as in Streamlit. */
type PersistedState = Pick<ImportsSlice, 'activeImports'>;

export const PERSIST_KEY = 'revenue-engine:client-state';
export const PERSIST_VERSION = 1;

/**
 * Upgrade state saved by an older build. Version 1 is the first schema, so
 * anything else (including a corrupt entry) is dropped rather than trusted.
 * Bump PERSIST_VERSION and add a case here whenever PersistedState changes.
 */
export function migratePersistedState(persisted: unknown, version: number): PersistedState {
  if (version === PERSIST_VERSION && isPersistedState(persisted)) return persisted;
  return { activeImports: {} };
}

function isPersistedState(value: unknown): value is PersistedState {
  if (typeof value !== 'object' || value === null || !('activeImports' in value)) return false;
  const imports: unknown = value.activeImports;
  return (
    typeof imports === 'object' &&
    imports !== null &&
    Object.values(imports).every((id) => typeof id === 'string')
  );
}

/**
 * The one client-state store, composed of per-domain slices.
 * Server data never goes here: it belongs to TanStack Query (see README.md).
 * Subscribe with a selector — `useAppStore((s) => s.reviewDrafts[id])` — and
 * `useShallow` for several fields; never take the whole store.
 */
export const useAppStore = create<AppState>()(
  devtools(
    persist(
      (...a) => ({
        ...createReviewSlice(...a),
        ...createColumnMapDraftSlice(...a),
        ...createImportsSlice(...a),
      }),
      {
        name: PERSIST_KEY,
        version: PERSIST_VERSION,
        // Per browser tab: an import started in one tab is that tab's to report.
        storage: createJSONStorage(() => sessionStorage),
        partialize: (state): PersistedState => ({ activeImports: state.activeImports }),
        migrate: migratePersistedState,
      },
    ),
    { name: 'revenue-engine', enabled: import.meta.env.DEV },
  ),
);
