import {
  Alert,
  Anchor,
  Badge,
  Button,
  Paper,
  SegmentedControl,
  Select,
  Skeleton,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconPencil } from '@tabler/icons-react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';

import type { Schemas } from '@/api/types';
import { DataTable } from '@/components/DataTable';
import { EmptyState } from '@/components/EmptyState';
import { QueryError } from '@/components/QueryError';
import { SupplierSelect } from '@/components/SupplierSelect';
import { useSelectedSupplier } from '@/hooks/useSelectedSupplier';
import { formatDecimal, formatText } from '@/lib/format';
import { routes, searchParamNames } from '@/lib/routes';
import { ruleCodec, useSearchParamState, type RuleSelection } from '@/lib/searchParams';
import { metaQuery } from '@/queries/reference';
import { allocationRulesQuery, extractionRulesQuery, suppliersQuery } from '@/queries/suppliers';

import { AllocationRuleForm } from './AllocationRuleForm';
import { ExtractionRuleForm } from './ExtractionRuleForm';

type ExtractionRule = Schemas['ExtractionRule'];
type AllocationRule = Schemas['AllocationRule'];
type Meta = Schemas['Meta'];

/** Rule ids belong to one supplier, so a supplier change clears them. */
const RULE_PARAMS = [searchParamNames.extractionRule, searchParamNames.allocationRule] as const;

const editButton = (label: string, onClick: () => void) => (
  <Button
    size="compact-sm"
    variant="subtle"
    leftSection={<IconPencil size={14} />}
    aria-label={label}
    onClick={onClick}
  >
    Edit
  </Button>
);

interface RuleEditorProps<R extends { id: number }> {
  title: string;
  /** e.g. "Extraction rule"; the mode switch's accessible name. */
  noun: string;
  rules: R[];
  selection: RuleSelection | null;
  onSelect: (selection: RuleSelection | null, options?: { replace?: boolean }) => void;
  describe: (rule: R) => string;
  children: (rule: R | null) => React.ReactNode;
}

/** "Add new" / "Edit existing" + which rule, all from the URL (`new` or a rule id). */
function RuleEditor<R extends { id: number }>({
  title,
  noun,
  rules,
  selection,
  onSelect,
  describe,
  children,
}: RuleEditorProps<R>) {
  const editing = typeof selection === 'number' ? rules.find((r) => r.id === selection) : undefined;
  const missing = typeof selection === 'number' && !editing;
  const mode = editing ? 'edit' : 'new';

  return (
    <Paper p="lg">
      <Stack gap="md">
        <Title order={2} size="h3">
          {title}
        </Title>
        {rules.length > 0 && (
          <SegmentedControl
            aria-label={noun}
            w="fit-content"
            value={mode}
            data={[
              { value: 'new', label: 'Add new' },
              { value: 'edit', label: 'Edit existing' },
            ]}
            onChange={(value) => {
              const first = rules[0];
              onSelect(value === 'edit' && first ? first.id : 'new');
            }}
          />
        )}
        {missing && (
          <Alert color="orange" variant="light">
            Rule #{String(selection)} no longer exists for this supplier. Showing a new rule
            instead.
          </Alert>
        )}
        {editing && (
          <Select
            label="Rule to edit"
            data={rules.map((r) => ({ value: String(r.id), label: describe(r) }))}
            value={String(editing.id)}
            allowDeselect={false}
            searchable
            maw={480}
            onChange={(id) => {
              if (id !== null) onSelect(Number(id));
            }}
          />
        )}
        {/* Keyed by rule so switching rules resets the form to that rule's values (F13). */}
        <div key={editing ? editing.id : 'new'}>{children(editing ?? null)}</div>
      </Stack>
    </Paper>
  );
}

function SupplierRules({ supplierId, meta }: { supplierId: number; meta: Meta }) {
  const extraction = useQuery(extractionRulesQuery(supplierId));
  const allocation = useQuery(allocationRulesQuery(supplierId));
  const [extractionSel, setExtractionSel] = useSearchParamState(
    searchParamNames.extractionRule,
    ruleCodec,
  );
  const [allocationSel, setAllocationSel] = useSearchParamState(
    searchParamNames.allocationRule,
    ruleCodec,
  );

  const error = extraction.error ?? allocation.error;
  if (error) {
    return (
      <QueryError
        title="Couldn't load Tier C rules"
        error={error}
        onRetry={() => {
          void extraction.refetch();
          void allocation.refetch();
        }}
      />
    );
  }
  if (!extraction.data || !allocation.data) return <Skeleton height={320} />;

  const extractionRules: ExtractionRule[] = extraction.data;
  const allocationRules: AllocationRule[] = allocation.data;
  const ruleNames = extractionRules.map((r) => r.rule_name);
  const scrollTo = (id: string) => {
    document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  };

  return (
    <Stack gap="lg">
      <Stack gap="sm">
        <Title order={2} size="h3">
          Extraction rules
        </Title>
        {extractionRules.length === 0 ? (
          <EmptyState title="No extraction rules yet" />
        ) : (
          <DataTable
            label="Extraction rules"
            pinLastColumn
            records={extractionRules}
            columns={[
              { accessor: 'rule_name', title: 'Rule' },
              { accessor: 'sheet_name', title: 'Sheet', render: (r) => formatText(r.sheet_name) },
              { accessor: 'extraction_method', title: 'Method' },
              {
                accessor: 'where',
                title: 'Where',
                render: (r) => {
                  switch (r.extraction_method) {
                    case 'named_cell':
                      return <span className="mono">{formatText(r.cell_address)}</span>;
                    case 'label_lookup':
                    case 'label_range_sum':
                      return `“${formatText(r.label_text)}” (${formatText(r.label_match_mode)})`;
                    case 'column_sum':
                      return `Column ${r.column_letter ?? String(r.column_index ?? '—')}, below row ${String(r.header_row ?? 0)}`;
                    case 'formula':
                      return <span className="mono">{formatText(r.formula_expression)}</span>;
                    case 'manual_entry':
                      return 'Entered manually';
                  }
                },
              },
              {
                accessor: 'is_sales_amount',
                title: 'Feeds',
                render: (r) => (r.is_sales_amount ? 'Volume' : 'Fee'),
              },
              { accessor: 'sort_order', title: 'Order', textAlign: 'right' },
              {
                accessor: 'actions',
                title: <span className="sr-only">Actions</span>,
                textAlign: 'right',
                render: (r) =>
                  editButton(`Edit extraction rule ${r.rule_name}`, () => {
                    setExtractionSel(r.id);
                    scrollTo('extraction-editor');
                  }),
              },
            ]}
          />
        )}
      </Stack>

      <Stack gap="sm">
        <Title order={2} size="h3">
          Allocation rules
        </Title>
        {allocationRules.length === 0 ? (
          <EmptyState title="No allocation rules yet" />
        ) : (
          <DataTable
            label="Allocation rules"
            pinLastColumn
            records={allocationRules}
            columns={[
              { accessor: 'extraction_rule_name', title: 'Extraction rule' },
              { accessor: 'member_name', title: 'Member' },
              {
                accessor: 'entity_code',
                title: 'Entity code',
                cellsClassName: 'mono',
                render: (r) => formatText(r.entity_code),
              },
              { accessor: 'allocation_type', title: 'Type' },
              {
                accessor: 'allocation_value',
                title: 'Value',
                textAlign: 'right',
                cellsClassName: 'tabular',
                render: (r) => {
                  if (r.allocation_type === 'remainder') return 'Remainder';
                  const value = formatDecimal(r.allocation_value);
                  return r.allocation_type === 'percent' ? `${value}%` : `$${value}`;
                },
              },
              {
                accessor: 'contract_number',
                title: 'Contract',
                render: (r) => formatText(r.contract_number),
              },
              {
                accessor: 'product_line',
                title: 'Product line',
                render: (r) => formatText(r.product_line),
              },
              {
                accessor: 'is_active',
                title: 'Active',
                render: (r) =>
                  r.is_active ? (
                    <Badge color="green">Active</Badge>
                  ) : (
                    <Badge color="slate">Off</Badge>
                  ),
              },
              {
                accessor: 'actions',
                title: <span className="sr-only">Actions</span>,
                textAlign: 'right',
                render: (r) =>
                  editButton(
                    `Edit allocation ${r.extraction_rule_name} to ${r.member_name}`,
                    () => {
                      setAllocationSel(r.id);
                      scrollTo('allocation-editor');
                    },
                  ),
              },
            ]}
          />
        )}
      </Stack>

      <div id="extraction-editor">
        <RuleEditor
          title="Add or edit an extraction rule"
          noun="Extraction rule"
          rules={extractionRules}
          selection={extractionSel}
          onSelect={setExtractionSel}
          describe={(r) => r.rule_name}
        >
          {(rule) => (
            <ExtractionRuleForm
              supplierId={supplierId}
              meta={meta}
              rule={rule}
              ruleNames={ruleNames}
              onSaved={(saved) => {
                setExtractionSel(saved.id, { replace: true });
              }}
              onDeleted={() => {
                setExtractionSel(null, { replace: true });
              }}
            />
          )}
        </RuleEditor>
      </div>

      <div id="allocation-editor">
        {ruleNames.length === 0 ? (
          <Paper p="lg">
            <Title order={2} size="h3" mb="xs">
              Add or edit an allocation rule
            </Title>
            <Text size="sm" c="dimmed">
              Add an extraction rule first: allocation rules link to one by name.
            </Text>
          </Paper>
        ) : (
          <RuleEditor
            title="Add or edit an allocation rule"
            noun="Allocation rule"
            rules={allocationRules}
            selection={allocationSel}
            onSelect={setAllocationSel}
            describe={(r) => `${r.extraction_rule_name} → ${r.member_name}`}
          >
            {(rule) => (
              <AllocationRuleForm
                supplierId={supplierId}
                meta={meta}
                rule={rule}
                extractionRuleNames={ruleNames}
                onSaved={(saved) => {
                  setAllocationSel(saved.id, { replace: true });
                }}
                onDeleted={() => {
                  setAllocationSel(null, { replace: true });
                }}
              />
            )}
          </RuleEditor>
        )}
      </div>
    </Stack>
  );
}

export function TierCTab() {
  const suppliers = useQuery(suppliersQuery({ job_type: 'pivot_allocation' }));
  const meta = useQuery(metaQuery);
  const [supplierId, selectSupplier] = useSelectedSupplier(suppliers.data, RULE_PARAMS);

  const intro = (
    <div>
      <Title order={2} size="h3">
        Tier C extraction and allocation rules
      </Title>
      <Text size="sm" c="dimmed">
        For pivot_allocation suppliers only: where to find each dollar total on an aggregate
        workpaper, and who it is allocated to.
      </Text>
    </div>
  );

  const error = suppliers.error ?? meta.error;
  if (error) {
    return (
      <QueryError
        title="Couldn't load Tier C suppliers"
        error={error}
        onRetry={() => {
          void suppliers.refetch();
          void meta.refetch();
        }}
      />
    );
  }
  if (!suppliers.data || !meta.data) return <Skeleton height={60} maw={360} />;
  const tierC = suppliers.data;
  if (tierC.length === 0) {
    return (
      <Stack gap="lg">
        {intro}
        <EmptyState title="No Tier C suppliers">
          No supplier has job type pivot_allocation yet. Set it in{' '}
          <Anchor component={Link} to={routes.admin.suppliers} inherit>
            Admin › Suppliers
          </Anchor>
          .
        </EmptyState>
      </Stack>
    );
  }
  const known = supplierId !== null && tierC.some((s) => s.id === supplierId);
  return (
    <Stack gap="lg">
      {intro}
      <SupplierSelect suppliers={tierC} value={supplierId} onChange={selectSupplier} />
      {supplierId !== null && !known && (
        <Alert color="orange" variant="light">
          Supplier #{supplierId} isn&apos;t a pivot_allocation supplier. Pick one above.
        </Alert>
      )}
      {known && <SupplierRules key={supplierId} supplierId={supplierId} meta={meta.data} />}
    </Stack>
  );
}
