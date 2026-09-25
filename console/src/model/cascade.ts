// The run settings of an assignment and where each effective value comes from: the
// cascade of decisions 0009 (rule 5) and 0010 (rule 5), as the engine's `settings.resolve`
// reads it. Four layers, nearest first:
//
//   assignment   semester-config/assignments.yml  assignments.<key>.<setting>
//   semester     semester-config/assignments.yml  defaults.<setting>
//   course       .github/dsl-course.yml            assignment_defaults.<setting>
//   institution  policy.yml                        defaults.<setting>  (console/schemas/policy.json)
//
// A value a layer states but the engine refuses (a typo, a negative team size) states
// nothing, and the next layer answers. The late pair is one rule: a layer stating either
// half states both, the other half absent meaning none.

import { YamlText, deepEqual, obj, type Path } from '../edit/yamlText';
import type { FileState, Files } from './files';
import { ASSIGNMENTS_FILE, CONFIG_REPO, COURSE_REPO } from './names';
import { DEFAULTS, penaltyRate } from './policy';

export const RUN_KEYS = ['team_formation', 'max_team_size', 'late_window_days', 'late_penalty_per_day', 'visibility', 'submit_url'] as const;
export type RunKey = (typeof RUN_KEYS)[number];
export const LATE_PAIR: RunKey[] = ['late_window_days', 'late_penalty_per_day'];

export type Source = 'assignment' | 'semester' | 'course' | 'institution';
export const SOURCES: Source[] = ['assignment', 'semester', 'course', 'institution'];

export type Block = Record<string, unknown>;
export type Layers = Record<Source, Block>;

export interface Effective {
  value: unknown;
  source: Source;
}

export const TEAM_FORMATION: Record<string, string> = { self_select: 'Students form their own', assigned: 'You assign them' };
export const VISIBILITY: Record<string, string> = { private: 'Private', public: 'Public', student_choice: 'Student’s choice' };

const whole = (v: unknown, min: number) => typeof v === 'number' && Number.isInteger(v) && v >= min;

/** Whether the engine's reader for `key` accepts `v`; a refused value states nothing. */
export function usable(key: string, v: unknown): boolean {
  if (v === null || v === undefined || v === '') return false;
  switch (key) {
    case 'team_formation':
      return typeof v === 'string' && v in TEAM_FORMATION;
    case 'visibility':
      return typeof v === 'string' && v in VISIBILITY;
    case 'max_team_size':
      return whole(v, 1);
    case 'late_window_days':
      return whole(v, 0);
    case 'late_penalty_per_day':
      return penaltyRate(v) !== null;
    case 'submit_url':
      return typeof v === 'string' && /^https:\/\/\S+$/.test(v);
    default:
      return true;
  }
}

/** A block with only the run keys it states usably. */
export function usableBlock(raw: unknown): Block {
  const b = obj(raw);
  return Object.fromEntries(RUN_KEYS.filter((k) => usable(k, b[k])).map((k) => [k, b[k]]));
}

/** The institution's layer: policy.json's defaults for the run keys. */
export function institutionLayer(): Block {
  const d = DEFAULTS as unknown as Block;
  return Object.fromEntries(RUN_KEYS.filter((k) => d[k] !== undefined).map((k) => [k, d[k]]));
}

function states(block: Block, key: RunKey): boolean {
  if (LATE_PAIR.includes(key)) return LATE_PAIR.some((k) => k in block);
  return block[key] !== undefined && block[key] !== null && block[key] !== '';
}

/** The effective value of `key` and the layer it comes from, looking only at `from` and the layers after it. */
export function resolve(key: RunKey, layers: Layers, from: Source = 'assignment'): Effective {
  for (const source of SOURCES.slice(SOURCES.indexOf(from))) {
    const b = layers[source];
    if (states(b, key)) return { value: b[key] ?? null, source };
  }
  return { value: null, source: 'institution' };
}

/** The layer after `s`, the one a cleared value falls back to. */
export function below(s: Source): Source {
  return SOURCES[Math.min(SOURCES.indexOf(s) + 1, SOURCES.length - 1)];
}

// ------------------------------------------------------------------ words

export const SOURCE_WORD: Record<Source, string> = {
  assignment: 'set for this assignment',
  semester: 'this semester’s default',
  course: 'this course’s default',
  institution: 'institution default',
};

/** A run setting's value in words: "<n> days", "Students form their own". */
export function valueWord(key: RunKey, v: unknown): string {
  if (v === null || v === undefined || v === '') return key === 'late_penalty_per_day' ? 'no penalty' : key === 'late_window_days' ? 'no late work' : 'not set';
  switch (key) {
    case 'team_formation':
      return TEAM_FORMATION[String(v)] ?? String(v);
    case 'visibility':
      return VISIBILITY[String(v)] ?? String(v);
    case 'late_window_days':
      return v === 0 ? 'no late work' : `${String(v)} day${v === 1 ? '' : 's'}`;
    case 'late_penalty_per_day':
      return `${String(v)} a day`;
    case 'max_team_size':
      return `up to ${String(v)}`;
    default:
      return String(v);
  }
}

/** "<n> days, institution default". */
export function effectiveWord(key: RunKey, e: Effective): string {
  return `${valueWord(key, e.value)}, ${SOURCE_WORD[e.source]}`;
}

/** The late rule in words: "<n> days at <penalty> a day", "no late work". */
export function lateWord(days: unknown, penalty: unknown): string {
  if (!whole(days, 1)) return 'no late work';
  return `${valueWord('late_window_days', days)}${penalty ? ` at ${valueWord('late_penalty_per_day', penalty)}` : ', no penalty'}`;
}

// ------------------------------------------------------------------ files

export const ASSIGNMENTS_STUB =
  '# INSTRUCTOR-OWNED - yours to edit freely; edits here are not overwritten.\n#\n# How THIS semester runs each assignment. The dates live in schedule.yml.\n';

export interface YamlFile {
  text: string;
  sha: string | null;
  doc: Block;
  error: string | null;
}

/** A YAML file as the forms edit it; a missing file reads as `stub` with no sha. */
export function yamlFile(f: FileState, stub: string): YamlFile | 'loading' | null {
  if (f.kind === 'loading') return 'loading';
  if (f.kind === 'error') return null;
  const text = f.kind === 'ready' ? f.text : stub;
  const y = new YamlText(text);
  return { text, sha: f.kind === 'ready' ? f.sha : null, doc: y.errors.length ? {} : obj(y.toJS()), error: y.errors[0] ?? null };
}

export function assignmentsFile(files: Files, semesterOrg: string): YamlFile | 'loading' | null {
  return yamlFile(files.file(semesterOrg, CONFIG_REPO, ASSIGNMENTS_FILE), ASSIGNMENTS_STUB);
}

/** The course's `assignment_defaults`: from dsl-course.yml as last read or written, else as discovered. */
export function courseBlock(files: Files, courseOrg: string, meta: Record<string, unknown> | null): Block {
  const f = files.file(courseOrg, COURSE_REPO, 'dsl-course.yml');
  if (f.kind === 'ready') {
    const y = new YamlText(f.text);
    if (!y.errors.length) return obj(obj(y.toJS()).assignment_defaults);
  }
  return obj(meta?.assignment_defaults);
}

/** The four layers for one assignment (`key`: its schedule key), or for the semester's defaults when `key` is empty. */
export function layersOf(course: Block, assignmentsDoc: Block, key = ''): Layers {
  return {
    assignment: key ? usableBlock(obj(obj(assignmentsDoc.assignments)[key])) : {},
    semester: usableBlock(assignmentsDoc.defaults),
    course: usableBlock(course),
    institution: institutionLayer(),
  };
}

/**
 * Write `after` into the block at `path`, key by key, touching only what changed; a
 * cleared key is removed, and an emptied block with it. `keys` limits what is compared.
 */
export function writeBlock(y: YamlText, path: Path, before: Block, after: Block, keys: readonly string[]): boolean {
  let changed = false;
  for (const k of keys) {
    const a = after[k] === '' ? undefined : after[k];
    if (deepEqual(before[k] === '' ? undefined : before[k], a)) continue;
    y.assign([...path, k], a);
    changed = true;
  }
  if (changed)
    for (let i = path.length; i > 0; i--) {
      const v = y.get(path.slice(0, i));
      const empty = v === null || (!!v && typeof v === 'object' && !Array.isArray(v) && !Object.keys(v).length);
      if (!empty) break;
      y.delete(path.slice(0, i));
    }
  return changed;
}

/** What a run-settings form edits at one layer: the block's own keys, as written. */
export function rawBlock(doc: Block, path: Path): Block {
  let cur: unknown = doc;
  for (const p of path) cur = obj(cur)[String(p)];
  return obj(cur);
}
