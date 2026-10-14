// S4 Semester overview (Dashboard, decision 0015).

import { useMemo, useState } from 'preact/hooks';
import {
  NOT_READY_YET, TYPE_CLASS, TYPE_LABEL, addDays, ago, daysBetween, fmtDate, fmtDay, fmtShort, fmtTime, fmtWhen, markWord, plural, releaseIdent,
  gapLabel,
} from '../model/format';
import { needsANumber, parseSchedule, scheduleRows, weekGroups, weekOf, weekStart, type Row, type Schedule, type Term, type WeekGroup, type WeekKey, termOf } from '../model/schedule';
import type { Assignment, Problem, Status } from '../model/types';
import { submissionsOf } from '../model/assignment';
import { checkAccess, checkNow, releaseEarly, type ReleaseRef } from '../ops/defs';
import { OpButtons, OpOpen } from '../ops/Panel';
import type { Release } from '../model/types';
import { OpsList, ProblemCards, ReleaseMarks, fixHref, ghUrl } from '../ui/bits';
import { Verdict, readinessTabs, type Link, type SetupItem } from '../ui/SetupPanel';
import { DashboardTabs, showTab } from '../ui/DashboardTabs';
import { useSetAside } from './SetAside';
import { horizonDays, problemFromDay, releaseMarks, standing, stepItems, suggestionsCount, tier, todoAside, verdictOf, verdictTab, type ReleaseMark, type Tiered } from '../model/readiness';
import { Hint } from '../ui/Hint';
import { MoreMenu, WithStatus, cohortScope, todayOf, tzOf, useOperations, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';
import { CONFIG_REPO } from '../model/names';

export function readSchedule(p: CohortProps): Schedule | null {
  const f = p.files.file(p.cohort.org, CONFIG_REPO, 'schedule.yml');
  return f.kind === 'ready' ? parseSchedule(f.text) : null;
}

export function asgSummary(a: Assignment, tz: string, year: number): string {
  switch (a.state) {
    case 'open':
      return `Due ${fmtWhen(a.due, tz, year)}; ${submissionsOf(a)} of ${a.units} submitted`;
    case 'late_window':
      return `Late work to ${fmtDay(a.grading_cutoff_datetime, tz, year)}; ${a.marks.filled} of ${a.marks.total} marked`;
    case 'marking':
      return `${a.marks.filled} of ${a.marks.total} marked`;
    case 'returned':
      return 'Marks returned';
    default:
      return `Hands out ${a.handout ? fmtDay(a.handout, tz, year) : 'by hand'}${a.teams !== null && a.teams !== undefined ? `; ${a.teams} teams so far` : ''}`;
  }
}

/**
 * The Dashboard's line under its title: what the course banner does not say (its name, dates
 * and week are there). The start while the semester has no end date yet; the exams and the
 * archive are the Schedule's and the agenda's.
 */
export function headerLine(status: Status, sched: Schedule | null, tz: string, thisYear: number): string {
  const sem = status.semester;
  const start = sem?.start ?? sched?.start ?? null, end = sem?.end ?? sched?.end ?? null;
  const year = start ? Number(start.slice(0, 4)) : thisYear;
  const out: string[] = [];
  if (sem?.week === 0 && start && !end) out.push(`Starts ${fmtDate(start, tz, year)}.`);
  return out.join(' ');
}

const KIND_ORDER = ['lec', 'lab', 'asg', 'exam', 'evt', 'semester'];
const KIND_WORD: Record<string, string> = { lec: 'lecture', lab: 'lab', asg: 'assignment', exam: 'exam', evt: 'event', semester: 'semester date' };

/** A week's badge on the strip: how many problems, and whether they are all later (hollow). */
export interface WeekCount { n: number; later: boolean }

/**
 * Each term week's badge (decision 0034 §6): red, the problems now or soon dated in it; else
 * hollow, its later ones. A week with both shows the red one.
 */
export function problemCounts(tiered: Tiered[], term: Term, tz: string): Map<WeekKey, WeekCount> {
  const tally = (later: boolean) => {
    const n = new Map<WeekKey, number>();
    for (const { p, b } of tiered) if (p.when && (b === 'later') === later) { const w = weekOf(p.when, term, tz); n.set(w, (n.get(w) ?? 0) + 1); }
    return n;
  };
  const out = new Map<WeekKey, WeekCount>();
  for (const [w, n] of tally(true)) out.set(w, { n, later: true });
  for (const [w, n] of tally(false)) out.set(w, { n, later: false });
  return out;
}

export function TermStrip({ rows, term, tz, today, counts, selected, onToggle }: {
  rows: Row[]; term: Term; tz: string; today: string; counts: Map<WeekKey, WeekCount>; selected: WeekKey[]; onToggle: (w: number) => void;
}) {
  const thisWeek = weekOf(today, term, tz);
  const cells = [];
  for (let w = 1; w <= term.weeks; w++) {
    const ws = weekStart(term, w);
    const inWeek = rows.filter((r) => r.when && weekOf(r.when, term, tz) === w);
    const kinds = new Set(inWeek.map((r) => TYPE_CLASS[r.type] ?? 'evt'));
    const rw = inWeek.some((r) => r.block === 'events' && /reading week/i.test(r.name));
    const c = counts.get(w), n = c?.n ?? 0;
    const now = w === thisWeek;
    const past = typeof thisWeek === 'number' ? w < thisWeek : thisWeek === 'after';
    const words = KIND_ORDER.filter((k) => kinds.has(k)).map((k) => KIND_WORD[k]);
    const cls = `wk${past ? ' past' : ''}${now ? ' now' : ''}${rw ? ' rw' : ''}`;
    cells.push(
      <button type="button" class={cls} aria-pressed={selected.includes(w)} onClick={() => onToggle(w)}
        aria-label={`Week ${w}, from ${fmtShort(ws)}${rw ? ', reading week' : ''}${words.length ? `: ${words.join(', ')}` : ''}${n ? (c!.later ? `; ${n} ${NOT_READY_YET}` : `; ${plural(n, 'problem')}`) : ''}${now ? '; this week' : ''}`}>
        {now ? <span class="today" style={`left:${Math.round(((Math.max(0, Math.min(6, daysBetween(ws, today))) + 0.5) / 7) * 100)}%`} aria-hidden="true" /> : null}
        {n ? <span class={`wk-count${c!.later ? ' later' : ''}`} aria-hidden="true">{n}</span> : null}
        <span class="wk-n">{rw ? 'RW' : w}</span>
        <span class="wk-d">{fmtShort(ws)}</span>
        <span class="wk-marks">{KIND_ORDER.filter((k) => kinds.has(k) && !(rw && k === 'evt')).map((k) => <i class={`m ${k}`} />)}</span>
      </button>,
    );
  }
  return (
    <div class="strip-wrap">
      <div class="term-strip" style={term.weeks !== 15 ? `grid-template-columns:repeat(${term.weeks},minmax(0,1fr))` : undefined}>{cells}</div>
      <div class="strip-legend">
        <span><i class="m lec" />Lecture</span><span><i class="m lab" />Lab</span><span><i class="m asg" />Assignment</span>
        <span><i class="m exam" />Exam</span><span><i class="m evt" />Event</span><span><i class="m term" />Semester date</span>
        <span><span class="lg-count" aria-hidden="true">n</span>problems</span><span><span class="lg-count later" aria-hidden="true">n</span>{NOT_READY_YET}</span>
      </div>
    </div>
  );
}

/** The marks of the held and late releases (`releaseMarks`), by release id: one map per [status, now]. */
export type Marks = Map<string, ReleaseMark>;

/** A release row's mark (decision 0034 §6); null for any other row, or a release with nothing to fix. */
export const rowMark = (marks: Marks, r: Row): ReleaseMark | null => (r.block === 'releases' ? marks.get(r.entry) ?? null : null);

/** A schedule row's stripe: red for a mark now or soon (or late), muted for a later one. */
export const markClass = (m: ReleaseMark | null) => (!m ? '' : m.bites === 'later' ? ' later' : ' fault');

/** The expanded strip: every week of the term, read-only; each entry opens the Schedule editor. */
function Timeline({ rows, term, tz, year, marks }: { rows: Row[]; term: Term; tz: string; year: number; marks: Marks }) {
  return (
    <div class="wk-timeline">
      {weekGroups(rows, (r) => r.when, term, tz, 'all').map((g) => {
        const from = g.from ? <span> from {fmtDay(g.from, tz, year)}</span> : null;
        if (!g.rows.length) return <p class="wk-empty">{g.label}{from}: nothing scheduled</p>;
        return (
          <section aria-label={g.label}>
            <h3 class="week-h">{g.label}{from}</h3>
            <ul class="timeline">
              {g.rows.map((r) => {
                const m = rowMark(marks, r);
                return (
                  <li class={`trow ${TYPE_CLASS[r.type] ?? 'evt'}${markClass(m)}`}>
                    <span class="k">{TYPE_LABEL[r.type] ?? r.type}</span>
                    <span class="d">{r.when ? fmtDay(r.when, tz, year) : 'TBC'}{r.when && fmtTime(r.when, tz) ? <span>{fmtTime(r.when, tz)}</span> : null}</span>
                    <span class="ttl"><a href={`#schedule-${r.entry}`}><b>{r.ident}</b>: {r.name}</a></span>
                    <span class="st">{m ? <><ReleaseMarks m={m} entry={r.entry} />{m.late ? <a class="textlink" href={`#release-${r.entry}`}>Details</a> : null}</> : <span class="st-chip">{r.state}</span>}</span>
                  </li>
                );
              })}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

/** A release as an operation names it; null while there is nothing to release (no deploy block, so no source). */
export function releaseRef(r: Release, tz: string, year: number): ReleaseRef | null {
  return r.source ? { id: r.id, ident: releaseIdent(r), title: r.title, when: fmtWhen(r.when, tz, year), source: r.source, dest: r.dest } : null;
}

/** The line a release with no deploy block shows instead of its actions. */
export const NOTHING_TO_RELEASE = 'Nothing to release yet: this entry has no deploy block';

/** The agenda's sentence for a release: what holds it back, or what happens to it. */
function releaseDetail(status: Status, rel: Release | undefined, m: ReleaseMark | null, tz: string): string {
  if (!rel) return 'Goes to students at its time; the site row goes live.';
  if (!rel.source) return `${NOTHING_TO_RELEASE}.`;
  if ((status.problems ?? []).some((x) => x.release === rel.id && x.kind === 'SOURCE_UNWRITTEN') && rel.state !== 'released') return 'Not ready yet: its file is still the placeholder.';
  const held = m ? markWord(m).replace(/^./, (c) => c.toUpperCase()) : '';
  if (rel.state === 'will_be_skipped') return needsANumber(status, rel.id) ? `${held}: it has no number.` : `${held}: its folder was not found.`;
  if (rel.state === 'released') return 'Released.';
  if (rel.state === 'late') return `Late: due ${fmtDay(rel.when, tz)}, not released yet.`;
  return 'Goes to students at its time; the site row goes live.';
}

/**
 * One agenda row, on one line: type chip, label, state, details (cut with an ellipsis; the row's
 * title carries the whole of it), then its actions.
 */
function RowItem({ r, status, p, marks }: { r: Row; status: Status; p: CohortProps; marks: Marks }) {
  const tz = tzOf(status), releases = status.releases ?? [];
  let chip = TYPE_LABEL[r.type] ?? r.type, cls = TYPE_CLASS[r.type] ?? 'evt', detail = '', buttons = null;
  const m = rowMark(marks, r);
  if (r.block === 'releases') {
    const rel = releases.find((x) => x.id === r.entry);
    const ref = rel && r.when ? releaseRef(rel, tz, Number(r.when.slice(0, 4))) : null;
    chip = 'Release';
    detail = releaseDetail(status, rel, m, tz);
    buttons = (
      <>
        {ref && rel?.state === 'planned' ? <OpButtons def={releaseEarly(cohortScope(p), ref)} small label={`Release ${r.ident} early`} /> : null}
        <a class="textlink" href={`#release-${r.entry}`}>Details</a>
      </>
    );
  } else if (r.type === 'due' || r.type === 'handout') {
    const asg = (status.assignments ?? []).find((a) => a.slug === r.entry);
    chip = r.type === 'due' ? 'Due' : 'Hand out';
    detail = r.type === 'due' && asg ? `${submissionsOf(asg)} of ${asg.units} submitted so far.` : 'Hands out at its time.';
    buttons = <a class="btn small quiet" href={`#assignment-${r.entry}`}>Open assignment</a>;
  } else {
    chip = chip.charAt(0).toUpperCase() + chip.slice(1);
  }
  const state = m ? markWord(m) : r.state;
  const details = [r.name, detail].filter(Boolean).join('. ');
  return (
    <li title={[chip, r.ident, state, details].filter(Boolean).join(' · ')}>
      <span class="when">{r.when ? <><b>{fmtDay(r.when, tz).replace(/ \w+$/, '')}</b><span>{fmtTime(r.when, tz)}</span></> : <b>TBC</b>}</span>
      <span class="what">
        <span class={`chip ${cls}`}>{chip}</span>
        <b class="t">{r.ident}</b>
        {m ? <ReleaseMarks m={m} entry={r.entry} /> : state ? <span class="st-chip">{state}</span> : null}
        {details ? <span class="d">{details}</span> : null}
      </span>
      {buttons ? <span class="actions">{buttons}</span> : null}
    </li>
  );
}

/** What the selected weeks hold, grouped by week when more than one is shown. */
function WeekRows({ status, p, rows, term, selected, name, marks }: { status: Status; p: CohortProps; rows: Row[]; term: Term; selected: number[]; name: string; marks: Marks }) {
  const tz = tzOf(status), year = yearOf(p.now, tz);
  const groups = weekGroups(rows, (r) => r.when, term, tz, selected.length ? selected : 'all').filter((g) => g.rows.length);
  if (!groups.length) return <p class="footnote">Nothing scheduled {inPhrase(name)}.</p>;
  if (groups.length === 1 && selected.length === 1) return <ul class="week">{groups[0].rows.map((r) => <RowItem r={r} status={status} p={p} marks={marks} />)}</ul>;
  return (
    <>
      {groups.map((g) => (
        <div class="wk-group">
          <h3 class="week-h">{g.label}{g.from ? <span> from {fmtDay(g.from, tz, year)}</span> : null}</h3>
          <ul class="week">{g.rows.map((r) => <RowItem r={r} status={status} p={p} marks={marks} />)}</ul>
        </div>
      ))}
    </>
  );
}

/**
 * The weeks "This week" selects: the current one; week 1 before the semester; none (All
 * weeks) after it, so the before and after buckets show only under All weeks.
 */
export function thisWeekOf(current: WeekKey): number[] {
  return current === 'before' ? [1] : current === 'after' ? [] : [current];
}

/** "This week", "All weeks", "Week 5", "Weeks 3 and 5". */
function selectionName(selected: number[], thisWeek: number[]): string {
  if (!selected.length) return 'All weeks';
  if (thisWeek.length === 1 && selected.length === 1 && selected[0] === thisWeek[0]) return 'This week';
  const nums = [...selected].sort((a, b) => a - b).map(String);
  return nums.length === 1 ? `Week ${nums[0]}` : `Weeks ${nums.slice(0, -1).join(', ')} and ${nums[nums.length - 1]}`;
}

/** A selection's name as a sentence ends: "this week", "in week 5", "in weeks 3 and 5". */
/** The agenda panel's heading: "Planned this week", "Planned in weeks 3 and 5", "Planned, all weeks". */
const plannedHeading = (name: string) => (name === 'All weeks' ? 'Planned, all weeks' : `Planned ${inPhrase(name)}`);
const inPhrase = (name: string) => (name === 'This week' ? 'this week' : name === 'All weeks' ? 'in any week' : `in ${name.charAt(0).toLowerCase()}${name.slice(1)}`);

/**
 * The Problems tab (decision 0034, amended): every problem now or soon, whatever weeks the strip
 * picks. Overdue (its moment passed), the next `days` days, then Any time (undated).
 */
export function problemGroups(list: Problem[], now: number, days: number): { key: string; label: string; rows: Problem[] }[] {
  const overdue = list.filter((p) => p.when && Date.parse(p.when) <= now);
  const next = list.filter((p) => p.when && Date.parse(p.when) > now);
  const any = list.filter((p) => !p.when);
  return [
    { key: 'overdue', label: 'Overdue', rows: overdue },
    { key: 'next', label: `Next ${days} days`, rows: next },
    { key: 'any', label: 'Any time', rows: any },
  ].filter((g) => g.rows.length);
}

// ------------------------------------------------------------------ Setup & To do (decision 0034)

/** The semester's setup steps (K1-K6), all needed. The archive date is a to-do. */
export const SEMESTER_STEPS: { id: string; name: string }[] = [
  { id: 'K1', name: 'Semester org read' },
  { id: 'K2', name: 'Semester set up on GitHub' },
  { id: 'K3', name: 'Instructors declared' },
  { id: 'K4', name: 'Schedule written' },
  { id: 'K5', name: 'Students on the roster' },
  { id: 'K6', name: 'Student site' },
];

const K_HINT: Record<string, string> = {
  K1: 'The console and automation can read the semester’s GitHub org.',
  K2: 'The semester’s repos exist (semester-config, join and the student site) and the course lists the semester.',
  K3: 'At least one instructor in semester.yml, on the Instructors page.',
  K4: 'schedule.yml with the semester’s start and end and at least one release or assignment.',
  K5: 'At least one student on the roster (students.csv).',
  K6: 'The student site’s repo exists.',
};

/** Where an open semester step, or a to-do on a screen, is done. */
const SCREEN_LINK: Record<string, Link> = {
  instructors: { href: '#instructors', label: 'Edit instructors' },
  schedule: { href: '#schedule', label: 'Edit schedule' },
  students: { href: '#students', label: 'Open roster' },
  site: { href: '#site', label: 'Edit site' },
};
const K_SCREEN: Record<string, string> = { K3: 'instructors', K4: 'schedule', K5: 'students', K6: 'site' };

/** A semester to-do's line, by its check; its sentence goes under it. */
const TODO_LABEL: Record<string, string> = { home: 'Site home page written', archive_date: 'Archive date declared', email: 'Instructor emails' };

/** The semester's Setup & To do rows: each step, then each to-do. */
export function semesterItems(status: Status, tiered: Tiered[], list: string[] | null = null): SetupItem[] {
  const s = status.semester;
  if (!s) return [];
  const steps = stepItems(s, SEMESTER_STEPS, tiered, list, {
    hint: K_HINT, fixHref,
    link: (id) => (K_SCREEN[id] ? SCREEN_LINK[K_SCREEN[id]] : { href: ghUrl(s.org), label: 'Open on GitHub', ext: true }),
  });
  const todos: SetupItem[] = (s.todo ?? []).map((t) => {
    const label = t.check ? TODO_LABEL[t.check] : undefined;
    return {
      id: t.id, label: label ?? t.text, kind: 'todo', need: t.need ?? 'needed', state: 'open',
      why: label ? t.text : undefined,
      link: t.screen ? SCREEN_LINK[t.screen] ?? { href: `#${t.screen}${t.entry ? `-${t.entry}` : ''}`, label: 'Open' } : undefined,
      aside: todoAside(t, list),
    };
  });
  // A set-aside gap (an empty week, decision 0032, or a release with nothing to release) has no row of its own once set aside: list it here so it can be brought back.
  const weeks: SetupItem[] = (list ?? []).flatMap((id) => {
    const label = gapLabel(id);
    return label ? [{ id, label, kind: 'todo' as const, need: 'suggested' as const, state: 'open' as const, aside: true }] : [];
  });
  return [...steps, ...todos, ...weeks];
}

/** One line of the semester's Coming up: a `later` problem. */
export interface ComingRow {
  key: string;
  when?: string;
  /** The day it becomes a problem (yyyy-mm-dd): the horizon's days before `when`. */
  from?: string;
  text: string;
  href: string;
  link: string;
  /** The problem itself, when it can be set aside (an empty week). */
  aside?: Problem;
}

/**
 * What this semester needs later (decision 0034): every `later` problem, the course's template
 * ones it cites included. Each becomes a problem once inside the horizon.
 */
export function comingRows(status: Status, tiered: Tiered[]): ComingRow[] {
  return tiered.filter((x) => x.b === 'later').map(({ p }) => ({
    key: p.id, when: p.when, from: p.when ? problemFromDay(p.when, status.horizon) : undefined, text: p.text, href: fixHref(p) ?? '#schedule', link: 'Fix',
    aside: p.aside ? p : undefined,
  }));
}

/** Coming up by week, in order; what no date pins last, as No date yet. */
export function comingGroups(rows: ComingRow[], term: Term, tz: string): WeekGroup<ComingRow>[] {
  const dated = rows.filter((r) => r.when), undated = rows.filter((r) => !r.when);
  const groups = weekGroups(dated, (r) => r.when, term, tz, [...new Set(dated.map((r) => weekOf(r.when!, term, tz)))]);
  return undated.length ? [...groups, { key: 'none', label: 'No date yet', rows: undated }] : groups;
}

/** The Coming up fold's body: by week, each week saying when its items become problems. */
export function ComingUp({ rows, term, tz, year, onSetAside }: { rows: ComingRow[]; term: Term; tz: string; year: number; onSetAside?: (p: Problem) => void }) {
  return (
    <>
      {comingGroups(rows, term, tz).map((g) => {
        const from = g.rows.map((r) => r.from).filter((x): x is string => !!x).sort()[0];
        return (
          <section aria-label={g.label}>
            <h3 class="week-h">{g.label}{g.from || from ? <span>{g.from ? ` from ${fmtShort(g.from)}` : ''}{from ? ` · escalates to a problem from ${fmtShort(from)}` : ''}</span> : null}</h3>
            <ul>
              {g.rows.map((r) => (
                <li key={r.key}><b>{r.when ? fmtDay(r.when, tz, year) : 'No date'}</b><span>{r.text}</span><span class="row-acts"><a class="textlink" href={r.href}>{r.link}</a>{onSetAside && r.aside ? <button class="textlink" type="button" onClick={() => onSetAside(r.aside!)}>Set aside</button> : null}</span></li>
              ))}
            </ul>
          </section>
        );
      })}
    </>
  );
}

/** The roster meter: rows, codes sent, joined; `split` (the Students tab reads the file) adds the students and auditors under it. */
export function StudentCounts({ status, split }: { status: Status; split?: { students: number; auditors: number } | null }) {
  const s = status.students ?? { rows: 0, codes_sent: 0, joined: 0 };
  return (
    <>
      <div class="counts">
        <div><span class="n">{s.rows}</span><span class="l">on the roster</span></div>
        <div><span class="n">{s.codes_sent}</span><span class="l">codes sent</span></div>
        <div><span class="n">{s.joined}</span><span class="l">joined</span></div>
      </div>
      <div class="meter"><i style={`width:${s.rows ? ((s.joined / s.rows) * 100).toFixed(1) : 0}%`} /></div>
      {split ? <p class="footnote roster-split">{plural(split.students, 'student')}, {plural(split.auditors, 'auditor')}</p> : null}
    </>
  );
}

/**
 * The roster's states, as counts the status carries, every one shown (a zero too): each opens the
 * Students tab. Whether a joined student has accepted the org invitation is not in the status,
 * so it is not counted here.
 */
function studentLines(status: Status): string[] {
  const s = status.students;
  if (!s) return [];
  const notSent = Math.max(0, s.rows - s.codes_sent), notJoined = Math.max(0, s.codes_sent - s.joined);
  return [`${notSent} on the roster, code not sent yet`, `${plural(notJoined, 'code')} sent, not joined yet`, `${s.joined} joined`];
}

/** The Dashboard's Students panel, in the roster meter's look; hidden once every row has joined. */
function StudentsPanel({ status }: { status: Status }) {
  const s = status.students;
  if (!s || s.joined >= s.rows) return null;
  return (
    <section class="panel section waiting">
      <div class="section-head"><h2>Students</h2><a class="btn small quiet" href="#students">Open roster</a></div>
      <StudentCounts status={status} />
      <p class="footnote">Incomplete: {s.rows - s.joined} of {s.rows} not joined yet.</p>
      <ul class="waiting-list">{studentLines(status).map((l) => <li><a class="textlink" href="#students">{l}</a></li>)}</ul>
    </section>
  );
}

export function AutomationHead({ heartbeat, now }: { heartbeat: CohortProps['heartbeat']; now: number }) {
  if (heartbeat === undefined)
    return <div class="auto-head"><span class="dot idle" aria-hidden="true" /><div><b>Reading automation</b><div class="sub">Automation checks this semester every 15 minutes.</div></div></div>;
  if (heartbeat === null)
    return <div class="auto-head"><span class="dot idle" aria-hidden="true" /><div><b>Automation's runs cannot be read</b><div class="sub">Automation checks this semester every 15 minutes.</div></div></div>;
  const late = heartbeat.late;
  return (
    <div class="auto-head">
      <span class={`dot ${late ? 'bad' : 'ok'}`} aria-hidden="true" />
      <div>
        <b>{heartbeat.lastTick ? (late ? `No check for ${ago(heartbeat.lastTick, now).replace(' ago', '')}` : `Checked ${ago(heartbeat.lastTick, now)}`) : 'No check yet'}</b>
        <div class="sub">Automation checks this semester every 15 minutes.</div>
      </div>
    </div>
  );
}

function Overview(p: ReadyProps) {
  const { status, now } = p;
  const tz = tzOf(status), year = yearOf(now, tz), today = todayOf(now, tz);
  const sched = readSchedule(p);
  const rows = scheduleRows(status, sched, now, tz);
  const term = termOf(status, sched, today);
  const current = weekOf(today, term, tz);
  const [expanded, setExpanded] = useState(false);
  const thisWeek = thisWeekOf(current);
  // The weeks the page is filtered to, picked in the strip; empty is all weeks. This week on load.
  const [selected, setSelected] = useState<number[]>(thisWeek);
  // Every problem with its time, once per render (decision 0034): now or soon are the strip's red
  // counts and the Problems tab; later ones are Coming up.
  const aside = useSetAside({ org: p.course.org, files: p.files, write: p.course.write });
  // An empty week the course set aside leaves the moment the list says so, before the status is rewritten.
  const tiered = useMemo(() => tier(status).filter(({ p: x }) => !(x.aside && aside.list?.includes(x.id))), [status, aside.list?.join('\n')]);
  const marks = useMemo(() => releaseMarks(status, tiered, now), [status, tiered, now]);
  const problems = standing(tiered);
  // The dialog names the problem as its card does: its label and the release it holds, as the site names it; a week gap by its week (the id's alone).
  const askLabel = (x: Problem) => {
    const r = x.fix?.entry ? status.releases?.find((rel) => rel.id === x.fix!.entry) : undefined;
    return x.label && r ? `${x.label}, ${releaseIdent(r)}` : gapLabel(x.id) ?? x.text;
  };
  const setAsideProblem = aside.onCircle ? (x: Problem) => aside.onCircle!({ id: x.id, label: askLabel(x) }) : undefined;
  const items = semesterItems(status, tiered, aside.list);
  const coming = comingRows(status, tiered);
  const days = horizonDays(status.horizon);
  const verdict = verdictOf(status.semester?.verdict, 'semester', suggestionsCount(items), days, coming.length);
  // The tab the verdict points at, until one is picked.
  const [picked, setTab] = useState<string | null>(null);
  const tab = picked ?? verdictTab(verdict);
  const late = (status.releases ?? []).filter((r) => r.state === 'late');
  const ops = useOperations(status.operations, p.cohort.org);
  const toggle = (w: number) => setSelected(selected.includes(w) ? selected.filter((k) => k !== w) : [...selected, w]);
  const name = selectionName(selected, thisWeek);
  const shown = problemGroups(problems, now, days);
  const header = headerLine(status, sched, tz, year);
  return (
    <>
      <div class="page-head">
        <div>
          <h2 class="h1">Dashboard <Hint doc="07-schedule-releases.md">What this semester has planned and what needs fixing before it can happen. Pick weeks in the strip to show only those weeks.</Hint></h2>
          {header ? <p class="lede"><span>{header}</span></p> : null}
        </div>
        <div class="actions"><OpButtons def={checkNow(cohortScope(p))} /><MoreMenu p={p} /></div>
      </div>
      {/* The verdict is the page's one problem count, and its link to the Problems tab (decision 0034). */}
      <Verdict v={verdict} onOpen={() => showTab(setTab, verdictTab(verdict))} />
      <div class="stack">
        <section class="panel section">
          <div class="section-head">
            {/* The chevron before the heading (decision 0031 rule 11), as in the side nav. */}
            <span class="lead">
              <button type="button" class="chev" aria-expanded={expanded} aria-controls="dash-weeks" aria-label={expanded ? 'Show the week strip' : 'Show every week as a list'} onClick={() => setExpanded(!expanded)}><span class="arrow" aria-hidden="true" /></button>
              <h2>Semester <Hint label="About the weeks">Pick one or more weeks to show only what falls in them; unpick them all to show every week. A red number counts that week's problems; a hollow one, what is not ready yet there and becomes a problem later.</Hint></h2>
            </span>
          </div>
          <div id="dash-weeks">
            {expanded
              ? <Timeline rows={rows} term={term} tz={tz} year={year} marks={marks} />
              : <TermStrip rows={rows} term={term} tz={tz} today={today} counts={problemCounts(tiered, term, tz)} selected={selected} onToggle={toggle} />}
          </div>
        </section>
        <div ref={aside.ref}>
          <DashboardTabs selected={tab} onSelect={setTab} tabs={[
            {
              key: 'problems', label: 'Problems', count: problems.length || 'done', bad: true,
              hint: <>What will not happen until you fix it: overdue, and the next {days} days.</>,
              body: (
                <>
                  {!problems.length ? <ProblemCards list={[]} cohort /> : null}
                  {shown.map((g) => (
                    <div class={`p-${g.key}`}>
                      <h3 class="week-h">{g.label}</h3>
                      <ProblemCards list={g.rows} cohort onSetAside={setAsideProblem} />
                    </div>
                  ))}
                </>
              ),
            },
            {
              key: 'coming', label: 'Coming up', count: coming.length,
              hint: <>Needed later, by week. Each item becomes a problem {days} days before the date it is needed by, and is listed here until then.</>,
              body: coming.length ? <ComingUp rows={coming} term={term} tz={tz} year={year} onSetAside={setAsideProblem} /> : <p class="footnote">Nothing is needed later.</p>,
            },
            ...readinessTabs({ items, scope: 'semester', busy: aside.busy, onCircle: aside.onCircle, onBack: aside.onBack(items) }),
          ]} />
          {aside.after}
        </div>
        <section class="panel section agenda">
            <div class="section-head"><h2>{plannedHeading(name)} <Hint label="About what is planned">Every release and deadline in the picked weeks, with its state. Late means its date has passed and it has not gone out. Pick weeks in the Semester strip above.</Hint></h2>{selected.length === 1 ? <span class="meta">{fmtDay(weekStart(term, selected[0]), tz).replace(/ \w+$/, '')} to {fmtDay(addDays(weekStart(term, selected[0]), 6), tz, year)}</span> : null}</div>
            <WeekRows status={status} p={p} rows={rows} term={term} selected={selected} name={name} marks={marks} />
            <p class="overdue">{late.length ? `${late.length} release${late.length > 1 ? 's are' : ' is'} late: ${late.map((r) => releaseIdent(r)).join(', ')}.` : 'Nothing overdue.'}</p>
        </section>
        <div class="grid-2">
          <StudentsPanel status={status} />
          <section class="panel section">
            <div class="section-head">
              <h2>Automation</h2>
              <span class="actions"><OpOpen def={checkAccess(cohortScope(p))} cls="btn small quiet" label="Check instructor access" /><a class="btn small quiet" href="#operations">All operations</a></span>
            </div>
            <AutomationHead heartbeat={p.heartbeat} now={now} />
            <OpsList list={ops.slice(0, 3)} now={now} runRepo={`${p.course.org}/.github`} />
          </section>
        </div>
      </div>
    </>
  );
}

export function CohortScreen(p: CohortProps) {
  return (
    <WithStatus props={p} title="Dashboard">
      {(r) => <Overview {...r} />}
    </WithStatus>
  );
}
