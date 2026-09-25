// The engine's operations registry as the console reads it: `schemas/ops.json`, exported by
// `python -m dsl_course.schemas`. Names, scopes, whether an op has a preview, its args schema.

import ops from '../../schemas/ops.json';

export interface OpSpec {
  name: string;
  runs_as: 'dispatch' | 'edit';
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

export type OpMode = 'gated' | 'preview' | 'direct' | 'previewOnly';

/**
 * How the panel offers an op. A gated op whose engine has no preview cannot be previewed
 * (the engine refuses `preview: true` with NO_PREVIEW), so it runs direct and asks for an
 * explicit confirmation instead.
 */
export function modeOf(op: string): OpMode {
  const s = opSpec(op);
  if (PREVIEW_ONLY.has(op)) return 'previewOnly';
  if (!s.preview) return 'direct';
  return GATED.has(op) ? 'gated' : 'preview';
}
