// The six tiers of design/inputs.md, as data a form renders from. A Tiers map lists the
// fields of one form in the order the instructor thinks (rule 2); a field missing from the
// map is not shown, and a `hidden` or `derived` one never becomes a field.

export type Tier = 'derived' | 'ask' | 'default' | 'conditional' | 'advanced' | 'hidden';

export type Widget = 'text' | 'number' | 'date' | 'time' | 'url' | 'email' | 'select' | 'radio' | 'checkbox' | 'textarea' | 'markdown';

export type Values = Record<string, unknown>;

export interface FieldTier {
  tier: Tier;
  label: string;
  /** The one sentence shown on focus: why we ask, what happens if you change it (rule 3). */
  reason?: string;
  /** The value that counts as unchanged, for "Advanced (n changed)" and the default marker. */
  default?: unknown;
  /** How the default is written beside the label: "default: 10%". */
  defaultLabel?: string;
  widget?: Widget;
  options?: { value: string; label: string; sub?: string; off?: string; href?: string }[];
  placeholder?: string;
  /** Conditional: the field it sits under. */
  under?: string;
  /** When the field shows at all, on any tier (a conditional field is never greyed out). */
  when?: (v: Values) => boolean;
  /** Forced by another field: shown read-only with its reason, and its value applied. */
  forced?: (v: Values) => { value: unknown; reason: string } | null;
  /** A check beyond the schema's (https only, both-or-neither). */
  check?: (value: unknown, v: Values) => string | null;
  proposed?: boolean;
  irreversible?: boolean;
}

export type Tiers = Record<string, FieldTier>;

/** One option of a select or radio field. */
export const opt = (value: string, label: string, sub?: string) => ({ value, label, sub });
