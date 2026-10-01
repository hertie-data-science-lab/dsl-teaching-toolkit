// S1 Home (All courses: the institution's catalogue in three sections, DSL courses, This
// semester and Past semesters, the person's own rows in colour and ordered by what needs them,
// each section with its own My courses checkbox, decisions 0021 rule 5 and 0030; Your semesters: the semesters the person
// is a student of, This semester then Past semesters as the instructor's page has them,
// decision 0029 rule 3), S0 Sign in, and the read-only view.

import { useEffect, useRef, useState } from 'preact/hooks';
import type { ConsoleAuth } from '../auth/console';
import { FINE_GRAINED_SETTINGS_URL, NEW_FINE_GRAINED_URL, NEW_TOKEN_URL } from '../auth/pat';
import type { GhUser } from '../github/client';
import { useEnv } from '../env';
import { cohortName, invitationUrl, semesterName, type Course, type CohortRef, type Invitation, type Semester, type TokenKind } from '../model/discovery';
import { endedNow, loadCatalogue, runningNow, termRank, type CatalogueCourse } from '../model/catalogue';
import { fmtWhen } from '../model/format';
import { currentOnly, myCoursesOnly, saveCurrentOnly, saveMyCoursesOnly, type CatalogueSection } from '../model/prefs';
import type { Loaded } from '../model/status';
import type { SemesterFacts } from '../model/student';
import { nextLine, semesterLine } from '../model/week';
import { studentHref } from '../router';
import { Crumbs, Probs, ghUrl } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { Ext } from '../ui/icons';
import { useLoad } from '../ui/load';
import { studentData } from './Student';
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
    return { ...base, sub, week: '', status: <span class="chip">{l.kind === 'absent' ? 'Not computed yet' : 'Unreadable'}</span>, next: [['', l.kind === 'absent' ? 'Open it and press Refresh.' : 'The status file could not be read.']], ro: false, past: false, urgency: 0 };
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

/** A catalogue row the person has no role in: greyed, and not a link. */
function OffRow({ name }: { name: string }) {
  return (
    <li>
      <div class="cohort-card ro off" aria-disabled="true">
        <span class="cc-name">{name}<span>{NOT_MINE}</span></span>
        <span class="cc-week" />
        <span />
        <span class="cc-next" />
      </div>
    </li>
  );
}

/** Past semesters shown at once, and added by each Show more. */
const PAGE = 10;

/** A section's head: its title, how many rows My courses hides, and the checkbox (only when the section has rows that are not the person's). */
function SectionHead({ id, title, others = 0, only = true, onFlip }: { id: string; title: string; others?: number; only?: boolean; onFlip?: () => void }) {
  return (
    <div class="section-head">
      <h2 id={id}>{title}{only && others ? <span class="meta"> (+{others} {others === 1 ? 'other' : 'others'})</span> : null}</h2>
      {others ? <label class="check my-only"><input type="checkbox" checked={only} onChange={onFlip} /><span>My courses</span></label> : null}
    </div>
  );
}

/** One row of This semester or Past semesters: the person's card, or a greyed catalogue semester. */
interface SemRow {
  key: string;
  course: string;
  rank: number;
  mine: boolean;
  el: preact.JSX.Element;
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

/** A semester the person studies in, as the instructor's cards show theirs: its week and what comes next, once its facts are read. */
function semesterCard(s: Semester, facts: SemesterFacts | null | undefined, now: number): Card {
  const live = !s.archived && facts;
  return {
    key: s.org,
    name: semesterName(s),
    sub: s.archived ? 'Archived; your work stays yours to read' : 'You are a student',
    week: live ? semesterLine(facts, now).week ?? '' : '',
    status: <span class="chip">{s.archived ? 'Archived' : 'Current'}</span>,
    next: live ? [['', nextLine(facts, now)]] : [],
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

/** The shared facts of each live semester in `semesters`, for their cards; {} until read. */
function useSemesterFacts(semesters: Semester[]): Record<string, SemesterFacts | null> {
  const env = useEnv();
  const live = semesters.filter((s) => !s.archived);
  const load = useLoad(
    env && live.length ? async () => Object.fromEntries(await Promise.all(live.map(async (s) => [s.org, await studentData(env.client).facts(s.org).catch(() => null)] as const))) : null,
    [live.map((s) => s.org).join(',')],
  );
  return load.kind === 'ready' ? load.value : {};
}

/** Your semesters as cards: the live ones first, then (unless `currentOnly`) the archived ones; under their own headings when `sections`. */
function SemesterCards({ semesters, now, sections, current = false }: { semesters: Semester[]; now: number; sections: boolean; current?: boolean }) {
  const facts = useSemesterFacts(semesters);
  const card = (s: Semester) => <CardRow c={semesterCard(s, facts[s.org], now)} />;
  const live = semesters.filter((s) => !s.archived);
  const past = current ? [] : semesters.filter((s) => s.archived);
  if (!sections) return <ul class="cohort-list">{[...live, ...past].map(card)}</ul>;
  return (
    <>
      <section class="section" aria-labelledby="h-live">
        <SectionHead id="h-live" title="This semester" />
        {live.length ? <ul class="cohort-list">{live.map(card)}</ul> : <p class="footnote">None of your semesters is running.</p>}
      </section>
      {past.length ? (
        <section class="section" aria-labelledby="h-past">
          <SectionHead id="h-past" title="Past semesters" />
          <ul class="cohort-list">{past.map(card)}</ul>
        </section>
      ) : null}
    </>
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
  const [only, setOnly] = useState<Record<CatalogueSection, boolean>>(() => ({ courses: myCoursesOnly(user.login, 'courses'), now: myCoursesOnly(user.login, 'now'), past: myCoursesOnly(user.login, 'past') }));
  const [pastShown, setPastShown] = useState(PAGE);
  const [current, setCurrent] = useState(() => currentOnly(user.login));
  const catalogue = useCatalogue(courses);
  // An invitation whose role cannot be told is most likely a student's.
  if (!courses.length && (semesters.length || (invited.length && invited.every((i) => i.role === null)))) {
    const flipCurrent = () => {
      saveCurrentOnly(user.login, !current);
      setCurrent(!current);
    };
    return (
      <>
        <Crumbs items={[{ t: 'Your semesters' }]} />
        <div class="page-head">
          <div><h1>Your semesters <Hint>Every semester you are a student of. Archived ones stay as history.</Hint></h1></div>
          {semesters.some((x) => x.archived) ? <div class="actions"><label class="check my-only"><input type="checkbox" checked={current} onChange={flipCurrent} /><span>Current only</span></label></div> : null}
        </div>
        <div class="stack">
          <Invitations invited={invited} kind={kind} />
          {semesters.length ? <SemesterCards semesters={semesters} now={now} sections current={current} /> : null}
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
  const mine = [...courses].sort((a, b) => need(b) - need(a) || a.name.localeCompare(b.name));
  const foreign = catalogue.list.filter((c) => !c.mine).sort((a, b) => a.name.localeCompare(b.name));
  // A semester the person studies in shows only under Your semesters, never greyed.
  const studying = new Set(semesters.map((s) => s.org.toLowerCase()));
  const offSemesters = (pick: (s: CatalogueCourse['semesters'][number]) => boolean): SemRow[] =>
    foreign.flatMap((c) => c.semesters.filter((s) => !studying.has(s.org.toLowerCase()) && pick(s)).map((s) => ({ key: s.org, course: c.name, rank: termRank(s.org), mine: false, el: <OffRow name={`${c.name}, ${s.termLabel}`} /> })));
  // By course, newest first within each.
  const othersNow = offSemesters((s) => runningNow(s, now)).sort((a, b) => a.course.localeCompare(b.course) || b.rank - a.rank || a.key.localeCompare(b.key));
  // Newest first; within one semester the person's own first.
  const pastRows = [...past.map((c): SemRow => ({ key: c.key, course: '', rank: termRank(c.key), mine: true, el: <CardRow c={c} /> })), ...offSemesters((s) => endedNow(s, now))]
    .sort((a, b) => b.rank - a.rank || Number(b.mine) - Number(a.mine) || a.key.localeCompare(b.key));
  const pastOthers = pastRows.filter((r) => !r.mine).length;
  const pastShownRows = (only.past ? pastRows.filter((r) => r.mine) : pastRows);
  const flip = (section: CatalogueSection) => () => {
    saveMyCoursesOnly(user.login, section, !only[section]);
    setOnly({ ...only, [section]: !only[section] });
    if (section === 'past') setPastShown(PAGE);
  };
  return (
    <>
      <Crumbs items={[{ t: 'All courses' }]} />
      <div class="page-head all-courses-head">
        <div><h1>All courses <Hint doc="01-new-course-org.md">A course org is a standing staging area for the materials and assignment templates you are working on, for every semester. Each semester runs in its own org: students join it, and materials are released, assignments handed out and marks returned there.</Hint></h1><p class="lede">Every course the lab runs, ordered by what needs your attention.</p></div>
        <div class="actions"><a class="btn" href="#new-course-1">New course</a></div>
      </div>
      <Invitations invited={invited} kind={kind} />
      {!courses.length ? (
        <NothingFound kind={kind} />
      ) : (
        <div class="stack all-courses">
          <section class="section" aria-labelledby="h-courses">
            <SectionHead id="h-courses" title="DSL courses" others={foreign.length} only={only.courses} onFlip={flip('courses')} />
            <ul class="cohort-list">
              {mine.map((c) => (
                <li><a class={`cohort-card${c.write ? '' : ' ro'}`} href={`?course=${c.org}#course`}><span class="cc-name">{c.name}<span>{courseSub(c, user)}</span></span><span class="cc-week" /><span /><span class="cc-next" /></a></li>
              ))}
              {only.courses ? null : foreign.map((c) => <OffRow name={c.name} />)}
            </ul>
            {catalogue.state === 'loading' && !only.courses ? <p class="footnote">Reading the catalogue…</p> : catalogue.state === 'failed' ? <p class="footnote">The catalogue could not be read.</p> : null}
          </section>
          <section class="section" aria-labelledby="h-live">
            <SectionHead id="h-live" title="This semester" others={othersNow.length} only={only.now} onFlip={flip('now')} />
            {live.length || (othersNow.length && !only.now) ? (
              <ul class="cohort-list">
                {live.map((c) => <CardRow c={c} />)}
                {only.now ? null : othersNow.map((r) => r.el)}
              </ul>
            ) : (
              <p class="footnote">{othersNow.length ? 'None of your semesters is running.' : 'No semester is running.'}</p>
            )}
          </section>
          {pastRows.length ? (
            <section class="section" aria-labelledby="h-past">
              <SectionHead id="h-past" title="Past semesters" others={pastOthers} only={only.past} onFlip={flip('past')} />
              {pastShownRows.length ? <ul class="cohort-list">{pastShownRows.slice(0, pastShown).map((r) => r.el)}</ul> : <p class="footnote">None of your semesters has ended.</p>}
              {pastShownRows.length > pastShown ? <div class="actions"><button class="btn outline small" type="button" onClick={() => setPastShown(pastShown + PAGE)}>Show more</button></div> : null}
            </section>
          ) : null}
          {courseOnly.length ? (
            <section class="section">
              <h2>Courses with no semester yet</h2>
              <ul class="cohort-list">
                {courseOnly.map((c) => (
                  <li><a class={`cohort-card${c.write ? '' : ' ro'}`} href={`?course=${c.org}#course`}><span class="cc-name">{c.name}<span>{c.code}</span></span><span class="cc-week" /><span /><span class="cc-next" /></a></li>
                ))}
              </ul>
            </section>
          ) : null}
          {semesters.length ? (
            <section class="section" aria-labelledby="h-semesters">
              <h2 id="h-semesters">Your semesters</h2>
              <SemesterCards semesters={semesters} now={now} sections={false} />
            </section>
          ) : null}
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
