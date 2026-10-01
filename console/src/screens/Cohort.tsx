// S4 Semester overview (Dashboard, decision 0015).

import { useState } from 'preact/hooks';
import {
  ASSIGNMENT_WORD, TYPE_CLASS, TYPE_LABEL, addDays, ago, assignmentTitle, daysBetween, fmtDate, fmtDay, fmtDays, fmtShort, fmtTime, fmtWhen, releaseIdent,
} from '../model/format';
import { inWeeks, needsANumber, parseSchedule, scheduleRows, weekOf, type Row, type Schedule, type Term, type WeekKey, termOf } from '../model/schedule';
import type { Assignment, Problem, Status } from '../model/types';
import { checkAccess, releaseEarly, type ReleaseRef } from '../ops/defs';
import { OpButtons, OpOpen } from '../ops/Panel';
import type { Release } from '../model/types';
import { Legend, OpsList, ProblemCards, Probs, Rail, fixHref } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { CheckNow, MoreMenu, WithStatus, cohortScope, todayOf, tzOf, useOperations, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';
import { CONFIG_REPO } from '../model/names';

export function readSchedule(p: CohortProps): Schedule | null {
  const f = p.files.file(p.cohort.org, CONFIG_REPO, 'schedule.yml');
  return f.kind === 'ready' ? parseSchedule(f.text) : null;
}

/** "Session 3: Trees" -> <b>Session 3</b>: Trees */
export function IdentTitle({ text }: { text: string }) {
  const i = text.indexOf(': ');
  return i < 0 ? <b>{text}</b> : <><b>{text.slice(0, i)}</b>: {text.slice(i + 2)}</>;
}

export function asgSummary(a: Assignment, tz: string, year: number): string {
  switch (a.state) {
    case 'open':
      return `Due ${fmtWhen(a.due, tz, year)}; ${a.submissions} of ${a.units} submitted`;
    case 'late_window':
      return `Late work until ${fmtDay(a.grading_cutoff_datetime, tz, year)}; ${a.submissions} of ${a.units} submitted`;
    case 'marking':
      return `${a.marks.filled} of ${a.marks.total} marked`;
    case 'returned':
      return 'Marks returned';
    default:
      return `Hands out ${a.handout ? fmtDay(a.handout, tz, year) : 'by hand'}${a.teams !== null && a.teams !== undefined ? `; ${a.teams} teams so far` : ''}`;
  }
}

export function AsgRows({ status, now }: { status: Status; now: number }) {
  const tz = tzOf(status), year = yearOf(now, tz);
  return (
    <>
      {(status.assignments ?? []).map((a) => (
        <li>
          <a href={`#assignment-${a.slug}`}>
            <span class="a-name">{assignmentTitle(a)}</span>
            <span class="a-sub">{asgSummary(a, tz, year)}</span>
            <span class="a-side">
              <span class={`chip ${a.state === 'open' ? 'asg' : ''}`}>{ASSIGNMENT_WORD[a.state]}</span>
              {a.problem ? <span class="a-flag">Assignment template has a problem</span> : null}
            </span>
          </a>
        </li>
      ))}
    </>
  );
}

/**
 * The Dashboard's line under its title: what the semester banner does not say (its name, dates
 * and week are there). The start while the semester has no end date yet, the exams, the archive.
 */
export function headerLine(status: Status, sched: Schedule | null, rows: Row[], tz: string, thisYear: number): string {
  const sem = status.semester;
  const start = sem?.start ?? sched?.start ?? null, end = sem?.end ?? sched?.end ?? null;
  const year = start ? Number(start.slice(0, 4)) : thisYear;
  const out: string[] = [];
  if (sem?.week === 0 && start && !end) out.push(`Starts ${fmtDate(start, tz, year)}.`);
  const exams = rows.filter((r) => r.type === 'exam' && r.when).map((r) => r.when!);
  if (exams.length) out.push(`${exams.length > 1 ? 'Exams' : 'Exam'} ${fmtDays(exams, tz, year)}.`);
  if (sem?.archive_date) out.push(`Archive ${fmtDate(sem.archive_date, tz, year)}.`);
  return out.join(' ');
}

const KIND_ORDER = ['lec', 'lab', 'asg', 'exam', 'evt', 'term'];
const KIND_WORD: Record<string, string> = { lec: 'lecture', lab: 'lab', asg: 'assignment', exam: 'exam', evt: 'event', term: 'semester date' };

const weekStart = (term: Term, w: number) => addDays(term.start, (w - 1) * 7);

/** Problems dated in each term week, for the strip's red counts. */
export function problemCounts(problems: Problem[], term: Term, tz: string): Map<WeekKey, number> {
  const n = new Map<WeekKey, number>();
  for (const p of problems) if (p.when) { const w = weekOf(p.when, term, tz); n.set(w, (n.get(w) ?? 0) + 1); }
  return n;
}

export function TermStrip({ rows, term, tz, today, counts, selected, onToggle }: {
  rows: Row[]; term: Term; tz: string; today: string; counts: Map<WeekKey, number>; selected: WeekKey[]; onToggle: (w: number) => void;
}) {
  const thisWeek = weekOf(today, term, tz);
  const cells = [];
  for (let w = 1; w <= term.weeks; w++) {
    const ws = weekStart(term, w);
    const inWeek = rows.filter((r) => r.when && weekOf(r.when, term, tz) === w);
    const kinds = new Set(inWeek.map((r) => TYPE_CLASS[r.type] ?? 'evt'));
    const rw = inWeek.some((r) => r.block === 'events' && /reading week/i.test(r.name));
    const n = counts.get(w) ?? 0;
    const now = w === thisWeek;
    const past = typeof thisWeek === 'number' ? w < thisWeek : thisWeek === 'after';
    const words = KIND_ORDER.filter((k) => kinds.has(k)).map((k) => KIND_WORD[k]);
    const cls = `wk${past ? ' past' : ''}${now ? ' now' : ''}${rw ? ' rw' : ''}`;
    cells.push(
      <button type="button" class={cls} aria-pressed={selected.includes(w)} onClick={() => onToggle(w)}
        aria-label={`Week ${w}, from ${fmtShort(ws)}${rw ? ', reading week' : ''}${words.length ? `: ${words.join(', ')}` : ''}${n ? `; ${n} problem${n > 1 ? 's' : ''}` : ''}${now ? '; this week' : ''}`}>
        {now ? <span class="today" style={`left:${Math.round(((Math.max(0, Math.min(6, daysBetween(ws, today))) + 0.5) / 7) * 100)}%`} aria-hidden="true" /> : null}
        {n ? <span class="wk-count" aria-hidden="true">{n}</span> : null}
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
      </div>
    </div>
  );
}

/** A group of rows under one heading: a term week, the before or after bucket, or no date yet. */
export interface WeekGroup {
  key: WeekKey | 'none';
  label: string;
  rows: Row[];
}

/**
 * Rows grouped by term week, in order. `all` adds every week of the term, empty ones too,
 * and the before and after buckets; otherwise only the weeks in `keys` that have rows.
 */
export function weekGroups(rows: Row[], term: Term, tz: string, keys: WeekKey[] | 'all'): WeekGroup[] {
  const by = new Map<WeekKey | 'none', Row[]>();
  for (const r of rows) {
    const k = r.when ? weekOf(r.when, term, tz) : 'none';
    by.set(k, [...(by.get(k) ?? []), r]);
  }
  const label = (k: WeekKey | 'none') => (k === 'before' ? 'Before the semester' : k === 'after' ? 'After the semester' : k === 'none' ? 'No date yet' : `Week ${k}`);
  const order: (WeekKey | 'none')[] = keys === 'all'
    ? ['before', ...Array.from({ length: term.weeks }, (_, i) => i + 1), 'after', 'none']
    : [...keys].sort((a, b) => rank(a, term) - rank(b, term));
  return order
    .filter((k) => (keys === 'all' && typeof k === 'number') || by.has(k))
    .map((k) => ({ key: k, label: label(k), rows: by.get(k) ?? [] }));
}

const rank = (k: WeekKey, term: Term) => (k === 'before' ? 0 : k === 'after' ? term.weeks + 1 : k);

/** The expanded strip: every week of the term, read-only; each entry opens the Schedule editor. */
function Timeline({ rows, term, tz, year }: { rows: Row[]; term: Term; tz: string; year: number }) {
  return (
    <div class="wk-timeline">
      {weekGroups(rows, term, tz, 'all').map((g) => {
        const from = typeof g.key === 'number' ? <span> from {fmtDay(weekStart(term, g.key), tz, year)}</span> : null;
        if (!g.rows.length) return <p class="wk-empty">{g.label}{from}: nothing scheduled</p>;
        return (
          <section aria-label={g.label}>
            <h3 class="week-h">{g.label}{from}</h3>
            <ul class="timeline">
              {g.rows.map((r) => (
                <li class={`trow ${TYPE_CLASS[r.type] ?? 'evt'}${r.fault && r.block === 'releases' ? ' fault' : ''}`}>
                  <span class="k">{TYPE_LABEL[r.type] ?? r.type}</span>
                  <span class="d">{r.when ? fmtDay(r.when, tz, year) : 'TBC'}{r.when && fmtTime(r.when, tz) ? <span>{fmtTime(r.when, tz)}</span> : null}</span>
                  <span class="ttl"><a href={`#schedule-${r.entry}`}><b>{r.ident}</b>: {r.name}</a></span>
                  <span class="st"><span class="st-chip">{r.fault && r.block === 'releases' ? 'will be skipped' : r.state}</span></span>
                </li>
              ))}
            </ul>
          </section>
        );
      })}
    </div>
  );
}

/** A release as an operation names it; null while there is nothing to release (no deploy block, so no source). */
export function releaseRef(r: Release, tz: string, year: number): ReleaseRef | null {
  return r.source ? { id: r.id, ident: releaseIdent(r), title: r.title, when: fmtWhen(r.when, tz, year), source: r.source } : null;
}

/** The line a release with no deploy block shows instead of its actions. */
export const NOTHING_TO_RELEASE = 'Nothing to release yet: this entry has no deploy block';

function RowItem({ r, status, p }: { r: Row; status: Status; p: CohortProps }) {
  const tz = tzOf(status), releases = status.releases ?? [];
  let chip = TYPE_LABEL[r.type] ?? r.type, cls = TYPE_CLASS[r.type] ?? 'evt', detail = '', buttons = null;
  if (r.block === 'releases') {
    const rel = releases.find((x) => x.id === r.entry);
    const ref = rel && r.when ? releaseRef(rel, tz, Number(r.when.slice(0, 4))) : null;
    chip = 'Release';
    detail = rel && !rel.source ? `${NOTHING_TO_RELEASE}.` : rel?.state === 'will_be_skipped' ? (needsANumber(status, rel.id) ? 'Will be skipped: it has no number.' : 'Will be skipped: its folder was not found.') : rel?.state === 'released' ? 'Released.' : 'Goes to students at its time; the site row goes live.';
    buttons = (
      <>
        {ref && rel?.state === 'planned' ? <OpButtons def={releaseEarly(cohortScope(p), ref)} small label={`Release ${r.ident} early`} /> : null}
        <a class="textlink" href={`#release-${r.entry}`}>Details</a>
      </>
    );
  } else if (r.type === 'due' || r.type === 'handout') {
    const asg = (status.assignments ?? []).find((a) => a.slug === r.entry);
    chip = r.type === 'due' ? 'Due' : 'Hand out';
    detail = r.type === 'due' && asg ? `${asg.submissions} of ${asg.units} submitted so far.` : 'Hands out at its time.';
    buttons = <a class="btn small quiet" href={`#assignment-${r.entry}`}>Open assignment</a>;
  } else {
    chip = chip.charAt(0).toUpperCase() + chip.slice(1);
  }
  return (
    <li>
      <span class="when">{r.when ? <><b>{fmtDay(r.when, tz).replace(/ \w+$/, '')}</b><span>{fmtTime(r.when, tz)}</span></> : <b>TBC</b>}</span>
      <span class="what">
        <span><span class={`chip ${cls}`}>{chip}</span></span>
        <span class="t"><b>{r.ident}</b>: {r.name}</span>
        {detail ? <span class="d">{detail}</span> : null}
        {buttons ? <span class="actions">{buttons}</span> : null}
      </span>
    </li>
  );
}

/** What the selected weeks hold, grouped by week when more than one is shown. */
function WeekRows({ status, p, rows, term, selected, name }: { status: Status; p: CohortProps; rows: Row[]; term: Term; selected: number[]; name: string }) {
  const tz = tzOf(status), year = yearOf(p.now, tz);
  const groups = weekGroups(rows, term, tz, selected.length ? selected : 'all').filter((g) => g.rows.length);
  if (!groups.length) return <p class="footnote">Nothing scheduled {inPhrase(name)}.</p>;
  if (groups.length === 1 && selected.length === 1) return <ul class="week">{groups[0].rows.map((r) => <RowItem r={r} status={status} p={p} />)}</ul>;
  return (
    <>
      {groups.map((g) => (
        <div class="wk-group">
          <h3 class="week-h">{g.label}{typeof g.key === 'number' ? <span> from {fmtDay(weekStart(term, g.key), tz, year)}</span> : null}</h3>
          <ul class="week">{g.rows.map((r) => <RowItem r={r} status={status} p={p} />)}</ul>
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
const inPhrase = (name: string) => (name === 'This week' ? 'this week' : name === 'All weeks' ? 'in any week' : `in ${name.charAt(0).toLowerCase()}${name.slice(1)}`);

/**
 * The problems a selection shows. This week also shows what is overdue: dated before it, in
 * its own group. Every other selection is strict. Undated problems always show.
 */
export function problemGroups(problems: Problem[], selected: number[], isThisWeek: boolean, term: Term, tz: string): { overdue: Problem[]; dated: Problem[]; undated: Problem[]; elsewhere: number } {
  const { dated, undated } = inWeeks(problems, (x) => x.when, selected, term, tz);
  const overdue = isThisWeek ? problems.filter((x) => x.when && rank(weekOf(x.when, term, tz), term) < selected[0]) : [];
  const elsewhere = problems.filter((x) => x.when).length - dated.length - overdue.length;
  return { overdue, dated, undated, elsewhere };
}

export function StudentCounts({ status }: { status: Status }) {
  const s = status.students ?? { rows: 0, codes_sent: 0, joined: 0 };
  return (
    <>
      <div class="counts">
        <div><span class="n">{s.rows}</span><span class="l">on the roster</span></div>
        <div><span class="n">{s.codes_sent}</span><span class="l">codes sent</span></div>
        <div><span class="n">{s.joined}</span><span class="l">joined</span></div>
      </div>
      <div class="meter"><i style={`width:${s.rows ? ((s.joined / s.rows) * 100).toFixed(1) : 0}%`} /></div>
    </>
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
  const [showSetup, setShowSetup] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const thisWeek = thisWeekOf(current);
  // The weeks the page is filtered to; empty is All weeks. This week on load.
  const [selected, setSelected] = useState<number[]>(thisWeek);
  const problems = status.problems ?? [];
  const stages = status.semester?.stages ?? {};
  const amber = Object.values(stages).filter((s) => s === 'problem').length;
  const late = (status.releases ?? []).filter((r) => r.state === 'late');
  const s = status.students;
  const ops = useOperations(status.operations, p.cohort.org);
  const fixOf = (stage: string) => {
    const pr = problems.find((x) => x.stage === stage);
    const h = pr && fixHref(pr);
    return h ? <a class="btn small" href={h}>Fix</a> : null;
  };
  const toggle = (w: number) => setSelected(selected.includes(w) ? selected.filter((k) => k !== w) : [...selected, w]);
  const name = selectionName(selected, thisWeek);
  const isThisWeek = name === 'This week';
  const shown = problemGroups(problems, selected, isThisWeek, term, tz);
  return (
    <>
      <div class="page-head">
        <div>
          <h2 class="h1">Dashboard <Hint doc="07-schedule-releases.md">What this semester has planned and what needs fixing before it can happen. Pick weeks in the strip to show only those weeks.</Hint></h2>
          <p class="lede">
            <span>{headerLine(status, sched, rows, tz, year)}</span>
            {amber ? <span class="amber">Setup done, but {amber} {amber > 1 ? 'stages have a problem' : 'stage has a problem'}.</span> : <span>Setup complete.</span>}
            <button class="textlink" type="button" aria-expanded={showSetup} onClick={() => setShowSetup(!showSetup)}>{showSetup ? 'Hide setup' : 'Show setup'}</button>
          </p>
        </div>
        <div class="actions"><Probs n={problems.length} /><CheckNow p={p} /><MoreMenu p={p} /></div>
      </div>
      <div class="stack">
        {showSetup ? (
          <section class="panel section">
            <div class="section-head"><h2>Setup</h2><Legend /></div>
            <Rail scope="cohort" stages={stages} problems={problems} acts={{
              K3: <a class="btn small quiet" href="#instructors">Instructors</a>, K4: fixOf('K4'), K5: fixOf('K5'),
              K6: <a class="btn small quiet" href="#site">Site</a>, K7: <a class="btn small quiet" href="#archive">Archive</a>,
            }} />
          </section>
        ) : null}
        <section class="panel section">
          <div class="section-head">
            <h2>Semester <Hint label="About the weeks">Pick one or more weeks to show only what falls in them. A red number counts that week's problems.</Hint></h2>
            <span class="wk-filters" role="group" aria-label="Show weeks">
              <button type="button" class="toggle" aria-pressed={isThisWeek} onClick={() => setSelected(thisWeek)}>This week</button>
              <button type="button" class="toggle" aria-pressed={!selected.length} onClick={() => setSelected([])}>All weeks</button>
              <button type="button" class="btn small quiet wk-expand" aria-expanded={expanded} aria-label="Show every week" onClick={() => setExpanded(!expanded)}>{expanded ? '<' : '>'}</button>
            </span>
          </div>
          {expanded
            ? <Timeline rows={rows} term={term} tz={tz} year={year} />
            : <TermStrip rows={rows} term={term} tz={tz} today={today} counts={problemCounts(problems, term, tz)} selected={selected} onToggle={toggle} />}
        </section>
        <section class="section">
          <div class="problems-head"><h2>Problems</h2><span class="footnote">What will not happen until you fix it.</span></div>
          {!problems.length ? <ProblemCards list={[]} cohort /> : null}
          {shown.overdue.length ? (
            <div class="p-overdue">
              <h3 class="week-h">Overdue</h3>
              <ProblemCards list={shown.overdue} cohort />
            </div>
          ) : null}
          {shown.dated.length ? <ProblemCards list={shown.dated} cohort /> : null}
          {!shown.overdue.length && !shown.dated.length && shown.elsewhere ? (
            <p class="footnote">
              No problems {inPhrase(name)}. {shown.elsewhere} in other weeks.{' '}
              <button class="textlink" type="button" onClick={() => setSelected([])}>Show all weeks</button>
            </p>
          ) : null}
          {shown.undated.length ? (
            <div class="p-anytime">
              <h3 class="week-h">Any time</h3>
              <ProblemCards list={shown.undated} cohort />
            </div>
          ) : null}
        </section>
        <div class="grid-2">
          <section class="panel section">
            <div class="section-head"><h2>{name}</h2>{selected.length === 1 ? <span class="meta">{fmtDay(weekStart(term, selected[0]), tz).replace(/ \w+$/, '')} to {fmtDay(addDays(weekStart(term, selected[0]), 6), tz, year)}</span> : null}</div>
            <WeekRows status={status} p={p} rows={rows} term={term} selected={selected} name={name} />
            <p class="overdue">{late.length ? `${late.length} release${late.length > 1 ? 's are' : ' is'} late: ${late.map((r) => releaseIdent(r)).join(', ')}.` : 'Nothing overdue.'}</p>
          </section>
          <section class="panel section">
            <div class="section-head"><h2>Assignments</h2><a class="btn small quiet" href="#assignments">All assignments</a></div>
            <ul class="asg-list"><AsgRows status={status} now={now} /></ul>
          </section>
        </div>
        <div class="grid-2">
          <section class="panel section">
            <div class="section-head"><h2>Students</h2><a class="btn small quiet" href="#students">Open roster</a></div>
            <StudentCounts status={status} />
            {s ? (
              <p class="footnote">
                {s.codes_sent - s.joined} students have a code but have not joined.
                {s.rows > s.codes_sent ? ` ${s.rows - s.codes_sent} ${s.rows - s.codes_sent > 1 ? 'have' : 'has'} no code.` : ''}
              </p>
            ) : null}
          </section>
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
