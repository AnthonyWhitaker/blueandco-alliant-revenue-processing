import type { StateCreator } from 'zustand';

import type { Schemas } from '@/api/types';

import type { AppState, AppStoreMutators } from './types';

export type InferredMapping = Schemas['InferredMapping'];

/**
 * Column-map draft slice — inferred mappings the user has not saved yet,
 * keyed by supplier id.
 *
 * Owns: the result of "Infer Column Map" until "Save Inferred Mappings".
 * Written by: Admin > Column Maps (infer mutation's onSuccess; cleared after save).
 * Replaces: `infer_suggestions_{sp_id}`.
 * This is API output, but not server state: nothing on the server holds it
 * and it cannot be refetched without re-uploading the sample file. It is the
 * seed of an unsaved draft, so it lives here and survives switching supplier
 * or Admin tab, as it did in Streamlit.
 */
export interface ColumnMapDraftSlice {
  inferredMappings: Record<number, InferredMapping[]>;
  setInferredMappings: (supplierId: number, mappings: InferredMapping[]) => void;
  discardInferredMappings: (supplierId: number) => void;
}

export const createColumnMapDraftSlice: StateCreator<
  AppState,
  AppStoreMutators,
  [],
  ColumnMapDraftSlice
> = (set) => ({
  inferredMappings: {},
  setInferredMappings: (supplierId, mappings) => {
    set(
      (state) => ({ inferredMappings: { ...state.inferredMappings, [supplierId]: mappings } }),
      undefined,
      'columnMapDraft/set',
    );
  },
  discardInferredMappings: (supplierId) => {
    set(
      (state) => {
        const { [supplierId]: _discarded, ...rest } = state.inferredMappings;
        return { inferredMappings: rest };
      },
      undefined,
      'columnMapDraft/discard',
    );
  },
});
