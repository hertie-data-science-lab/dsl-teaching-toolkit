// schedule.yml entries as the entry sheet edits them (design/inputs.md "Schedule editor",
// revision brief v3 section 4), and back. Writing merges into the entry as it is in the
// file, so keys the sheet does not show are kept, and a default is written only when the
// file already spelt it.

import { obj, type YamlText } from '../edit/yamlText';
import { kebab, labelNumber } from './format';
import { solutionBeforeCutoff } from './labels';
import { ARCHIVE_GRACE_DAYS, DEFAULT_DEST_REPO } from './policy';

export type Block = 'releases' | 'assignments' | 'events';

export interface DeployDraft {
  repo: string;
  folder: string;
  dest: string;
  path: string;
  diff: boolean;
  atDate: string;
  atTime: string;
}

export interface ReleaseDraft {
  kind: 'releases';
  id: string;
  type: string;
  /** The row's number (decision 0020). Undefined until proposed; '' while the box is empty. */
  number?: number | '';
  title: string;
  date: string;
  time: string;
  details: string;
  show: boolean;
  tbc: boolean;
  deploys: DeployDraft[];
}

export interface AssignmentDraft {
  kind: 'assignments';
  id: string;
  template: string;
  /** Its number (decision 0020); a new entry's key is `assignment-<number>`. Undefined until proposed; '' while the box is empty. */
  number?: number | '';
  title: string;
  manual: boolean;
  handoutDate: string;
  handoutTime: string;
  dueDate: string;
  dueTime: string;
  solutionOn: boolean;
  solutionDate: string;
  solutionTime: string;
  details: string;
  show: boolean;
  tbc: boolean;
}

export interface EventDraft {
  kind: 'events';
  id: string;
  type: string; // exam | special_event
  title: string;
  date: string;
  time: string;
  details: string;
  show: boolean;
  tbc: boolean;
}

export interface SemesterDraft {
  kind: 'semester';
  start: string;
  end: string;
  tz: string;
}

export interface ArchiveDraft {
  kind: 'archive';
  on: boolean;
  date: string;
  title: string;
  details: string;
  show: boolean;
  tbc: boolean;
  /** Days after the semester ends that the default archive date falls; '' while the box is empty. */
  graceDays: number | '';
}


export type Draft = ReleaseDraft | AssignmentDraft | EventDraft | SemesterDraft | ArchiveDraft;

type Raw = Record<string, unknown>;

const s = (v: unknown): string => (v == null ? '' : v instanceof Date ? v.toISOString().slice(0, 16) : String(v));

/** "2026-10-08T10:00" -> ["2026-10-08", "10:00"]; a bare date has no time. */
export function splitWhen(v: unknown): [string, string] {
  const t = s(v);
  if (!t || t.toLowerCase() === 'tbc') return ['', ''];
  const m = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(t);
  return m ? [m[1], m[2] ?? ''] : [t, ''];
}

export function joinWhen(date: string, time: string): string | undefined {
  if (!date) return undefined;
  return time ? `${date}T${time}` : date;
}

/**
 * The moment to write for a date and time the sheet shows: the file's own value when it
 * still says that - so an offset (`+01:00`), seconds or a space the instructor wrote are
 * kept, not rewritten to the sheet's `T10:00` - else the sheet's.
 */
export function whenOf(raw: unknown, date: string, time: string): unknown {
  const t = s(raw);
  if (t && t.toLowerCase() !== 'tbc') {
    const [d, h] = splitWhen(raw);
    if (d === date && h === time) return raw;
  }
  return joinWhen(date, time);
}

/** Which block an entry id is in. */
export function blockOf(doc: Raw, id: string): Block | null {
  for (const b of ['releases', 'assignments', 'events'] as Block[]) if (id in obj(doc[b])) return b;
  return null;
}

/** The sheet's key for an entry: "semester", "archive", or the id (ids are unique across blocks in practice). */
export function readDraft(doc: Raw, key: string): Draft | null {
  if (key === 'semester') return { kind: 'semester', start: s(doc.semester_start), end: s(doc.semester_end), tz: s(doc.timezone) };
  if (key === 'archive') {
    const a = doc.archive === undefined ? null : obj(doc.archive);
    return { kind: 'archive', on: a !== null, date: splitWhen(a?.event_datetime)[0], title: s(a?.title), details: s(a?.details), show: a?.show_on_site !== false, tbc: a?.tbc === true, graceDays: typeof a?.grace_days === 'number' ? a.grace_days : ARCHIVE_GRACE_DAYS };
  }
  const b = blockOf(doc, key);
  if (!b) return null;
  const e = obj(obj(doc[b])[key]);
  const common = { id: key, title: s(e.title), details: s(e.details), show: e.show_on_site !== false, tbc: e.tbc === true };
  const number = entryNumber(key, e) ?? '';
  if (b === 'releases') {
    const [date, time] = splitWhen(e.event_datetime);
    const deploys = (Array.isArray(e.deploy) ? e.deploy : []).map((d: unknown) => {
      const x = obj(d);
      const [atDate, atTime] = splitWhen(x.deploy_datetime);
      return { repo: s(x.course_source_repo), folder: s(x.course_source_path), dest: s(x.semester_dest_repo), path: s(x.semester_dest_path), diff: !!x.deploy_datetime, atDate, atTime };
    });
    return { kind: 'releases', ...common, type: s(e.kind), number, date, time, deploys };
  }
  if (b === 'assignments') {
    const [handoutDate, handoutTime] = splitWhen(e.handout_datetime);
    const [dueDate, dueTime] = splitWhen(e.due_datetime);
    const [solutionDate, solutionTime] = splitWhen(e.solution_datetime);
    return {
      kind: 'assignments', ...common, template: s(e.course_source_repo), number, manual: !e.handout_datetime, handoutDate, handoutTime, dueDate, dueTime,
      solutionOn: !!e.solution_datetime, solutionDate, solutionTime,
    };
  }
  const [date, time] = splitWhen(e.event_datetime);
  return { kind: 'events', ...common, type: s(e.kind) || 'special_event', date, time, tbc: common.tbc || s(e.event_datetime).toLowerCase() === 'tbc' };
}

/** A value to write: `v`, unless it is the default and the file did not already spell it. */
function keep<T>(raw: unknown, v: T, dflt: T): T | undefined {
  if (v !== dflt) return v;
  return raw === dflt ? v : undefined;
}
const text = (v: string) => (v.trim() ? v : undefined);

function display(raw: Raw, d: { title: string; details: string; show: boolean; tbc: boolean }): Raw {
  return { title: text(d.title), details: text(d.details), show_on_site: keep(raw.show_on_site, d.show, true), tbc: keep(raw.tbc, d.tbc, false) };
}

/**
 * The `number:` to write (decision 0020): always on a new entry, and on one that already
 * spells it; else only when it differs from the number the key carries, so a
 * label-numbered entry is left as it is written.
 */
function numberValue(d: ReleaseDraft | AssignmentDraft, raw: Raw, fresh: boolean): number | undefined {
  if (d.number === undefined || d.number === '') return undefined;
  return fresh || 'number' in raw || d.number !== labelNumber(d.id) ? d.number : undefined;
}

/** The entry as it goes into the file, merged over what the file has (none: a new entry). */
export function entryValue(d: ReleaseDraft | AssignmentDraft | EventDraft, rawEntry: unknown): Raw {
  const raw = obj(rawEntry);
  const fresh = rawEntry === undefined;
  if (d.kind === 'releases') {
    const rawDeploys = Array.isArray(raw.deploy) ? raw.deploy : [];
    return {
      ...raw,
      event_datetime: whenOf(raw.event_datetime, d.date, d.time),
      kind: text(d.type),
      number: numberValue(d, raw, fresh),
      ...display(raw, d),
      deploy: d.deploys.length
        ? d.deploys.map((dp, i) => {
            const r = obj(rawDeploys[i]);
            return {
              ...r, course_source_repo: dp.repo, course_source_path: dp.folder,
              semester_dest_repo: dp.dest && (dp.dest !== DEFAULT_DEST_REPO || r.semester_dest_repo) ? dp.dest : undefined,
              semester_dest_path: text(dp.path), deploy_datetime: dp.diff ? whenOf(r.deploy_datetime, dp.atDate, dp.atTime) : undefined,
            };
          })
        : undefined,
    };
  }
  if (d.kind === 'assignments') {
    return {
      // Timings only (decision 0009): the title is the template's, the repo name and the
      // late cutoff are assignments.yml's. None is written here, and a retired key the
      // file still carries is left exactly as it is: the engine faults it and the
      // migration moves it.
      ...raw, ...display(raw, d), title: raw.title, course_source_repo: d.template, number: numberValue(d, raw, fresh),
      handout_datetime: d.manual ? undefined : whenOf(raw.handout_datetime, d.handoutDate, d.handoutTime),
      due_datetime: whenOf(raw.due_datetime, d.dueDate, d.dueTime),
      solution_datetime: d.solutionOn && !d.manual ? whenOf(raw.solution_datetime, d.solutionDate, d.solutionTime) : undefined,
    };
  }
  return {
    ...raw, kind: keep(raw.kind, d.type, 'special_event'), ...display(raw, d),
    event_datetime: whenOf(raw.event_datetime, d.date, d.time) ?? 'tbc', tbc: d.date ? keep(raw.tbc, d.tbc, false) : undefined,
  };
}

/** Write one draft into the file. */
export function writeDraft(y: YamlText, d: Draft, doc: Raw): void {
  if (d.kind === 'semester') {
    y.assign(['semester_start'], text(d.start));
    y.assign(['semester_end'], text(d.end));
    y.assign(['timezone'], text(d.tz));
    return;
  }
  if (d.kind === 'archive') {
    if (!d.on) {
      y.delete(['archive']);
      return;
    }
    const raw = obj(doc.archive);
    y.assign(['archive'], {
      ...raw,
      event_datetime: d.date ? whenOf(raw.event_datetime, d.date, splitWhen(raw.event_datetime)[1]) : 'event_datetime' in raw ? null : undefined,
      title: text(d.title),
      details: text(d.details),
      show_on_site: keep(raw.show_on_site, d.show, true),
      tbc: keep(raw.tbc, d.tbc, false),
      grace_days: keep(raw.grace_days, d.graceDays === '' ? ARCHIVE_GRACE_DAYS : d.graceDays, ARCHIVE_GRACE_DAYS),
    });
    return;
  }
  y.assign([d.kind, d.id], entryValue(d, obj(doc[d.kind])[d.id]));
}

/** A fresh id in `block`: `lecture-6`, `lab-4`, `assignment-2`, `midterm-exam`. */
export function freshId(doc: Raw, block: Block, stem: string): string {
  const taken = takenKeys(doc);
  const base = kebab(stem) || block.slice(0, -1);
  if (/-\d+$/.test(base) || block !== 'releases') {
    let id = base, n = 2;
    while (taken.has(id)) id = `${base}-${n++}`;
    return id;
  }
  let n = 1;
  while (taken.has(`${base}-${n}`)) n++;
  return `${base}-${n}`;
}

/** Every key the file uses, in any block: keys are unique across blocks. */
function takenKeys(doc: Raw): Set<string> {
  return new Set([...Object.keys(obj(doc.releases)), ...Object.keys(obj(doc.assignments)), ...Object.keys(obj(doc.events))]);
}

/** The key of a new assignment entry numbered `n` (decision 0014: the ordinal is the schedule's). */
export const assignmentKey = (n: number | '' | undefined) => `assignment-${n === undefined || n === '' ? 'N' : n}`;

/** The number an entry carries (decision 0020): its `number:`, else its key's own (`lecture_03`). Never a position. */
export function entryNumber(key: string, entry: unknown): number | null {
  const n = obj(entry).number;
  return Number.isInteger(n) && (n as number) > 0 ? (n as number) : labelNumber(key);
}

/** A `releases:` entry's kind as the file spells it; the engine's inferred kind is the caller's. */
export type KindOf = (key: string, entry: unknown) => string;
const spelledKind: KindOf = (_key, entry) => s(obj(entry).kind) || 'lecture';

/**
 * Whether an entry must carry a number (decision 0020): every assignment, and every release
 * row the site shows except readings, where a number joins that lecture.
 */
export function needsNumber(d: ReleaseDraft | AssignmentDraft, kind: string): boolean {
  return d.kind === 'assignments' || (d.show && kind !== 'readings');
}

/**
 * The number to propose for a new entry of `kind`: one more than the highest number an entry
 * of that kind carries (its `number:`, else its key's own number; one without adds nothing).
 * For an assignment, stepped past a key already taken, since its key is `assignment-<n>`.
 */
export function nextNumber(doc: Raw, kind: string, kindOf: KindOf = spelledKind): number {
  if (kind !== 'assignment') {
    const nums = Object.entries(obj(doc.releases)).filter(([k, e]) => kindOf(k, e) === kind).map(([k, e]) => entryNumber(k, e) ?? 0);
    return 1 + Math.max(0, ...nums);
  }
  const taken = takenKeys(doc);
  let n = 1 + Math.max(0, ...Object.entries(obj(doc.assignments)).map(([k, e]) => entryNumber(k, e) ?? 0));
  while (taken.has(assignmentKey(n))) n++;
  return n;
}

/**
 * A new entry's draft with its number proposed, when it needs one and has none yet; any other
 * draft as it is. `kind` is a release's kind as the engine will read it (its own, else the one
 * inferred from its folder). Nothing is proposed for readings: a number joins that lecture
 * (decision 0013 rule 3), so only one typed in is kept.
 */
export function withNumber<T extends Draft>(d: T, doc: Raw, kindOf: KindOf = spelledKind, kind?: string): T {
  if ((d.kind !== 'assignments' && d.kind !== 'releases') || d.id) return d;
  const k = d.kind === 'assignments' ? 'assignment' : kind || d.type || 'lecture';
  if (k === 'readings') return d;
  return d.number !== undefined ? d : { ...d, number: nextNumber(doc, k, kindOf) };
}

export function blankDraft(type: string, defaults: { repo: string }): ReleaseDraft | AssignmentDraft | EventDraft {
  const common = { id: '', title: '', details: '', show: true, tbc: false };
  if (type === 'handout')
    return { kind: 'assignments', ...common, template: '', manual: false, handoutDate: '', handoutTime: '10:00', dueDate: '', dueTime: '23:59', solutionOn: false, solutionDate: '', solutionTime: '' };
  if (type === 'exam' || type === 'special_event') return { kind: 'events', ...common, type, date: '', time: '' };
  return { kind: 'releases', ...common, type, date: '', time: '10:00', deploys: [{ repo: defaults.repo, folder: '', dest: '', path: '', diff: false, atDate: '', atTime: '' }] };
}

/** A sheet moment as the engine reads it, sortable: a bare day at its start, or (a due date) its end. */
function moment(when: string, endOfDay = false): string {
  if (when.length === 10) return `${when}T${endOfDay ? '23:59:59' : '00:00:00'}`;
  return when.length === 16 ? `${when}:00` : when;
}

/**
 * What is wrong with a draft, by field; empty when it can be saved. `cutoff` is an assignment's
 * late cutoff as `cutoffOf` gives it: a solution shown before it is refused, as the engine does.
 */
export function draftErrors(d: Draft, others?: { templateUsers?: (template: string) => number; doc?: Raw; kind?: string }, cutoff?: string | null): Record<string, string> {
  const e: Record<string, string> = {};
  if (d.kind === 'releases' || d.kind === 'assignments') {
    const kind = d.kind === 'assignments' ? 'assignment' : others?.kind ?? (d.type || 'lecture');
    if (d.number === '' || d.number === undefined) {
      if (needsNumber(d, kind)) e.number = 'A number is needed.';
      // The key's own number joins the lecture whatever the field says (`readings-3`).
      else if (kind === 'readings' && labelNumber(d.id) !== null) e.number = `The key ${d.id} carries the number ${labelNumber(d.id)}. Rename the key in schedule.yml to make this a row of its own.`;
    } else if (!(Number.isInteger(d.number) && d.number >= 1 && d.number <= 999)) e.number = 'A whole number from 1 to 999.';
    else if (d.kind === 'assignments' && !d.id && others?.doc && takenKeys(others.doc).has(assignmentKey(d.number))) e.number = `${assignmentKey(d.number)} is already in this schedule.`;
  }
  if (d.kind === 'releases') {
    if (!d.date) e.date = 'When is needed.';
    d.deploys.forEach((dp, i) => {
      if (!dp.repo) e[`deploy${i}.repo`] = 'Choose the repo.';
      if (!dp.folder) e[`deploy${i}.folder`] = 'Name the folder or file.';
      if (dp.diff && !dp.atDate) e[`deploy${i}.at`] = 'Give its own time, or untick.';
    });
  } else if (d.kind === 'assignments') {
    if (!d.template) e.template = 'Choose the template.';
    if (!d.dueDate) e.due = 'Due is needed.';
    if (!d.manual && !d.handoutDate) e.handout = 'Give the hand out date, or choose to hand out manually.';
    const h = joinWhen(d.handoutDate, d.handoutTime) ?? '';
    const due = joinWhen(d.dueDate, d.dueTime) ?? '';
    if (h && due && due < h) e.due = 'Due must come after the hand out.';
    const sol = joinWhen(d.solutionDate, d.solutionTime);
    if (d.solutionOn && !d.manual && !sol) e.solution = 'Give the date the solution is shown.';
    if (d.solutionOn && sol && h && sol <= h) e.solution = 'Must be after the hand out.';
    else if (d.solutionOn && sol && cutoff && moment(sol) < moment(cutoff, true)) {
      const shown = (m: string) => m.slice(0, 16).replace('T', ' ');
      e.solution = solutionBeforeCutoff(d.id || assignmentKey(d.number), shown(moment(sol)), shown(moment(cutoff, true)));
    }
  } else if (d.kind === 'events') {
    if (!d.title.trim()) e.title = 'A title is needed; the student site shows only this.';
  } else if (d.kind === 'semester') {
    if (d.start && d.end && d.end <= d.start) e.end = 'The semester must end after it starts.';
  } else if (d.kind === 'archive') {
    if (d.on && d.graceDays !== '' && !(Number.isInteger(d.graceDays) && d.graceDays >= 0)) e.graceDays = 'A whole number of days, 0 or more.';
  }
  return e;
}
