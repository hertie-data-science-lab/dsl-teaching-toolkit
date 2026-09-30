// S1 Home (All courses: the institution's catalogue, the person's own courses in colour and
// ordered by what needs them, decision 0021 rule 5; Your semesters: the semesters the person
// is a student of), S0 Sign in, and the read-only view.

import { useEffect, useRef, useState } from 'preact/hooks';
import type { ConsoleAuth } from '../auth/console';
import { FINE_GRAINED_SETTINGS_URL, NEW_FINE_GRAINED_URL, NEW_TOKEN_URL } from '../auth/pat';
import type { GhUser } from '../github/client';
import { useEnv } from '../env';
import { cohortName, invitationUrl, semesterName, type Course, type CohortRef, type Invitation, type Semester, type TokenKind } from '../model/discovery';
import { loadCatalogue, runningNow, type CatalogueCourse } from '../model/catalogue';
import { fmtWhen } from '../model/format';
import { hiddenSemesters, myCoursesOnly, saveHiddenSemesters, saveMyCoursesOnly } from '../model/prefs';
import type { Loaded } from '../model/status';
import { studentHref } from '../router';
import { Crumbs, Probs, ghUrl } from '../ui/bits';
import { Hint } from '../ui/Hint';
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

/** "E1234; you are a course admin". */
function courseSub(course: Course, user: GhUser): string {
  const who = course.admins.includes(user.login) ? 'you are a course admin' : 'you are an instructor';
  return `${course.code ? `${course.code}; ` : ''}${course.write ? who : 'read only'}`;
}

function cardOf(course: Course, c: CohortRef, l: Loaded | undefined, user: GhUser): Card {
  const name = cohortName({ course, cohort: c });
  const base = { key: c.org, name, href: `?cohort=${c.org}#dashboard` };
  const sub = courseSub(course, user);
  if (!course.write)
    return { ...base, sub, week: '', status: <span class="chip">read only</span>, next: [['', 'Problems and dates need write access']], ro: true, past: false, urgency: -1 };
  if (!l || l.kind === 'loading') return { ...base, sub, week: '…', status: <span class="chip">Reading</span>, next: [], ro: false, past: false, urgency: 0 };
  if (l.kind !== 'ready')
    return { ...base, sub, week: '', status: <span class="chip">{l.kind === 'absent' ? 'Not computed yet' : 'Unreadable'}</span>, next: [['', l.kind === 'absent' ? 'Open it and press Re-check.' : 'The status file could not be read.']], ro: false, past: false, urgency: 0 };
  const s = l.status, tz = s.semester?.timezone, n = (s.problems ?? []).length;
  const past = s.semester?.live === false;
  return {
    ...base,
    sub: past && s.semester?.archive_date ? `Archived ${fmtWhen(s.semester.archive_date, tz)}` : sub,
    week: past ? 'Finished' : s.semester ? `Week ${s.semester.week} of ${s.semester.weeks}` : '',
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

const NOT_MINE = 'Not one of your courses';

/** A catalogue row the person has no role in: greyed, and not a link. `quiet` keeps its sub-line for screen readers only. */
function OffRow({ name, quiet = false }: { name: string; quiet?: boolean }) {
  return (
    <li>
      <div class="cohort-card ro off" aria-disabled="true">
        <span class="cc-name">{name}<span class={quiet ? 'sr-only' : undefined}>{NOT_MINE}</span></span>
        <span class="cc-week" />
        <span />
        <span class="cc-next" />
      </div>
    </li>
  );
}

type CatalogueState = { list: CatalogueCourse[]; state: 'off' | 'loading' | 'ready' | 'failed' };

/** The catalogue, read after the estate for a person with a course; 'off' otherwise, and with no one signed in (a render test). */
function useCatalogue(courses: Course[]): CatalogueState {
  const env = useEnv();
  const client = courses.length ? env?.client : undefined;
  const [st, setSt] = useState<CatalogueState>({ list: [], state: client ? 'loading' : 'off' });
  const key = courses.map((c) => c.org).join(' ');
  useEffect(() => {
    if (!client) {
      setSt({ list: [], state: 'off' });
      return;
    }
    let live = true;
    setSt({ list: [], state: 'loading' });
    loadCatalogue(client, courses, (list) => live && setSt({ list, state: 'loading' }))
      .then((list) => live && setSt({ list, state: 'ready' }))
      .catch(() => live && setSt({ list: [], state: 'failed' }));
    return () => {
      live = false;
    };
  }, [client, key]);
  return st;
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

const article = (role: string) => (/^[aeiou]/.test(role) ? `an ${role}` : `a ${role}`);

/**
 * The person's pending org invitations, at the top of Home and of a semester's student
 * screens: GitHub shows the org to no one who has not accepted. A classic token accepts in
 * the console; the App and a fine-grained token cannot (it takes Members: write, decision
 * 0002), so they, and a refused accept, go to GitHub's accept page, and the list is read
 * again when the person comes back to this tab.
 */
export function Invitations({ invited, kind }: { invited: Invitation[]; kind?: TokenKind }) {
  const env = useEnv();
  const [state, setState] = useState<Record<string, 'accepting' | 'accepted' | 'refused'>>({});
  const away = useRef(false);
  useEffect(() => {
    const back = () => {
      if (!away.current) return;
      away.current = false;
      void env?.rediscover?.();
    };
    window.addEventListener('focus', back);
    return () => window.removeEventListener('focus', back);
  }, [env]);
  const shown = invited.filter((i) => state[i.org] !== 'accepted');
  if (!shown.length) return null;
  const accept = async (org: string) => {
    if (!env) return;
    setState((s) => ({ ...s, [org]: 'accepting' }));
    try {
      await env.client.acceptInvitation(org);
    } catch {
      setState((s) => ({ ...s, [org]: 'refused' }));
      return;
    }
    setState((s) => ({ ...s, [org]: 'accepted' }));
    await env.rediscover?.();
  };
  return (
    <section class="panel section invitations" aria-labelledby="h-invited">
      <h2 id="h-invited">{shown.length > 1 ? 'Invitations' : 'Invitation'}</h2>
      <ul>
        {shown.map((i) => {
          const st = state[i.org];
          return (
            <li>
              <span>You have been invited to <b>{i.name}</b>{i.role ? ` as ${article(i.role)}` : ''}.{st === 'refused' ? ' GitHub did not let the console accept it; accept it on GitHub instead.' : ''}</span>
              {kind === 'classic' && st !== 'refused' ? (
                <button class="btn small" type="button" disabled={st === 'accepting' || !env} onClick={() => void accept(i.org)}>{st === 'accepting' ? 'Accepting…' : 'Accept'}</button>
              ) : (
                <a class="btn small" href={invitationUrl(i.org)} target="_blank" rel="noopener" onClick={() => (away.current = true)}>Accept on GitHub <Ext /></a>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
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

export function HomeScreen({ courses, semesters = [], invited = [], kind, cohortStates, user, now }: HomeProps) {
  const [hidden, setHidden] = useState(() => hiddenSemesters(user.login));
  const [only, setOnly] = useState(() => myCoursesOnly(user.login));
  const catalogue = useCatalogue(courses);
  // An invitation whose role cannot be told is most likely a student's.
  if (!courses.length && (semesters.length || (invited.length && invited.every((i) => i.role === null)))) {
    return (
      <>
        <Crumbs items={[{ t: 'Your semesters' }]} />
        <div class="page-head"><div><h1>Your semesters</h1><p class="lede">Every semester you are a student of; archived ones stay here as history</p></div></div>
        <div class="stack">
          <Invitations invited={invited} kind={kind} />
          <StudentWeekHome semesters={semesters.filter((x) => !hidden.has(x.org))} now={now} />
          {semesters.length ? <SemestersGroup semesters={semesters} login={user.login} lead hidden={hidden} setHidden={setHidden} /> : null}
          <JoinStart />
        </div>
      </>
    );
  }
  const cards = courses.flatMap((course) => course.cohorts.map((c) => cardOf(course, c, cohortStates[c.org], user)));
  const live = cards.filter((c) => !c.past).sort((a, b) => b.urgency - a.urgency);
  const past = cards.filter((c) => c.past);
  const courseOnly = courses.filter((c) => !c.cohorts.length);
  // The person's courses by what needs them (their running semesters' problems), then the rest by name.
  const need = (c: Course) => cards.filter((k) => !k.past && c.cohorts.some((h) => h.org === k.key)).reduce((n, k) => n + Math.max(k.urgency, 0), 0);
  const mine = courses.map((c) => ({ c, n: need(c) })).sort((a, b) => b.n - a.n || a.c.name.localeCompare(b.c.name));
  const foreign = catalogue.list.filter((c) => !c.mine);
  const others = only ? [] : foreign.sort((a, b) => a.name.localeCompare(b.name));
  const othersNow = others
    .flatMap((c) => c.semesters.filter((s) => runningNow(s, now)).map((s) => `${c.name}, ${s.termLabel}`))
    .sort((a, b) => a.localeCompare(b));
  const flipOnly = () => {
    saveMyCoursesOnly(user.login, !only);
    setOnly(!only);
  };
  return (
    <>
      <Crumbs items={[{ t: 'All courses' }]} />
      <div class="page-head">
        <div><h1>All courses <Hint doc="01-new-course-org.md">A course holds your materials and assignment templates for every semester; each semester runs in its own org, which students join. The console offers only what your GitHub account can do.</Hint></h1><p class="lede">Every course the lab runs; yours in colour, ordered by what needs your attention</p></div>
        <div class="actions">
          {foreign.length ? <label class="check my-only"><input type="checkbox" checked={only} onChange={flipOnly} /><span>My courses</span></label> : null}
          <a class="btn" href="#new-course-1">New course</a>
        </div>
      </div>
      <Invitations invited={invited} kind={kind} />
      {!courses.length ? (
        <NothingFound kind={kind} />
      ) : (
        <div class="stack">
          <section class="section" aria-labelledby="h-courses">
            <h2 id="h-courses">Courses</h2>
            <ul class="cohort-list">
              {mine.map(({ c }) => (
                <li><a class={`cohort-card${c.write ? '' : ' ro'}`} href={`?course=${c.org}#course`}><span class="cc-name">{c.name}<span>{courseSub(c, user)}</span></span><span class="cc-week" /><span /><span class="cc-next">Open the course</span></a></li>
              ))}
              {others.map((c) => <OffRow name={c.name} />)}
            </ul>
            {catalogue.state === 'loading' && !only ? <p class="footnote">Reading the catalogue…</p> : catalogue.state === 'failed' ? <p class="footnote">The catalogue could not be read.</p> : null}
          </section>
          <section class="section" aria-labelledby="h-live">
            <h2 id="h-live">This semester</h2>
            {live.length || othersNow.length ? (
              <ul class="cohort-list">
                {live.map((c) => <CardRow c={c} />)}
                {othersNow.map((name) => <OffRow name={name} quiet />)}
              </ul>
            ) : (
              <p class="footnote">No semester is running.</p>
            )}
          </section>
          {past.length ? (
            <section class="section" aria-labelledby="h-past">
              <h2 id="h-past">Past semesters</h2>
              <ul class="cohort-list">{past.map((c) => <CardRow c={c} />)}</ul>
            </section>
          ) : null}
          {courseOnly.length ? (
            <section class="section">
              <h2>Courses with no semester yet</h2>
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

/** The line under the sign-in button (decision 0021 rule 2). */
const REACH_LINE = 'The console can see and change only what your GitHub account can.';

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
      <div class="page-head" style="margin-bottom:0"><div><h1>Sign in to the DSL Teaching Console</h1><p class="lede">Where instructors run their courses and semesters, and students find their materials, assignments and marks. Everything lives on GitHub; the console is the one place to work it from.</p></div></div>
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
          <p class="footnote">{REACH_LINE}</p>
          <details class="fold">
            <summary>Use a token instead</summary>
            <div class="fold-body">{tokenForm}</div>
          </details>
        </section>
      ) : (
        <section class="panel section">
          {problem}
          {tokenForm}
          <p class="footnote">{REACH_LINE}</p>
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
        <span>You are not an instructor on this course, so this shows only what your GitHub account can see: the course’s public details and its student site. No roster, no marks, no buttons.</span>
      </div>
      <p class="footnote" style="margin:-8px 0 18px">Who can change what: instructors and teaching assistants of a semester can change that semester and the course’s materials and assignment templates; course admins can change everything in the course. This follows GitHub’s own access.</p>
      <section class="panel section">
        <h2>Course</h2>
        <dl class="kv">
          <dt>Code</dt><dd>{course.code || 'not set'}</dd>
          {course.description ? <><dt>About</dt><dd>{course.description}</dd></> : null}
          <dt>Course on GitHub</dt><dd><a href={ghUrl(course.org)} target="_blank" rel="noopener">{course.org}</a></dd>
          {cohort ? <><dt>Semester on GitHub</dt><dd><a href={ghUrl(cohort.org)} target="_blank" rel="noopener">{cohort.org}</a></dd></> : null}
        </dl>
      </section>
    </>
  );
}
