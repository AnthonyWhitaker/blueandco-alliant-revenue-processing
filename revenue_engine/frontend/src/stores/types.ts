import type { ColumnMapDraftSlice } from './columnMapDraftSlice';
import type { ImportsSlice } from './importsSlice';
import type { ReviewSlice } from './reviewSlice';

export type AppState = ReviewSlice & ColumnMapDraftSlice & ImportsSlice;

/** Middleware stack of `useAppStore`, outermost first; slice creators must declare it. */
export type AppStoreMutators = [['zustand/devtools', never], ['zustand/persist', unknown]];
