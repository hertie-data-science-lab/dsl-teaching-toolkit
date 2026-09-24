// Join: the Join course and Join team forms, filled in the console, and what the semester's
// automation answered. The issues are the SAME issues the seeded forms open, with the same
// body: the console fills a form and opens GitHub's own issue form with those answers, and
// the student presses Create there. It cannot create the issue itself: GitHub drops labels
// on an issue created through the API by anyone without push on the repo, and the
// `welcome` workflows run only on the form's label (`onboarding`, `team-formation`). GitHub
// fills an issue form's text inputs from the link, not its dropdowns, so the Action (and an
// assignment offered as a dropdown) is chosen once more on GitHub; the form says which.
// The answer is read back by polling the student's own issues in `welcome` (ETag'd, free
// when nothing changed).

import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { GhComment, GhIssue } from '../github/client';
import { fmtWhen } from '../model/format';
import { readable, type Mine } from '../model/mine';
import type { SemesterFacts } from '../model/student';
import { ORG_RE } from '../router';
import { Crumbs, Md } from '../ui/bits';
import { Ext } from '../ui/icons';

export const WELCOME = 'welcome';
export const TEAM_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
export const ENROL_CODE = /^dsl-[a-z0-9]{6}$/i;

/** GitHub's Join course form in `welcome`, with the code filled in. */
export const joinCourseUrl = (org: string, code: string) =>
  `https://github.com/${org}/${WELCOME}/issues/new?template=01-join-course.yml&enrol_code=${encodeURIComponent(code.trim())}`;

/** GitHub's Join team form, with the assignment and team filled in (text inputs only). */
export const joinTeamUrl = (org: string, assignment: string, team: string) =>
  `https://github.com/${org}/${WELCOME}/issues/new?template=02-join-team.yml&assignment=${encodeURIComponent(assignment)}&team=${encodeURIComponent(team.trim())}`;

export type Tone = 'ok' | 'bad' | 'busy' | 'warn';

/** What the automation said on an issue, from its labels (the workflows' three outcomes each). */
export function issueState(i: Pick<GhIssue, 'labels' | 'state'>): { word: string; tone: Tone; settled: boolean } {
  const has = (l: string) => i.labels.some((x) => x.name === l);
  if (has('onboarded')) return { word: 'You joined: accept the invitation GitHub emailed you', tone: 'ok', settled: true };
  if (has('staff')) return { word: 'Recorded as an instructor', tone: 'ok', settled: true };
  if (has('team-recorded')) return { word: 'Your team is recorded', tone: 'ok', settled: true };
  if (has('team-refused')) return { word: 'Refused: the reply says why and what to do', tone: 'bad', settled: true };
  if (has('needs-review')) return { word: 'Waiting for your instructors to look at it', tone: 'warn', settled: true };
  return i.state === 'closed' ? { word: 'Closed', tone: 'warn', settled: true } : { word: 'Waiting for the automation to answer', tone: 'busy', settled: false };
}

const ROUTES = ['onboarding', 'team-formation'];

interface Asked {
  issue: GhIssue;
  reply: GhComment | null;
}

/** The person's Join course and Join team issues in `welcome`, newest first, each with the automation's last reply. */
async function readAsked(env: NonNullable<ReturnType<typeof useEnv>>, org: string): Promise<Asked[]> {
  const mine = (await env.client.listIssues(org, WELCOME, `creator=${encodeURIComponent(env.user.login)}&state=all`))
    .filter((i) => i.labels.some((l) => ROUTES.includes(l.name)))
    .slice(0, 5);
  return Promise.all(mine.map(async (issue) => {
    const cs = issue.comments ? await env.client.listIssueComments(org, WELCOME, issue.number).catch(() => []) : [];
    return { issue, reply: cs[cs.length - 1] ?? null };
  }));
}

const POLL_MS = 15000;
const POLLS = 20;

/** Your requests, read now and every 15 s (up to 5 min) while one still waits for the automation. */
export function JoinRequests({ org }: { org: string }) {
  const env = useEnv();
  const [asked, setAsked] = useState<Asked[] | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!env) return;
    let live = true;
    let polls = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = () =>
      readAsked(env, org).then((a) => {
        if (!live) return;
        setAsked(a);
        if (a.some((x) => !issueState(x.issue).settled) && ++polls < POLLS) timer = setTimeout(read, POLL_MS);
      }, () => live && setAsked((x) => x ?? []));
    void read();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [org, tick, !!env]);
  return (
    <section class="panel section" aria-labelledby="h-asked">
      <div class="a-head"><h2 id="h-asked">Your requests</h2><button class="textlink" type="button" onClick={() => setTick(tick + 1)}>Check again</button></div>
      <AskedList asked={asked} />
    </section>
  );
}

export function AskedList({ asked }: { asked: Asked[] | null }) {
  if (asked === null) return <p class="footnote">Reading…</p>;
  if (!asked.length) return <p class="footnote">You have opened no Join course or Join team request here yet. After you press Create on GitHub, it shows here within a few seconds.</p>;
  return (
    <ul class="asked">
      {asked.map(({ issue, reply }) => {
        const s = issueState(issue);
        return (
          <li>
            <div class={`check-line ${s.tone}`}><span><b>{issue.title}</b>, {fmtWhen(issue.created_at)}: {s.word}. <a href={issue.html_url} target="_blank" rel="noopener">Open <Ext /></a></span></div>
            {reply ? <Md class="reply" src={readable(reply.body)} /> : null}
          </li>
        );
      })}
    </ul>
  );
}

export function TeamForm({ org, assignments, mine }: { org: string; assignments: SemesterFacts['assignments']; mine: Mine | null }) {
  const open = assignments.filter((a) => a.teamFormation);
  const [slug, setSlug] = useState(open[0]?.slug ?? '');
  const [action, setAction] = useState<'join' | 'create'>('join');
  const [team, setTeam] = useState('');
  if (!open.length) return <p class="footnote">No assignment is forming teams now.</p>;
  const a = open.find((x) => x.slug === slug) ?? open[0];
  const current = mine?.units[a.slug]?.team;
  const bad = team.trim() !== '' && !TEAM_NAME.test(team.trim());
  const ready = team.trim() !== '' && !bad;
  return (
    <form class="stack" onSubmit={(e) => e.preventDefault()}>
      <div class="field">
        <label for="j-asg">Assignment</label>
        <select id="j-asg" value={a.slug} onChange={(e) => setSlug((e.target as HTMLSelectElement).value)}>
          {open.map((x) => <option value={x.slug}>{x.title}{x.subtitle ? `: ${x.subtitle}` : ''}</option>)}
        </select>
        <p class="hint">
          {a.teamFormation?.closes ? `Teams can form until ${a.teamFormation.closes}. ` : ''}
          {a.teamFormation?.cap ? `At most ${a.teamFormation.cap} in a team. ` : ''}
          {current ? `You are in ${current}; joining or creating another moves you out of it.` : 'You have no team yet.'}
        </p>
      </div>
      <fieldset class="field">
        <legend class="label">Action</legend>
        <label class="check"><input type="radio" name="j-act" checked={action === 'join'} onChange={() => setAction('join')} /><span>Join an existing team</span></label>
        <label class="check"><input type="radio" name="j-act" checked={action === 'create'} onChange={() => setAction('create')} /><span>Create a new team</span></label>
      </fieldset>
      <div class="field">
        <label for="j-team">Team</label>
        <input type="text" id="j-team" value={team} spellcheck={false} autocomplete="off" aria-invalid={bad ? 'true' : undefined} onInput={(e) => setTeam((e.target as HTMLInputElement).value)} placeholder="e.g. team-x" />
        <p class="hint">{bad ? 'Letters, numbers and dashes only, starting with a letter or number.' : action === 'join' ? 'Spell it exactly as the team’s members do.' : 'A name nobody is using yet.'}</p>
      </div>
      <div class="actions">
        {ready ? <a class="btn" href={joinTeamUrl(org, a.slug, team)} target="_blank" rel="noopener">Open the Join team form on GitHub <Ext /></a> : <button class="btn" type="button" disabled>Open the Join team form on GitHub</button>}
      </div>
      <p class="footnote">GitHub opens the form with the team{' '}filled in. There, choose <b>{action === 'join' ? 'Join an existing team' : 'Create a new team'}</b> as the Action (and {a.slug} as the assignment if it asks), then press Create. The answer shows under Your requests.</p>
    </form>
  );
}

export function JoinScreen({ org, facts, mine, studentView }: { org: string; facts: SemesterFacts; mine: Mine | null; studentView: boolean }) {
  return (
    <div class="stack">
      <section class="panel section" aria-labelledby="h-team">
        <h2 id="h-team">Join or create a team</h2>
        {studentView ? <p class="footnote">A student forms teams here; the form opens the semester’s Join team issue.</p> : <TeamForm org={org} assignments={facts.assignments} mine={mine} />}
      </section>
      {studentView ? null : <JoinRequests org={org} />}
    </div>
  );
}

export function JoinCourseForm({ org }: { org: string }) {
  const [code, setCode] = useState('');
  const bad = code.trim() !== '' && !ENROL_CODE.test(code.trim());
  const ready = code.trim() !== '' && !bad;
  return (
    <form class="stack" onSubmit={(e) => e.preventDefault()}>
      <div class="field">
        <label for="j-code">Enrolment code</label>
        <input type="text" id="j-code" value={code} spellcheck={false} autocomplete="off" aria-invalid={bad ? 'true' : undefined} onInput={(e) => setCode((e.target as HTMLInputElement).value)} placeholder="dsl-ab3k9m" />
        <p class="hint">{bad ? 'A code looks like dsl- and six letters or numbers.' : 'The code emailed to your school address.'}</p>
      </div>
      <div class="actions">
        {ready ? <a class="btn" href={joinCourseUrl(org, code)} target="_blank" rel="noopener">Open the Join course form on GitHub <Ext /></a> : <button class="btn" type="button" disabled>Open the Join course form on GitHub</button>}
      </div>
      <p class="footnote">GitHub opens the form with your code filled in; press Create there. The request is a public issue: the automation removes the code from it straight away. It then invites you to the semester by email: accept that invitation, and the semester appears in the console.</p>
    </form>
  );
}

/** `?join=<org>`: joining a semester you are not a member of yet (its `welcome` repo is public). */
export function JoinCourseScreen({ org }: { org: string }) {
  return (
    <>
      <Crumbs items={[{ t: 'Your semesters', href: '#home' }, { t: `Join ${org}` }]} />
      <div class="page-head"><div><h1>Join a semester</h1><p class="lede">{org}</p></div></div>
      <div class="stack">
        <section class="panel section"><JoinCourseForm org={org} /></section>
        <JoinRequests org={org} />
      </div>
    </>
  );
}

/** Where someone with a code but no semester yet starts: the semester's GitHub organisation name. */
export function JoinStart() {
  const [org, setOrg] = useState('');
  const ok = ORG_RE.test(org.trim());
  return (
    <section class="panel section" aria-labelledby="h-join">
      <h2 id="h-join">Have an enrolment code?</h2>
      <div class="field">
        <label for="j-org">The semester’s GitHub organisation, as your email names it</label>
        <input type="text" id="j-org" value={org} spellcheck={false} autocomplete="off" onInput={(e) => setOrg((e.target as HTMLInputElement).value)} placeholder="hertie-dsl-demo-f2026" />
      </div>
      <div class="actions">{ok ? <a class="btn outline" href={`?join=${encodeURIComponent(org.trim())}`}>Join this semester</a> : <button class="btn outline" type="button" disabled>Join this semester</button>}</div>
    </section>
  );
}
