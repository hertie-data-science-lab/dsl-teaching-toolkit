// The console's root: sign-in, discovery, the route, and which screen renders.

import { signal } from '@preact/signals';
import { EnvCtx, type Env } from './env';
import { useEffect } from 'preact/hooks';
import { PatAuth } from './auth/pat';
import type { Auth } from './auth/types';
import { GitHubClient, type GhUser } from './github/client';
import { discoverCourses, type Course } from './model/discovery';
import { LiveFiles } from './model/files';
import { loadHeartbeat, type Heartbeat } from './model/heartbeat';
import { StatusStore, type Loaded } from './model/status';
import { DispatchAdapter, outcomePath } from './ops/adapter';
import { OpPanel } from './ops/Panel';
import { OpsSession } from './ops/session';
import { ArchiveScreen } from './screens/Archive';
import { DetailsScreen, MaterialsScreen, WebsiteScreen } from './screens/CourseEdit';
import { COHORT_SCREENS, COURSE_SCREENS, WIZARD_NAV, hashOf, landing, parseHash, parseSearch, resolveContext, wizardOf } from './router';
import { AssignmentScreen, AssignmentsScreen } from './screens/Assignments';
import { CohortScreen } from './screens/Cohort';
import { CourseScreen, TemplateScreen } from './screens/Course';
import { MaterialsIndexScreen, TemplatesIndexScreen } from './screens/CourseIndex';
import { HomeScreen, ReadonlyScreen, SignInScreen } from './screens/Home';
import { StaffScreen, StudentsScreen } from './screens/People';
import { ReleaseScreen, ScheduleScreen } from './screens/Schedule';
import { OperationsScreen, SiteScreen } from './screens/Site';
import { HelpScreen } from './screens/Help';
import { MarksOverviewScreen } from './screens/Marking';
import { NewAssignmentScreen } from './screens/NewAssignment';
import { NewCohortScreen } from './screens/NewCohort';
import { NewCourseScreen } from './screens/NewCourse';
import { NewMaterialsScreen } from './screens/NewMaterials';
import type { CohortProps, CourseProps } from './screens/types';
import { Loading } from './ui/bits';
import { ScreenBoundary } from './ui/boundary';
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
  // An old `#teams-<slug>` / `#marks-<slug>` link: show the tab, and write its new hash.
  const canonical = s.hash.value && s.hash.value !== '#' ? hashOf(route) : null;
  useEffect(() => {
    if (canonical && decodeURIComponent(s.hash.value) !== canonical) {
      history.replaceState(null, '', `${location.search}${canonical}`);
      s.hash.value = canonical;
    }
  }, [canonical]);
  useEffect(() => {
    document.body.classList.remove('nav-open');
    s.navOpen.value = false;
    const el = route.screen === 'schedule' && route.entry ? document.querySelector('.trow.current') : null;
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
  const sel = parseSearch(s.search.value);
  const ctx = resolveContext(courses, sel, r);
  const wiz = wizardOf(screen);
  const cohortStates: Record<string, Loaded> = {};
  const wanted = screen === 'home' ? courses.filter((c) => c.write).flatMap((c) => c.cohorts) : ctx.course?.write ? ctx.course.cohorts : [];
  for (const k of wanted) cohortStates[k.org] = s.statuses.cohort(k.org).value;
  const cohortLoaded = ctx.cohort && ctx.course?.write ? s.statuses.cohort(ctx.cohort.org).value : undefined;
  const problems = cohortLoaded?.kind === 'ready' ? (cohortLoaded.status.problems ?? []).length : 0;
  const navKey = COHORT_SCREENS[screen] ?? COURSE_SCREENS[screen] ?? (wiz ? WIZARD_NAV[wiz.name] : undefined) ?? screen;

  let body;
  if (wiz?.name === 'new-course') {
    body = <NewCourseScreen files={s.files} step={wiz.step} />;
  } else if (screen === 'help') {
    body = <HelpScreen />;
  } else if (screen === 'home') {
    body = <HomeScreen courses={courses} cohortStates={cohortStates} now={s.now.value} user={user} />;
  } else if (!ctx.course) {
    body = <HomeScreen courses={courses} cohortStates={cohortStates} now={s.now.value} user={user} />;
  } else if (!ctx.course.write) {
    body = <ReadonlyScreen course={ctx.course} cohort={ctx.cohort} />;
  } else if (wiz || screen in COURSE_SCREENS || !ctx.cohort) {
    const cp: CourseProps = { course: ctx.course, loaded: s.statuses.course(ctx.course.org).value, cohortStates, files: s.files, now: s.now.value, entry: route.entry };
    body = wiz?.name === 'new-cohort' ? <NewCohortScreen {...cp} step={wiz.step} />
      : wiz?.name === 'new-assignment' ? <NewAssignmentScreen {...cp} step={wiz.step} />
      : wiz?.name === 'new-materials' ? <NewMaterialsScreen {...cp} />
      : screen === 'template' ? <TemplateScreen {...cp} />
      : screen === 'details' ? <DetailsScreen {...cp} />
      : screen === 'website' ? <WebsiteScreen {...cp} />
      : screen === 'materials' && route.entry ? <MaterialsScreen {...cp} />
      : screen === 'materials' ? <MaterialsIndexScreen {...cp} />
      : screen === 'templates' ? <TemplatesIndexScreen {...cp} />
      : <CourseScreen {...cp} />;
  } else {
    const cp: CohortProps = {
      course: ctx.course, cohort: ctx.cohort, loaded: cohortLoaded ?? { kind: 'loading' }, files: s.files, now: s.now.value, entry: route.entry, tab: route.tab,
      heartbeat: s.heartbeat(ctx.course.org), prefill: sel.template,
    };
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
      marks: () => <MarksOverviewScreen {...cp} />,
      archive: () => <ArchiveScreen {...cp} />,
    };
    body = (screens[screen] ?? screens.cohort)();
  }

  return (
    <EnvCtx.Provider value={s.env(user)}>
      <Topbar user={user} course={ctx.course} cohort={ctx.cohort} onSignOut={s.signOut} navOpen={s.navOpen.value} onMenu={s.toggleNav} />
      <div class="shell">
        <aside class="sidenav" id="sidenav-wrap" aria-label="Console navigation">
          <Sidenav courses={courses} course={ctx.course} cohort={ctx.cohort} cohortStates={cohortStates} current={navKey} problems={problems} />
        </aside>
        <main id="view" tabindex={-1}>
          {sel.wizard && ctx.course && !wiz ? <p class="note" style="margin-bottom:18px"><a href={`?course=${ctx.course.org}#${sel.wizard}`}>Back to the {sel.wizard.startsWith('new-cohort') ? 'New cohort' : 'wizard'}</a> when you are done here.</p> : null}
          <ScreenBoundary key={s.search.value + s.hash.value}>{body}</ScreenBoundary>
        </main>
      </div>
      <Footer course={ctx.course} cohort={ctx.cohort} />
      <OpPanel />
    </EnvCtx.Provider>
  );
}

// --------------------------------------------------------------------------- state

export type AppState = ReturnType<typeof createState>;

export function createState({ auth, client }: AppDeps) {
  const statuses = new StatusStore(client);
  const files = new LiveFiles(client);
  const beats = new Map<string, ReturnType<typeof signal<Heartbeat | null | undefined>>>();
  let env: Env | null = null;
  const ops = new OpsSession(new DispatchAdapter(client, () => st.user.value?.login ?? ''), {
    onFinished: (def) => {
      if (def.cohortOrg) {
        void statuses.reload(def.cohortOrg, 'classroom-config');
        files.refresh(def.cohortOrg, 'classroom-config', outcomePath(def.op));
      }
      void statuses.reload(def.courseOrg, '.github');
    },
  });
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
    ops,
    /** What screens need to change anything, for the signed-in user. */
    env(user: GhUser): Env {
      if (!env || env.user !== user) env = { client, user, ops, statuses, files, rediscover: st.rediscover };
      return env;
    },
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
    async rediscover() {
      try {
        st.courses.value = await discoverCourses(client);
      } catch {
        /* the old list stays; the next sign-in reads it again */
      }
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
      ops.current.value = null;
      env = null;
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
