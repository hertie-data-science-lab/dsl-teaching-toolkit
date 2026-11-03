// The semester's schedule.yml, read for what status.json does not carry: the Details text
// students see, the semester's own dates (start, end, breaks, archive). States come from status.
// One block per kind (decision 0038): the block is the kind, the key names the entry.

import { parse } from 'yaml';
import { obj } from '../edit/yamlText';
import { ASSIGNMENT_WORD, RELEASE_WORD, addDays, assignmentIdent, dayKey, daysBetween, kindIdent, releaseIdent, sortKey, zoned } from './format';
import { pluralOf } from './policy';
import { ASSIGNMENTS_BLOCK, CONTENT_BLOCKS, EVENT_KIND, SEMESTER_BLOCK } from './scheduleEdit';
import type { Release, Status } from './types';

export interface SchedEntry {
  title: string;
  details: string;
  tbc: boolean;
  show: boolean;
  when: string | null;
}

/** A break in the semester: a grey band, no files. */
export interface SchedBreak extends SchedEntry {
  id: string;
  to: string | null;
}

export interface Schedule {
  timezone: string | null;
  start: string | null;
  end: string | null;
  /** Every content entry by its key, whatever its block. */
  entries: Record<string, SchedEntry>;
  assignments: Record<string, SchedEntry>;
  /** Exams and events with no `files:` key: display rows the engine keeps out of status's releases (`schedule.Event`). */
  events: (SchedEntry & { id: string; kind: string; number: number | null })[];
  breaks: SchedBreak[];
  /** The archive entry, with its key. */
  archive: (SchedEntry & { id: string }) | null;
}

function s(v: unknown): string {
  if (v instanceof Date) return v.toISOString().slice(0, 10);
  return typeof v === 'string' ? v : v == null ? '' : String(v);
}

function entry(raw: unknown, dflt: Partial<SchedEntry> = {}): SchedEntry {
  const e = obj(raw);
  const first = Array.isArray(e.event_datetime) ? e.event_datetime[0] : e.event_datetime;
  const when = s(e.from ?? (first && typeof first === 'object' ? obj(first).at : first) ?? e.due_datetime);
  return {
    title: s(e.title) || dflt.title || '',
    details: s(e.details),
    tbc: e.tbc === true || when.toLowerCase() === 'tbc',
    show: e.show_on_site !== false,
    when: when && when.toLowerCase() !== 'tbc' ? when : null,
  };
}

export function parseSchedule(text: string | null | undefined): Schedule | null {
  if (!text) return null;
  let d: unknown;
  try {
    d = parse(text);
  } catch {
    return null;
  }
  if (!d || typeof d !== 'object') return null;
  const doc = d as Record<string, unknown>;
  const entries: Record<string, SchedEntry> = {};
  for (const b of CONTENT_BLOCKS) for (const [k, v] of Object.entries(obj(doc[b]))) entries[k] = entry(v);
  const assignments: Record<string, SchedEntry> = {};
  for (const [k, v] of Object.entries(obj(doc[ASSIGNMENTS_BLOCK]))) assignments[k] = entry(v);
  const events = (['exam', EVENT_KIND] as const).flatMap((kind) =>
    Object.entries(obj(doc[pluralOf(kind)])).filter(([, v]) => !('files' in obj(v))).map(([k, v]) => {
      const n = obj(v).number;
      return { id: k, kind: kind === 'exam' ? 'exam' : 'special_event', number: Number.isInteger(n) && (n as number) > 0 ? (n as number) : null, ...entry(v) };
    }),
  );
  const sem = Object.entries(obj(doc[SEMESTER_BLOCK]));
  const day = (type: string) => entry(sem.find(([, e]) => obj(e).type === type)?.[1]).when;
  const arch = sem.find(([, e]) => obj(e).type === 'archive');
  return {
    timezone: s(doc.timezone) || null,
    start: day('start'),
    end: day('end'),
    entries,
    assignments,
    events,
    breaks: sem.filter(([, e]) => obj(e).type === 'break').map(([k, e]) => ({ id: k, ...entry(e, { title: 'Break' }), to: s(obj(e).to) || null })),
    archive: arch ? { id: arch[0], ...entry(arch[1], { title: 'Semester archived' }) } : null,
  };
}

export type Block = 'releases' | 'assignments' | 'events';

export interface Row {
  entry: string; // the token after `#schedule-`
  block: Block;
  type: string; // lecture | lab | readings | handout | due | exam | other | semester | archive
  when: string | null;
  ident: string; // "Session 3"
  name: string; // "Regularisation"
  state: string; // in vocabulary words
  details: string; // markdown, as the site shows it
  tbc: boolean;
  show: boolean;
}

/** Whether `status` names `entry` as needing a number (decision 0020: a `number:*` problem). */
export function needsANumber(status: Status, entry: string): boolean {
  return (status.problems ?? []).some((x) => x.id.startsWith('number:') && x.fix?.entry === entry);
}

/** Exams and events filter as Events; every other content kind as Releases. */
const blockOfKind = (kind: string | undefined): Block => (kind === 'exam' || kind === EVENT_KIND ? 'events' : 'releases');

/** Every row of the semester, in date order, the way the schedule screen and the semester strip read it. */
export function scheduleRows(status: Status, sched: Schedule | null, now: number, tz: string): Row[] {
  const rows: Row[] = [];
  const releases: Release[] = status.releases ?? [];
  const nowKey = sortKey(new Date(now).toISOString(), tz);
  const past = (w: string | null) => (w ? sortKey(w, tz) < nowKey : false);
  for (const r of releases) {
    const e = sched?.entries[r.id];
    rows.push({
      entry: r.id, block: blockOfKind(r.kind), type: r.kind ?? 'release', when: r.when, ident: releaseIdent(r), name: r.title,
      state: RELEASE_WORD[r.state] ?? r.state, details: e?.details ?? '', tbc: r.tbc, show: r.show_on_site,
    });
  }
  for (const a of status.assignments ?? []) {
    const e = sched?.assignments[a.slug];
    const base = { entry: a.slug, block: 'assignments' as Block, ident: assignmentIdent(a.slug, 'Assignment', a.number), name: a.title, state: ASSIGNMENT_WORD[a.state] ?? a.state, details: e?.details ?? '', tbc: e?.tbc ?? false, show: e?.show ?? true };
    if (a.handout) rows.push({ ...base, type: 'handout', when: a.handout });
    if (a.due) rows.push({ ...base, type: 'due', when: a.due });
  }
  if (sched) {
    for (const ev of sched.events) {
      rows.push({
        entry: ev.id, block: 'events', type: ev.kind, when: ev.when, ident: kindIdent(ev.kind === 'exam' ? 'exam' : EVENT_KIND, ev.number),
        name: ev.title, state: ev.when ? (past(ev.when) ? 'past' : 'upcoming') : 'upcoming', details: ev.details, tbc: ev.tbc, show: ev.show,
      });
    }
    if (sched.start) rows.push({ entry: 'semester', block: 'events', type: 'semester', when: sched.start, ident: 'Semester', name: 'Starts', state: past(sched.start) ? 'past' : 'upcoming', details: '', tbc: false, show: true });
    if (sched.end) rows.push({ entry: 'semester', block: 'events', type: 'semester', when: sched.end, ident: 'Semester', name: 'Ends', state: past(sched.end) ? 'past' : 'upcoming', details: '', tbc: false, show: true });
    for (const b of sched.breaks) rows.push({ entry: b.id, block: 'events', type: 'semester', when: b.when, ident: 'Break', name: b.title, state: past(b.when) ? 'past' : 'upcoming', details: b.details, tbc: b.tbc, show: b.show });
  }
  const archive = sched?.archive?.when ?? status.semester?.archive_date ?? null;
  if (archive) rows.push({ entry: sched?.archive?.id ?? 'archive', block: 'events', type: 'archive', when: archive, ident: 'Archive', name: sched?.archive?.title || 'Semester archived', state: 'scheduled', details: (sched?.archive?.details ?? '').replace('{date}', archive), tbc: false, show: sched?.archive?.show ?? true });
  // TBC with no date sorts at the end of the semester, as the site does.
  rows.sort((a, b) => (a.when ? sortKey(a.when, tz) : '9999') .localeCompare(b.when ? sortKey(b.when, tz) : '9999'));
  return rows;
}

// ------------------------------------------------------------------ term weeks (the Dashboard)

/** A term week, 1 to the last, or the bucket of what falls before or after the term. */
export type WeekKey = number | 'before' | 'after';

export interface Term {
  start: string; // yyyy-mm-dd, the first day of week 1
  end: string;
  weeks: number;
}

/**
 * The term the week strip counts in: status.json's dates, else schedule.yml's, else week 1
 * counted back from the engine's `week` to the Monday of `today`.
 */
export function termOf(status: Status, sched: Schedule | null, today: string): Term {
  const weeks = status.semester?.weeks ?? 15;
  let start = status.semester?.start ?? sched?.start ?? null;
  if (!start) {
    const monday = addDays(today, -((zoned(today).dow + 6) % 7));
    start = addDays(monday, -7 * ((status.semester?.week ?? 1) - 1));
  }
  const end = status.semester?.end ?? sched?.end ?? addDays(start, 7 * weeks - 3);
  return { start, end, weeks };
}

/** The term week `iso` falls in, read in `tz`; outside the term, its bucket. */
export function weekOf(iso: string, term: Term, tz: string): WeekKey {
  const n = Math.floor(daysBetween(term.start, dayKey(iso, tz)) / 7) + 1;
  return n < 1 ? 'before' : n > term.weeks ? 'after' : n;
}

/** A semester week's first day (yyyy-mm-dd): week 1 starts on the semester's start. */
export const weekStart = (term: Term, w: number): string => addDays(term.start, (w - 1) * 7);

/** A week key's place in time: before the semester, its weeks, after it, then no date yet. */
export const weekRank = (k: WeekKey | 'none', term: Term): number => (k === 'before' ? 0 : k === 'after' ? term.weeks + 1 : k === 'none' ? term.weeks + 2 : k);

/** A group of items under one heading: a semester week, the before or after bucket, or no date yet. */
export interface WeekGroup<T> {
  key: WeekKey | 'none';
  label: string;
  /** A semester week's first day, for its heading; none for the buckets. */
  from?: string;
  rows: T[];
}

/**
 * Items grouped by semester week, in order, for the instructor's and the student's schedules
 * alike (`when` gives an item's time; one with none is No date yet). `all` gives every week of
 * the semester, empty ones too, and each bucket that has items; otherwise only the weeks in
 * `keys` that have items.
 */
export function weekGroups<T>(items: T[], when: (t: T) => string | null | undefined, term: Term, tz: string, keys: WeekKey[] | 'all'): WeekGroup<T>[] {
  const by = new Map<WeekKey | 'none', T[]>();
  for (const it of items) {
    const w = when(it);
    const k = w ? weekOf(w, term, tz) : 'none';
    by.set(k, [...(by.get(k) ?? []), it]);
  }
  const label = (k: WeekKey | 'none') => (k === 'before' ? 'Before the semester' : k === 'after' ? 'After the semester' : k === 'none' ? 'No date yet' : `Week ${k}`);
  const order: (WeekKey | 'none')[] = keys === 'all'
    ? ['before', ...Array.from({ length: term.weeks }, (_, i) => i + 1), 'after', 'none']
    : [...keys].sort((a, b) => weekRank(a, term) - weekRank(b, term));
  return order
    .filter((k) => (keys === 'all' && typeof k === 'number') || by.has(k))
    .map((k) => ({ key: k, label: label(k), ...(typeof k === 'number' ? { from: weekStart(term, k) } : {}), rows: by.get(k) ?? [] }));
}
