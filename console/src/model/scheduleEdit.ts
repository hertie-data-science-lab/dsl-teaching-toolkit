// schedule.yml entries as the schedule editor's forms edit them (decision 0038 and its two
// amendments; forms mock-up v3), and back. One block per kind (`lectures:`, `labs:`, ...,
// `assignments:`, `semester:`); the block decides the kind, the key names what the toolkit
// creates for the entry and is never parsed. Writing merges into the entry as it is in the
// file, so keys a form does not show are kept, and a default is written only when the file
// already spelt it.

import { YamlText, obj } from '../edit/yamlText';
import { kebab } from './format';
import { solutionBeforeCutoff } from './labels';
import { ARCHIVE_GRACE_DAYS, DEFAULT_DEST_REPO, EVENT_KIND, KINDS, pluralOf } from './policy';

type Raw = Record<string, unknown>;

// ------------------------------------------------------------------ kinds and blocks

/** The kinds whose entries belong to a teaching unit and must name it (`schedule.SESSION_KINDS`). */
export const SESSION_KINDS = ['lecture', 'lab'];
/** The readings kind: its entries carry a reading guide and attach to a unit by `session`. */
export const READINGS_KIND = 'readings';
export { EVENT_KIND };
/** The block of the term's own dates: start, end, breaks, archive (0038 amendment 2). */
export const SEMESTER_BLOCK = 'semester';
export const ASSIGNMENTS_BLOCK = 'assignments';
/** The types a `semester:` entry may have, in the form's order. */
export const SEMESTER_TYPES = ['start', 'end', 'break', 'archive'] as const;
export type SemesterType = (typeof SEMESTER_TYPES)[number];

/** The kinds an instructor adds entries of: the institution's, minus the engine's own (assignment, semester, archive). */
export const CONTENT_KINDS: string[] = KINDS.filter((k) => !k.system).map((k) => k.key);
/** The content blocks, in the policy's order. */
export const CONTENT_BLOCKS: string[] = CONTENT_KINDS.map(pluralOf);
const kindOfBlock = (block: string) => KINDS.find((k) => !k.system && k.plural === block)?.key ?? null;
/** Whether entries of `kind` are released but never a row (supporting files): shown off by default. */
export const hiddenKind = (kind: string) => KINDS.find((k) => k.key === kind)?.shown === false;
/** Whether entries of `kind` take a Number at all: every kind but events. */
export const numberedField = (kind: string) => kind !== EVENT_KIND;

/** Every block a key may live in, in file order. */
const ALL_BLOCKS = () => [...CONTENT_BLOCKS, ASSIGNMENTS_BLOCK, SEMESTER_BLOCK];

/** The block holding `key`, or null. Keys are unique across the file. */
export function blockOf(doc: Raw, key: string): string | null {
  for (const b of ALL_BLOCKS()) if (key in obj(doc[b])) return b;
  return null;
}

/** Every key the file uses, in any block. */
export function takenKeys(doc: Raw): Set<string> {
  return new Set(ALL_BLOCKS().flatMap((b) => Object.keys(obj(doc[b]))));
}

// ------------------------------------------------------------------ drafts

export interface FileDraft {
  repo: string;
  path: string;
  /** `to_semester_repo`; blank is the default (`defaults.materials_repo`). */
  toRepo: string;
  /** `to_path`; blank is the same as the source path. */
  toPath: string;
  /** Its own release time; blank ships it at the default (`defaultReleaseAt`). */
  atDate: string;
  atTime: string;
  /** A readings file the open site may host (`public: true`, decision 0040 via WP-SEL). */
  public: boolean;
  /** Its index in the file's `files:` list; none for one added here. */
  src?: number;
}

export interface Slot {
  date: string;
  time: string;
  location: string;
  details: string;
}

/** One entry of a content block (lecture, lab, readings, exam, event, supporting files, ...). */
export interface EntryDraft {
  kind: 'entry';
  /** The policy kind (`lecture`); its block is the kind's plural. */
  type: string;
  /** The entry's key; '' for a new one (generated on save). */
  id: string;
  session: number | '';
  /** The row word's number; '' is the default (the session, for a unit's kinds) or none. */
  number: number | '';
  title: string;
  details: string;
  /** At least one. With one slot its location is the entry's `location:`; with more, each slot carries its own. */
  slots: Slot[];
  files: FileDraft[];
  /** `reading_guide:` (readings only). */
  guide: string;
  show: boolean;
  /** Date provisional: `tbc: true` beside a real date. */
  provisional: boolean;
  /** Date TBC: `event_datetime: tbc`, the date cleared and disabled. */
  tbc: boolean;
  /** A lecture's or lab's "Readings for this session": the readings entry with its session, or null for none yet. */
  twin?: EntryDraft | null;
}

export interface AssignmentDraft {
  kind: 'assignments';
  id: string;
  template: string;
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
  /** Dates provisional (`tbc: true`). */
  tbc: boolean;
  /** Dates TBC: `due_datetime: tbc`, nothing handed out automatically. */
  datesTbc?: boolean;
  marksOn?: boolean;
  marksDate?: string;
  marksTime?: string;
  /** `type:` override ('' = the template's). */
  group?: '' | 'group' | 'individual';
  /** `submit_via:` override ('' = the template's). */
  submitVia?: string;
  /** The inline run settings this entry overrides, by their cascade names. */
  run?: Raw;
  /** `repo_name:` ('' = the key). */
  repoName?: string;
}

/** The semester's dates sheet: its start, end and timezone. */
export interface SemesterDraft {
  kind: 'semester';
  start: string;
  end: string;
  tz: string;
}

/** One entry of the `semester:` block. */
export interface SemesterEntryDraft {
  kind: 'semesterEntry';
  id: string;
  type: SemesterType;
  title: string;
  /** The day (start, end, archive), or a break's first day. */
  from: string;
  /** A break's last day. */
  to: string;
  details: string;
  show: boolean;
  provisional: boolean;
  /** An archive with no date: the days after the semester ends it falls (`grace_days`); '' while the box is empty. */
  graceDays: number | '';
}

export type Draft = EntryDraft | AssignmentDraft | SemesterDraft | SemesterEntryDraft;

// ------------------------------------------------------------------ moments

const s = (v: unknown): string => (v == null ? '' : v instanceof Date ? v.toISOString().slice(0, 16) : String(v));
const isTbc = (v: unknown) => s(v).trim().toLowerCase() === 'tbc';

/** "2026-10-08T10:00" -> ["2026-10-08", "10:00"]; a bare date has no time. */
export function splitWhen(v: unknown): [string, string] {
  const t = s(v);
  if (!t || isTbc(t)) return ['', ''];
  const m = /^(\d{4}-\d{2}-\d{2})(?:[T ](\d{2}:\d{2}))?/.exec(t);
  return m ? [m[1], m[2] ?? ''] : [t, ''];
}

export function joinWhen(date: string, time: string): string | undefined {
  if (!date) return undefined;
  return time ? `${date}T${time}` : date;
}

/**
 * The moment to write for a date and time the form shows: the file's own value when it
 * still says that (an offset, seconds or a space the instructor wrote are kept), else the form's.
 */
export function whenOf(raw: unknown, date: string, time: string): unknown {
  const t = s(raw);
  if (t && !isTbc(t)) {
    const [d, h] = splitWhen(raw);
    if (d === date && h === time) return raw;
  }
  return joinWhen(date, time);
}

/**
 * When a file that names no release time ships, as its chip says it: an hour before a timed
 * entry (the engine's default offset, on the wall clock), the day's start for a whole-day one,
 * nothing for an undated one.
 */
export function defaultReleaseAt(date: string, time: string): [string, string] {
  if (!date || !time) return [date, ''];
  const [y, mo, d] = date.split('-').map(Number);
  const [h, mi] = time.split(':').map(Number);
  const at = new Date(Date.UTC(y, mo - 1, d, h, mi) - 3_600_000).toISOString();
  return [at.slice(0, 10), at.slice(11, 16)];
}

/** A file's release time as typed: emptied, or back at the default, it is blank again, so no `release_datetime` is written. */
export function releaseAt(first: { date: string; time: string }, date: string, time: string): Pick<FileDraft, 'atDate' | 'atTime'> {
  const [dd, dt] = defaultReleaseAt(first.date, first.time);
  return !date || (date === dd && time === dt) ? { atDate: '', atTime: '' } : { atDate: date, atTime: time };
}

// ------------------------------------------------------------------ reading

const int = (v: unknown): number | '' => (Number.isInteger(v) && (v as number) > 0 ? (v as number) : '');

function readFiles(raw: unknown): FileDraft[] {
  return (Array.isArray(raw) ? raw : []).map((f: unknown, src: number) => {
    const x = obj(f);
    const [atDate, atTime] = splitWhen(x.release_datetime);
    return { repo: s(x.from_course_repo), path: s(x.from_path), toRepo: s(x.to_semester_repo), toPath: s(x.to_path), atDate, atTime, public: x.public === true, src };
  });
}

function readSlots(e: Raw): Slot[] {
  const loc = s(e.location);
  if (Array.isArray(e.event_datetime) && e.event_datetime.length) {
    return e.event_datetime.map((x: unknown) => {
      const o = typeof x === 'object' && x !== null ? obj(x) : { at: x };
      const [date, time] = splitWhen(o.at);
      return { date, time, location: s(o.location) || loc, details: s(o.details) };
    });
  }
  const [date, time] = splitWhen(e.event_datetime);
  return [{ date, time, location: loc, details: '' }];
}

/** A content entry's draft, as the file has it. */
export function readEntry(type: string, id: string, raw: unknown): EntryDraft {
  const e = obj(raw);
  return {
    kind: 'entry', type, id, session: int(e.session), number: int(e.number) === int(e.session) && SESSION_KINDS.includes(type) ? '' : int(e.number),
    title: s(e.title), details: s(e.details), slots: readSlots(e), files: readFiles(e.files), guide: s(e.reading_guide),
    show: e.show_on_site === undefined || e.show_on_site === null ? !hiddenKind(type) : e.show_on_site !== false,
    provisional: e.tbc === true, tbc: isTbc(e.event_datetime),
  };
}

/** The readings entry attached to `session` (the first in the file), or null. */
export function twinOf(doc: Raw, session: number | ''): [string, unknown] | null {
  if (session === '') return null;
  return Object.entries(obj(doc[pluralOf(READINGS_KIND)])).find(([, e]) => obj(e).session === session) ?? null;
}

function readAssignment(id: string, raw: unknown): AssignmentDraft {
  const e = obj(raw);
  const [handoutDate, handoutTime] = splitWhen(e.handout_datetime);
  const [dueDate, dueTime] = splitWhen(e.due_datetime);
  const [solutionDate, solutionTime] = splitWhen(e.solution_datetime);
  const marksRaw = typeof e.marks_return_datetime === 'object' && e.marks_return_datetime !== null ? obj(e.marks_return_datetime).event_datetime : e.marks_return_datetime;
  const [marksDate, marksTime] = splitWhen(marksRaw);
  const run: Raw = {};
  for (const [k, file] of RUN_FILE_KEYS) if (e[file] !== undefined && e[file] !== null) run[k] = e[file];
  return {
    kind: 'assignments', id, template: s(e.template), number: int(e.number), title: s(e.title), manual: !e.handout_datetime,
    handoutDate, handoutTime, dueDate, dueTime, solutionOn: !!e.solution_datetime, solutionDate, solutionTime,
    details: s(e.details), show: e.show_on_site !== false, tbc: e.tbc === true, datesTbc: isTbc(e.due_datetime),
    marksOn: !!marksRaw, marksDate, marksTime, group: e.type === 'group' || e.type === 'individual' ? e.type : '', submitVia: s(e.submit_via),
    run, repoName: s(e.repo_name),
  };
}

function readSemesterEntry(id: string, raw: unknown): SemesterEntryDraft {
  const e = obj(raw);
  const type = (SEMESTER_TYPES as readonly string[]).includes(s(e.type)) ? (s(e.type) as SemesterType) : 'break';
  return {
    kind: 'semesterEntry', id, type, title: s(e.title), from: splitWhen(type === 'break' ? e.from : e.event_datetime)[0], to: splitWhen(e.to)[0],
    details: s(e.details), show: e.show_on_site !== false, provisional: e.tbc === true,
    graceDays: typeof e.grace_days === 'number' ? e.grace_days : ARCHIVE_GRACE_DAYS,
  };
}

/** The `semester:` entry of `type` (the first), as [key, entry], or null. */
export function semesterEntryOf(doc: Raw, type: SemesterType): [string, Raw] | null {
  const hit = Object.entries(obj(doc[SEMESTER_BLOCK])).find(([, e]) => obj(e).type === type);
  return hit ? [hit[0], obj(hit[1])] : null;
}

/** The draft of the entry with sheet key `key`: "semester" is the dates sheet, anything else an entry's key. */
export function readDraft(doc: Raw, key: string): Draft | null {
  if (key === 'semester') {
    const day = (t: SemesterType) => splitWhen(semesterEntryOf(doc, t)?.[1].event_datetime)[0];
    return { kind: 'semester', start: day('start'), end: day('end'), tz: s(doc.timezone) };
  }
  const b = blockOf(doc, key);
  if (!b) return null;
  const raw = obj(doc[b])[key];
  if (b === ASSIGNMENTS_BLOCK) return readAssignment(key, raw);
  if (b === SEMESTER_BLOCK) return readSemesterEntry(key, raw);
  const type = kindOfBlock(b)!;
  const d = readEntry(type, key, raw);
  if (SESSION_KINDS.includes(type)) {
    const t = twinOf(doc, d.session);
    d.twin = t ? readEntry(READINGS_KIND, t[0], t[1]) : null;
  }
  return d;
}

// ------------------------------------------------------------------ new drafts

const blankSlot = (): Slot => ({ date: '', time: '', location: '', details: '' });

/** The next unit of `kind`: one more than the highest session its entries name. */
export function nextSession(doc: Raw, kind: string): number {
  return 1 + Math.max(0, ...Object.values(obj(doc[pluralOf(kind)])).map((e) => int(obj(e).session) || 0));
}

/** The next number of `kind` (an assignment's included): one more than the highest its entries carry (number, else session). */
export function nextNumber(doc: Raw, kind: string): number {
  const block = kind === 'assignment' ? ASSIGNMENTS_BLOCK : pluralOf(kind);
  return 1 + Math.max(0, ...Object.values(obj(doc[block])).map((e) => int(obj(e).number) || int(obj(e).session) || 0));
}

/** A new entry of `kind` from the Add menu, its unit or number prefilled. */
export function newDraft(kind: string, doc: Raw): EntryDraft | AssignmentDraft | SemesterEntryDraft {
  if (kind === 'assignment') {
    return {
      kind: 'assignments', id: '', template: '', number: nextNumber(doc, 'assignment'), title: '', manual: false, handoutDate: '', handoutTime: '14:00', dueDate: '', dueTime: '23:59',
      solutionOn: false, solutionDate: '', solutionTime: '', details: '', show: true, tbc: false, datesTbc: false, marksOn: false, marksDate: '', marksTime: '', group: '', submitVia: '', run: {}, repoName: '',
    };
  }
  if (kind === 'semester') return { kind: 'semesterEntry', id: '', type: 'break', title: '', from: '', to: '', details: '', show: true, provisional: false, graceDays: ARCHIVE_GRACE_DAYS };
  const session = SESSION_KINDS.includes(kind) ? nextSession(doc, kind) : '';
  const d: EntryDraft = {
    kind: 'entry', type: kind, id: '', session, number: '', title: '', details: '', slots: [blankSlot()], files: [], guide: '',
    show: !hiddenKind(kind), provisional: false, tbc: false,
  };
  // A lab is prefilled from the lecture of that unit when one exists: its date, time and place.
  if (kind !== 'lecture' && SESSION_KINDS.includes(kind)) {
    const lec = Object.values(obj(doc[pluralOf('lecture')])).find((e) => obj(e).session === session);
    if (lec) d.slots = [{ ...readSlots(obj(lec))[0], details: '' }];
  }
  if (SESSION_KINDS.includes(kind)) d.twin = null;
  return d;
}

/** A lecture's or lab's readings twin before anything is typed into it: its session, its date and time. */
export function emptyTwin(d: EntryDraft): EntryDraft {
  const first = d.slots[0] ?? blankSlot();
  return {
    kind: 'entry', type: READINGS_KIND, id: '', session: d.session, number: '', title: '', details: '', slots: [{ date: first.date, time: first.time, location: '', details: '' }],
    files: [], guide: '', show: true, provisional: false, tbc: false,
  };
}

/** Whether a new twin holds anything worth writing. */
const twinUsed = (t: EntryDraft) => !!(t.id || t.files.length || t.guide.trim() || t.title.trim());

// ------------------------------------------------------------------ keys

/** The word a kind's keys start with (`lecture`, `lab`, `exam`, `event`). */
export const keyPrefix = (kind: string) => (kind === EVENT_KIND ? 'event' : kind === 'assignment' ? 'assignment' : kebab(kind));

/** The number a draft's row shows: its own, else its session's (a unit's kinds). */
export function effectiveNumber(d: EntryDraft): number | '' {
  return d.number !== '' ? d.number : SESSION_KINDS.includes(d.type) ? d.session : '';
}

/** A slug key's stem from the title, without the kind's own words ("Midterm exam" -> `exam-midterm`). */
function slugKey(kind: string, title: string): string {
  const own = new Set([kind, keyPrefix(kind), pluralOf(kind), ...(KINDS.find((k) => k.key === kind)?.label.toLowerCase().split(/\s+/) ?? []), kind === READINGS_KIND ? 'reading' : '']);
  const words = kebab(title).split('-').filter((w) => w && !own.has(w));
  return [keyPrefix(kind), ...words].join('-');
}

/** `base`, or `base-2`, `base-3`... when another entry has it. */
function unique(taken: Set<string>, base: string): string {
  let id = base, n = 1;
  while (taken.has(id)) id = `${base}-${++n}`;
  return id;
}

/** The key a new draft is written under: `lecture-5`, `readings-5`, `exam-midterm`, `assignment-3`. */
export function newKey(d: EntryDraft | AssignmentDraft | SemesterEntryDraft, taken: Set<string>): string {
  if (d.kind === 'assignments') return `assignment-${d.number || 'N'}`;
  if (d.kind === 'semesterEntry') return unique(taken, d.type === 'break' ? kebab(d.title) || 'break' : d.type);
  const n = effectiveNumber(d);
  if (n !== '' && d.type !== READINGS_KIND) return `${keyPrefix(d.type)}-${n}`;
  if (d.type === READINGS_KIND && d.session !== '' && !d.title.trim()) return unique(taken, `readings-${d.session}`);
  return unique(taken, slugKey(d.type, d.title));
}

/**
 * The key a saved draft moves to when its number changed (decision 0038 amendment 2): only a key
 * in the console's own form (`lecture-3`, `readings-3`) follows; a key written by hand stays.
 * An assignment's key names its student repos, so it moves only before the hand-out (`handedOut`).
 */
export function renamedKey(d: EntryDraft | AssignmentDraft, base: EntryDraft | AssignmentDraft, handedOut = false): string {
  if (d.kind === 'assignments' && base.kind === 'assignments') {
    if (handedOut || !d.number || d.number === base.number || d.id !== `assignment-${base.number}`) return d.id;
    return `assignment-${d.number}`;
  }
  if (d.kind !== 'entry' || base.kind !== 'entry') return d.id;
  if (d.type === READINGS_KIND) {
    if (d.session !== '' && d.session !== base.session && d.id === `readings-${base.session}`) return `readings-${d.session}`;
    return d.id;
  }
  const was = effectiveNumber(base), now = effectiveNumber(d);
  if (was !== '' && now !== '' && was !== now && d.id === `${keyPrefix(d.type)}-${was}`) return `${keyPrefix(d.type)}-${now}`;
  return d.id;
}

// ------------------------------------------------------------------ writing

/** A value to write: `v`, unless it is the default and the file did not already spell it. */
function keep<T>(raw: unknown, v: T, dflt: T): T | undefined {
  if (v !== dflt) return v;
  return raw === dflt ? v : undefined;
}
const text = (v: string) => (v.trim() ? v : undefined);

/** The inline run settings: the cascade's name and the schedule entry's key. */
export const RUN_FILE_KEYS: [string, string][] = [
  ['late_window_days', 'late_window_days'], ['late_penalty_per_day', 'late_penalty_per_day'], ['team_formation', 'team_formation'],
  ['max_team_size', 'max_team_size'], ['visibility', 'assignment_repo_visibility'], ['marks_per_question', 'marks_per_question'], ['submit_url', 'submit_url'],
];

function fileValue(f: FileDraft, raw: unknown): Raw {
  const r = obj(raw);
  return {
    ...r, from_course_repo: f.repo, from_path: f.path,
    to_semester_repo: f.toRepo && (f.toRepo !== DEFAULT_DEST_REPO || r.to_semester_repo) ? f.toRepo : undefined,
    to_path: text(f.toPath), release_datetime: f.atDate ? whenOf(r.release_datetime, f.atDate, f.atTime) : undefined,
    public: f.public ? true : r.public === false ? false : undefined,
  };
}

function slotsValue(d: EntryDraft, raw: Raw): { event_datetime: unknown; location: unknown } {
  if (d.tbc) return { event_datetime: 'tbc', location: text(d.slots[0]?.location ?? '') };
  if (d.slots.length === 1) {
    const [x] = d.slots;
    const one = Array.isArray(raw.event_datetime) ? undefined : raw.event_datetime;
    return { event_datetime: whenOf(one, x.date, x.time), location: text(x.location) };
  }
  const was = Array.isArray(raw.event_datetime) ? raw.event_datetime : [];
  return {
    event_datetime: d.slots.map((x, i) => {
      const r = obj(was[i]);
      return { ...r, at: whenOf(r.at ?? (typeof was[i] === 'string' ? was[i] : undefined), x.date, x.time), location: text(x.location), details: text(x.details) };
    }),
    location: undefined,
  };
}

/** The `number:` to write: none where it is the default (the session), unless the file already spelt it. */
function numberValue(d: EntryDraft, raw: Raw, unit: boolean): number | undefined {
  const n = d.number === '' && unit ? d.session : d.number;
  if (n === '') return undefined;
  return unit && n === d.session && raw.number !== n ? undefined : n;
}

/** A content entry as it goes into the file, merged over what the file has (none: a new entry). */
export function entryValue(d: EntryDraft, rawEntry: unknown): Raw {
  const raw = obj(rawEntry);
  const rawFiles = Array.isArray(raw.files) ? raw.files : [];
  const unit = SESSION_KINDS.includes(d.type);
  const when = slotsValue(d, raw);
  return {
    ...raw,
    session: d.session === '' ? undefined : d.session,
    // A unit's number is written only where it differs from the session (0038 amendment rule 3).
    number: numberValue(d, raw, unit),
    title: text(d.title),
    event_datetime: when.event_datetime,
    location: when.location,
    details: text(d.details),
    reading_guide: d.type === READINGS_KIND ? text(d.guide) : raw.reading_guide,
    files: d.files.length ? d.files.map((f) => fileValue(f, f.src === undefined ? undefined : rawFiles[f.src])) : undefined,
    show_on_site: keep(raw.show_on_site, d.show, !hiddenKind(d.type)),
    tbc: d.tbc ? (raw.tbc === undefined ? undefined : raw.tbc) : keep(raw.tbc, d.provisional, false),
  };
}

/** An assignment entry as it goes into the file, its run settings inline (decision 0038 rule 9). */
export function assignmentValue(d: AssignmentDraft, rawEntry: unknown): Raw {
  const raw = obj(rawEntry);
  const run = d.run ?? {};
  const out: Raw = {
    ...raw,
    number: d.number === '' ? undefined : d.number,
    template: d.template,
    type: d.group || undefined,
    submit_via: d.submitVia || undefined,
    handout_datetime: d.manual || d.datesTbc ? undefined : whenOf(raw.handout_datetime, d.handoutDate, d.handoutTime),
    due_datetime: d.datesTbc ? 'tbc' : whenOf(raw.due_datetime, d.dueDate, d.dueTime),
    solution_datetime: d.solutionOn && !d.manual ? whenOf(raw.solution_datetime, d.solutionDate, d.solutionTime) : undefined,
    marks_return_datetime: d.marksOn === undefined ? raw.marks_return_datetime : marksValue(d, raw.marks_return_datetime),
    title: raw.title,
    details: text(d.details),
    show_on_site: keep(raw.show_on_site, d.show, true),
    tbc: keep(raw.tbc, d.tbc, false),
    repo_name: d.repoName === undefined ? raw.repo_name : text(d.repoName),
  };
  if (d.run !== undefined) for (const [k, file] of RUN_FILE_KEYS) out[file] = run[k] === '' || run[k] === null ? undefined : run[k];
  return out;
}

function marksValue(d: AssignmentDraft, raw: unknown): unknown {
  if (!d.marksOn) return undefined;
  if (typeof raw === 'object' && raw !== null) {
    const r = obj(raw);
    return { ...r, event_datetime: whenOf(r.event_datetime, d.marksDate ?? '', d.marksTime ?? '') };
  }
  return whenOf(raw, d.marksDate ?? '', d.marksTime ?? '');
}

export function semesterEntryValue(d: SemesterEntryDraft, rawEntry: unknown): Raw {
  const raw = obj(rawEntry);
  const isBreak = d.type === 'break';
  return {
    ...raw,
    type: d.type,
    title: text(d.title),
    event_datetime: isBreak ? undefined : d.from ? whenOf(raw.event_datetime, d.from, splitWhen(raw.event_datetime)[1]) : undefined,
    from: isBreak ? whenOf(raw.from, d.from, '') : undefined,
    to: isBreak ? whenOf(raw.to, d.to, '') : undefined,
    // Only the archive alternative chosen is written: a date, or the days after the semester ends (written once it differs from the default).
    grace_days: d.type === 'archive' && !d.from ? keep(raw.grace_days, d.graceDays === '' ? ARCHIVE_GRACE_DAYS : d.graceDays, ARCHIVE_GRACE_DAYS) : undefined,
    details: text(d.details),
    show_on_site: keep(raw.show_on_site, d.show, true),
    tbc: keep(raw.tbc, d.provisional, false),
  };
}

/** Delete the `files:` items a draft removed, from the last up, so each item left is written over its own text. */
function dropRemovedFiles(y: YamlText, path: (string | number)[], d: EntryDraft, rawEntry: unknown): void {
  const raw = obj(rawEntry).files;
  if (!Array.isArray(raw) || !d.files.length) return;
  const kept = d.files.map((f) => f.src).filter((i): i is number => i !== undefined);
  for (let i = raw.length - 1; i >= 0; i--) if (!kept.includes(i)) y.delete([...path, 'files', i]);
}

/** The block a draft is written in. */
export function blockFor(d: EntryDraft | AssignmentDraft | SemesterEntryDraft): string {
  return d.kind === 'assignments' ? ASSIGNMENTS_BLOCK : d.kind === 'semesterEntry' ? SEMESTER_BLOCK : pluralOf(d.type);
}

/** Write the semester's dates sheet: its start and end entries and the timezone. */
function writeDates(y: YamlText, d: SemesterDraft, doc: Raw): void {
  y.assign(['timezone'], text(d.tz));
  for (const [type, day] of [['start', d.start], ['end', d.end]] as const) {
    const hit = semesterEntryOf(doc, type);
    if (!day && !hit) continue;
    const key = hit?.[0] ?? type;
    if (!day) y.delete([SEMESTER_BLOCK, key]);
    else y.assign([SEMESTER_BLOCK, key], { ...(hit?.[1] ?? { type }), event_datetime: whenOf(hit?.[1].event_datetime, day, splitWhen(hit?.[1].event_datetime)[1]) });
  }
}

/** Write one draft in place under its own key (no rename, no sort): the assignment Overview's timings and the dates sheet. */
export function writeDraft(y: YamlText, d: Draft, doc: Raw): void {
  if (d.kind === 'semester') return writeDates(y, d, doc);
  const block = blockFor(d);
  const raw = obj(doc[block])[d.id];
  if (d.kind === 'entry') {
    dropRemovedFiles(y, [block, d.id], d, raw);
    y.assign([block, d.id], entryValue(d, raw));
  } else if (d.kind === 'assignments') y.assign([block, d.id], assignmentValue(d, raw));
  else y.assign([block, d.id], semesterEntryValue(d, raw));
}

export interface SaveContext {
  /** Whether the assignment with this key has been handed out (its key then stays). */
  handedOut?: (key: string) => boolean;
}

/** One entry to write: its draft, the key it was read under ('' for a new one) and the draft it was read as. */
export interface Change {
  from: string;
  d: EntryDraft | AssignmentDraft | SemesterEntryDraft;
  base: EntryDraft | AssignmentDraft | SemesterEntryDraft | null;
}

/** Every entry a sheet's drafts write: each draft, and a lecture's or lab's readings twin beside it. */
export function changesOf(drafts: Draft[], bases: (Draft | null)[]): Change[] {
  const out: Change[] = [];
  drafts.forEach((d, i) => {
    if (d.kind === 'semester') return;
    const base = bases[i] as Change['base'];
    out.push({ from: d.id, d: d.kind === 'entry' ? { ...d, twin: undefined } : d, base: base?.kind === 'entry' ? { ...base, twin: undefined } : base });
    if (d.kind === 'entry' && d.twin && (d.twin.id || twinUsed(d.twin))) {
      const bt = base?.kind === 'entry' ? base.twin ?? null : null;
      // The twin follows its session's unit.
      const twin = { ...d.twin, session: d.session };
      if (!deepSame(twin, bt)) out.push({ from: twin.id, d: twin, base: bt });
    }
  });
  return out;
}

const deepSame = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Sort keys of a numbered block by number (then by key), each entry's number as its row shows it. */
function numberOrder(block: Raw): string[] {
  const n = (e: unknown) => int(obj(e).number) || int(obj(e).session) || Number.MAX_SAFE_INTEGER;
  return Object.keys(block).sort((a, b) => n(block[a]) - n(block[b]));
}

/**
 * schedule.yml after a save: `removed` keys deleted, then every change written under its key (a
 * new one generated; a renumbered one renamed, decision 0038 amendment 2), and each numbered
 * block that gained or renamed an entry re-sorted by number. Returns the text and the key each
 * change was written under.
 */
export function applyChanges(textIn: string, doc: Raw, changes: Change[], removed: string[], ctx: SaveContext = {}): { text: string; keys: string[] } {
  const y = new YamlText(textIn);
  for (const k of removed) {
    const b = blockOf(doc, k);
    if (b) y.delete([b, k]);
  }
  const taken = new Set([...takenKeys(doc)].filter((k) => !removed.includes(k)));
  const keys = changes.map((c) => {
    if (!c.from) {
      const k = newKey(c.d, taken);
      taken.add(k);
      return k;
    }
    if (!c.base || c.d.kind === 'semesterEntry' || c.base.kind === 'semesterEntry') return c.from;
    return renamedKey(c.d as EntryDraft | AssignmentDraft, c.base as EntryDraft | AssignmentDraft, ctx.handedOut?.(c.from) ?? false);
  });
  // Renames go through a temporary key, so lecture-3 -> lecture-4 and lecture-4 -> lecture-5 in one save never meet.
  const renamed = changes.map((c, i) => (c.from && keys[i] !== c.from ? i : -1)).filter((i) => i >= 0);
  renamed.forEach((i) => y.renameKey([blockFor(changes[i].d), changes[i].from], `__renaming_${i}`));
  renamed.forEach((i) => y.renameKey([blockFor(changes[i].d), `__renaming_${i}`], keys[i]));
  const sortBlocks = new Set<string>();
  changes.forEach((c, i) => {
    const block = blockFor(c.d);
    const raw = c.from ? obj(doc[block])[c.from] : undefined;
    const d = { ...c.d, id: keys[i] };
    if (d.kind === 'entry') {
      dropRemovedFiles(y, [block, keys[i]], d, raw);
      y.assign([block, keys[i]], entryValue(d, raw));
    } else if (d.kind === 'assignments') y.assign([block, keys[i]], assignmentValue(d, raw));
    else y.assign([block, keys[i]], semesterEntryValue(d, raw));
    const numbered = block === ASSIGNMENTS_BLOCK || KINDS.some((k) => k.plural === block && k.numbered && !k.system);
    if (numbered && (!c.from || keys[i] !== c.from || (c.base && c.d.kind !== 'semesterEntry' && numberChanged(c.d, c.base)))) sortBlocks.add(block);
  });
  for (const b of sortBlocks) y.sortMap([b], numberOrder(obj(obj(y.toJS())[b])));
  // A block whose last entry went stays a mapping (`labs: {}`), as the schema wants.
  const after = obj(y.toJS());
  for (const b of ALL_BLOCKS()) if (b in after && after[b] === null && b in doc) y.set([b], {});
  return { text: y.text, keys };
}

function numberChanged(d: Change['d'], base: NonNullable<Change['base']>): boolean {
  if (d.kind === 'entry' && base.kind === 'entry') return effectiveNumber(d) !== effectiveNumber(base);
  if (d.kind === 'assignments' && base.kind === 'assignments') return d.number !== base.number;
  return false;
}

// ------------------------------------------------------------------ validation

/** A sheet moment as the engine reads it, sortable: a bare day at its start, or (a due date) its end. */
function moment(when: string, endOfDay = false): string {
  if (when.length === 10) return `${when}T${endOfDay ? '23:59:59' : '00:00:00'}`;
  return when.length === 16 ? `${when}:00` : when;
}

const wholeOk = (n: number | '' | undefined) => n === '' || n === undefined || (Number.isInteger(n) && n >= 1 && n <= 999);

export interface ErrorContext {
  doc?: Raw;
  /** Keys being removed in this save: they clash with nothing. */
  removed?: string[];
}

/** Another entry of the same block with this number (its row word), not `self`. */
function numberTaken(doc: Raw, d: EntryDraft, removed: string[]): string | null {
  const n = effectiveNumber(d);
  if (n === '') return null;
  const unit = SESSION_KINDS.includes(d.type);
  for (const [k, e] of Object.entries(obj(doc[pluralOf(d.type)]))) {
    if (k === d.id || removed.includes(k)) continue;
    const other = int(obj(e).number) || (unit ? int(obj(e).session) : '');
    if (other === n) return k;
  }
  return null;
}

function entryErrors(d: EntryDraft, ctx: ErrorContext, prefix = ''): Record<string, string> {
  const e: Record<string, string> = {};
  const label = KINDS.find((k) => k.key === d.type)?.label ?? d.type;
  if (SESSION_KINDS.includes(d.type) && d.session === '') e[`${prefix}session`] = 'Give the session.';
  if (!wholeOk(d.session)) e[`${prefix}session`] = 'A whole number from 1 to 999.';
  if (!wholeOk(d.number)) e[`${prefix}number`] = 'A whole number from 1 to 999.';
  else if (ctx.doc && !e[`${prefix}session`]) {
    const clash = numberTaken(ctx.doc, d, ctx.removed ?? []);
    const n = effectiveNumber(d);
    if (clash) e[`${prefix}number`] = `${label} ${n} is already in the schedule (${clash}).`;
  }
  if (!d.tbc) {
    d.slots.forEach((x, i) => {
      const at = d.slots.length > 1 ? `${prefix}slot${i}` : `${prefix}date`;
      if (!x.date) e[at] = d.slots.length > 1 ? `Give slot ${i + 1} a date.` : 'Give the date, or turn on Date TBC.';
      else if (!/^\d{4}-\d{2}-\d{2}$/.test(x.date)) e[at] = 'A date as yyyy-mm-dd.';
    });
  }
  if (d.type === EVENT_KIND && !d.title.trim()) e[`${prefix}title`] = 'A title is needed; the student site shows only this.';
  d.files.forEach((f, i) => {
    if (!f.repo || !f.path.trim()) e[`${prefix}file${i}`] = 'Each file needs a repo and a path.';
    else if (f.atTime && !f.atDate) e[`${prefix}file${i}`] = `${f.path}: give the release date too, or clear the time.`;
  });
  return e;
}

/**
 * What is wrong with a draft, by field; empty when it can be saved. The Save button names the
 * first. `cutoff` is an assignment's late cutoff: a solution shown before it is refused, as the
 * engine does.
 */
export function draftErrors(d: Draft, ctx: ErrorContext = {}, cutoff?: string | null): Record<string, string> {
  if (d.kind === 'entry') {
    const e = entryErrors(d, ctx);
    if (d.twin && (d.twin.id || twinUsed(d.twin))) {
      const twinDoc = ctx.doc ?? {};
      Object.assign(e, entryErrors({ ...d.twin, session: d.session }, { ...ctx, doc: twinDoc }, 'twin.'));
    }
    return e;
  }
  const e: Record<string, string> = {};
  if (d.kind === 'assignments') {
    if (!d.template) e.template = 'Choose the course template.';
    if (d.number === '' || d.number === undefined) e.number = 'A number is needed.';
    else if (!wholeOk(d.number)) e.number = 'A whole number from 1 to 999.';
    else if (!d.id && ctx.doc && takenKeys(ctx.doc).has(`assignment-${d.number}`)) e.number = `assignment-${d.number} is already in this schedule.`;
    if (!d.datesTbc) {
      if (!d.dueDate) e.due = 'Give the due date, or turn on Dates TBC.';
      if (!d.manual && !d.handoutDate) e.handout = 'Give the hand-out date, or choose By hand, later.';
      const h = joinWhen(d.handoutDate, d.handoutTime) ?? '';
      const due = joinWhen(d.dueDate, d.dueTime) ?? '';
      if (h && due && !d.manual && moment(due, true) < moment(h)) e.due = 'Due must come after the hand-out.';
      const sol = joinWhen(d.solutionDate, d.solutionTime);
      if (d.solutionOn && !d.manual && !sol) e.solution = 'Give the date the solution is shown.';
      if (d.solutionOn && sol && h && !d.manual && sol <= h) e.solution = 'Must be after the hand-out.';
      else if (d.solutionOn && sol && cutoff && moment(sol) < moment(cutoff, true)) {
        const shown = (m: string) => m.slice(0, 16).replace('T', ' ');
        e.solution = solutionBeforeCutoff(d.id || `assignment-${d.number}`, shown(moment(sol)), shown(moment(cutoff, true)));
      }
      if (d.marksOn && !d.marksDate) e.marks = 'Give the date marks are returned, or leave it to be done by hand.';
    }
    if (d.repoName && !/^[A-Za-z0-9._-]+$/.test(d.repoName)) e.repoName = 'Not a repo name (letters, digits, `.`, `_`, `-`).';
    return e;
  }
  if (d.kind === 'semester') {
    if (d.start && d.end && d.end <= d.start) e.end = 'The semester must end after it starts.';
    return e;
  }
  if (d.type === 'break') {
    if (!d.from) e.from = 'Give the first day of the break.';
    if (!d.to) e.to = 'Give the last day of the break.';
    else if (d.from && d.to < d.from) e.to = 'The break must end on or after its first day.';
  } else if (d.type !== 'archive' && !d.from) e.from = 'Give the date.';
  if (d.type === 'archive' && !d.from && d.graceDays !== '' && !(Number.isInteger(d.graceDays) && d.graceDays >= 0)) e.graceDays = 'A whole number of days, 0 or more.';
  if (d.type !== 'break' && ctx.doc) {
    const hit = semesterEntryOf(ctx.doc, d.type);
    if (hit && hit[0] !== d.id && !(ctx.removed ?? []).includes(hit[0])) e.type = `The semester already has a${d.type === 'archive' ? 'n' : ''} ${d.type} entry (${hit[0]}).`;
  }
  return e;
}
