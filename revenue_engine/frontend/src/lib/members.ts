import type { Member } from '@/queries/members';

import { formatText } from './format';

/** "Mercy General — 12345 (Omaha, NE)": how a member appears in pickers. */
export function memberLabel(member: Member): string {
  const place = [member.billing_city, member.billing_state].filter(Boolean).join(', ');
  return `${member.name} — ${formatText(member.entity_code)}${place ? ` (${place})` : ''}`;
}
