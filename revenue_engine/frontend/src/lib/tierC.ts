import type { Schemas } from '@/api/types';

type Notice = Schemas['Notice'];

/**
 * Rule names that are substrings of other names make formula references
 * ambiguous. Mirrors core.tier_c.formula_name_notices (pure, on names only),
 * so the warning shows while typing instead of after saving.
 */
export function formulaNameNotices(ruleNames: readonly string[]): Notice[] {
  const notices: Notice[] = [];
  for (const a of ruleNames) {
    for (const b of ruleNames) {
      if (a !== b && b.includes(a)) {
        notices.push({
          level: 'warning',
          message: `Rule name '${a}' is a substring of '${b}' — formula references can collide. Consider distinct names.`,
        });
      }
    }
  }
  return notices;
}
