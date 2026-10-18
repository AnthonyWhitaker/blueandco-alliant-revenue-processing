import { Select, type SelectProps } from '@mantine/core';
import { useDebouncedValue } from '@mantine/hooks';
import { useQuery } from '@tanstack/react-query';
import { useState } from 'react';

import { memberLabel } from '@/lib/members';
import { membersQuery } from '@/queries/members';
import type { PickedMember } from '@/stores/reviewSlice';

/** The legacy pickers searched from 2 characters, 20 results at a time. */
export const MIN_SEARCH_LENGTH = 2;
const RESULT_LIMIT = 20;

interface MemberPickerProps extends Omit<
  SelectProps,
  'value' | 'onChange' | 'data' | 'searchable' | 'error'
> {
  value: PickedMember | null;
  onChange: (member: PickedMember | null) => void;
  /** Review only offers active members; Admin pickers search all of them (F12). */
  activeOnly: boolean;
  error?: string | null;
}

/** Server-side member search (name or entity code) as a searchable select. */
export function MemberPicker({ value, onChange, activeOnly, error, ...props }: MemberPickerProps) {
  const [search, setSearch] = useState('');
  const [debounced] = useDebouncedValue(search.trim(), 250);
  const enabled = debounced.length >= MIN_SEARCH_LENGTH && debounced !== value?.label;
  const {
    data,
    isFetching,
    error: queryError,
  } = useQuery({
    ...membersQuery({ search: debounced, active_only: activeOnly, limit: RESULT_LIMIT }),
    enabled,
  });

  const options = (data?.items ?? []).map((m) => ({ value: String(m.id), label: memberLabel(m) }));
  // Keep the current selection selectable (and labelled) even when it isn't in the results.
  if (value && !options.some((o) => o.value === String(value.id))) {
    options.unshift({ value: String(value.id), label: value.label });
  }

  let nothingFound: string;
  if (search.trim().length < MIN_SEARCH_LENGTH) nothingFound = 'Type at least 2 characters';
  else if (isFetching) nothingFound = 'Searching…';
  else nothingFound = 'No matching members found';

  return (
    <Select
      searchable
      clearable
      placeholder="Search by name or entity code…"
      {...props}
      data={options}
      value={value ? String(value.id) : null}
      onChange={(id, option) => {
        onChange(id === null ? null : { id: Number(id), label: option.label });
      }}
      searchValue={search}
      onSearchChange={setSearch}
      // Results are already filtered by the server.
      filter={({ options: all }) => all}
      nothingFoundMessage={nothingFound}
      error={error ?? (queryError ? queryError.message : null)}
    />
  );
}
