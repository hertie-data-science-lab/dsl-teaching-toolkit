// S16 Assignments index and S10 Assignment detail.

import { ASSIGNMENT_WORD, assignmentIdent, assignmentTitle, fmtDay, fmtTime, fmtWhen } from '../model/format';
import type { Assignment, AssignmentState, Status } from '../model/types';
import { Check } from '../ui/icons';
import { collect, handout, returnMarks, updateCopies, type AsgRef } from '../ops/defs';
import { OpButtons, OpOpen } from '../ops/Panel';
import { Crumbs, Help, ProblemCards } from '../ui/bits';
import { asgSummary } from './Cohort';
import { WithStatus, cohortCrumbs, cohortName, cohortScope, todayOf, tzOf, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';

function nextDate(a: Assignment, tz: string, year: number): string {
  switch (a.state) {
    case 'open':
      return `Due ${fmtWhen(a.due, tz, year)}`;
    case 'late_window':
      return `Late work until ${fmtWhen(a.late_until, tz, year)}`;
    case 'marking':
    case 'returned':
      return a.solution_shown ? `Solution shown ${fmtDay(a.solution_shown, tz, year)}` : '';
    default:
      return a.handout ? `Hands out ${fmtWhen(a.handout, tz, year)}` : 'Hand out by hand';
  }
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
            {list.length === 1 ? 'One' : list.length} this term.{open ? ` ${open === 1 ? 'One is' : `${open} are`} open.` : ''}{marking ? ` ${marking === 1 ? 'One is' : `${marking} are`} being marked.` : ''}
          </p>
        </div>
        <div class="actions"><a class="btn" href={`?course=${p.course.org}#new-assignment-1`}>New assignment</a></div>
      </div>
      <Help title="How assignments move" doc="09-release-assignment-to-cohort.md">
        <p>Declared, then open at hand out, then the late window after the due date, then marking, then returned. Dates live in the schedule; settings live on the assignment template.</p>
      </Help>
      <div class="table-wrap">
        <table class="grid" style="min-width:720px">
          <thead><tr><th>Assignment</th><th>State</th><th>Next date</th><th>Progress</th><th>Problem</th></tr></thead>
          <tbody>
            {list.map((a) => (
              <tr>
                <td>
                  <a class="rowlink" href={`#assignment-${a.slug}`}>{assignmentTitle(a)}</a><br />
                  <span class="footnote">{a.teams !== null && a.teams !== undefined ? 'In teams' : 'Alone'}</span>
                </td>
                <td><span class={`chip ${a.state === 'open' ? 'asg' : ''}`}>{ASSIGNMENT_WORD[a.state]}</span></td>
                <td class="num">{nextDate(a, tz, year)}</td>
                <td class="num">{asgSummary(a, tz, year).split('; ').pop()}</td>
                <td>{a.problem ? <span class="chip bad">Assignment template has a problem</span> : <span class="footnote">None</span>}</td>
              </tr>
            ))}
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

function Detail(p: ReadyProps & { a: Assignment }) {
  const { status, now, a } = p;
  const tz = tzOf(status), year = yearOf(now, tz), today = todayOf(now, tz);
  const cur = stepOf(a.state), group = a.teams !== null && a.teams !== undefined;
  const handedOut = cur >= 2;
  const subs = [
    'In the schedule',
    group ? (cur === 1 ? `${a.teams} teams so far` : cur > 1 ? 'Closed' : 'Before hand out') : 'Skipped: students work alone',
    cur === 2 ? `${a.submissions} of ${a.units} in` : cur > 2 ? `Closed ${fmtDay(a.due, tz, year)}` : `Opens ${a.handout ? fmtDay(a.handout, tz, year) : 'at hand out'}`,
    cur === 3 ? `Until ${fmtDay(a.late_until, tz, year)}` : cur > 3 ? `Closed ${fmtDay(a.late_until, tz, year)}` : `Opens after ${fmtDay(a.due, tz, year)}`,
    cur === 4 ? `${a.marks.filled} of ${a.marks.total} marked` : cur > 4 ? 'Done' : `Opens after ${fmtDay(a.late_until, tz, year)}`,
    cur === 5 ? 'Returned' : 'Opens after marks are returned',
  ];
  const dueIn = a.due ? Math.round((Date.parse(a.due) - now) / 864e5) : null;
  const tplProblems = (status.problems ?? []).filter((x) => x.fix?.screen === 'template' && x.fix.entry === a.slug);
  const row = (cls: string, state: string, why: string, ops?: preact.ComponentChildren) => (
    <li class={cls}><span class="sa-state">{state}</span><div class="sa-body"><span class="sa-why">{why}</span>{ops}</div></li>
  );
  const lede =
    a.state === 'open' ? `Open. Due ${fmtDay(a.due, tz, year)} at ${fmtTime(a.due, tz)}; ${a.submissions} of ${a.units} submitted so far.`
    : a.state === 'late_window' ? `Late window. Late work until ${fmtDay(a.late_until, tz, year)}; ${a.submissions} of ${a.units} submitted.`
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
      <Crumbs items={cohortCrumbs(p, assignmentIdent(a.slug), [{ t: 'Assignments', href: '#assignments' }])} />
      <div class="page-head">
        <div><h1>{assignmentTitle(a)}</h1><p class="lede">{lede}</p></div>
        <div class="actions"><a class="btn quiet" href={`#schedule-${a.slug}`}>Edit dates</a></div>
      </div>
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
        <section class="panel" aria-label="Dates">
          <div class="dates">
            <div><div class="l">Hand out</div><div class="v">{a.handout ? fmtWhen(a.handout, tz, year) : 'By hand'}</div><div class="s">{handedOut ? `${a.units} repos created` : a.handout ? 'scheduled' : 'from this page'}</div></div>
            <div><div class="l">Due</div><div class="v">{fmtWhen(a.due, tz, year)}</div><div class="s">{dueIn !== null && dueIn >= 0 && a.state === 'open' ? `in ${dueIn} day${dueIn === 1 ? '' : 's'}` : a.due && a.due.slice(0, 10) < today ? 'passed' : ''}</div></div>
            <div><div class="l">Late work until</div><div class="v">{fmtWhen(a.late_until, tz, year) || 'No late work'}</div><div class="s">with the late penalty</div></div>
            <div><div class="l">Solution shown</div><div class="v">{a.solution_shown ? fmtWhen(a.solution_shown, tz, year) : 'Not shown'}</div><div class="s">{a.solution_shown ? 'in materials' : 'off for this assignment'}</div></div>
          </div>
        </section>
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
            {group ? row(cur === 1 ? 'now' : 'past', 'Teams forming', 'Students form teams on the student site; you can assign the rest.', <div class="sa-op"><a class="btn small quiet" href={`#teams-${a.slug}`}>Open teams</a></div>) : null}
            {row(cur === 2 || cur === 3 ? 'now' : cur > 3 ? 'past' : 'later', 'Open, late window',
              cur < 2 ? 'Opens after hand out.' : cur > 3 ? `Closed ${fmtDay(a.late_until, tz, year)}.` : 'Update every copy pushes an assignment template file to every student and posts a note on each receipts thread. Collect now pulls the latest work.',
              cur === 2 || cur === 3 ? (
                <>
                  <div class="sa-op"><span class="opname">Update every copy</span><OpButtons def={updateCopies(scope, ref, templateFiles)} small /></div>
                  <div class="sa-op"><span class="opname">Collect now</span><OpOpen def={collect({ ...scope }, { ...ref, when: a.due ? `Due ${fmtDay(a.due, tz, year)}` : ref.when })} cls="btn small" label="Collect now" /></div>
                </>
              ) : null)}
            {row(cur === 4 ? 'now' : cur > 4 ? 'past' : 'later', 'Marking',
              cur < 4 ? `Opens after late work closes${a.late_until ? `, ${fmtDay(a.late_until, tz, year)}` : ''}.` : 'Marks and feedback go to students; your private notes do not.',
              cur === 4 ? (
                <>
                  <div class="sa-op"><span class="opname">Marks</span><a class="btn small quiet" href={`#marks-${a.slug}`}>Open marks</a></div>
                  <div class="sa-op"><span class="opname">Return marks</span><OpButtons def={returnMarks(scope, ref, a.marks.filled)} small /></div>
                </>
              ) : null)}
            {row(cur === 5 ? 'now' : 'later', 'Returned', cur === 5 ? 'Marks are with students. Changed marks can be returned again.' : 'Opens after marks are returned. Changed marks can then be returned again.',
              cur === 5 ? (
                <>
                  <div class="sa-op"><span class="opname">Marks</span><a class="btn small quiet" href={`#marks-${a.slug}`}>Open marks</a></div>
                  <div class="sa-op"><span class="opname">Return changed marks</span><OpButtons def={returnMarks(scope, ref, a.marks.filled)} small label="Return changed marks" /></div>
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
        return a ? <Detail {...r} a={a} /> : <NotFound {...r} what={`No assignment called ${p.entry} in ${cohortName(p)}.`} back="#assignments" />;
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
