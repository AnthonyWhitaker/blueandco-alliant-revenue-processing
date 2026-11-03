import { act, render } from '@testing-library/react';
import { createMemoryRouter, RouterProvider } from 'react-router';

import {
  idCodec,
  ruleCodec,
  usePatchSearchParams,
  useSearchParamState,
  type RuleSelection,
} from './searchParams';

/** Mount the hooks under a memory router at `url` and expose their latest values. */
function setup(url: string) {
  const latest: {
    supplier?: ReturnType<typeof useSearchParamState<number>>;
    rule?: ReturnType<typeof useSearchParamState<RuleSelection>>;
    patch?: ReturnType<typeof usePatchSearchParams>;
  } = {};
  function Probe() {
    latest.supplier = useSearchParamState('supplier', idCodec);
    latest.rule = useSearchParamState('extractionRule', ruleCodec);
    latest.patch = usePatchSearchParams();
    return null;
  }
  const router = createMemoryRouter([{ path: '*', element: <Probe /> }], {
    initialEntries: [url],
  });
  render(<RouterProvider router={router} />);
  const current = () => {
    const { supplier, rule, patch } = latest;
    if (!supplier || !rule || !patch) throw new Error('Probe did not render');
    return { supplier, rule, patch };
  };
  return { router, current, search: () => router.state.location.search };
}

describe('codecs', () => {
  it('accepts positive integer ids only', () => {
    expect(idCodec.parse('12')).toBe(12);
    for (const bad of ['0', '-1', '1.5', '12abc', '', ' 3', '01']) {
      expect(idCodec.parse(bad)).toBeNull();
    }
  });

  it('accepts "new" or an id for rule selections', () => {
    expect(ruleCodec.parse('new')).toBe('new');
    expect(ruleCodec.parse('8')).toBe(8);
    expect(ruleCodec.parse('edit')).toBeNull();
  });
});

describe('useSearchParamState', () => {
  it('reads typed values and treats malformed ones as absent', () => {
    const { current } = setup('/admin/tier-c?supplier=12&extractionRule=bogus');
    expect(current().supplier[0]).toBe(12);
    expect(current().rule[0]).toBeNull();
  });

  it('sets a value without dropping the other params, pushing history', () => {
    const { current, search, router } = setup('/admin/tier-c?supplier=12&tab=x');
    act(() => {
      current().rule[1]('new');
    });
    expect(search()).toBe('?supplier=12&tab=x&extractionRule=new');
    expect(current().rule[0]).toBe('new');
    expect(router.state.historyAction).toBe('PUSH');
  });

  it('clears a value, and can replace instead of push', () => {
    const { current, search, router } = setup('/review?supplier=3');
    act(() => {
      current().supplier[1](null, { replace: true });
    });
    expect(search()).toBe('');
    expect(router.state.historyAction).toBe('REPLACE');
  });
});

describe('usePatchSearchParams', () => {
  it('changes a parent selection and clears its children in one navigation', () => {
    const { current, search } = setup('/admin/tier-c?supplier=12&extractionRule=4');
    act(() => {
      current().patch({ supplier: '13', extractionRule: null });
    });
    expect(search()).toBe('?supplier=13');
  });
});
