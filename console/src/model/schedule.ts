// The semester's schedule.yml, read for what status.json does not carry: the Details text
// students see, the events, the semester dates and the archive entry. States come from status.

import { parse } from 'yaml';
import { obj } from '../edit/yamlText';
import { ASSIGNMENT_WORD, RELEASE_WORD, addDays, assignmentIdent, dayKey, daysBetween, releaseIdent, sortKey, zoned } from './format';
import type { Release, Status } from './types';

export interface SchedEntry {
  title: string;
  details: string;
  tbc: boolean;
  show: boolean;
  when: string | null;
  kind: string;
}

export interface Schedule {
  timezone: string | null;
  start: string | null;
  end: string | null;
  releases: Record<string, SchedEntry>;
  assignments: Record<string, SchedEntry>;
  events: (SchedEntry & { id: string })[];
  archive: SchedEntry | null;
}

function s(v: unknown): string {
  if (v instanceof Date) return v.toISOString().slice(0, 10);
  return typeof v === 'string' ? v : v == null ? '' : String(v);
}

function entry(raw: unknown, dflt: Partial<SchedEntry> = {}): SchedEntry {
  const e = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const when = s(e.event_datetime ?? e.due_datetime);
  return {
    title: s(e.title) || dflt.title || '',
    details: s(e.details),
    tbc: e.tbc === true || when.toLowerCase() === 'tbc',
    show: e.show_on_site !== false,
    when: when && when.toLowerCase() !== 'tbc' ? when : null,
    kind: s(e.kind) || dflt.kind || '',
  };
}

function pretty(id: string): string {
  const t = id.replace(/[-_]+/g, ' ').trim();
  return t.charAt(0).toUpperCase() + t.slice(1);
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
  const releases: Record<string, SchedEntry> = {};
  for (const [k, v] of Object.entries(obj(doc.releases))) releases[k] = entry(v);
  const assignments: Record<string, SchedEntry> = {};
  for (const [k, v] of Object.entries(obj(doc.assignments))) assignments[k] = entry(v);
  const events = Object.entries(obj(doc.events)).map(([k, v]) => ({ id: k, ...entry(v, { title: pretty(k), kind: 'special_event' }) }));
  return {
    timezone: s(doc.timezone) || null,
    start: s(doc.semester_start) || null,
    end: s(doc.semester_end) || null,
    releases,
    assignments,
    events,
    archive: doc.archive ? entry(doc.archive, { title: 'Semester archived' }) : null,
  };
}

export type Block = 'releases' | 'assignments' | 'events';

export interface Row {
  entry: string; // the token after `#schedule-`
  block: Block;
  type: string; // lecture | lab | readings | handout | due | exam | special_event | term | archive
  when: string | null;
  ident: string; // "Session 3"
  name: string; // "Regularisation"
  state: string; // in vocabulary words
  details: string; // markdown, as the site shows it
  tbc: boolean;
  show: boolean;
  fault: boolean;
}

/** Every row of the semester, in date order, the way the schedule screen and the semester strip read it. */
export function scheduleRows(status: Status, sched: Schedule | null, now: number, tz: string): Row[] {
  const rows: Row[] = [];
  const releases: Release[] = status.releases ?? [];
  const nowKey = sortKey(new Date(now).toISOString(), tz);
  const past = (w: string | null) => (w ? sortKey(w, tz) < nowKey : false);
  for (const r of releases) {
    const e = sched?.releases[r.id];
    rows.push({
      entry: r.id, block: 'releases', type: r.kind ?? 'release', when: r.when, ident: releaseIdent(r, releases), name: r.title,
      state: RELEASE_WORD[r.state] ?? r.state, details: e?.details ?? '', tbc: r.tbc, show: r.show_on_site, fault: r.state === 'will_be_skipped',
    });
  }
  for (const a of status.assignments ?? []) {
    const e = sched?.assignments[a.slug];
    const base = { entry: a.slug, block: 'assignments' as Block, ident: assignmentIdent(a.slug), name: a.title, state: ASSIGNMENT_WORD[a.state] ?? a.state, details: e?.details ?? '', tbc: e?.tbc ?? false, show: e?.show ?? true, fault: a.problem };
    if (a.handout) rows.push({ ...base, type: 'handout', when: a.handout });
    if (a.due) rows.push({ ...base, type: 'due', when: a.due });
  }
  if (sched) {
    for (const ev of sched.events)
      rows.push({
        entry: ev.id, block: 'events', type: ev.kind === 'exam' ? 'exam' : 'special_event', when: ev.when, ident: ev.kind === 'exam' ? 'Exam' : 'Event',
        name: ev.title, state: ev.when ? (past(ev.when) ? 'past' : 'upcoming') : 'upcoming', details: ev.details, tbc: ev.tbc, show: ev.show, fault: false,
      });
    if (sched.start) rows.push({ entry: 'semester', block: 'events', type: 'term', when: sched.start, ident: 'Semester', name: 'Starts', state: past(sched.start) ? 'past' : 'upcoming', details: '', tbc: false, show: true, fault: false });
    if (sched.end) rows.push({ entry: 'semester', block: 'events', type: 'term', when: sched.end, ident: 'Semester', name: 'Ends', state: past(sched.end) ? 'past' : 'upcoming', details: '', tbc: false, show: true, fault: false });
  }
  const archive = sched?.archive?.when ?? status.semester?.archive_date ?? null;
  if (archive) rows.push({ entry: 'archive', block: 'events', type: 'archive', when: archive, ident: 'Archive', name: sched?.archive?.title || 'Semester archived', state: 'scheduled', details: (sched?.archive?.details ?? '').replace('{date}', archive), tbc: false, show: sched?.archive?.show ?? true, fault: false });
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

/**
 * Split `items` for a week selection: `dated` holds those in the selected weeks (every dated
 * one when `selected` is empty, which is All weeks), `undated` those no date pins.
 */
export function inWeeks<T>(items: T[], when: (t: T) => string | null | undefined, selected: WeekKey[], term: Term, tz: string): { dated: T[]; undated: T[] } {
  const dated: T[] = [], undated: T[] = [];
  for (const it of items) {
    const w = when(it);
    if (!w) undated.push(it);
    else if (!selected.length || selected.includes(weekOf(w, term, tz))) dated.push(it);
  }
  return { dated, undated };
}
