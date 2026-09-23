// S1 Home (every course and cohort, ordered by what needs you), S0 Sign in, and the
// read-only view.

import { useState } from 'preact/hooks';
import type { Auth } from '../auth/types';
import { NEW_TOKEN_URL } from '../auth/pat';
import type { GhUser } from '../github/client';
import type { Course, CohortRef } from '../model/discovery';
import { fmtWhen } from '../model/format';
import type { Loaded } from '../model/status';
import { Crumbs, Help, Probs } from '../ui/bits';
import { Ext } from '../ui/icons';
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
  const name = `${course.name}, ${c.termLabel}`;
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

export function HomeScreen({ courses, cohortStates, user }: HomeProps) {
  const cards = courses.flatMap((course) => course.cohorts.map((c) => cardOf(course, c, cohortStates[c.org], user)));
  const live = cards.filter((c) => !c.past).sort((a, b) => b.urgency - a.urgency);
  const past = cards.filter((c) => c.past);
  const courseOnly = courses.filter((c) => !c.cohorts.length);
  return (
    <>
      <Crumbs items={[{ t: 'All courses' }]} />
      <div class="page-head">
        <div><h1>Your courses</h1><p class="lede">Ordered by what needs you. Greyed rows are courses you can see but not change.</p></div>
        <div class="actions"><span class="soon" title="Coming in this build"><button class="btn" type="button" disabled>New course</button></span></div>
      </div>
      <Help title="What am I looking at?" doc="01-new-course-org.md">
        <p>A course holds your materials and assignment templates for every term. Each term runs as its own cohort, which students join. What you can change follows GitHub: the console only offers what your account can do.</p>
      </Help>
      {!courses.length ? (
        <section class="panel section stub">
          <h2>No courses found</h2>
          <p>None of the GitHub organisations your account belongs to is a course. A course is an organisation whose <code>.github</code> repo carries the <code>dsl-course-hub</code> topic.</p>
        </section>
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
        </div>
      )}
    </>
  );
}

// --------------------------------------------------------------------------- S0

export function SignInScreen({ auth, onSignedIn }: { auth: Auth; onSignedIn: (u: GhUser) => void }) {
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [who, setWho] = useState<GhUser | null>(null);
  const submit = async (e: Event) => {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setWho(await auth.signIn(token));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };
  return (
    <div class="signin">
      <div class="page-head" style="margin-bottom:0"><div><h1>Sign in</h1><p class="lede">The console works with your GitHub account: it can change exactly what you can change on GitHub, and nothing else.</p></div></div>
      {who ? (
        <section class="panel section">
          <div class="who-card"><img src={who.avatar_url} alt="" /><div><b>{who.name || who.login}</b><div class="footnote">Signed in as {who.login}</div></div></div>
          <div class="actions"><button class="btn" type="button" onClick={() => onSignedIn(who)}>Continue</button></div>
        </section>
      ) : (
        <form class="panel section" onSubmit={submit}>
          <div class="field">
            <label for="pat">GitHub token</label>
            <input type="password" id="pat" autocomplete="off" spellcheck={false} value={token} onInput={(e) => setToken((e.target as HTMLInputElement).value)} aria-invalid={error ? 'true' : undefined} />
            <p class="hint">
              A classic personal access token with the <code>repo</code> and <code>workflow</code> scopes.{' '}
              <a href={NEW_TOKEN_URL} target="_blank" rel="noopener">Create one on GitHub <Ext /></a>
            </p>
          </div>
          {error ? <div class="invalid-msg"><span /><span>{error}</span></div> : null}
          <div class="actions"><button class="btn" type="submit" disabled={busy}>{busy ? 'Checking…' : 'Sign in'}</button></div>
          <p class="footnote">The token stays in this browser tab only and is forgotten when you close it. Sign-in with the lab’s GitHub App replaces this later.</p>
        </form>
      )}
    </div>
  );
}

// --------------------------------------------------------------------------- read-only and placeholders

export function ReadonlyScreen({ course, cohort }: { course: Course; cohort?: CohortRef }) {
  const title = cohort ? `${course.name}, ${cohort.termLabel}` : course.name;
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
      <p class="footnote" style="margin:-8px 0 18px">Who can change what: instructors and teaching assistants of a cohort can change that cohort and the course’s materials and templates; course admins can change everything in the course. This follows GitHub’s own access.</p>
      <section class="panel section">
        <h2>Course</h2>
        <dl class="kv">
          <dt>Code</dt><dd>{course.code || 'not set'}</dd>
          {course.description ? <><dt>About</dt><dd>{course.description}</dd></> : null}
          <dt>Course on GitHub</dt><dd><a href={`https://github.com/${course.org}`} target="_blank" rel="noopener">{course.org}</a></dd>
          {cohort ? <><dt>Cohort on GitHub</dt><dd><a href={`https://github.com/${cohort.org}`} target="_blank" rel="noopener">{cohort.org}</a></dd></> : null}
        </dl>
      </section>
    </>
  );
}
