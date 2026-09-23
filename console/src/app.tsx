// The console's root: sign-in, discovery, the route, and which screen renders.

import { signal } from '@preact/signals';
import { useEffect } from 'preact/hooks';
import { PatAuth } from './auth/pat';
import type { Auth } from './auth/types';
import { GitHubClient, type GhUser } from './github/client';
import { discoverCourses, type Course } from './model/discovery';
import { LiveFiles } from './model/files';
import { loadHeartbeat, type Heartbeat } from './model/heartbeat';
import { StatusStore, type Loaded } from './model/status';
import { COHORT_SCREENS, COURSE_SCREENS, landing, parseHash, parseSearch, resolveContext } from './router';
import { AssignmentScreen, AssignmentsScreen } from './screens/Assignments';
import { CohortScreen } from './screens/Cohort';
import { CourseScreen, TemplateScreen } from './screens/Course';
import { HomeScreen, LaterScreen, ReadonlyScreen, SignInScreen } from './screens/Home';
import { StaffScreen, StudentsScreen } from './screens/People';
import { ReleaseScreen, ScheduleScreen } from './screens/Schedule';
import { OperationsScreen, SiteScreen } from './screens/Site';
import type { CohortProps, CourseProps } from './screens/types';
import { Loading } from './ui/bits';
import { Footer, Sidenav, Topbar } from './ui/shell';

export interface AppDeps {
  auth: Auth;
  client: GitHubClient;
}

export function createDeps(): AppDeps {
  const auth = new PatAuth();
  const client = new GitHubClient({ token: () => auth.token() });
  return { auth, client };
}

const LATER: Record<string, string> = {
  teams: 'Teams per group assignment: the window, teams with their members, and students without a team.',
  archive: 'What archiving does, the scheduled date from the schedule, and a preview listing every repo.',
  details: 'The course’s name, code, description, admins and defaults as an editable page. The current values are on the course overview.',
  website: 'The public website’s state, source, readings and lectures, with Preview and Publish.',
};

export function App({ state: s }: { state: AppState }) {
  const auth = s.auth;

  useEffect(() => {
    const sync = () => {
      s.hash.value = location.hash;
      s.search.value = location.search;
    };
    const onClick = (e: MouseEvent) => {
      const a = (e.target as Element)?.closest?.('a');
      const href = a?.getAttribute('href');
      if (!a || !href || !href.startsWith('?') || e.metaKey || e.ctrlKey || e.shiftKey) return;
      e.preventDefault();
      history.pushState(null, '', href);
      sync();
    };
    window.addEventListener('hashchange', sync);
    window.addEventListener('popstate', sync);
    document.addEventListener('click', onClick);
    const tick = setInterval(() => (s.now.value = Date.now()), 60000);
    if (!s.user.value) void auth.restore().then((u) => (u ? s.signedIn(u) : (s.restoring.value = false)));
    return () => {
      window.removeEventListener('hashchange', sync);
      window.removeEventListener('popstate', sync);
      document.removeEventListener('click', onClick);
      clearInterval(tick);
    };
  }, []);

  const route = parseHash(s.hash.value);
  useEffect(() => {
    document.body.classList.remove('nav-open');
    s.navOpen.value = false;
    const target = { materials: 'sec-materials', templates: 'sec-templates' }[route.screen];
    const el = target ? document.getElementById(target) : route.screen === 'schedule' && route.entry ? document.querySelector('.trow.current') : null;
    if (el) el.scrollIntoView({ block: route.screen === 'schedule' ? 'center' : 'start' });
    else window.scrollTo(0, 0);
  }, [s.hash.value, s.search.value]);

  const user = s.user.value;
  if (!user) {
    return (
      <>
        <Topbar user={null} navOpen={false} onMenu={() => {}} />
        <div class="shell" style="grid-template-columns:minmax(0,1fr)">
          <main>{s.restoring.value ? <Loading what="Signing in" /> : <SignInScreen auth={auth} onSignedIn={s.signedIn} />}</main>
        </div>
        <Footer />
      </>
    );
  }

  const courses = s.courses.value;
  if (!courses) {
    return (
      <>
        <Topbar user={user} navOpen={false} onMenu={() => {}} onSignOut={s.signOut} />
        <div class="shell" style="grid-template-columns:minmax(0,1fr)">
          <main>{s.error.value ? <div class="check-line bad"><span>{s.error.value}</span></div> : <Loading what="Finding your courses" />}</main>
        </div>
        <Footer />
      </>
    );
  }

  const screen = route.screen || landing(courses);
  const r = { ...route, screen };
  const ctx = resolveContext(courses, parseSearch(s.search.value), r);
  const cohortStates: Record<string, Loaded> = {};
  const wanted = screen === 'home' ? courses.filter((c) => c.write).flatMap((c) => c.cohorts) : ctx.course?.write ? ctx.course.cohorts : [];
  for (const k of wanted) cohortStates[k.org] = s.statuses.cohort(k.org).value;
  const cohortLoaded = ctx.cohort && ctx.course?.write ? s.statuses.cohort(ctx.cohort.org).value : undefined;
  const problems = cohortLoaded?.kind === 'ready' ? (cohortLoaded.status.problems ?? []).length : 0;
  const navKey = COHORT_SCREENS[screen] ?? COURSE_SCREENS[screen] ?? screen;

  let body;
  if (screen === 'home') {
    body = <HomeScreen courses={courses} cohortStates={cohortStates} now={s.now.value} user={user} />;
  } else if (!ctx.course) {
    body = <HomeScreen courses={courses} cohortStates={cohortStates} now={s.now.value} user={user} />;
  } else if (!ctx.course.write) {
    body = <ReadonlyScreen course={ctx.course} cohort={ctx.cohort} />;
  } else if (screen in COURSE_SCREENS || !ctx.cohort) {
    const cp: CourseProps = { course: ctx.course, loaded: s.statuses.course(ctx.course.org).value, cohortStates, files: s.files, now: s.now.value, entry: route.entry };
    const crumbs = [{ t: ctx.course.name, href: '#course' }, { t: screen === 'details' ? 'Course details' : 'Public website' }];
    body = screen === 'template' ? <TemplateScreen {...cp} />
      : screen === 'details' || screen === 'website' ? <LaterScreen title={crumbs[1].t} crumbs={crumbs} what={LATER[screen]} />
      : <CourseScreen {...cp} />;
  } else {
    const cp: CohortProps = {
      course: ctx.course, cohort: ctx.cohort, loaded: cohortLoaded ?? { kind: 'loading' }, files: s.files, now: s.now.value, entry: route.entry,
      heartbeat: s.heartbeat(ctx.course.org),
    };
    const later = (t: string) => <LaterScreen title={t} crumbs={[{ t: `${ctx.course!.name}, ${ctx.cohort!.termLabel}`, href: '#cohort' }, { t }]} what={LATER[screen]} />;
    const screens: Record<string, () => preact.JSX.Element> = {
      cohort: () => <CohortScreen {...cp} />,
      schedule: () => <ScheduleScreen {...cp} />,
      release: () => <ReleaseScreen {...cp} />,
      assignments: () => <AssignmentsScreen {...cp} />,
      assignment: () => <AssignmentScreen {...cp} />,
      students: () => <StudentsScreen {...cp} />,
      roster: () => <StudentsScreen {...cp} />,
      staff: () => <StaffScreen {...cp} />,
      site: () => <SiteScreen {...cp} />,
      operations: () => <OperationsScreen {...cp} />,
      teams: () => later('Teams'),
      archive: () => later(`Archive ${ctx.cohort!.termLabel}`),
    };
    body = (screens[screen] ?? screens.cohort)();
  }

  return (
    <>
      <Topbar user={user} course={ctx.course} cohort={ctx.cohort} onSignOut={s.signOut} navOpen={s.navOpen.value} onMenu={s.toggleNav} />
      <div class="shell">
        <aside class="sidenav" id="sidenav-wrap" aria-label="Console navigation">
          <Sidenav courses={courses} course={ctx.course} cohort={ctx.cohort} cohortStates={cohortStates} current={navKey} problems={problems} />
        </aside>
        <main id="view" tabindex={-1}>{body}</main>
      </div>
      <Footer course={ctx.course} cohort={ctx.cohort} />
    </>
  );
}

// --------------------------------------------------------------------------- state

export type AppState = ReturnType<typeof createState>;

export function createState({ auth, client }: AppDeps) {
  const statuses = new StatusStore(client);
  const files = new LiveFiles(client);
  const beats = new Map<string, ReturnType<typeof signal<Heartbeat | null | undefined>>>();
  const st = {
    auth,
    user: signal<GhUser | null>(null),
    restoring: signal(true),
    courses: signal<Course[] | null>(null),
    error: signal<string | null>(null),
    hash: signal(typeof location !== 'undefined' ? location.hash : ''),
    search: signal(typeof location !== 'undefined' ? location.search : ''),
    now: signal(Date.now()),
    navOpen: signal(false),
    statuses,
    files,
    heartbeat(org: string): Heartbeat | null | undefined {
      let b = beats.get(org);
      if (!b) {
        const sig = signal<Heartbeat | null | undefined>(undefined);
        b = sig;
        beats.set(org, sig);
        void loadHeartbeat(client, org, Date.now()).then((h) => (sig.value = h));
      }
      return b.value;
    },
    signedIn(u: GhUser) {
      st.user.value = u;
      st.restoring.value = false;
      st.error.value = null;
      discoverCourses(client)
        .then((c) => (st.courses.value = c))
        .catch((e: unknown) => (st.error.value = `Could not list your courses: ${e instanceof Error ? e.message : String(e)}`));
    },
    signOut() {
      auth.signOut();
      client.clearCache();
      files.forget();
      statuses.forget();
      beats.clear();
      st.user.value = null;
      st.courses.value = null;
      st.restoring.value = false;
    },
    toggleNav() {
      st.navOpen.value = !st.navOpen.value;
      document.body.classList.toggle('nav-open', st.navOpen.value);
    },
  };
  return st;
}
