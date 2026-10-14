// The run settings as the console reads, says and writes them. The cascade itself is the
// engine's (decision 0039 rule 2): status.json carries every effective value and the layer
// that gave it (`settings.effective_defaults`, `status_json.run_settings`), and the console
// reads it there; `over` lays a form's unsaved values over that answer for a live preview.
// A value the engine refuses (a typo, a negative team size) states nothing, and the next
// layer answers. The late pair is one rule: a layer stating either half states both.

import { YamlText, deepEqual, isObj, obj, type Path } from '../edit/yamlText';
import type { FileState, Files } from './files';
import { ASSIGNMENTS_FILE, CONFIG_REPO } from './names';
import assignmentsSchema from '../../schemas/assignments.schema.json';
import courseSchema from '../../schemas/dsl_course.schema.json';
import gradingSchema from '../../schemas/grading_config.schema.json';
// WP-C removes: the 0.10 assignments.yml / dsl-course.yml schemas the settings screens
// still validate against, frozen with the schedule's.
// WP-C removes: the schedule editor still writes the 0.10 shape, so it validates against a
// frozen copy of that schema until it writes the 0.11 one (decision 0038).
import scheduleSchema from '../../schemas/schedule-0.10.schema.json';
import { labelMap, labelOf } from './labels';
import { DEFAULTS, penaltyRate } from './policy';
import type { Status } from './types';
import { validator } from './validate';

export const RUN_KEYS = ['team_formation', 'max_team_size', 'late_window_days', 'late_penalty_per_day', 'visibility', 'submit_url', 'marks_per_question'] as const;
export type RunKey = (typeof RUN_KEYS)[number];
export const LATE_PAIR: RunKey[] = ['late_window_days', 'late_penalty_per_day'];

/** Where an effective value comes from (`settings.SOURCES`). */
export type Source = 'entry' | 'semester' | 'course' | 'institution';

export type Block = Record<string, unknown>;

export interface Effective {
  value: unknown;
  source: Source;
}

/** Every effective value at one level, keyed as the files spell it (`assignment_repo_visibility`). */
export type Effectives = Partial<Record<string, Effective>>;

/** A run key as the files and status.json spell it. */
export const fileKey = (key: string): string => (key === 'visibility' ? 'assignment_repo_visibility' : key);


const ENUMS = (assignmentsSchema.properties.defaults.properties as unknown as Record<string, { enum?: string[] }>);
/** The closed vocabularies, as the exported schema lists them (`setting_readers._one_of`). */
export const TEAM_FORMATIONS: string[] = ENUMS.team_formation.enum ?? [];
export const VISIBILITIES: string[] = ENUMS.visibility.enum ?? [];
/** Their words, from the engine's labels export. */
export const TEAM_FORMATION = labelMap('team_formation', TEAM_FORMATIONS);
export const VISIBILITY = labelMap('visibility', VISIBILITIES);

/** The placeholder the scaffold seeds in a submit link (`course.SETTING_PLACEHOLDER`, not exported). */
const SETTING_PLACEHOLDER = 'CHANGE-ME';

/** Python's `int(str(v).strip())`: a whole number spelt in digits, or null. */
function pyInt(v: unknown): number | null {
  if (typeof v === 'boolean' || v === null || v === undefined) return null;
  const t = String(v).trim();
  return /^[+-]?\d+$/.test(t) ? Number(t) : null;
}

/** A URL's host name; '' for text that is not a URL. */
export function hostOf(url: string): string {
  try {
    return new URL(url).hostname;
  } catch {
    return '';
  }
}

/**
 * The value the engine's reader for `key` reads from `v` (`setting_readers.READERS`), or
 * undefined for a value it refuses or a blank one: either states nothing, and the next layer
 * answers. Enums are lower-cased; a negative late window reads as 0.
 */
export function readSetting(key: string, v: unknown): unknown {
  if (v === null || v === undefined || (typeof v === 'string' && !v.trim())) return undefined;
  switch (key) {
    case 'team_formation':
    case 'visibility': {
      const t = String(v).trim().toLowerCase();
      return (key === 'visibility' ? VISIBILITIES : TEAM_FORMATIONS).includes(t) ? t : undefined;
    }
    case 'max_team_size': {
      const n = pyInt(v);
      return n !== null && n > 0 ? n : undefined;
    }
    case 'late_window_days': {
      const n = pyInt(v);
      return n === null ? undefined : Math.max(0, n);
    }
    case 'late_penalty_per_day':
      return penaltyRate(v) === null ? undefined : String(v).trim();
    case 'submit_url': {
      const t = String(v).trim();
      return t.toLowerCase().startsWith('https://') && hostOf(t) && !t.includes(SETTING_PLACEHOLDER) ? t : undefined;
    }
    case 'marks_per_question':
      // `setting_readers._boolean`: true, yes, on or 1 is on; anything else is off.
      return typeof v === 'boolean' ? v : ['true', 'yes', 'on', '1'].includes(String(v).trim().toLowerCase());
    default:
      return v;
  }
}

/** A form's value as the file writes it: a yes/no setting chosen from a select is a boolean, not text. */
export function fileValue(key: string, v: unknown): unknown {
  return key === 'marks_per_question' && typeof v === 'string' && v !== '' ? readSetting(key, v) : v;
}

/** Whether the engine reads `v` for `key`. */
export const usable = (key: string, v: unknown) => readSetting(key, v) !== undefined;

/** A block as the engine reads it: only the `keys` it states usably, each as read. */
export function usableBlock(raw: unknown, keys: readonly string[] = RUN_KEYS): Block {
  const b = obj(raw);
  const out: Block = {};
  for (const k of keys) {
    const v = readSetting(k, b[k]);
    if (v !== undefined) out[k] = v;
  }
  return out;
}

/** The name the semester's artefacts use for an assignment (`schedule.semester_name`): its
 *  `semester_dest_repo` in assignments.yml when that is a repo name, else its schedule key. */
export function semesterName(assignmentsDoc: Block, key: string): string {
  const dest = String(obj(obj(assignmentsDoc.assignments)[key]).semester_dest_repo ?? '').trim();
  return dest && REPO_NAME_RE.test(dest) ? dest : key;
}

/** A repo name as `settings._REPO_NAME` accepts one. */
export const REPO_NAME_RE = /^[A-Za-z0-9._-]+$/;

/** The institution's answer for every key it states (`policy.json`), the floor every level falls back to. */
export function institutionDefaults(): Effectives {
  return Object.fromEntries(Object.entries(DEFAULTS as unknown as Block).map(([k, v]) => [k, { value: v, source: 'institution' as const }]));
}

/** `key`'s effective value at a level (`status.json`), else the institution's, else none. */
export function effectiveOf(at: Effectives | undefined, key: string): Effective {
  return at?.[fileKey(key)] ?? institutionDefaults()[fileKey(key)] ?? { value: null, source: 'institution' };
}

/** A semester's effective defaults: what an assignment's own settings fall back to. */
export const semesterDefaults = (s: Status): Effectives => s.semester?.defaults ?? {};
/** A course's effective defaults: what a semester's own fall back to. */
export const courseDefaults = (s: Status | null | undefined): Effectives => s?.course?.defaults ?? {};

/**
 * `key` for a level being edited: `own` (its unsaved values, as `usableBlock` reads them) when
 * it states the key - a layer naming either half of the late pair states both - else what
 * `below` says. A live preview only: the saved answer is status.json's.
 */
export function over(key: RunKey, own: Block, source: Source, below: Effectives | undefined): Effective {
  if (LATE_PAIR.includes(key) ? LATE_PAIR.some((k) => k in own) : own[key] !== undefined && own[key] !== null && own[key] !== '') return { value: own[key] ?? null, source };
  return effectiveOf(below, key);
}

// ------------------------------------------------------------------ words

export const SOURCE_WORD: Record<Source, string> = {
  entry: 'set for this assignment',
  semester: 'this semester’s default',
  course: 'this course’s default',
  institution: 'institution default',
};

/** A run setting's value in words: "<n> days", "Students form their own". */
export function valueWord(key: RunKey, v: unknown): string {
  if (v === null || v === undefined || v === '') return key === 'late_penalty_per_day' ? 'no penalty' : key === 'late_window_days' ? 'no late work' : 'not set';
  switch (key) {
    case 'team_formation':
      return labelOf('team_formation', String(v));
    case 'visibility':
      return labelOf('visibility', String(v));
    case 'late_window_days':
      return v === 0 ? 'no late work' : `${String(v)} day${v === 1 ? '' : 's'}`;
    case 'late_penalty_per_day':
      return `${String(v)} a day`;
    case 'max_team_size':
      return `up to ${String(v)}`;
    case 'marks_per_question':
      return readSetting(key, v) ? 'on' : 'off';
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
  if (!(typeof days === 'number' && Number.isInteger(days) && days >= 1)) return 'no late work';
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

/** A YAML file as the forms edit it; a missing file reads as `stub` with no sha (null: missing is unreadable). */
export function yamlFile(f: FileState, stub: string | null): YamlFile | 'loading' | null {
  if (f.kind === 'loading') return 'loading';
  if (f.kind === 'error' || (f.kind === 'absent' && stub === null)) return null;
  const text = f.kind === 'ready' ? f.text : (stub ?? '');
  const y = new YamlText(text);
  return { text, sha: f.kind === 'ready' ? f.sha : null, doc: y.errors.length ? {} : obj(y.toJS()), error: y.errors[0] ?? null };
}

export function assignmentsFile(files: Files, semesterOrg: string): YamlFile | 'loading' | null {
  return yamlFile(files.file(semesterOrg, CONFIG_REPO, ASSIGNMENTS_FILE), ASSIGNMENTS_STUB);
}

export function scheduleFile(files: Files, semesterOrg: string): YamlFile | 'loading' | null {
  return yamlFile(files.file(semesterOrg, CONFIG_REPO, 'schedule.yml'), null);
}

/** Each instructor file checked against its exported schema, before anything is written. */
export const validAssignments = validator(assignmentsSchema);
export const validSchedule = validator(scheduleSchema);
export const validCourse = validator(courseSchema);
export const validGrading = validator(gradingSchema);

/**
 * Write `after` into the block at `path`, key by key, touching only what changed; a
 * cleared key is removed, and an emptied block with it. `keys` limits what is compared.
 */
export function writeBlock(y: YamlText, path: Path, before: Block, after: Block, keys: readonly string[]): boolean {
  let changed = false;
  for (const k of keys) {
    const a = after[k] === '' ? undefined : fileValue(k, after[k]);
    if (deepEqual(before[k] === '' ? undefined : before[k], a)) continue;
    y.assign([...path, k], a);
    changed = true;
  }
  if (changed)
    for (let i = path.length; i > 0; i--) {
      const v = y.get(path.slice(0, i));
      const empty = v === null || (isObj(v) && !Object.keys(v).length);
      if (!empty) break;
      y.delete(path.slice(0, i));
    }
  return changed;
}

/**
 * assignments.yml's text with one run setting of assignment `key` set to `value`, or null when
 * its block already says so. Throws when the file cannot be written as it stands.
 */
export function withRunSetting(f: YamlFile, key: string, setting: string, value: unknown): string | null {
  if (f.error) throw new Error(`${ASSIGNMENTS_FILE} does not parse (${f.error}); nothing was saved`);
  const path: Path = ['assignments', key];
  const before = rawBlock(f.doc, path);
  const y = new YamlText(f.text);
  if (!writeBlock(y, path, before, { ...before, [setting]: value }, [setting])) return null;
  if (!validAssignments(y.toJS() ?? {})) throw new Error(`${ASSIGNMENTS_FILE} would not be valid; nothing was saved`);
  return y.text;
}

/** What a run-settings form edits at one layer: the block's own keys, as written. */
export function rawBlock(doc: Block, path: Path): Block {
  let cur: unknown = doc;
  for (const p of path) cur = obj(cur)[String(p)];
  return obj(cur);
}
