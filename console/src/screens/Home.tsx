// S1 Home (Your courses: every course and cohort, ordered by what needs you; Your
// semesters: the semesters the person is a student of), S0 Sign in, and the read-only view.

import { useState } from 'preact/hooks';
import type { ConsoleAuth } from '../auth/console';
import { FINE_GRAINED_SETTINGS_URL, NEW_FINE_GRAINED_URL, NEW_TOKEN_URL } from '../auth/pat';
import type { GhUser } from '../github/client';
import { cohortName, semesterName, type Course, type CohortRef, type Semester, type TokenKind } from '../model/discovery';
import { fmtWhen } from '../model/format';
import { hiddenSemesters, saveHiddenSemesters } from '../model/prefs';
import type { Loaded } from '../model/status';
import { studentHref } from '../router';
import { Crumbs, Help, Probs, ghUrl } from '../ui/bits';
import { Ext } from '../ui/icons';
import { StudentWeekHome } from './Student';
import { JoinStart } from './StudentJoin';
import type { HomeProps } from './types';

interface Card {
  key: string;
  name: string;
  sub: string;
  week: string;
  status: preact.ComponentChildren;
  next: [string, string][];
  href: string;
  ro: boolean;
  past: boolean;
  urgency: number;
}

function cardOf(course: Course, c: CohortRef, l: Loaded | undefined, user: GhUser): Card {
  const name = cohortName({ course, cohort: c });
  const base = { key: c.org, name, href: `?cohort=${c.org}#cohort` };
  const who = course.admins.includes(user.login) ? 'you are a course admin' : 'you are staff';
  const sub = `${course.code ? `${course.code}; ` : ''}${course.write ? who : 'read only'}`;
  if (!course.write)
    return { ...base, sub, week: '', status: <span class="chip">read only</span>, next: [['', 'Problems and dates need write access']], ro: true, past: false, urgency: -1 };
  if (!l || l.kind === 'loading') return { ...base, sub, week: '…', status: <span class="chip">Reading</span>, next: [], ro: false, past: false, urgency: 0 };
  if (l.kind !== 'ready')
    return { ...base, sub, week: '', status: <span class="chip">{l.kind === 'absent' ? 'Not computed yet' : 'Unreadable'}</span>, next: [['', l.kind === 'absent' ? 'Open it and press Check now.' : 'The status file could not be read.']], ro: false, past: false, urgency: 0 };
  const s = l.status, tz = s.cohort?.timezone, n = (s.problems ?? []).length;
  const past = s.cohort?.live === false;
  return {
    ...base,
    sub: past && s.cohort?.archive_date ? `Archived ${fmtWhen(s.cohort.archive_date, tz)}` : sub,
    week: past ? 'Finished' : s.cohort ? `Week ${s.cohort.week} of ${s.cohort.weeks}` : '',
    status: past ? <span class="probs none">Archived</span> : <Probs n={n} />,
    next: past ? [['', 'Students keep access. Nothing was deleted.']] : (s.this_week ?? []).slice(0, 2).map((w) => [fmtWhen(w.when, tz), w.title] as [string, string]),
    ro: false,
    past,
    urgency: n,
  };
}

function CardRow({ c }: { c: Card }) {
  return (
    <li>
      <a class={`cohort-card${c.ro ? ' ro' : ''}${c.past ? ' past' : ''}`} href={c.href}>
        <span class="cc-name">{c.name}<span>{c.sub}</span></span>
        <span class="cc-week">{c.week}</span>
        {c.status}
        <span class="cc-next">{c.next.map(([b, t]) => <span>{b ? <b>{b}</b> : null}{b ? ' ' : ''}{t}</span>)}</span>
      </a>
    </li>
  );
}

function semesterCard(s: Semester): Card {
  return {
    key: s.org,
    name: semesterName(s),
    sub: s.archived ? 'Archived; your work stays yours to read' : 'You are a student',
    week: '',
    status: <span class="chip">{s.archived ? 'Archived' : 'Current'}</span>,
    next: [['', 'Open the semester']],
    href: studentHref(s.org),
    ro: false,
    past: s.archived,
    urgency: 0,
  };
}

/** Your semesters, and the per-viewer choice of which of them show. */
function SemestersGroup({ semesters, login, lead, hidden, setHidden }: { semesters: Semester[]; login: string; lead: boolean; hidden: Set<string>; setHidden: (h: Set<string>) => void }) {
  const flip = (org: string) => {
    const next = new Set(hidden);
    if (!next.delete(org)) next.add(org);
    saveHiddenSemesters(login, next);
    setHidden(next);
  };
  const shown = semesters.filter((s) => !hidden.has(s.org));
  return (
    <section class="section" aria-labelledby="h-semesters">
      {lead ? null : <h2 id="h-semesters">Your semesters</h2>}
      {shown.length ? <ul class="cohort-list">{shown.map((s) => <CardRow c={semesterCard(s)} />)}</ul> : <p class="footnote">Every semester is hidden; choose which to show below.</p>}
      <details class="fold" style="margin-top:12px">
        <summary>Show these semesters</summary>
        {semesters.map((s) => (
          <label class="check"><input type="checkbox" checked={!hidden.has(s.org)} onChange={() => flip(s.org)} /><span>{semesterName(s)}</span></label>
        ))}
        <p class="footnote">Kept in this browser only.</p>
      </details>
    </section>
  );
}

function NothingFound({ kind }: { kind?: TokenKind }) {
  if (kind === 'fine-grained') {
    return (
      <section class="panel section stub">
        <h2>No courses or semesters found</h2>
        <p>This token can see no course or semester orgs; when creating it, add the orgs under Resource owner and grant the organisation permission Members: read (<a href={FINE_GRAINED_SETTINGS_URL} target="_blank" rel="noopener">your tokens <Ext /></a>).</p>
      </section>
    );
  }
  return (
    <>
    <section class="panel section stub">
      <h2>No courses found</h2>
      <p>None of the GitHub organisations your account belongs to is a course or a semester. A course is an organisation whose <code>.github</code> repo carries the <code>dsl-course-hub</code> topic.</p>
    </section>
    <JoinStart />
    </>
  );
}

export function HomeScreen({ courses, semesters = [], kind, cohortStates, user, now }: HomeProps) {
  const [hidden, setHidden] = useState(() => hiddenSemesters(user.login));
  if (!courses.length && semesters.length) {
    return (
      <>
        <Crumbs items={[{ t: 'Your semesters' }]} />
        <div class="page-head"><div><h1>Your semesters</h1><p class="lede">Every semester you are a student of; archived ones stay here as history</p></div></div>
        <div class="stack">
          <StudentWeekHome semesters={semesters.filter((x) => !hidden.has(x.org))} now={now} />
          <SemestersGroup semesters={semesters} login={user.login} lead hidden={hidden} setHidden={setHidden} />
          <JoinStart />
        </div>
      </>
    );
  }
  const cards = courses.flatMap((course) => course.cohorts.map((c) => cardOf(course, c, cohortStates[c.org], user)));
  const live = cards.filter((c) => !c.past).sort((a, b) => b.urgency - a.urgency);
  const past = cards.filter((c) => c.past);
  const courseOnly = courses.filter((c) => !c.cohorts.length);
  return (
    <>
      <Crumbs items={[{ t: 'All courses' }]} />
      <div class="page-head">
        <div><h1>Your courses</h1><p class="lede">All courses past &amp; present; ordered by what needs your attention</p></div>
        <div class="actions"><a class="btn" href="#new-course-1">New course</a></div>
      </div>
      <Help title="What am I looking at?" doc="01-new-course-org.md">
        <p>A course holds your materials and assignment templates for every term. Each term runs as its own cohort, which students join. What you can change follows GitHub: the console only offers what your account can do.</p>
      </Help>
      {!courses.length ? (
        <NothingFound kind={kind} />
      ) : (
        <div class="stack">
          <section class="section" aria-labelledby="h-live">
            <h2 id="h-live">This term</h2>
            {live.length ? <ul class="cohort-list">{live.map((c) => <CardRow c={c} />)}</ul> : <p class="footnote">No cohort is running.</p>}
          </section>
          {past.length ? (
            <section class="section" aria-labelledby="h-past">
              <h2 id="h-past">Past terms</h2>
              <ul class="cohort-list">{past.map((c) => <CardRow c={c} />)}</ul>
            </section>
          ) : null}
          {courseOnly.length ? (
            <section class="section">
              <h2>Courses with no cohort yet</h2>
              <ul class="cohort-list">
                {courseOnly.map((c) => (
                  <li><a class={`cohort-card${c.write ? '' : ' ro'}`} href={`?course=${c.org}#course`}><span class="cc-name">{c.name}<span>{c.code}</span></span><span class="cc-week" /><span /><span class="cc-next">Open the course</span></a></li>
                ))}
              </ul>
            </section>
          ) : null}
          {semesters.length ? <SemestersGroup semesters={semesters} login={user.login} lead={false} hidden={hidden} setHidden={setHidden} /> : null}
        </div>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- S0

export function SignInScreen({ auth, onSignedIn }: { auth: ConsoleAuth; onSignedIn: (u: GhUser) => void }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState<'token' | 'app' | null>(null);
  const [error, setError] = useState<string | null>(auth.notice);
  const [who, setWho] = useState<GhUser | null>(null);
  const submit = async (e: Event) => {
    e.preventDefault();
    setBusy('token');
    setError(null);
    try {
      setWho(await auth.signIn(token));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(null);
    }
  };
  const withGitHub = () => {
    setBusy('app');
    setError(null);
    auth.signInWithApp().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : String(err));
      setBusy(null);
    });
  };
  const tokenForm = (
    <form onSubmit={submit}>
      <div class="field">
        <label for="pat">GitHub token</label>
        <input type="password" id="pat" autocomplete="off" spellcheck={false} value={token} onInput={(e) => setToken((e.target as HTMLInputElement).value)} aria-invalid={error ? 'true' : undefined} />
        <p class="hint">
          A fine-grained token owned by your course’s organisation, with Contents, Actions and Issues read and write and Members read (<a href={NEW_FINE_GRAINED_URL} target="_blank" rel="noopener">create one <Ext /></a>), or a classic token with the <code>repo</code> and <code>workflow</code> scopes (<a href={NEW_TOKEN_URL} target="_blank" rel="noopener">create one <Ext /></a>).
        </p>
      </div>
      <div class="actions"><button class={auth.app ? 'btn outline' : 'btn'} type="submit" disabled={busy !== null}>{busy === 'token' ? 'Checking…' : 'Sign in with the token'}</button></div>
      <p class="footnote">For a console set up without a sign-in relay, or when signing in with GitHub is blocked. The token stays in this browser tab only and is forgotten when you close it.</p>
    </form>
  );
  const problem = error ? <div class="invalid-msg"><span /><span>{error}</span></div> : null;
  return (
    <div class="signin">
      <div class="page-head" style="margin-bottom:0"><div><h1>Sign in</h1><p class="lede">The console works with your GitHub account: it can change exactly what you can change on GitHub, and nothing else.</p></div></div>
      {who ? (
        <section class="panel section">
          <div class="who-card"><img src={who.avatar_url} alt="" /><div><b>{who.name || who.login}</b><div class="footnote">Signed in as {who.login}</div></div></div>
          <Reach reach={auth.pat.reach()} />
          <div class="actions"><button class="btn" type="button" onClick={() => onSignedIn(who)}>Continue</button></div>
        </section>
      ) : auth.app ? (
        <section class="panel section">
          {problem}
          <div class="actions"><button class="btn" type="button" onClick={withGitHub} disabled={busy !== null}>{busy === 'app' ? 'Going to GitHub…' : 'Sign in with GitHub'}</button></div>
          <p class="footnote">You approve the lab’s GitHub App on github.com once; the sign-in lasts until you close this tab.</p>
          <details class="fold">
            <summary>Use a token instead</summary>
            {tokenForm}
          </details>
        </section>
      ) : (
        <section class="panel section">
          {problem}
          {tokenForm}
        </section>
      )}
    </div>
  );
}

/** What a fine-grained token cannot see; nothing for a classic token or the App. */
function Reach({ reach }: { reach: { seen: string[]; unseen: string[] } | null }) {
  if (!reach) return null;
  return (
    <div class="check-line warn">
      <span>
        {reach.unseen.length ? <>This token cannot see {reach.unseen.join(', ')}. </> : null}
        {reach.seen.length ? <>It can see {reach.seen.join(', ')}. </> : null}
        A fine-grained token reaches only the one organisation that owns it, and GitHub lists only your public memberships to it, so an organisation missing here may still be out of its reach.
      </span>
    </div>
  );
}

// --------------------------------------------------------------------------- read-only and placeholders

export function ReadonlyScreen({ course, cohort }: { course: Course; cohort?: CohortRef }) {
  const title = cohort ? cohortName({ course, cohort }) : course.name;
  return (
    <>
      <Crumbs items={[{ t: 'All courses', href: '#home' }, { t: title }]} />
      <div class="page-head">
        <div><h1>{title}</h1></div>
        {cohort ? <div class="actions"><a class="btn outline" href={`https://${cohort.org}.github.io`} target="_blank" rel="noopener">Open the student site <Ext /></a></div> : null}
      </div>
      <div class="ro-banner">
        <b>Read only.</b>
        <span>You are not staff on this course, so this shows only what your GitHub account can see: the course’s public details and its student site. No roster, no marks, no buttons.</span>
      </div>
      <p class="footnote" style="margin:-8px 0 18px">Who can change what: instructors and teaching assistants of a cohort can change that cohort and the course’s materials and assignment templates; course admins can change everything in the course. This follows GitHub’s own access.</p>
      <section class="panel section">
        <h2>Course</h2>
        <dl class="kv">
          <dt>Code</dt><dd>{course.code || 'not set'}</dd>
          {course.description ? <><dt>About</dt><dd>{course.description}</dd></> : null}
          <dt>Course on GitHub</dt><dd><a href={ghUrl(course.org)} target="_blank" rel="noopener">{course.org}</a></dd>
          {cohort ? <><dt>Cohort on GitHub</dt><dd><a href={ghUrl(cohort.org)} target="_blank" rel="noopener">{cohort.org}</a></dd></> : null}
        </dl>
      </section>
    </>
  );
}
