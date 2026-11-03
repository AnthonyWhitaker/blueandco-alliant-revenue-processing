import { Select } from '@mantine/core';

import type { Job } from '@/queries/jobs';

interface JobSelectProps {
  label: string;
  jobs: Job[];
  value: number | null;
  onChange: (jobId: number) => void;
  describe: (job: Job) => string;
}

export function JobSelect({ label, jobs, value, onChange, describe }: JobSelectProps) {
  const data = jobs.map((j) => ({ value: String(j.id), label: describe(j) }));
  // A deep link to a job that isn't in this list still shows which job is open.
  if (value !== null && !jobs.some((j) => j.id === value)) {
    data.unshift({ value: String(value), label: `Job #${value}` });
  }
  return (
    <Select
      label={label}
      data={data}
      value={value === null ? null : String(value)}
      onChange={(id) => {
        if (id !== null) onChange(Number(id));
      }}
      allowDeselect={false}
      searchable
      maw={720}
    />
  );
}
