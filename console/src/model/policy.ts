// The institution's policy and the engine's name rules, as the engine exports them beside
// the JSON Schemas (`console/schemas/policy.json`, `request.schema.json`). The console keeps
// no default of its own (decision 0009 rule 8): every default a form shows, pre-fills or
// falls back to is read here, so the console cannot disagree with the engine.

import policy from '../../schemas/policy.json';
import requestSchema from '../../schemas/request.schema.json';
import gradingSchema from '../../schemas/grading_config.schema.json';

export interface Policy {
  contact: string;
  defaults: {
    archive: { grace_days: number };
    formats: string[];
    late_penalty_per_day: string;
    late_window_days: number;
    max_team_size: number;
    semester_dest_repo: string;
    team_formation: string;
    timezone: string;
    visibility: string;
  };
  institution: Record<string, string>;
  kinds: { key: string; label: string; colour: string; background: string; system: boolean }[];
  licences: { name: string; url: string }[];
}

export const POLICY: Policy = policy;
export const DEFAULTS = POLICY.defaults;

/** The semester's timezone when its schedule names none. */
export const DEFAULT_TIMEZONE = DEFAULTS.timezone;
/** Days after the semester ends that it is archived, when the schedule says nothing. */
export const ARCHIVE_GRACE_DAYS = DEFAULTS.archive.grace_days;
/** Where a release lands in the semester when its deploy names no repo. */
export const DEFAULT_DEST_REPO = DEFAULTS.semester_dest_repo;
/** The starter formats a new template gets when nobody chose; the first is the runnable one. */
export const DEFAULT_FORMATS = DEFAULTS.formats;

const pattern = (key: string) => new RegExp((requestSchema.properties as unknown as Record<string, { pattern: string }>)[key].pattern);
/** A GitHub handle, as the engine's request schema spells it. */
export const HANDLE_RE = pattern('actor');
/** A GitHub organisation name, as the engine's request schema spells it. */
export const ORG_NAME_RE = pattern('course_org');

/** Where a template's students submit when it says nothing: the toolkit's shape default
 * (`setting_readers.READERS['submit_via']`), not a policy value, which policy.json does not export. */
export const SUBMIT_VIA_DEFAULT = 'assignment_repo';
/** Every place students may submit, in the schema's order. */
export const SUBMIT_VIA_KEYS: string[] = gradingSchema.properties.submit_via.enum;

/** Every format a template may list, in the schema's order. */
export const FORMAT_KEYS: string[] = (gradingSchema.properties.formats.oneOf[0] as { items: { enum: string[] } }).items.enum;

/** `late_penalty_per_day` as a fraction (a percentage and a fraction alike), or null for a value the
 * engine refuses (`setting_readers.penalty_fault`): not a number, a bare number of 1 or more,
 * negative, or more than 100% a day. Blank is null too: no penalty. */
export function penaltyRate(text: unknown): number | null {
  if (text === null || text === undefined || typeof text === 'boolean') return null;
  const raw = String(text).trim();
  if (!raw) return null;
  const pct = raw.endsWith('%');
  const body = (pct ? raw.slice(0, -1) : raw).trim();
  if (!/^[+-]?(\d+\.?\d*|\.\d+)$/.test(body)) return null;
  let rate = Number(body);
  if (pct) rate /= 100;
  else if (rate >= 1) return null;
  return rate < 0 || rate > 1 ? null : rate;
}

/** Why a typed penalty cannot be used, or null. */
export function penaltyError(text: unknown): string | null {
  if (text === undefined || text === null || String(text).trim() === '') return null;
  return penaltyRate(text) === null ? `Write a percentage (${DEFAULTS.late_penalty_per_day}) or a fraction of the mark per day.` : null;
}
