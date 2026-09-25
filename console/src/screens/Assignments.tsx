// S16 Assignments index and S10 Assignment detail: the hub, with tabs Overview | Teams |
// Marks (decision 0008). Teams shows only when the template says teams.

import { readTable } from '../edit/csv';
import { parseRoster } from '../model/people';
import { ASSIGNMENT_WORD, assignmentIdent, assignmentTitle, fmtDay, fmtTime, fmtWhen } from '../model/format';
import type { Assignment, AssignmentState, Status } from '../model/types';
import { Check } from '../ui/icons';
import { collect, handout, returnMarks, updateCopies, type AsgRef } from '../ops/defs';
import { OpButtons, OpOpen } from '../ops/Panel';
import { useEffect } from 'preact/hooks';
import { replaceHash, tabHref, type AssignmentTab } from '../router';
import { Crumbs, Help, ProblemCards } from '../ui/bits';
import { asgSummary } from './Cohort';
import { MarksTab, TeamsTab } from './Marking';
import { AssignmentRun, SemesterDefaults, sheetName } from './RunSettings';
import { WithStatus, cohortCrumbs, cohortName, cohortScope, tzOf, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';
import { CONFIG_REPO } from '../model/names';

function nextDate(a: Assignment, tz: string, year: number): string {
  switch (a.state) {
    case 'open':
      return `Due ${fmtWhen(a.due, tz, year)}`;
    case 'late_window':
      return `Late work until ${fmtWhen(a.grading_cutoff_datetime, tz, year)}`;
    case 'marking':
    case 'returned':
      return a.solution_shown ? `Solution shown ${fmtDay(a.solution_shown, tz, year)}` : '';
    default:
      return a.handout ? `Hands out ${fmtWhen(a.handout, tz, year)}` : 'Hand out by hand';
  }
}

/** Joined students in no team of `slug` (teams.csv keys on its semester-side name), as the Teams tab counts them; null until both files are read. */
function teamless(p: ReadyProps, slug: string): number | null {
  const roster = p.files.file(p.cohort.org, CONFIG_REPO, 'students.csv');
  const teams = p.files.file(p.cohort.org, CONFIG_REPO, 'teams.csv');
  if (roster.kind === 'loading' || teams.kind === 'loading') return null;
  const joined = roster.kind === 'ready' ? parseRoster(roster.text).rows.filter((s) => s.handle) : [];
  const placed = new Set(
    (teams.kind === 'ready' ? readTable(teams.text).rows : [])
      .filter((r) => (r.assignment ?? '').trim() === sheetName(p, slug) && (r.github_handle ?? '').trim())
      .map((r) => r.github_handle.trim().toLowerCase()),
  );
  return joined.filter((s) => !placed.has(s.handle.toLowerCase())).length;
}

function Index(p: ReadyProps) {
  const { status, now } = p;
  const tz = tzOf(status), year = yearOf(now, tz);
  const list = status.assignments ?? [];
  const open = list.filter((a) => a.state === 'open' || a.state === 'late_window').length;
  const marking = list.filter((a) => a.state === 'marking').length;
  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Assignments')} />
      <div class="page-head">
        <div>
          <h1>Assignments</h1>
          <p class="lede">
            {list.length === 1 ? 'One' : list.length} this semester.{open ? ` ${open === 1 ? 'One is' : `${open} are`} open.` : ''}{marking ? ` ${marking === 1 ? 'One is' : `${marking} are`} being marked.` : ''}
          </p>
        </div>
        <div class="actions"><a class="btn" href={`?course=${p.course.org}#new-assignment-1`}>New assignment</a></div>
      </div>
      <Help title="How assignments move" doc="09-release-assignment-to-cohort.md">
        <p>Declared, then open at hand out, then the late window after the due date, then marking, then returned. Dates live in the schedule; how this semester runs each assignment (teams, late work, who sees each repo) lives in assignments.yml, with the defaults below; what the task is lives on the assignment template.</p>
      </Help>
      <div style="margin-bottom:18px"><SemesterDefaults p={p} /></div>
      <div class="table-wrap">
        <table class="grid" style="min-width:960px">
          <thead><tr><th>Assignment</th><th>State</th><th>Next date</th><th>Progress</th><th>Teams</th><th>Marked</th><th>Returned</th><th>Problem</th></tr></thead>
          <tbody>
            {list.map((a) => {
              const free = isGroup(a) ? teamless(p, a.slug) : null;
              return (
              <tr>
                <td>
                  <a class="rowlink" href={`#assignment-${a.slug}`}>{assignmentTitle(a)}</a><br />
                  <span class="footnote">{a.teams !== null && a.teams !== undefined ? 'In teams' : 'Alone'}</span>
                </td>
                <td><span class={`chip ${a.state === 'open' ? 'asg' : ''}`}>{ASSIGNMENT_WORD[a.state]}</span></td>
                <td class="num">{nextDate(a, tz, year)}</td>
                <td class="num">{asgSummary(a, tz, year).split('; ').pop()}</td>
                <td class="num">{isGroup(a) ? <>{a.teams} formed{free !== null ? <><br /><span class="footnote">{free} without a team</span></> : null}</> : <span class="footnote">—</span>}</td>
                <td class="num">{a.marks.total ? `${a.marks.filled} / ${a.marks.total}` : <span class="footnote">No marks yet</span>}</td>
                <td>{a.returned ? <span class="chip ok">Yes</span> : 'No'}</td>
                <td>{a.problem ? <span class="chip bad">Assignment template has a problem</span> : <span class="footnote">None</span>}</td>
              </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

export function AssignmentsScreen(p: CohortProps) {
  return <WithStatus props={p} title="Assignments" crumbs={cohortCrumbs(p, 'Assignments')}>{(r) => <Index {...r} />}</WithStatus>;
}

// --------------------------------------------------------------------------- detail

const LIFE: [string, AssignmentState[]][] = [
  ['Declared', ['declared']],
  ['Teams forming', ['teams_forming', 'blocked']],
  ['Open', ['open']],
  ['Late window', ['late_window']],
  ['Marking', ['marking']],
  ['Returned', ['returned']],
];

function stepOf(state: AssignmentState): number {
  return LIFE.findIndex(([, s]) => s.includes(state));
}

export const isGroup = (a: Assignment) => a.teams !== null && a.teams !== undefined;

/** The tab an assignment opens on: Teams while teams form, Marks once marking starts. */
export function defaultTab(a: Assignment): AssignmentTab {
  if ((a.state === 'teams_forming' || a.state === 'blocked') && isGroup(a)) return 'teams';
  if (a.state === 'marking' || a.state === 'returned') return 'marks';
  return 'overview';
}

/** The hash to show instead, when a link asks for the Teams tab of an assignment done alone. */
export function misrouted(a: Assignment, tab: AssignmentTab | undefined): string | null {
  return tab === 'teams' && !isGroup(a) ? tabHref(a.slug, 'overview') : null;
}

/** Writes `to` into the address bar once rendered, so the address names the tab shown. */
function Rehash({ to }: { to: string }) {
  useEffect(() => replaceHash(to), [to]);
  return null;
}

const TAB_NAME: Record<AssignmentTab, string> = { overview: 'Overview', teams: 'Teams', marks: 'Marks' };

function TabBar({ a, cur }: { a: Assignment; cur: AssignmentTab }) {
  const tabs: AssignmentTab[] = isGroup(a) ? ['overview', 'teams', 'marks'] : ['overview', 'marks'];
  return (
    <nav class="tabs" aria-label={`${assignmentIdent(a.slug)} pages`}>
      {tabs.map((t) => <a href={tabHref(a.slug, t)} aria-current={t === cur ? 'page' : undefined}>{TAB_NAME[t]}</a>)}
    </nav>
  );
}

export interface TabProps extends ReadyProps {
  a: Assignment;
  tabs: preact.ComponentChildren;
}

function Overview(p: TabProps) {
  const { status, now, a } = p;
  const tz = tzOf(status), year = yearOf(now, tz);
  const cur = stepOf(a.state), group = isGroup(a);
  const handedOut = cur >= 2;
  const subs = [
    'In the schedule',
    group ? (cur === 1 ? `${a.teams} teams so far` : cur > 1 ? 'Closed' : 'Before hand out') : 'Skipped: students work alone',
    cur === 2 ? `${a.submissions} of ${a.units} in` : cur > 2 ? `Closed ${fmtDay(a.due, tz, year)}` : `Opens ${a.handout ? fmtDay(a.handout, tz, year) : 'at hand out'}`,
    cur === 3 ? `Until ${fmtDay(a.grading_cutoff_datetime, tz, year)}` : cur > 3 ? `Closed ${fmtDay(a.grading_cutoff_datetime, tz, year)}` : `Opens after ${fmtDay(a.due, tz, year)}`,
    cur === 4 ? `${a.marks.filled} of ${a.marks.total} marked` : cur > 4 ? 'Done' : `Opens after ${fmtDay(a.grading_cutoff_datetime, tz, year)}`,
    cur === 5 ? 'Returned' : 'Opens after marks are returned',
  ];
  const tplProblems = (status.problems ?? []).filter((x) => x.fix?.screen === 'template' && x.fix.entry === a.slug);
  const row = (cls: string, state: string, why: string, ops?: preact.ComponentChildren) => (
    <li class={cls}><span class="sa-state">{state}</span><div class="sa-body"><span class="sa-why">{why}</span>{ops}</div></li>
  );
  const lede =
    a.state === 'open' ? `Open. Due ${fmtDay(a.due, tz, year)} at ${fmtTime(a.due, tz)}; ${a.submissions} of ${a.units} submitted so far.`
    : a.state === 'late_window' ? `Late window. Late work until ${fmtDay(a.grading_cutoff_datetime, tz, year)}; ${a.submissions} of ${a.units} submitted.`
    : a.state === 'marking' ? `Marking. ${a.marks.filled} of ${a.marks.total} marked.`
    : a.state === 'returned' ? 'Returned. Marks are with students.'
    : a.state === 'teams_forming' ? `Teams forming. Hands out ${fmtDay(a.handout, tz, year)} at ${fmtTime(a.handout, tz)}.`
    : a.state === 'blocked' ? 'Blocked: some students have no team, and hand out is due.'
    : a.handout ? `Declared. Hands out ${fmtDay(a.handout, tz, year)} at ${fmtTime(a.handout, tz)}.` : 'Declared. You hand it out from this page.';
  const big = cur === 4 || cur === 5 ? a.marks.filled : cur <= 1 ? (a.teams ?? 0) : a.submissions;
  const bigOf = cur === 4 || cur === 5 ? a.marks.total : a.units;
  const scope = cohortScope(p);
  const ref: AsgRef = { slug: a.slug, title: assignmentTitle(a), template: a.template, units: a.units, group, when: a.handout ? `Scheduled ${fmtDay(a.handout, tz, year)}` : 'Hand out by hand' };
  const tree = p.files.tree(p.course.org, a.template);
  const templateFiles = tree.kind === 'ready' ? tree.paths.filter((x) => !x.dir && !x.path.startsWith('.github/')).map((x) => x.path) : [];
  return (
    <>
      <div class="page-head">
        <div><h1>{assignmentTitle(a)}</h1><p class="lede">{lede}</p></div>
      </div>
      {p.tabs}
      <Help title="What happens now" doc="10-grade-and-return-assignments.md">
        <p>
          {a.state === 'open' || a.state === 'late_window'
            ? 'Students push to their own repo until the due date. Late work is accepted with the penalty until late work closes.'
            : a.state === 'marking' ? 'Marks and feedback go to students; notes you keep for yourself do not. Return marks previews first.'
            : a.state === 'teams_forming' || a.state === 'blocked' ? 'Students form teams on the student site; you can assign the rest. Team-less students get their own repo at hand out.'
            : 'Hands out at the scheduled time, or now.'}
        </p>
        <p>Preview never changes anything students see.</p>
      </Help>
      <div class="stack">
        <section class="panel" aria-label="State">
          <ol class="lifeline" aria-label="Assignment state">
            {LIFE.map(([nm], i) => {
              const c = i === 1 && !group ? 'skip' : i < cur ? 'done' : i === cur ? 'now' : 'future';
              return (
                <li class={c} aria-current={i === cur ? 'step' : undefined}>
                  <span class="node">{c === 'done' ? <Check /> : null}</span>
                  <span class="ln">{nm}</span>
                  <span class="ls">{subs[i]}</span>
                </li>
              );
            })}
          </ol>
        </section>
        <AssignmentRun p={p} a={a} group={group} />
        <div class="grid-2">
          <section class="panel section">
            <div class="section-head"><h2>{cur >= 4 ? 'Marking' : cur <= 1 ? (group ? 'Teams' : 'Submissions') : 'Submissions'}</h2></div>
            <div class="big">{big} <small>/ {bigOf}</small></div>
            <div class="meter"><i style={`width:${bigOf ? ((big / bigOf) * 100).toFixed(1) : 0}%;background:var(--asg-ink)`} /></div>
            <p class="footnote">
              {cur === 2 || cur === 3 ? `${a.units - a.submissions} have not pushed since hand out.` : cur >= 4 ? `${a.marks.total - a.marks.filled} still to mark.` : group ? 'Teams form on the student site until hand out.' : 'Nothing handed out yet.'}
            </p>
          </section>
          <section class="panel section">
            <h2>Assignment template</h2>
            {tplProblems.length ? (
              <ProblemCards list={tplProblems} />
            ) : (
              <div class="check-line ok"><Check /><span><b>Assignment template ready.</b> Brief written; settings check out.</span></div>
            )}
            <a class="textlink" href={`#template-${a.slug}`}>Assignment template settings</a>
          </section>
        </div>
        <section class="panel section">
          <div class="section-head"><h2>What you can do, by state</h2><span class="meta">Preview never changes anything students see</span></div>
          <ul class="state-actions">
            {row(cur <= 1 ? 'now' : 'past', 'Declared', handedOut ? `Handed out ${fmtWhen(a.handout, tz, year)} to ${a.units} ${group ? 'teams' : 'students'}.` : 'Hands out at its time, or now.',
              handedOut ? null : <div class="sa-op"><span class="opname">Hand out now</span><OpButtons def={handout(scope, ref)} small /></div>)}
            {group ? row(cur === 1 ? 'now' : 'past', 'Teams forming', 'Students form teams on the student site; you can assign the rest.', <div class="sa-op"><a class="btn small quiet" href={tabHref(a.slug, 'teams')}>Open teams</a></div>) : null}
            {row(cur === 2 || cur === 3 ? 'now' : cur > 3 ? 'past' : 'later', 'Open, late window',
              cur < 2 ? 'Opens after hand out.' : cur > 3 ? `Closed ${fmtDay(a.grading_cutoff_datetime, tz, year)}.` : 'Update every copy pushes an assignment template file to every student and posts a note on each Submission receipts issue. Collect now pulls the latest work.',
              cur === 2 || cur === 3 ? (
                <>
                  <div class="sa-op"><span class="opname">Update every copy</span><OpButtons def={updateCopies(scope, ref, templateFiles)} small /></div>
                  <div class="sa-op"><span class="opname">Collect now</span><OpOpen def={collect({ ...scope }, { ...ref, when: a.due ? `Due ${fmtDay(a.due, tz, year)}` : ref.when })} cls="btn small" label="Collect now" /></div>
                </>
              ) : null)}
            {row(cur === 4 ? 'now' : cur > 4 ? 'past' : 'later', 'Marking',
              cur < 4 ? `Opens after late work closes${a.grading_cutoff_datetime ? `, ${fmtDay(a.grading_cutoff_datetime, tz, year)}` : ''}.` : 'Marks and feedback go to students; your private notes do not.',
              cur === 4 ? (
                <>
                  <div class="sa-op"><span class="opname">Marks</span><a class="btn small quiet" href={tabHref(a.slug, 'marks')}>Open marks</a></div>
                  <div class="sa-op"><span class="opname">Return marks</span><OpButtons def={returnMarks(scope, ref, a.marks.filled, sheetName(p, a.slug))} small /></div>
                </>
              ) : null)}
            {row(cur === 5 ? 'now' : 'later', 'Returned', cur === 5 ? 'Marks are with students. Changed marks can be returned again.' : 'Opens after marks are returned. Changed marks can then be returned again.',
              cur === 5 ? (
                <>
                  <div class="sa-op"><span class="opname">Marks</span><a class="btn small quiet" href={tabHref(a.slug, 'marks')}>Open marks</a></div>
                  <div class="sa-op"><span class="opname">Return changed marks</span><OpButtons def={returnMarks(scope, ref, a.marks.filled, sheetName(p, a.slug))} small label="Return changed marks" /></div>
                </>
              ) : null)}
          </ul>
        </section>
      </div>
    </>
  );
}

export function AssignmentScreen(p: CohortProps) {
  const title = p.entry ? assignmentIdent(p.entry) : 'Assignment';
  return (
    <WithStatus props={p} title={title} crumbs={cohortCrumbs(p, title, [{ t: 'Assignments', href: '#assignments' }])}>
      {(r) => {
        const a = (r.status.assignments ?? []).find((x) => x.slug === p.entry);
        if (!a) return <NotFound {...r} what={`No assignment called ${p.entry} in ${cohortName(p)}.`} back="#assignments" />;
        const moved = misrouted(a, p.tab);
        const tab = moved ? 'overview' : p.tab ?? defaultTab(a);
        const tp: TabProps = { ...r, a, tabs: <TabBar a={a} cur={tab} /> };
        return (
          <>
            {moved ? <Rehash to={moved} /> : null}
            <Crumbs items={cohortCrumbs(p, tab === 'overview' ? assignmentIdent(a.slug) : TAB_NAME[tab], [{ t: 'Assignments', href: '#assignments' }, ...(tab === 'overview' ? [] : [{ t: assignmentIdent(a.slug), href: tabHref(a.slug, 'overview') }])])} />
            {tab === 'teams' ? <TeamsTab {...tp} /> : tab === 'marks' ? <MarksTab {...tp} /> : <Overview {...tp} />}
          </>
        );
      }}
    </WithStatus>
  );
}

export function NotFound({ what, back }: { what: string; back: string; status?: Status }) {
  return (
    <section class="panel section stub">
      <h2>Not found</h2>
      <p>{what}</p>
      <p><a class="textlink" href={back}>Back</a></p>
    </section>
  );
}
