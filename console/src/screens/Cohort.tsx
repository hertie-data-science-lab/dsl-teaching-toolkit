// S4 Cohort overview ("This week").

import { useState } from 'preact/hooks';
import {
  ASSIGNMENT_WORD, TYPE_CLASS, addDays, ago, assignmentTitle, dayKey, daysBetween, fmtDay, fmtShort, fmtTime, fmtWhen, releaseIdent, zoned,
} from '../model/format';
import { parseSchedule, scheduleRows, type Row, type Schedule } from '../model/schedule';
import type { Assignment, Status } from '../model/types';
import { checkAccess, releaseEarly, type ReleaseRef } from '../ops/defs';
import { OpButtons, OpOpen } from '../ops/Panel';
import type { Release } from '../model/types';
import { Crumbs, Help, Legend, OpsList, ProblemCards, Probs, Rail, fixHref } from '../ui/bits';
import { CheckNow, MoreMenu, WithStatus, cohortName, cohortScope, todayOf, tzOf, useOperations, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';

export function readSchedule(p: CohortProps): Schedule | null {
  const f = p.files.file(p.cohort.org, 'classroom-config', 'schedule.yml');
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
      return `Late work until ${fmtDay(a.late_until, tz, year)}; ${a.submissions} of ${a.units} submitted`;
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

/** Where week 1 starts: the schedule's semester_start, else counted back from `week`. */
export function termStart(status: Status, sched: Schedule | null, now: number): string {
  if (sched?.start) return sched.start;
  const tz = tzOf(status), today = todayOf(now, tz);
  const monday = addDays(today, -((zoned(today).dow + 6) % 7));
  return addDays(monday, -7 * ((status.cohort?.week ?? 1) - 1));
}

const KIND_ORDER = ['lec', 'lab', 'asg', 'exam', 'evt', 'term'];
const KIND_WORD: Record<string, string> = { lec: 'lecture', lab: 'lab', asg: 'assignment', exam: 'exam', evt: 'event', term: 'term date' };

export function TermStrip({ status, rows, start, now }: { status: Status; rows: Row[]; start: string; now: number }) {
  const tz = tzOf(status), weeks = status.cohort?.weeks ?? 15, today = todayOf(now, tz);
  const thisWeek = Math.floor(daysBetween(start, today) / 7) + 1;
  const cells = [];
  for (let w = 1; w <= weeks; w++) {
    const ws = addDays(start, (w - 1) * 7), we = addDays(ws, 7);
    const inWeek = rows.filter((r) => r.when && dayKey(r.when, tz) >= ws && dayKey(r.when, tz) < we);
    const kinds = new Set(inWeek.map((r) => TYPE_CLASS[r.type] ?? 'evt'));
    const rw = inWeek.some((r) => r.block === 'events' && /reading week/i.test(r.name));
    const prob = inWeek.some((r) => r.fault && r.block === 'releases');
    const words = KIND_ORDER.filter((k) => kinds.has(k)).map((k) => KIND_WORD[k]);
    const cls = `wk${w < thisWeek ? ' past' : ''}${w === thisWeek ? ' now' : ''}${rw ? ' rw' : ''}${prob ? ' has-problem' : ''}`;
    const di = daysBetween(ws, today);
    cells.push(
      <a class={cls} href="#schedule" aria-label={`Week ${w}, from ${fmtShort(ws)}${rw ? ', reading week' : ''}${words.length ? `: ${words.join(', ')}` : ''}${prob ? '; one release will be skipped' : ''}${w === thisWeek ? '; this week' : ''}`}>
        {w === thisWeek ? <span class="today" style={`left:${Math.round(((di + 0.5) / 7) * 100)}%`} aria-hidden="true" /> : null}
        {prob ? <span class="wk-flag" aria-hidden="true">!</span> : null}
        <span class="wk-n">{rw ? 'RW' : w}</span>
        <span class="wk-d">{fmtShort(ws)}</span>
        <span class="wk-marks">{KIND_ORDER.filter((k) => kinds.has(k) && !(rw && k === 'evt')).map((k) => <i class={`m ${k}`} />)}</span>
      </a>,
    );
  }
  return (
    <div class="strip-wrap">
      <div class="term-strip" style={weeks !== 15 ? `grid-template-columns:repeat(${weeks},minmax(0,1fr))` : undefined}>{cells}</div>
      <div class="strip-legend">
        <span><i class="m lec" />Lecture</span><span><i class="m lab" />Lab</span><span><i class="m asg" />Assignment</span>
        <span><i class="m exam" />Exam</span><span><i class="m evt" />Event</span><span><i class="m term" />Term date</span>
        <span style="color:var(--bad-ink);font-weight:600">! A release will be skipped</span>
      </div>
    </div>
  );
}

/** A release as an operation names it; null while there is nothing to release (no deploy block, so no source). */
export function releaseRef(r: Release, all: Release[], tz: string, year: number): ReleaseRef | null {
  return r.source ? { id: r.id, ident: releaseIdent(r, all), title: r.title, when: fmtWhen(r.when, tz, year), source: r.source } : null;
}

/** The line a release with no deploy block shows instead of its actions. */
export const NOTHING_TO_RELEASE = 'Nothing to release yet: this entry has no deploy block';

function WeekItems({ status, p }: { status: Status; p: CohortProps }) {
  const tz = tzOf(status), releases = status.releases ?? [];
  const items = status.this_week ?? [];
  if (!items.length) return <p class="footnote">Nothing scheduled for the rest of this week.</p>;
  return (
    <ul class="week">
      {items.map((it) => {
        const rel = releases.find((r) => r.id === it.ref);
        const asg = (status.assignments ?? []).find((a) => a.slug === it.ref);
        let chip = it.type, cls = TYPE_CLASS[it.type] ?? 'evt', detail = '', buttons = null;
        if (it.type === 'release') {
          const ref = rel ? releaseRef(rel, releases, tz, zoned(it.when, tz).y) : null;
          chip = 'Release';
          cls = TYPE_CLASS[rel ? rel.type ?? 'release' : 'lecture'];
          const ident = rel ? releaseIdent(rel, releases) : it.title.split(':')[0];
          detail = rel && !rel.source ? `${NOTHING_TO_RELEASE}.` : rel?.state === 'will_be_skipped' ? 'Will be skipped: its folder was not found.' : rel?.state === 'released' ? 'Released.' : 'Goes to students at its time; the site row goes live.';
          buttons = (
            <>
              {ref && rel?.state === 'planned' ? <OpButtons def={releaseEarly(cohortScope(p), ref)} small label={`Release ${ident} early`} /> : null}
              <a class="textlink" href={`#release-${it.ref}`}>Details</a>
            </>
          );
        } else if (it.type === 'due' || it.type === 'handout') {
          chip = it.type === 'due' ? 'Due' : 'Hand out';
          cls = 'asg';
          detail = it.type === 'due' && asg ? `${asg.submissions} of ${asg.units} submitted so far.` : 'Hands out at its time.';
          buttons = <a class="btn small quiet" href={`#assignment-${it.ref}`}>Open assignment</a>;
        }
        return (
          <li>
            <span class="when"><b>{fmtDay(it.when, tz).replace(/ \w+$/, '')}</b><span>{fmtTime(it.when, tz)}</span></span>
            <span class="what">
              <span><span class={`chip ${cls}`}>{chip}</span></span>
              <span class="t"><IdentTitle text={it.title} /></span>
              <span class="d">{detail}</span>
              {buttons ? <span class="actions">{buttons}</span> : null}
            </span>
          </li>
        );
      })}
    </ul>
  );
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
    return <div class="auto-head"><span class="dot idle" aria-hidden="true" /><div><b>Reading automation</b><div class="sub">Automation checks this cohort every 15 minutes.</div></div></div>;
  if (heartbeat === null)
    return <div class="auto-head"><span class="dot idle" aria-hidden="true" /><div><b>Automation's runs cannot be read</b><div class="sub">Automation checks this cohort every 15 minutes.</div></div></div>;
  const late = heartbeat.late;
  return (
    <div class="auto-head">
      <span class={`dot ${late ? 'bad' : 'ok'}`} aria-hidden="true" />
      <div>
        <b>{heartbeat.lastTick ? (late ? `No check for ${ago(heartbeat.lastTick, now).replace(' ago', '')}` : `Checked ${ago(heartbeat.lastTick, now)}`) : 'No check yet'}</b>
        <div class="sub">Automation checks this cohort every 15 minutes.</div>
      </div>
    </div>
  );
}

function Overview(p: ReadyProps) {
  const [showSetup, setShowSetup] = useState(false);
  const { status, now } = p;
  const tz = tzOf(status), year = yearOf(now, tz), today = todayOf(now, tz);
  const problems = status.problems ?? [];
  const stages = status.cohort?.stages ?? {};
  const amber = Object.values(stages).filter((s) => s === 'problem').length;
  const sched = readSchedule(p);
  const rows = scheduleRows(status, sched, now, tz);
  const start = termStart(status, sched, now);
  const end = sched?.end ?? addDays(start, 7 * (status.cohort?.weeks ?? 15) - 3);
  const sunday = addDays(today, (7 - zoned(today).dow) % 7);
  const late = (status.releases ?? []).filter((r) => r.state === 'late');
  const s = status.students;
  const ops = useOperations(status.operations, p.cohort.org);
  const fixOf = (stage: string) => {
    const pr = problems.find((x) => x.stage === stage);
    const h = pr && fixHref(pr);
    return h ? <a class="btn small" href={h}>Fix</a> : null;
  };
  return (
    <>
      <Crumbs items={[{ t: 'All courses', href: '#home' }, { t: cohortName(p) }]} />
      <div class="page-head">
        <div>
          <h1>{cohortName(p)}</h1>
          <p class="lede">
            {status.cohort ? <span>Week {status.cohort.week} of {status.cohort.weeks}.</span> : null}
            {amber ? <span class="amber">Setup done, but {amber} {amber > 1 ? 'stages have a problem' : 'stage has a problem'}</span> : <span>Setup complete</span>}
            {status.cohort?.archive_date ? <span>; archive scheduled {fmtDay(status.cohort.archive_date, tz, year)}.</span> : null}
            <button class="textlink" type="button" aria-expanded={showSetup} onClick={() => setShowSetup(!showSetup)}>{showSetup ? 'Hide setup' : 'Show setup'}</button>
          </p>
        </div>
        <div class="actions"><Probs n={problems.length} /><CheckNow p={p} /><MoreMenu p={p} /></div>
      </div>
      <Help title="What happens here" doc="07-schedule-releases.md">
        <p>This week lists what will happen without you. Problems lists what will not happen until you fix it; each Fix opens the editor at the entry at fault.</p>
        <p>Check now re-reads every file and re-runs every check; automation does the same every 15 minutes.</p>
      </Help>
      <div class="stack">
        {showSetup ? (
          <section class="panel section">
            <div class="section-head"><h2>Setup</h2><Legend /></div>
            <Rail scope="cohort" stages={stages} problems={problems} acts={{
              K3: <a class="btn small quiet" href="#staff">Staff</a>, K4: fixOf('K4'), K5: fixOf('K5'),
              K6: <a class="btn small quiet" href="#site">Site</a>, K7: <a class="btn small quiet" href="#archive">Archive</a>,
            }} />
          </section>
        ) : null}
        <section class="panel section">
          <div class="section-head"><h2>Term</h2><span class="meta">{fmtDay(start, tz, year)} to {fmtDay(end, tz, year)}, {tz}</span></div>
          <TermStrip status={status} rows={rows} start={start} now={now} />
        </section>
        <section class="section">
          <div class="problems-head"><h2>Problems</h2><span class="footnote">What will not happen until you fix it.</span></div>
          <ProblemCards list={problems} cohort />
        </section>
        <div class="grid-2">
          <section class="panel section">
            <div class="section-head"><h2>This week</h2><span class="meta">{fmtDay(today, tz).replace(/ \w+$/, '')} to {fmtDay(sunday, tz, year)}</span></div>
            <WeekItems status={status} p={p} />
            <p class="overdue">{late.length ? `${late.length} release${late.length > 1 ? 's are' : ' is'} late: ${late.map((r) => releaseIdent(r, status.releases ?? [])).join(', ')}.` : 'Nothing overdue.'}</p>
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
                {s.rows > s.codes_sent ? ` ${s.rows - s.codes_sent} ${s.rows - s.codes_sent > 1 ? 'have' : 'has'} no code (see Problems).` : ''}
              </p>
            ) : null}
          </section>
          <section class="panel section">
            <div class="section-head">
              <h2>Automation</h2>
              <span class="actions"><OpOpen def={checkAccess(cohortScope(p))} cls="btn small quiet" label="Check staff access" /><a class="btn small quiet" href="#operations">All operations</a></span>
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
    <WithStatus props={p} title={cohortName(p)} crumbs={[{ t: 'All courses', href: '#home' }, { t: cohortName(p) }]}>
      {(r) => <Overview {...r} />}
    </WithStatus>
  );
}

