import type { StateCreator } from 'zustand';

import type { TaskKind } from '@/api/types';

import type { AppState, AppStoreMutators } from './types';

/**
 * Imports slice — the background import the user is waiting on, per kind.
 *
 * Owns: task ids returned by `POST /member-imports` and `POST /ledger-imports`.
 * The task's status and result are server data and come from the
 * `GET /tasks/{id}` query; only the id lives here.
 * Written by: Admin > Members and Admin > Payment Ledger.
 * Replaces: nothing. Streamlit ran imports inside the request; the API runs
 * them in the background (MIGRATION_AUDIT §5), so the page must remember
 * which task to poll. Persisted for the tab session so leaving the Admin tab
 * or refreshing mid-import still shows how it ended.
 */
export interface ImportsSlice {
  activeImports: Partial<Record<TaskKind, string>>;
  trackImport: (kind: TaskKind, taskId: string) => void;
  /** Call when the user dismisses the result, or the task is gone (404 after an API restart). */
  forgetImport: (kind: TaskKind) => void;
}

export const createImportsSlice: StateCreator<AppState, AppStoreMutators, [], ImportsSlice> = (
  set,
) => ({
  activeImports: {},
  trackImport: (kind, taskId) => {
    set(
      (state) => ({ activeImports: { ...state.activeImports, [kind]: taskId } }),
      undefined,
      'imports/track',
    );
  },
  forgetImport: (kind) => {
    set(
      (state) => {
        const { [kind]: _forgotten, ...rest } = state.activeImports;
        return { activeImports: rest };
      },
      undefined,
      'imports/forget',
    );
  },
});
