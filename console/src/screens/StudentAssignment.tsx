// One assignment as a student sees it (decision 0035 rule 6): the 0.9.0 site's assignment page,
// its blocks in its order and wording (the dates and points, the two numbered team steps with
// the teams table, the submission callout with the shape sentence, cutoff sentence and late
// rule, the brief, the shape note), merged with what the console knows: the state chip, the
// student's repo with its Open button, team and contributions rows, "Pull before you
// continue", every comment on Submission receipts, and the mark with its feedback once returned
// (rule 7). Joining a team happens in place (rule 8). `AssignmentBody` is the page's content,
// and each card on the Assignments tab renders the same body.

import { useState } from 'preact/hooks';
import { hostOf } from '../model/cascade';
import { fmtDay, fmtWhen } from '../model/format';
import { isMarked, patchNotes, repoUrl, type MarkEntry, type Mine, type Receipts, type ThreadKind } from '../model/mine';
import { DEFAULT_TIMEZONE } from '../model/policy';
import { MY_STATE_WORD, STUDENT_CHOICE, myState, type MyState, type SemesterAssignment, type SemesterFacts } from '../model/student';
import { formingAt, closesWords } from '../model/week';
import { studentHref } from '../router';
import { Md } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { Ext } from '../ui/icons';
import { OpenButton } from '../ui/OpenButton';
import { GhMd, LazyFold } from '../ui/rendered';
import { JoinRequests, TeamForm, TeamList } from './StudentJoin';

/** The link to an assignment's own page. */
export const assignmentHref = (org: string, slug: string) => studentHref(org, `assignment-${slug}`);

/** The state chip's words: the mark itself once returned ("returned 18 / 20"). */
export function stateWord(st: MyState, e: MarkEntry | undefined): string {
  if (st !== 'returned' || !e?.finalGrade) return MY_STATE_WORD[st];
  return `returned ${e.finalGrade}${e.maxPoints ? ` / ${e.maxPoints}` : ''}`;
}

export function StateChip({ st, entry }: { st: MyState; entry?: MarkEntry }) {
  return <span class={`chip ${st === 'returned' ? 'ok' : st === 'late_window' ? 'amber' : st === 'open' ? 'asg' : ''}`}>{stateWord(st, entry)}</span>;
}

export interface BodyProps {
  org: string;
  a: SemesterAssignment;
  mine: Mine | null;
  now: number;
  tz: string;
  studentView: boolean;
  /** The person's role could not be read: promise them nothing. */
  unknownRole?: boolean;
  /** Receipts per repo; undefined while they are read. */
  receipts?: Record<string, Receipts | null>;
  /** The signed-in login, for the repo and folder names the sentences spell. */
  login: string;
  /** On the page the brief is shown; on a card it is folded. */
  page?: boolean;
}

/** The repo the shape sentences name: the student's own once found, else the shape's name for it. */
function repoName(a: SemesterAssignment, u: Mine['units'][string] | undefined, handle: string): string {
  if (u?.repo) return u.repo;
  if (a.submitVia === 'shared_dropbox_repo') return `${a.slug}-submissions`;
  return a.group ? `${a.slug}-${u?.team ?? '<your-team>'}` : `${a.slug}-${handle}`;
}

const PUBLIC = 'assignment-repo-public';

/** The 0.9.0 pending sentence: what appears once the assignment is handed out. */
function pendingSentence(a: SemesterAssignment, repo: string) {
  const coming = a.submitVia === 'external' ? <>the brief appears here when it is</>
    : a.submitVia === 'shared_dropbox_repo' ? <>the <code>{a.slug}-submissions</code> drop box appears when it is</>
    : <>your {a.shape === PUBLIC ? 'public' : 'private'} <code>{repo}</code> repo appears when it is</>;
  return <p class="a-pending"><em><b>{a.title} is not yet released</b> - {coming}.</em></p>;
}

/** The 0.9.0 header lines: hand-out, due (bold), points. */
function DateLines({ a, tz, year }: { a: SemesterAssignment; tz: string; year: number }) {
  const tbc = a.tbc ? ' (TBC)' : '';
  return (
    <div class="a-dates">
      {a.handout ? <p>{a.handedOut ? 'Released on' : 'Hands out on'} {fmtDay(a.handout, tz, year)}{tbc}</p> : null}
      {a.due ? <p class="a-due"><b>Due {fmtWhen(a.due, tz, year)}{tbc}</b></p> : null}
      {a.maxPoints ? <p>Worth {a.maxPoints} points</p> : null}
      {a.solutionShown ? <p class="footnote">Solution shown {fmtWhen(a.solutionShown, tz, year)}</p> : null}
    </div>
  );
}

export function AssignmentBody({ org, a, mine, now, tz, studentView, unknownRole = false, receipts, login, page = false }: BodyProps) {
  const year = new Date(now).getFullYear();
  const auditor = mine?.auditor === true || unknownRole;
  const own = !studentView && !auditor;
  const u = own ? mine?.units[a.slug] : undefined;
  const handle = own && login ? login : '<your-handle>';
  const repo = repoName(a, u, handle);
  const rc = u?.repo ? receipts?.[u.repo] : undefined;
  const patch = own ? patchNotes(rc).at(-1) : undefined;
  const st = myState(a, isMarked(mine?.gradebook ?? null, a.slug), now, tz);
  const past = st === 'marking' || st === 'returned';
  const entry = own ? mine?.gradebook?.entries[a.slug] : undefined;
  const forming = a.group && formingAt(a.teamFormation, now, tz);
  const steps = forming && !auditor;
  const inTeam = !!u?.team;
  const brief = a.brief ? <GhMd src={a.brief} context={u?.repo ? `${org}/${u.repo}` : undefined} /> : null;
  return (
    <div class="a-body">
      <DateLines a={a} tz={tz} year={year} />
      {steps ? <TeamSteps org={org} a={a} mine={own ? mine : null} studentView={studentView} tz={tz} now={now} /> : null}
      {!forming && a.group && a.teams.length && !auditor ? <TeamList a={a} current={u?.team ?? null} /> : null}
      {!a.handedOut ? pendingSentence(a, repo) : (
        <section class="callout callout-submit" aria-label="Hand in">
          {steps ? (
            <h3 class="a-step" aria-disabled={own && !inTeam ? 'true' : undefined}>
              <span class="step-num">2</span> Open your submission repo
              {own && !inTeam ? <span class="footnote a-step-hint"> Form or join a team first.</span> : null}
            </h3>
          ) : null}
          {auditor ? <p class="footnote">{unknownRole ? 'Could not read your role: your repo, team and receipts are not shown.' : 'As an auditor you hand in no work for this assignment.'}</p> : (
            <>
              <SubmitSentence a={a} repo={repo} folder={a.group ? (u?.team ?? '<your-team>') : handle} />
              {a.cutoffSentence && a.submitVia !== 'external' ? <p>{a.cutoffSentence}</p> : null}
              {a.lateRule && a.submitVia !== 'external' ? <p>Late work: {a.lateRule}{a.lateCutoff && a.lateCutoff !== a.due ? ` (until ${fmtWhen(a.lateCutoff, tz, year)})` : ''}.</p> : null}
              {patch ? <div class="note" role="note"><b>Pull before you continue.</b> <span class="footnote">{fmtWhen(patch.when, tz, year)}</span><Md src={patch.text} /></div> : null}
              {studentView ? <p class="footnote">A student’s repo, team and receipts show here.</p>
                : <dl class="kv"><MyUnitRows org={org} a={a} mine={mine} receipts={rc} loading={u?.repo ? receipts === undefined : false} tz={tz} year={year} /></dl>}
            </>
          )}
        </section>
      )}
      {rc && rc.thread.length ? <ThreadView receipts={rc} tz={tz} year={year} /> : null}
      {brief ? (page ? <article class="a-brief">{brief}</article> : <LazyFold summary="The brief">{() => brief}</LazyFold>) : null}
      {a.shapeNote && a.handedOut && !auditor ? <p class="shape-note"><em>{a.shapeNote}</em></p> : null}
      {a.shape === STUDENT_CHOICE && past && u?.repo ? (
        <p class="footnote">The late cutoff has passed: you may make {u.repo} public, if you want it in your portfolio, under <a href={`${repoUrl(org, u.repo)}/settings`} target="_blank" rel="noopener">Settings, Danger zone <Ext /></a>.</p>
      ) : null}
      {entry?.finalGrade ? (
        <section class="a-mark" aria-label="Mark">
          <div class="a-head"><h3>Mark</h3><span class="mark-big">{entry.finalGrade}{entry.maxPoints ? <span> / {entry.maxPoints}</span> : null}</span></div>
          <MarkBody e={entry} />
        </section>
      ) : null}
    </div>
  );
}

/** The 0.9.0 callout's one sentence for the shape. */
function SubmitSentence({ a, repo, folder }: { a: SemesterAssignment; repo: string; folder: string }) {
  if (a.submitVia === 'external') {
    return (
      <>
        {a.submitUrl ? <p class="actions"><a class="btn" href={a.submitUrl} target="_blank" rel="noopener">Submit on {hostOf(a.submitUrl) || a.submitUrl} <Ext /></a></p> : null}
        <p>Handed in outside GitHub.{a.submitUrl ? '' : ' See the brief.'}</p>
      </>
    );
  }
  if (a.submitVia === 'shared_dropbox_repo') return <p>Push your work into the <code>{folder}/</code> folder of <code>{repo}</code> - that push is your submission.</p>;
  return <p>Your work goes in your {a.shape === PUBLIC ? '' : 'private '}repo <code>{repo}</code>. Clone it, commit as you go, and push to <code>main</code> - that push is your submission.</p>;
}

/** Step 1 of a self-select group assignment while formation is open: who you are with, the teams so far, and the form in place. */
function TeamSteps({ org, a, mine, studentView, tz, now }: { org: string; a: SemesterAssignment; mine: Mine | null; studentView: boolean; tz: string; now: number }) {
  const [open, setOpen] = useState(false);
  const [picked, setPicked] = useState('');
  const [sent, setSent] = useState(0);
  const team = mine?.units[a.slug]?.team ?? null;
  const cap = a.teamFormation?.cap;
  const closes = a.teamFormation?.closes;
  return (
    <section class="callout team-step-form" aria-label="Form your team">
      <h3 class="a-step"><span class="step-num">1</span> Form your team</h3>
      {studentView ? null : <p class="team-found">{team ? <>✓ You’re in team <b>{team}</b></> : 'Not in a team yet'}</p>}
      <p>
        You pick your own team for this assignment{cap ? `, of up to ${cap} people` : ''}. Start a team or join one with Join or create a team{closes ? ` - team formation closes on ${closesWords(closes, tz)}` : ''}.
        {a.submitVia === 'assignment_repo' ? ' Your team’s submission repo appears once the team does.' : ''}
      </p>
      {a.teams.length ? <TeamList a={a} current={team} onPick={open ? setPicked : undefined} picked={picked} />
        : <p class="footnote">No teams yet - be the first: choose <em>Create a new team</em>.</p>}
      {studentView ? <p class="footnote">A student joins or creates a team here; the form opens the semester’s Join team issue.</p> : (
        <>
          <p class="actions"><button class="btn outline" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>Join or create a team</button></p>
          {open ? (
            <div class="stack">
              <TeamForm org={org} a={a} mine={mine} tz={tz} now={now} picked={picked} onSent={() => setSent(sent + 1)} />
              <JoinRequests org={org} sent={sent} />
            </div>
          ) : null}
        </>
      )}
    </section>
  );
}

function MyUnitRows({ org, a, mine, receipts, loading, tz, year }: { org: string; a: SemesterAssignment; mine: Mine | null; receipts: Receipts | null | undefined; loading: boolean; tz: string; year: number }) {
  if (!mine || a.submitVia === 'external') return null;
  const u = mine.units[a.slug];
  const out = [];
  if (u?.repo) {
    out.push(
      <><dt>{u.shared ? 'Shared repo' : 'Your repo'}</dt><dd class="a-repo">
        <a href={repoUrl(org, u.repo)} target="_blank" rel="noopener" title="Open the submission repo on GitHub">{u.repo} <Ext /></a> <OpenButton org={org} repo={u.repo} home={org} small />
        {u.shared ? <span class="footnote"> (your work goes in a folder named after {a.group ? 'your team' : 'you'})</span> : null}
      </dd></>,
    );
  } else if (!(a.group && a.teamFormation)) out.push(<><dt>Your repo</dt><dd class="footnote">Not there yet: your instructors hand it out.</dd></>);
  if (a.group) {
    out.push(<><dt>Your team</dt><dd>{u?.team ? <>{u.team}{u.members?.length ? <span class="footnote">: {u.members.join(', ')}</span> : null}</> : <span class="footnote">No team yet.</span>}</dd></>);
  }
  if (a.group && u?.repo && !u.shared) {
    out.push(<><dt>Contributions</dt><dd>Fill in <a href={`${repoUrl(org, u.repo)}/blob/HEAD/CONTRIBUTIONS.md`} target="_blank" rel="noopener">CONTRIBUTIONS.md <Ext /></a> in your team repo before the deadline.</dd></>);
  }
  if (u?.repo && a.privateRepo) {
    out.push(
      <><dt>Submission receipts</dt><dd>
        {loading ? <span class="footnote">Reading…</span> : receipts ? (
          <>
            <a href={receipts.url} target="_blank" rel="noopener">Open the thread <Ext /></a>
            {receipts.last ? <div class="receipt"><span class="footnote">{fmtWhen(receipts.last.when, tz, year)}</span><Md src={receipts.last.text} /></div> : <span class="footnote"> No receipt yet: the first comes at the deadline.</span>}
          </>
        ) : <span class="footnote">No thread in the repo yet.</span>}
      </dd></>,
    );
  }
  return <>{out}</>;
}

const THREAD_WORD: Record<ThreadKind, string> = { receipt: 'Receipt', patch: 'Files updated', marks: 'Marks', comment: 'Comment' };

/** All of the Submission receipts issue: its own text, then every comment. */
export function ThreadView({ receipts, tz, year }: { receipts: Receipts; tz: string; year: number }) {
  return (
    <details class="fold">
      <summary>Every comment on Submission receipts <span class="cnt">{receipts.thread.length}</span></summary>
      <div class="fold-body">
        {receipts.body ? <div class="receipt"><Md src={receipts.body} /></div> : null}
        {receipts.thread.map((c) => (
          <div class={`receipt r-${c.kind}`}><span class="footnote"><b>{THREAD_WORD[c.kind]}</b>, {fmtWhen(c.when, tz, year)}</span><Md src={c.text} /></div>
        ))}
        <p><a href={receipts.url} target="_blank" rel="noopener">Open Submission receipts on GitHub <Ext /></a></p>
      </div>
    </details>
  );
}

function questionsOf(e: MarkEntry): string[] {
  return [...new Set([...Object.keys(typeof e.score === 'object' && e.score ? e.score : {}), ...Object.keys(e.questionFeedback)])];
}

/** One returned mark: submitted, days late, penalty, team, feedback, by question, team feedback. */
export function MarkBody({ e }: { e: MarkEntry }) {
  const qs = questionsOf(e);
  return (
    <>
      <dl class="kv">
        {e.submitted ? <><dt>Submitted</dt><dd>{e.submitted}</dd></> : null}
        {e.daysLate && e.daysLate !== '0' ? <><dt>Days late</dt><dd>{e.daysLate}</dd></> : null}
        {e.penalty ? <><dt>Late penalty</dt><dd>{e.penalty}</dd></> : null}
        {typeof e.score === 'string' && e.score !== e.finalGrade ? <><dt>Marks</dt><dd>{e.score}</dd></> : null}
        {e.team ? <><dt>Team</dt><dd>{e.team}</dd></> : null}
      </dl>
      {e.feedback ? <div class="fb"><h4>Feedback</h4><Md src={e.feedback} /></div> : null}
      {qs.length ? (
        <div class="fb">
          <h4>By question</h4>
          <ul class="q-list">
            {qs.map((q) => {
              const s = typeof e.score === 'object' && e.score ? e.score[q] : '';
              return <li><b>{q}</b>{s ? <span class="q-score">{s}</span> : null}{e.questionFeedback[q] ? <Md src={e.questionFeedback[q]} /> : null}</li>;
            })}
          </ul>
        </div>
      ) : null}
      {e.teamFeedback ? <div class="fb"><h4>Team feedback</h4><Md src={e.teamFeedback} /></div> : null}
    </>
  );
}

/** `#assignment-<slug>`: the page, with its own head (kicker and title, the state chip). */
export function AssignmentPage({ slug, facts, hint, ...rest }: Omit<BodyProps, 'a' | 'tz' | 'page'> & { slug: string; facts: SemesterFacts; hint: string }) {
  const tz = facts.timezone || DEFAULT_TIMEZONE;
  const a = facts.assignments.find((x) => x.slug.toLowerCase() === slug.toLowerCase());
  if (!a) return <p class="footnote">This semester has no assignment {slug}. <a href={studentHref(rest.org, 'assignments')}>All assignments</a></p>;
  const auditor = rest.mine?.auditor === true || rest.unknownRole;
  const own = !rest.studentView && !auditor;
  const st = myState(a, isMarked(rest.mine?.gradebook ?? null, a.slug), rest.now, tz);
  return (
    <>
      <div class="page-head a-page-head">
        <div>
          {a.subtitle ? <p class="kicker">{a.title}</p> : null}
          <h2 class="h1">{a.subtitle || a.title} <Hint>{hint}</Hint></h2>
        </div>
        {auditor ? null : <StateChip st={st} entry={own ? rest.mine?.gradebook?.entries[a.slug] : undefined} />}
      </div>
      <AssignmentBody {...rest} a={a} tz={tz} page />
    </>
  );
}
