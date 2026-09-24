// The cohort's schedule.yml, read for what status.json does not carry: the Details text
// students see, the events, the term dates and the archive entry. States come from status.

import { parse } from 'yaml';
import { obj } from '../edit/yamlText';
import { ASSIGNMENT_WORD, RELEASE_WORD, assignmentIdent, releaseIdent, sortKey } from './format';
import type { Release, Status } from './types';

export interface SchedEntry {
  title: string;
  details: string;
  tbc: boolean;
  show: boolean;
  when: string | null;
  type: string;
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
    type: s(e.type) || dflt.type || '',
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
  const events = Object.entries(obj(doc.events)).map(([k, v]) => ({ id: k, ...entry(v, { title: pretty(k), type: 'special_event' }) }));
  return {
    timezone: s(doc.timezone) || null,
    start: s(doc.semester_start) || null,
    end: s(doc.semester_end) || null,
    releases,
    assignments,
    events,
    archive: doc.archive ? entry(doc.archive, { title: 'Cohort archived' }) : null,
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

/** Every row of the term, in date order, the way the schedule screen and the term strip read it. */
export function scheduleRows(status: Status, sched: Schedule | null, now: number, tz: string): Row[] {
  const rows: Row[] = [];
  const releases: Release[] = status.releases ?? [];
  const nowKey = sortKey(new Date(now).toISOString(), tz);
  const past = (w: string | null) => (w ? sortKey(w, tz) < nowKey : false);
  for (const r of releases) {
    const e = sched?.releases[r.id];
    rows.push({
      entry: r.id, block: 'releases', type: r.type ?? 'lecture', when: r.when, ident: releaseIdent(r, releases), name: r.title,
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
        entry: ev.id, block: 'events', type: ev.type === 'exam' ? 'exam' : 'special_event', when: ev.when, ident: ev.type === 'exam' ? 'Exam' : 'Event',
        name: ev.title, state: ev.when ? (past(ev.when) ? 'past' : 'upcoming') : 'upcoming', details: ev.details, tbc: ev.tbc, show: ev.show, fault: false,
      });
    if (sched.start) rows.push({ entry: 'term', block: 'events', type: 'term', when: sched.start, ident: 'Term', name: 'Starts', state: past(sched.start) ? 'past' : 'upcoming', details: '', tbc: false, show: true, fault: false });
    if (sched.end) rows.push({ entry: 'term', block: 'events', type: 'term', when: sched.end, ident: 'Term', name: 'Ends', state: past(sched.end) ? 'past' : 'upcoming', details: '', tbc: false, show: true, fault: false });
  }
  const archive = sched?.archive?.when ?? status.cohort?.archive_date ?? null;
  if (archive) rows.push({ entry: 'archive', block: 'events', type: 'archive', when: archive, ident: 'Archive', name: sched?.archive?.title || 'Cohort archived', state: 'scheduled', details: (sched?.archive?.details ?? '').replace('{date}', archive), tbc: false, show: sched?.archive?.show ?? true, fault: false });
  // TBC with no date sorts at the end of term, as the site does.
  rows.sort((a, b) => (a.when ? sortKey(a.when, tz) : '9999') .localeCompare(b.when ? sortKey(b.when, tz) : '9999'));
  return rows;
}
