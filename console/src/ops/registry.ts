// The engine's operations registry as the console reads it: `schemas/ops.json`, exported by
// `python -m dsl_course.schemas`. Names, scopes, whether an op has a preview, its args schema.

import ops from '../../schemas/ops.json';

export interface OpSpec {
  name: string;
  scope: 'course' | 'semester';
  required_team: string;
  args_schema: Record<string, unknown> & { properties?: Record<string, Record<string, unknown>>; required?: string[] };
  help: string;
  doc: string;
  preview: boolean;
  via: string;
  counts_doc: string;
}

export const OPS: Record<string, OpSpec> = Object.fromEntries((ops.ops as unknown as OpSpec[]).map((o) => [o.name, o]));

export function opSpec(name: string): OpSpec {
  const s = OPS[name];
  if (!s) throw new Error(`${name} is not in the operations registry`);
  return s;
}

/**
 * Operations whose verb unlocks only after a preview in this session (inputs.md rule 5):
 * hand out, return marks, archive, update every copy, send new codes, publish website.
 */
export const GATED = new Set([
  'assignment.handout_now',
  'grades.return',
  'semester.archive',
  'assignment.update_copies',
  'roster.send_codes',
  'course.publish_website',
]);

/** An op that is itself a look, never a change: it always runs as a preview. */
export const PREVIEW_ONLY = new Set(['semester.preview_automation']);

/**
 * Ops the engine can preview whose preview the console does not offer (decision 0031 rule
 * 6): it shows nothing anyone acts on. Derive and the releases report a count (a release's
 * path checks refuse the real run just the same, before it copies anything); Keep for
 * future semesters proposes changes you accept on GitHub anyway;
 * the team-window email and instructor access report a count; Collect now's preview only
 * starts another workflow the panel does not follow.
 */
export const PREVIEW_NOT_OFFERED = new Set([
  'assignment.derive_starter',
  'release.entry',
  'release.adhoc',
  'release.propagate_back',
  'teams.open_window',
  'access.check',
  'assignment.collect_now',
]);

export type OpMode = 'gated' | 'preview' | 'direct' | 'previewOnly';

/**
 * How the panel offers an op. A gated op whose engine has no preview cannot be previewed
 * (the engine refuses `preview: true` with NO_PREVIEW), so it runs direct and asks for an
 * explicit confirmation instead.
 */
export function modeOf(op: string): OpMode {
  const s = opSpec(op);
  if (PREVIEW_ONLY.has(op)) return 'previewOnly';
  if (!s.preview || PREVIEW_NOT_OFFERED.has(op)) return 'direct';
  return GATED.has(op) ? 'gated' : 'preview';
}
