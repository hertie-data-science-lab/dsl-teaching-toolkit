// The student's Assignments and Marks (decision 0011 rule 2): one card per assignment with
// the student's repo, team, receipts and the brief; the marks and feedback returned from
// their private gradebook. Each assignment states its own late rule; there is no course-wide
// late-work section (decision 0035 rule 6).

import { hostOf } from '../model/cascade';
import { DEFAULT_TIMEZONE } from '../model/policy';
import { fmtWhen } from '../model/format';
import { gradebookUrl, isMarked, patchNotes, repoUrl, type Gradebook, type MarkEntry, type Mine, type Receipts, type ThreadKind } from '../model/mine';
import { MY_STATE_WORD, STUDENT_CHOICE, instant, myState, type SemesterAssignment, type SemesterFacts } from '../model/student';
import { studentHref } from '../router';
import { Md } from '../ui/bits';
import { Ext } from '../ui/icons';
import { GhMd, LazyFold } from '../ui/rendered';

// --------------------------------------------------------------------------- Assignments

const SUBMIT_WORD: Record<string, string> = {
  assignment_repo: 'Push to your repo', shared_dropbox_repo: 'Push to your folder in the shared repo', external: 'Handed in outside GitHub',
};

export function AssignmentsView({ org, facts, mine, now, studentView, receipts, unknownRole = false }: {
  org: string; facts: SemesterFacts; mine: Mine | null; now: number; studentView: boolean;
  /** The person's role could not be read: promise them nothing. */
  unknownRole?: boolean;
  /** Receipts per repo; undefined while they are read. */
  receipts?: Record<string, Receipts | null>;
}) {
  const tz = facts.timezone || DEFAULT_TIMEZONE;
  const year = new Date(now).getFullYear();
  if (!facts.assignments.length) return <p class="footnote">No assignments are planned yet.</p>;
  const list = [...facts.assignments].sort((a, b) => (a.due ? instant(a.due, tz) : Infinity) - (b.due ? instant(b.due, tz) : Infinity));
  return (
    <div class="stack">
      {list.map((a) => {
        const auditor = mine?.auditor === true || unknownRole;
        const st = myState(a, isMarked(mine?.gradebook ?? null, a.slug), now, tz);
        const u = mine?.units[a.slug];
        const rc = u?.repo ? receipts?.[u.repo] : undefined;
        const tbc = a.tbc ? ' (TBC)' : '';
        const patch = patchNotes(rc).at(-1);
        const past = st === 'marking' || st === 'returned';
        return (
          <section class="panel section a-card" aria-label={a.title}>
            <div class="a-head">
              <h2>{a.title}{a.subtitle ? <span>{a.subtitle}</span> : null}</h2>
              {auditor ? null : <span class={`chip ${st === 'returned' ? 'ok' : st === 'late_window' ? 'amber' : st === 'open' ? 'asg' : ''}`}>{MY_STATE_WORD[st]}</span>}
            </div>
            {patch && !auditor ? <div class="note" role="note"><b>Pull before you continue.</b> <span class="footnote">{fmtWhen(patch.when, tz, year)}</span><Md src={patch.text} /></div> : null}
            <dl class="kv">
              {a.handout ? <><dt>Handed out</dt><dd>{fmtWhen(a.handout, tz, year)}{tbc}</dd></> : null}
              {a.due ? <><dt>Due</dt><dd>{fmtWhen(a.due, tz, year)}{tbc}</dd></> : null}
              {a.lateCutoff && a.lateCutoff !== a.due ? <><dt>Late cutoff</dt><dd>{fmtWhen(a.lateCutoff, tz, year)}{tbc}</dd></> : null}
              {a.lateRule ? <><dt>Late work</dt><dd>{a.lateRule}</dd></> : null}
              {a.maxPoints ? <><dt>Out of</dt><dd>{a.maxPoints} points</dd></> : null}
              {a.solutionShown ? <><dt>Solution shown</dt><dd>{fmtWhen(a.solutionShown, tz, year)}</dd></> : null}
              {a.submitVia && !auditor ? <><dt>How to hand in</dt><dd>{SUBMIT_WORD[a.submitVia]}{a.submitVia === 'external' && a.submitUrl ? <>: <a href={a.submitUrl} target="_blank" rel="noopener">{hostOf(a.submitUrl) || a.submitUrl} <Ext /></a></> : null}</dd></> : null}
              {studentView ? <><dt>Yours</dt><dd class="footnote">A student’s repo, team and receipts show here.</dd></>
                : unknownRole ? <><dt>Yours</dt><dd class="footnote">Could not read your role: your repo, team and receipts are not shown.</dd></>
                : auditor ? <><dt>Yours</dt><dd class="footnote">As an auditor you hand in no work for this assignment.</dd></>
                : <MyUnitRows org={org} a={a} mine={mine} receipts={rc} loading={u?.repo ? receipts === undefined : false} tz={tz} year={year} />}
            </dl>
            {a.shapeNote && !auditor ? <p class="footnote shape-note">{a.shapeNote}</p> : null}
            {a.shape === STUDENT_CHOICE && past && u?.repo && !auditor ? (
              <p class="footnote">The late cutoff has passed: you may make {u.repo} public, if you want it in your portfolio, under <a href={`${repoUrl(org, u.repo)}/settings`} target="_blank" rel="noopener">Settings, Danger zone <Ext /></a>.</p>
            ) : null}
            {a.cutoffSentence && a.submitVia !== 'external' && !auditor ? <p class="footnote">{a.cutoffSentence}</p> : null}
            {a.brief ? <LazyFold summary="The brief">{() => <GhMd src={a.brief} context={u?.repo ? `${org}/${u.repo}` : undefined} />}</LazyFold> : null}
            {rc && rc.thread.length ? <ThreadView receipts={rc} tz={tz} year={year} /> : null}
          </section>
        );
      })}
    </div>
  );
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


function MyUnitRows({ org, a, mine, receipts, loading, tz, year }: { org: string; a: SemesterAssignment; mine: Mine | null; receipts: Receipts | null | undefined; loading: boolean; tz: string; year: number }) {
  if (!mine || a.submitVia === 'external') return null;
  const u = mine.units[a.slug];
  const out = [];
  if (u?.repo) out.push(<><dt>{u.shared ? 'Shared repo' : 'Your repo'}</dt><dd><a href={repoUrl(org, u.repo)} target="_blank" rel="noopener">{u.repo} <Ext /></a>{u.shared ? <span class="footnote"> (your work goes in a folder named after {a.group ? 'your team' : 'you'})</span> : null}</dd></>);
  else if (a.handedOut && !(a.group && a.teamFormation)) out.push(<><dt>Your repo</dt><dd class="footnote">Not there yet: your instructors hand it out.</dd></>);
  if (a.group) {
    out.push(<><dt>Your team</dt><dd>{u?.team ? <>{u.team}{u.members?.length ? <span class="footnote">: {u.members.join(', ')}</span> : null}</> : a.teamFormation ? <a href={studentHref(org, 'join')}>You have no team yet: join or create one</a> : <span class="footnote">No team yet.</span>}</dd></>);
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

// --------------------------------------------------------------------------- Marks

function questionsOf(e: MarkEntry): string[] {
  const keys = new Set([...Object.keys(typeof e.score === 'object' && e.score ? e.score : {}), ...Object.keys(e.questionFeedback)]);
  return [...keys];
}

export function MarksView({ org, login, facts, gradebook, studentView, loaded = true, auditor = false }: { org: string; login: string; facts: SemesterFacts; gradebook: Gradebook | null; studentView: boolean; loaded?: boolean; auditor?: boolean }) {
  if (studentView) return <p class="footnote">A student’s marks show here, from their private gradebook; your own account has none in this semester.</p>;
  if (!loaded) return null;
  if (auditor && !gradebook) return <p class="footnote">As an auditor you get no marks in this semester.</p>;
  if (!gradebook) return <p class="footnote">No marks yet. They appear here when your instructors return them.</p>;
  const titles = new Map(facts.assignments.map((a) => [a.slug, a.subtitle ? `${a.title}: ${a.subtitle}` : a.title]));
  const slugs = Object.keys(gradebook.entries);
  return (
    <div class="stack">
      {gradebook.total ? <p class="lede"><b>Semester total:</b> {gradebook.total}</p> : null}
      {!slugs.length ? <p class="footnote">No marks yet. They appear here when your instructors return them.</p> : null}
      {slugs.map((slug) => {
        const e = gradebook.entries[slug];
        const qs = questionsOf(e);
        return (
          <section class="panel section" aria-label={titles.get(slug) ?? slug}>
            <div class="a-head">
              <h2>{titles.get(slug) ?? slug}</h2>
              {e.finalGrade ? <span class="mark-big">{e.finalGrade}{e.maxPoints ? <span> / {e.maxPoints}</span> : null}</span> : <span class="chip">marking</span>}
            </div>
            <dl class="kv">
              {e.submitted ? <><dt>Submitted</dt><dd>{e.submitted}</dd></> : null}
              {e.daysLate && e.daysLate !== '0' ? <><dt>Days late</dt><dd>{e.daysLate}</dd></> : null}
              {e.penalty ? <><dt>Late penalty</dt><dd>{e.penalty}</dd></> : null}
              {typeof e.score === 'string' && e.score !== e.finalGrade ? <><dt>Marks</dt><dd>{e.score}</dd></> : null}
              {e.team ? <><dt>Team</dt><dd>{e.team}</dd></> : null}
            </dl>
            {e.feedback ? <div class="fb"><h3>Feedback</h3><Md src={e.feedback} /></div> : null}
            {qs.length ? (
              <div class="fb">
                <h3>By question</h3>
                <ul class="q-list">
                  {qs.map((q) => {
                    const s = typeof e.score === 'object' && e.score ? e.score[q] : '';
                    return <li><b>{q}</b>{s ? <span class="q-score">{s}</span> : null}{e.questionFeedback[q] ? <Md src={e.questionFeedback[q]} /> : null}</li>;
                  })}
                </ul>
              </div>
            ) : null}
            {e.teamFeedback ? <div class="fb"><h3>Team feedback</h3><Md src={e.teamFeedback} /></div> : null}
          </section>
        );
      })}
      <p class="footnote"><a href={gradebookUrl(org, login)} target="_blank" rel="noopener">Your gradebook on GitHub <Ext /></a>: private to you.</p>
    </div>
  );
}
