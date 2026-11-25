import {
  Badge,
  Button,
  Checkbox,
  Divider,
  Group,
  Paper,
  Radio,
  SimpleGrid,
  Stack,
  Text,
  Title,
} from '@mantine/core';
import { IconBan, IconCheck, IconUserPlus, IconX } from '@tabler/icons-react';

import type { AliasScope, Schemas } from '@/api/types';
import { MemberPicker } from '@/components/MemberPicker';
import { formatCurrency, formatText, pluralize } from '@/lib/format';
import { useAppStore } from '@/stores';
import { useReviewDraft } from '@/stores/hooks';
import { resolveReviewMemberId } from '@/stores/reviewSlice';

import { NewFacilityForm } from './NewFacilityForm';
import { useReviewDecision } from './useReviewDecision';

type ReviewItem = Schemas['ReviewItem'];
type ReviewCandidate = Schemas['ReviewCandidate'];

/** Why an item is here when it isn't just "no match" (member_review_item.review_reason). */
const REASONS: Record<string, string> = {
  gpo_id_conflict: 'GPO ID conflict',
  jde_gpo_conflict: 'JDE # and GPO ID disagree',
  entity_code_no_primary: 'Shared GPO ID, no primary site',
  fuzzy_suggestion: 'Similar name, not confirmed',
  low_confidence: 'Low-confidence match',
  gpo_key_fallback: 'Not verified by the GPO key',
};

/** gpo_key_then_member: why the row failed the GPO key (member_review_item.key_reason). */
const KEY_REASONS: Record<string, string> = {
  account_not_in_key: 'account not in the key',
  missing_account_number: 'no account number',
  name_mismatch: "name doesn't match the key",
  address_mismatch: "address doesn't match the key",
  name_and_address_mismatch: "name and address don't match the key",
};

const FIELD_LABELS: Record<ReviewCandidate['matched'][number], string> = {
  name: 'Name',
  street: 'Street',
  zip: 'ZIP',
  jde: 'JDE #',
  gpo_id: 'GPO ID',
};

const SCOPES: { value: AliasScope; label: string; description: string }[] = [
  { value: 'supplier', label: 'This supplier', description: 'Future files from this supplier' },
  { value: 'global', label: 'All suppliers', description: 'Any supplier file' },
  { value: 'job_only', label: 'This job only', description: 'No alias is saved' },
];

function scoreColor(score: number): string {
  if (score >= 80) return 'green';
  if (score >= 60) return 'orange';
  return 'red';
}

function Detail({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <Group gap="xs" wrap="nowrap" align="baseline">
      <Text size="xs" c="dimmed" w={96} style={{ flexShrink: 0 }}>
        {label}
      </Text>
      <Text size="sm" component="div">
        {children}
      </Text>
    </Group>
  );
}

const place = (...parts: (string | null)[]) => parts.filter(Boolean).join(', ') || '—';

function CandidateRow({
  candidate,
  selected,
  onUse,
}: {
  candidate: ReviewCandidate;
  selected: boolean;
  onUse: () => void;
}) {
  return (
    <Paper
      withBorder
      p="xs"
      component="li"
      aria-label={candidate.name}
      style={selected ? { borderColor: 'var(--mantine-color-green-6)' } : undefined}
    >
      <Group justify="space-between" align="flex-start" wrap="nowrap" gap="xs">
        <Stack gap={2}>
          <Text size="sm" fw={600}>
            {candidate.name}
          </Text>
          <Text size="xs" c="dimmed">
            <span className="mono">{formatText(candidate.entity_code)}</span> ·{' '}
            {place(candidate.street, candidate.city, candidate.state, candidate.zip)}
          </Text>
          <Group gap={4}>
            <Badge size="sm" color={scoreColor(candidate.score)}>
              Name {candidate.score}
            </Badge>
            {candidate.matched
              .filter((f) => f !== 'name')
              .map((f) => (
                <Badge key={f} size="sm" variant="light" color="green">
                  {FIELD_LABELS[f]} matches
                </Badge>
              ))}
          </Group>
        </Stack>
        <Button
          size="compact-sm"
          variant={selected ? 'filled' : 'default'}
          onClick={onUse}
          aria-pressed={selected}
          aria-label={`Use ${candidate.name}`}
        >
          {selected ? 'Selected' : 'Use this'}
        </Button>
      </Group>
    </Paper>
  );
}

export function ReviewCard({ item }: { item: ReviewItem }) {
  const draft = useReviewDraft(item.id);
  const pickReviewMember = useAppStore((s) => s.pickReviewMember);
  const resetReviewSelection = useAppStore((s) => s.resetReviewSelection);
  const rejectReviewCandidates = useAppStore((s) => s.rejectReviewCandidates);
  const setReviewCreateAlias = useAppStore((s) => s.setReviewCreateAlias);
  const setReviewAliasScope = useAppStore((s) => s.setReviewAliasScope);
  const decide = useReviewDecision(item);

  const memberId = resolveReviewMemberId(draft, item.suggestion);
  const candidates = item.candidates ?? [];
  const selection = draft.selection;
  const name = item.customer_name_sample ?? 'Unnamed customer';
  const headingId = `review-item-${item.id}`;

  return (
    <Paper p="lg" component="article" aria-labelledby={headingId}>
      <Stack gap="md">
        <Group justify="space-between" align="flex-start" gap="xs">
          <Title order={3} id={headingId}>
            {name}
          </Title>
          <Group gap="xs">
            {item.review_reason && (
              <Badge color="orange">
                {REASONS[item.review_reason] ?? item.review_reason}
                {item.key_reason && `: ${KEY_REASONS[item.key_reason] ?? item.key_reason}`}
              </Badge>
            )}
            <Badge variant="outline" color="slate" className="mono">
              JDE {formatText(item.jde_number)}
            </Badge>
            <Badge color="ledger">Fee {formatCurrency(item.total_fee ?? 0)}</Badge>
            <Badge variant="outline" color="slate">
              {pluralize(item.affected_row_count ?? 0, 'row')}
            </Badge>
          </Group>
        </Group>

        <SimpleGrid cols={{ base: 1, sm: 2 }} spacing="lg">
          <Stack gap={4}>
            <Text fw={600} size="sm">
              Unmatched customer
            </Text>
            <Detail label="Name">{name}</Detail>
            <Detail label="JDE #">
              <span className="mono">{formatText(item.jde_number)}</span>
            </Detail>
            <Detail label="Location">{place(item.city, item.state, item.zip)}</Detail>
            <Detail label="Fee amount">{formatCurrency(item.total_fee ?? 0)}</Detail>
            <Detail label="Sales">{formatCurrency(item.total_sales)}</Detail>
          </Stack>
          <Stack gap={4}>
            <Text fw={600} size="sm">
              Candidates
            </Text>
            {candidates.length > 0 ? (
              <Stack gap={6} component="ul" p={0} m={0} style={{ listStyle: 'none' }}>
                {candidates.map((c) => (
                  <CandidateRow
                    key={c.member_id}
                    candidate={c}
                    selected={memberId === c.member_id}
                    onUse={() => {
                      if (c.member_id === item.suggestion?.member_id) resetReviewSelection(item.id);
                      else pickReviewMember(item.id, { id: c.member_id, label: c.name });
                    }}
                  />
                ))}
              </Stack>
            ) : (
              <Text size="sm" c="dimmed" fs="italic">
                No suggestion available
              </Text>
            )}
          </Stack>
        </SimpleGrid>

        <Divider />

        <Stack gap="xs">
          <MemberPicker
            label="Match to a different member"
            description={
              selection.kind === 'picked' && selection.created
                ? 'New facility created for this customer'
                : 'Search by name or entity code. Leave empty to use the suggestion.'
            }
            activeOnly
            value={selection.kind === 'picked' ? selection.member : null}
            onChange={(member) => {
              if (member) pickReviewMember(item.id, member);
              else resetReviewSelection(item.id);
            }}
            maw={560}
          />
          {selection.kind !== 'none' && memberId !== null && (
            <Button
              variant="subtle"
              size="compact-sm"
              leftSection={<IconUserPlus size={14} />}
              w="fit-content"
              onClick={() => {
                rejectReviewCandidates(item.id);
              }}
            >
              None of these: add a new facility
            </Button>
          )}
        </Stack>

        {selection.kind === 'none' && item.suggestion && (
          <Button
            variant="subtle"
            size="compact-sm"
            w="fit-content"
            onClick={() => {
              resetReviewSelection(item.id);
            }}
          >
            Use the suggestion ({item.suggestion.name}) instead
          </Button>
        )}

        {memberId === null && <NewFacilityForm key={item.id} item={item} />}

        <Group align="flex-start" gap="xl">
          <Checkbox
            label="Create alias (remember this match for future files)"
            checked={draft.createAlias}
            onChange={(e) => {
              setReviewCreateAlias(item.id, e.currentTarget.checked);
            }}
            mt={4}
          />
          <Radio.Group
            label="Alias scope"
            value={draft.aliasScope}
            onChange={(value) => {
              const scope = SCOPES.find((s) => s.value === value);
              if (scope) setReviewAliasScope(item.id, scope.value);
            }}
          >
            <Group gap="md" mt={4}>
              {SCOPES.map((s) => (
                <Radio key={s.value} value={s.value} label={s.label} description={s.description} />
              ))}
            </Group>
          </Radio.Group>
        </Group>

        <Group>
          <Button
            leftSection={<IconCheck size={16} />}
            disabled={memberId === null}
            loading={decide.isPending && decide.variables.kind === 'approve'}
            onClick={() => {
              if (memberId === null) return;
              decide.mutate({
                kind: 'approve',
                memberId,
                createAlias: draft.createAlias,
                aliasScope: draft.aliasScope,
              });
            }}
            aria-label={`Approve match for ${name}`}
          >
            Approve
          </Button>
          <Button
            variant="default"
            leftSection={<IconX size={16} />}
            loading={decide.isPending && decide.variables.kind === 'dismiss'}
            disabled={decide.isPending}
            onClick={() => {
              decide.mutate({ kind: 'dismiss' });
            }}
            aria-label={`Dismiss ${name} (no match)`}
          >
            Dismiss (no match)
          </Button>
          <Button
            variant="default"
            color="red"
            leftSection={<IconBan size={16} />}
            loading={decide.isPending && decide.variables.kind === 'not_member'}
            disabled={decide.isPending}
            onClick={() => {
              decide.mutate({ kind: 'not_member' });
            }}
            aria-label={`Mark ${name} as not a member`}
          >
            Not a member
          </Button>
        </Group>
      </Stack>
    </Paper>
  );
}
