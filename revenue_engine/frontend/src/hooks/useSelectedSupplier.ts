import { useCallback, useEffect } from 'react';

import { searchParamNames } from '@/lib/routes';
import { idCodec, usePatchSearchParams, useSearchParamState } from '@/lib/searchParams';
import type { Supplier } from '@/queries/suppliers';

const NO_CHILDREN: readonly string[] = [];

/**
 * The selected supplier lives in `?supplier=`. With no param, the first
 * supplier (alphabetical, as the legacy selectbox) is selected, replacing the
 * history entry. `childParams` are cleared whenever the supplier changes,
 * because their ids belong to the previous supplier.
 */
export function useSelectedSupplier(
  suppliers: Supplier[] | undefined,
  childParams: readonly string[] = NO_CHILDREN,
) {
  const [supplierId] = useSearchParamState(searchParamNames.supplier, idCodec);
  const patch = usePatchSearchParams();
  const first = suppliers?.[0]?.id;

  useEffect(() => {
    if (supplierId === null && first !== undefined) {
      patch({ [searchParamNames.supplier]: String(first) }, { replace: true });
    }
  }, [supplierId, first, patch]);

  const select = useCallback(
    (id: number) => {
      const cleared = Object.fromEntries(childParams.map((name) => [name, null]));
      patch({ ...cleared, [searchParamNames.supplier]: String(id) });
    },
    [patch, childParams],
  );

  return [supplierId, select] as const;
}
