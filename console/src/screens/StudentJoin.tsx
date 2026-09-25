// Join: the Join course and Join team forms, filled in the console, and what the semester's
// automation answered. The console opens the issue itself, through the API, with the body the
// seeded form would write under a hidden first line (`JOIN_MARKERS`): GitHub drops the form's
// routing label on an issue an account without push creates that way, so the join repo's
// workflows run on that line as well as on the label. If the API refuses (a token that may not
// open issues there), the console offers GitHub's own form, prefilled, instead. The answer is
// read back by polling the student's own issues in the join repo (ETag'd, free when nothing
// changed).

import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { GhComment, GhIssue } from '../github/client';
import { invitationUrl } from '../model/discovery';
import { fmtWhen } from '../model/format';
import { readable, type Mine } from '../model/mine';
import { JOIN_MARKERS, JOIN_REPO } from '../model/names';
import type { SemesterFacts } from '../model/student';
import { ORG_NAME_RE } from '../model/policy';
import { Crumbs, Md } from '../ui/bits';
import { Ext } from '../ui/icons';

export const WELCOME = JOIN_REPO;
export const TEAM_NAME = /^[A-Za-z0-9][A-Za-z0-9_-]*$/;
export const ENROL_CODE = /^dsl-[a-z0-9]{6}$/i;

/** GitHub's Join course form in the join repo, with the code filled in. */
export const joinCourseUrl = (org: string, code: string) =>
  `https://github.com/${org}/${WELCOME}/issues/new?template=01-join-course.yml&enrol_code=${encodeURIComponent(code.trim())}`;

/** GitHub's Join team form, with the assignment and team filled in (text inputs only). */
export const joinTeamUrl = (org: string, assignment: string, team: string) =>
  `https://github.com/${org}/${WELCOME}/issues/new?template=02-join-team.yml&assignment=${encodeURIComponent(assignment)}&team=${encodeURIComponent(team.trim())}`;

/** The Join course issue's body: the seeded form's, under the marker that routes it. */
export const joinCourseBody = (code: string) => `${JOIN_MARKERS.join_course}\n### Enrolment code\n\n${code.trim()}\n`;

export const TEAM_ACTIONS = { join: 'Join an existing team', create: 'Create a new team' } as const;

/** The Join team issue's body, field by field as the seeded form writes it. */
export const joinTeamBody = (assignment: string, action: keyof typeof TEAM_ACTIONS, team: string) =>
  `${JOIN_MARKERS.join_team}\n### Assignment\n\n${assignment}\n\n### Action\n\n${TEAM_ACTIONS[action]}\n\n### Team\n\n${team.trim()}\n`;

/** Whether an issue is a Join request: the form's label, or the console's hidden first line. */
export const isJoinRequest = (i: Pick<GhIssue, 'labels' | 'body'>) =>
  i.labels.some((l) => ROUTES.includes(l.name)) || [JOIN_MARKERS.join_course, JOIN_MARKERS.join_team].some((m) => (i.body ?? '').startsWith(m));

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

/** The person's Join course and Join team issues in the join repo, newest first, each with the automation's last reply. */
async function readAsked(env: NonNullable<ReturnType<typeof useEnv>>, org: string): Promise<Asked[]> {
  const mine = (await env.client.listIssues(org, WELCOME, `creator=${encodeURIComponent(env.user.login)}&state=all`))
    .filter((i) => !i.pull_request && isJoinRequest(i))
    .slice(0, 5);
  return Promise.all(mine.map(async (issue) => {
    const cs = issue.comments ? await env.client.listIssueComments(org, WELCOME, issue.number).catch(() => []) : [];
    return { issue, reply: cs[cs.length - 1] ?? null };
  }));
}

const POLL_MS = 15000;
const POLLS = 20;

/** Your requests, read now and every 15 s (up to 5 min) while one still waits for the automation. `sent` changes when the console has just opened one. */
export function JoinRequests({ org, sent = 0 }: { org: string; sent?: number }) {
  const env = useEnv();
  const [asked, setAsked] = useState<Asked[] | null>(null);
  const [pending, setPending] = useState(false);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!env) return;
    let live = true;
    let polls = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const read = () =>
      Promise.all([readAsked(env, org), env.client.getMyMembership(org).catch(() => null)]).then(([a, ms]) => {
        if (!live) return;
        setAsked(a);
        setPending(ms?.state === 'pending');
        if (a.some((x) => !issueState(x.issue).settled) && ++polls < POLLS) timer = setTimeout(read, POLL_MS);
      }, () => live && setAsked((x) => x ?? []));
    void read();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [org, tick, sent, !!env]);
  return (
    <section class="panel section" aria-labelledby="h-asked">
      <div class="a-head"><h2 id="h-asked">Your requests</h2><button class="textlink" type="button" onClick={() => setTick(tick + 1)}>Check again</button></div>
      <AskedList asked={asked} org={org} invitePending={pending} />
    </section>
  );
}

/** `invitePending`: the person's membership of `org` is still an unaccepted invitation (only then is the accept link offered). */
export function AskedList({ asked, org, invitePending = false }: { asked: Asked[] | null; org?: string; invitePending?: boolean }) {
  if (asked === null) return <p class="footnote">Reading…</p>;
  if (!asked.length) return <p class="footnote">You have opened no Join course or Join team request here yet. Once you send one, it shows here within a few seconds.</p>;
  return (
    <ul class="asked">
      {asked.map(({ issue, reply }) => {
        const s = issueState(issue);
        return (
          <li>
            <div class={`check-line ${s.tone}`}><span><b>{issue.title}</b>, {fmtWhen(issue.created_at)}: {s.word}. <a href={issue.html_url} target="_blank" rel="noopener">Open <Ext /></a>{org && invitePending && issue.labels.some((l) => l.name === 'onboarded') ? <> <a href={invitationUrl(org)} target="_blank" rel="noopener">Accept the invitation <Ext /></a></> : null}</span></div>
            {reply ? <Md class="reply" src={readable(reply.body)} /> : null}
          </li>
        );
      })}
    </ul>
  );
}

/** Sends one Join request through the API; on a refusal, offers GitHub's own form instead. */
function SendRequest({ org, title, body, ready, fallback, label, onSent }: { org: string; title: string; body: string; ready: boolean; fallback: string; label: string; onSent?: () => void }) {
  const env = useEnv();
  const [state, setState] = useState<{ kind: 'idle' | 'busy' | 'sent' } | { kind: 'failed'; error: string }>({ kind: 'idle' });
  const send = () => {
    if (!env || !ready) return;
    setState({ kind: 'busy' });
    env.client.createIssue(org, WELCOME, title, body).then(
      () => {
        setState({ kind: 'sent' });
        onSent?.();
      },
      (e: unknown) => setState({ kind: 'failed', error: e instanceof Error ? e.message : String(e) }),
    );
  };
  return (
    <>
      <div class="actions">
        <button class="btn" type="button" disabled={!ready || !env || state.kind === 'busy'} onClick={send}>{state.kind === 'busy' ? 'Sending…' : label}</button>
      </div>
      {state.kind === 'sent' ? <p class="footnote">Sent. The answer shows under Your requests.</p> : null}
      {state.kind === 'failed' ? <p class="footnote">GitHub did not take the request ({state.error}). <a href={fallback} target="_blank" rel="noopener">Open the form on GitHub instead <Ext /></a>, then press Create there.</p> : null}
    </>
  );
}

export function TeamForm({ org, assignments, mine, onSent }: { org: string; assignments: SemesterFacts['assignments']; mine: Mine | null; onSent?: () => void }) {
  const open = assignments.filter((a) => a.teamFormation);
  const [slug, setSlug] = useState(open[0]?.slug ?? '');
  const [action, setAction] = useState<'join' | 'create'>('join');
  const [team, setTeam] = useState('');
  if (!open.length) return <p class="footnote">No assignment is forming teams now.</p>;
  const a = open.find((x) => x.slug === slug) ?? open[0];
  const current = mine?.units[a.slug]?.team;
  const bad = team.trim() !== '' && !TEAM_NAME.test(team.trim());
  const ready = team.trim() !== '' && !bad;
  const pick = (name: string) => {
    setAction('join');
    setTeam(name);
  };
  return (
    <form class="stack" onSubmit={(e) => e.preventDefault()}>
      <TeamList a={a} current={current ?? null} onPick={pick} picked={action === 'join' ? team.trim() : ''} />
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
      <SendRequest org={org} title="Join team" body={joinTeamBody(a.slug, action, team)} ready={ready} fallback={joinTeamUrl(org, a.slug, team)} label={action === 'join' ? 'Send: join this team' : 'Send: create this team'} onSent={onSent} />
      <p class="footnote">The request is an issue in the semester’s public join repo, opened as you. The automation answers it under Your requests.</p>
    </form>
  );
}

/** The teams formed so far for `a`: name and headcount, those with room first; picking one fills in the form. */
export function TeamList({ a, current, onPick, picked }: { a: SemesterFacts['assignments'][number]; current: string | null; onPick: (name: string) => void; picked: string }) {
  if (!a.teams.length) return <p class="footnote">No team has formed for {a.title} yet: create the first one.</p>;
  const cap = (t: { cap: number | null }) => t.cap ?? a.teamFormation?.cap ?? null;
  const room = (t: { members: number; cap: number | null }) => cap(t) === null || t.members < cap(t)!;
  const list = [...a.teams].sort((x, y) => Number(room(y)) - Number(room(x)) || x.name.localeCompare(y.name));
  return (
    <div class="field">
      <span class="label">Teams so far</span>
      <ul class="plain-list team-list">
        {list.map((t) => {
          const mine = current !== null && t.name.toLowerCase() === current.toLowerCase();
          return (
            <li>
              <b>{t.name}</b> <span class="footnote">{t.members}{cap(t) !== null ? ` of ${cap(t)}` : ''} {t.members === 1 && cap(t) === null ? 'member' : 'members'}{mine ? '; your team' : ''}</span>{' '}
              {mine ? null : room(t) ? <button class="btn outline small" type="button" aria-pressed={picked === t.name} onClick={() => onPick(t.name)}>{picked === t.name ? 'Picked' : 'Pick'}</button> : <span class="chip">full</span>}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function JoinScreen({ org, facts, mine, studentView }: { org: string; facts: SemesterFacts; mine: Mine | null; studentView: boolean }) {
  const [sent, setSent] = useState(0);
  return (
    <div class="stack">
      <section class="panel section" aria-labelledby="h-team">
        <h2 id="h-team">Join or create a team</h2>
        {studentView ? <p class="footnote">A student forms teams here; the form opens the semester’s Join team issue.</p>
          : mine?.auditor ? <p class="footnote">As an auditor you do not join a team.</p>
          : <TeamForm org={org} assignments={facts.assignments} mine={mine} onSent={() => setSent(sent + 1)} />}
      </section>
      {studentView ? null : <JoinRequests org={org} sent={sent} />}
    </div>
  );
}

export function JoinCourseForm({ org, onSent }: { org: string; onSent?: () => void }) {
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
      <SendRequest org={org} title="Join course" body={joinCourseBody(code)} ready={ready} fallback={joinCourseUrl(org, code)} label="Send the Join request" onSent={onSent} />
      <p class="footnote">The request is a public issue in the semester’s join repo, opened as you: the automation removes the code from it straight away. It then invites you to the semester by email: accept that invitation, and the semester appears in the console.</p>
    </form>
  );
}

/** `?join=<org>`: joining a semester you are not a member of yet (its join repo is public). */
export function JoinCourseScreen({ org }: { org: string }) {
  const [sent, setSent] = useState(0);
  return (
    <>
      <Crumbs items={[{ t: 'Your semesters', href: '#home' }, { t: `Join ${org}` }]} />
      <div class="page-head"><div><h1>Join a semester</h1><p class="lede">{org}</p></div></div>
      <div class="stack">
        <section class="panel section"><JoinCourseForm org={org} onSent={() => setSent(sent + 1)} /></section>
        <JoinRequests org={org} sent={sent} />
      </div>
    </>
  );
}

/** Where someone with a code but no semester yet starts: the semester's GitHub organisation name. */
export function JoinStart() {
  const [org, setOrg] = useState('');
  const ok = ORG_NAME_RE.test(org.trim());
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
