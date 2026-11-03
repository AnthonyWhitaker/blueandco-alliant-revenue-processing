import { Select } from '@mantine/core';

import type { Supplier } from '@/queries/suppliers';

interface SupplierSelectProps {
  suppliers: Supplier[];
  value: number | null;
  onChange: (supplierId: number) => void;
}

export function SupplierSelect({ suppliers, value, onChange }: SupplierSelectProps) {
  return (
    <Select
      label="Supplier"
      data={suppliers.map((s) => ({ value: String(s.id), label: s.name }))}
      value={value === null ? null : String(value)}
      onChange={(id) => {
        if (id !== null) onChange(Number(id));
      }}
      allowDeselect={false}
      searchable
      maw={360}
    />
  );
}
