// The console's root: sign-in, discovery, the route, and which screen renders: the
// instructor screens, or a semester's student screens (a student's own, or an instructor's
// Student view).

import { computed, signal } from '@preact/signals';
import { EnvCtx, type Env } from './env';
import { useEffect } from 'preact/hooks';
import { createAuth, type ConsoleAuth } from './auth/console';
import { GitHubClient, type GhUser } from './github/client';
import { discoverEstate, studentSemesters, type Estate, type Mode } from './model/discovery';
import { LiveFiles } from './model/files';
import { forgetMyTeams } from './model/mine';
import { forgetStudentPrefs } from './model/prefs';
import { courseLeftovers, semesterLeftovers, type Leftover } from './model/migration';
import { loadHeartbeat, type Heartbeat } from './model/heartbeat';
import { StatusStore, type Loaded } from './model/status';
import { DispatchAdapter, outcomePath } from './ops/adapter';
import { OpPanel } from './ops/Panel';
import { OpsSession } from './ops/session';
import { ArchiveScreen } from './screens/Archive';
import { DetailsScreen, MaterialsScreen, WebsiteScreen } from './screens/CourseEdit';
import { COHORT_SCREENS, COURSE_SCREENS, WIZARD_NAV, landing, modeOf, movedHash, parseHash, replaceHash, parseSearch, resolveContext, studentContext, wizardOf } from './router';
import { AssignmentScreen, AssignmentsScreen } from './screens/Assignments';
import { CohortScreen } from './screens/Cohort';
import { CourseScreen, TemplateScreen } from './screens/Course';
import { MaterialsIndexScreen, TemplatesIndexScreen } from './screens/CourseIndex';
import { HomeScreen, ReadonlyScreen, SignInScreen } from './screens/Home';
import { InstructorsScreen, StudentsScreen } from './screens/People';
import { ReleaseScreen, ScheduleScreen } from './screens/Schedule';
import { OperationsScreen, SiteScreen } from './screens/Site';
import { HelpScreen } from './screens/Help';
import { MarksOverviewScreen } from './screens/Marking';
import { NewAssignmentScreen } from './screens/NewAssignment';
import { NewCohortScreen } from './screens/NewCohort';
import { NewCourseScreen } from './screens/NewCourse';
import { NewMaterialsScreen } from './screens/NewMaterials';
import { MigrationUnknownScreen, NotMigratedScreen } from './screens/NotMigrated';
import { StudentScreen, forgetStudentData, studentScreen } from './screens/Student';
import { JoinCourseScreen } from './screens/StudentJoin';
import type { CohortProps, CourseProps } from './screens/types';
import { Loading } from './ui/bits';
import { ScreenBoundary } from './ui/boundary';
import { forgetRendered } from './ui/rendered';
import { Footer, Sidenav, StudentNav, Topbar } from './ui/shell';
import { CONFIG_REPO, COURSE_REPO } from './model/names';

export interface AppDeps {
  auth: ConsoleAuth;
  client: GitHubClient;
}

export function createDeps(): AppDeps {
  const auth = createAuth();
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
  const moved = movedHash(s.hash.value);
  useEffect(() => {
    if (moved) {
      replaceHash(moved);
      s.hash.value = moved;
    }
  }, [moved]);
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

  const estate = s.estate.value;
  if (!estate) {
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

  const courses = estate.courses;
  const semesters = studentSemesters(estate);
  const sel = parseSearch(s.search.value);
  const title = s.mode.value === 'student' ? 'Student Console' : 'Instructor Console';

  if (sel.join) {
    return (
      <EnvCtx.Provider value={s.env(user)}>
        <Topbar user={user} title="Student Console" navOpen={false} onMenu={() => {}} onSignOut={s.signOut} />
        <div class="shell" style="grid-template-columns:minmax(0,1fr)"><main id="view" tabindex={-1}><ScreenBoundary key={s.search.value}><JoinCourseScreen org={sel.join} /></ScreenBoundary></main></div>
        <Footer />
      </EnvCtx.Provider>
    );
  }

  const stu = studentContext(estate, sel, s.archivedOf);
  if (stu?.pending) {
    return (
      <>
        <Topbar user={user} title={title} navOpen={false} onMenu={() => {}} onSignOut={s.signOut} />
        <div class="shell" style="grid-template-columns:minmax(0,1fr)"><main><Loading what="Opening the semester" /></main></div>
        <Footer />
      </>
    );
  }
  if (stu) {
    const key = studentScreen(route.screen);
    return (
      <EnvCtx.Provider value={s.env(user)}>
        <Topbar user={user} title={title} onSignOut={s.signOut} navOpen={s.navOpen.value} onMenu={s.toggleNav} />
        <div class="shell">
          <aside class="sidenav" id="sidenav-wrap" aria-label="Semester navigation">
            <StudentNav courses={courses} cohortStates={{}} semesters={semesters} semester={stu.semester} current={key} />
          </aside>
          <main id="view" tabindex={-1}>
            <ScreenBoundary key={s.search.value + s.hash.value}><StudentScreen semester={stu.semester} screen={key} studentView={stu.studentView} entry={route.entry} now={s.now.value} /></ScreenBoundary>
          </main>
        </div>
        <Footer />
      </EnvCtx.Provider>
    );
  }

  const screen = route.screen || landing(courses);
  const r = { ...route, screen };
  const ctx = resolveContext(courses, sel, r);
  const wiz = wizardOf(screen);
  // An org still on retired names gets one screen, before anything of it is read. Only the
  // orgs the page is about are checked: a semester only when one of its screens opens.
  const nav = s.search.value + s.hash.value;
  const aboutCourse = !!ctx.course && screen !== 'home' && screen !== 'help' && wiz?.name !== 'new-course';
  const semesterPage = !!ctx.cohort && !!ctx.course?.write && !wiz && screen !== 'home' && screen !== 'help' && !(screen in COURSE_SCREENS);
  const courseLeft = aboutCourse ? s.leftovers('course', ctx.course!.org, nav) : [];
  const semLeft = semesterPage ? s.leftovers('semester', ctx.cohort!.org, nav) : [];
  const failed = courseLeft === 'failed' ? { what: 'course' as const, org: ctx.course!.org } : semLeft === 'failed' ? { what: 'semester' as const, org: ctx.cohort!.org } : null;
  const pending = courseLeft === undefined || semLeft === undefined;
  const unmigrated = Array.isArray(courseLeft) && courseLeft.length ? { what: 'course' as const, org: ctx.course!.org, list: courseLeft } : Array.isArray(semLeft) && semLeft.length ? { what: 'semester' as const, org: ctx.cohort!.org, list: semLeft } : null;
  const blocked = !!unmigrated || !!failed || pending;
  const cohortStates: Record<string, Loaded> = {};
  const wanted = blocked ? [] : screen === 'home' ? courses.filter((c) => c.write).flatMap((c) => c.cohorts) : ctx.course?.write ? ctx.course.cohorts : [];
  for (const k of wanted) cohortStates[k.org] = s.statuses.cohort(k.org).value;
  const cohortLoaded = ctx.cohort && ctx.course?.write && !blocked ? s.statuses.cohort(ctx.cohort.org).value : undefined;
  const problems = cohortLoaded?.kind === 'ready' ? (cohortLoaded.status.problems ?? []).length : 0;
  const navKey = COHORT_SCREENS[screen] ?? COURSE_SCREENS[screen] ?? (wiz ? WIZARD_NAV[wiz.name] : undefined) ?? screen;

  let body;
  if (unmigrated) {
    body = <NotMigratedScreen what={unmigrated.what} org={unmigrated.org} leftovers={unmigrated.list} />;
  } else if (failed) {
    body = <MigrationUnknownScreen what={failed.what} org={failed.org} />;
  } else if (pending) {
    body = <Loading what="Opening" />;
  } else if (wiz?.name === 'new-course') {
    body = <NewCourseScreen files={s.files} step={wiz.step} />;
  } else if (screen === 'help') {
    body = <HelpScreen />;
  } else if (screen === 'home') {
    body = <HomeScreen courses={courses} semesters={semesters} invited={estate.invited} kind={estate.kind} cohortStates={cohortStates} now={s.now.value} user={user} />;
  } else if (!ctx.course) {
    body = <HomeScreen courses={courses} semesters={semesters} invited={estate.invited} kind={estate.kind} cohortStates={cohortStates} now={s.now.value} user={user} />;
  } else if (!ctx.course.write) {
    body = <ReadonlyScreen course={ctx.course} cohort={ctx.cohort} />;
  } else if (wiz || screen in COURSE_SCREENS || !ctx.cohort) {
    const cp: CourseProps = { migrated: Array.isArray(courseLeft) && !courseLeft.length, course: ctx.course, loaded: s.statuses.course(ctx.course.org).value, cohortStates, files: s.files, now: s.now.value, entry: route.entry };
    body = wiz?.name === 'new-semester' ? <NewCohortScreen {...cp} step={wiz.step} />
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
      semester: () => <CohortScreen {...cp} />,
      schedule: () => <ScheduleScreen {...cp} />,
      release: () => <ReleaseScreen {...cp} />,
      assignments: () => <AssignmentsScreen {...cp} />,
      assignment: () => <AssignmentScreen {...cp} />,
      students: () => <StudentsScreen {...cp} />,
      roster: () => <StudentsScreen {...cp} />,
      instructors: () => <InstructorsScreen {...cp} />,
      site: () => <SiteScreen {...cp} />,
      operations: () => <OperationsScreen {...cp} />,
      marks: () => <MarksOverviewScreen {...cp} />,
      archive: () => <ArchiveScreen {...cp} />,
    };
    body = (screens[screen] ?? screens.semester)();
  }

  return (
    <EnvCtx.Provider value={s.env(user)}>
      <Topbar user={user} title={title} course={ctx.course} cohort={ctx.cohort} onSignOut={s.signOut} navOpen={s.navOpen.value} onMenu={s.toggleNav} />
      <div class="shell">
        <aside class="sidenav" id="sidenav-wrap" aria-label="Console navigation">
          <Sidenav courses={courses} semesters={semesters} course={ctx.course} cohort={ctx.cohort} cohortStates={cohortStates} current={navKey} problems={problems} />
        </aside>
        <main id="view" tabindex={-1}>
          {sel.wizard && ctx.course && !wiz ? <p class="note" style="margin-bottom:18px"><a href={`?course=${ctx.course.org}#${sel.wizard}`}>Back to the {sel.wizard.startsWith('new-semester') ? 'New semester' : 'wizard'}</a> when you are done here.</p> : null}
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
  const archived = new Map<string, ReturnType<typeof signal<boolean | undefined>>>();
  const left = new Map<string, { sig: ReturnType<typeof signal<Leftover[] | 'failed' | undefined>>; nav: string }>();
  let env: Env | null = null;
  const ops = new OpsSession(new DispatchAdapter(client, () => st.user.value?.login ?? ''), {
    onFinished: (def) => {
      if (def.cohortOrg) {
        void statuses.reload(def.cohortOrg, CONFIG_REPO);
        files.refresh(def.cohortOrg, CONFIG_REPO, outcomePath(def.op));
      }
      void statuses.reload(def.courseOrg, COURSE_REPO);
    },
  });
  const estate = signal<Estate | null>(null);
  const search = signal(typeof location !== 'undefined' ? location.search : '');
  const st = {
    auth,
    user: signal<GhUser | null>(null),
    restoring: signal(true),
    /** Every course and semester the person can see, and their role in each; null until discovered. */
    estate,
    /** Which shell renders: a semester's student screens, or the instructor screens. */
    mode: computed<Mode>(() => (estate.value ? modeOf(estate.value, parseSearch(search.value)) : 'instructor')),
    error: signal<string | null>(null),
    hash: signal(typeof location !== 'undefined' ? location.hash : ''),
    search,
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
    discover(): Promise<Estate> {
      return discoverEstate(client, { kind: auth.kind() ?? 'classic', login: st.user.value?.login ?? '' });
    },
    /** Whether a semester's `.github` is archived, read once on first ask; undefined until it answers. */
    archivedOf(org: string): boolean | undefined {
      let a = archived.get(org);
      if (!a) {
        const sig = signal<boolean | undefined>(undefined);
        a = sig;
        archived.set(org, sig);
        void client.getRepo(org, COURSE_REPO).then((r) => (sig.value = r?.archived === true), () => (sig.value = false));
      }
      return a.value;
    },
    /**
     * What an org still carries under a retired name; undefined until it answers. An answer is
     * kept; a check that failed is 'failed' until the next navigation (`nav` changes), which
     * asks again. A failure is never taken for "migrated".
     */
    leftovers(kind: 'course' | 'semester', org: string, nav: string): Leftover[] | 'failed' | undefined {
      const k = `${kind}:${org.toLowerCase()}`;
      let l = left.get(k);
      if (!l || (l.sig.value === 'failed' && l.nav !== nav)) {
        const sig = signal<Leftover[] | 'failed' | undefined>(undefined);
        l = { sig, nav };
        left.set(k, l);
        void (kind === 'course' ? courseLeftovers(client, org) : semesterLeftovers(client, org)).then((v) => (sig.value = v), () => (sig.value = 'failed'));
      }
      return l.sig.value;
    },
    async rediscover() {
      try {
        st.estate.value = await st.discover();
      } catch {
        /* the old list stays; the next sign-in reads it again */
      }
    },
    signedIn(u: GhUser) {
      st.user.value = u;
      st.restoring.value = false;
      st.error.value = null;
      st.discover()
        .then((e) => (st.estate.value = e))
        .catch((e: unknown) => (st.error.value = `Could not list your courses: ${e instanceof Error ? e.message : String(e)}`));
    },
    signOut() {
      const login = st.user.value?.login;
      if (login) forgetStudentPrefs(login);
      forgetRendered();
      forgetMyTeams(client);
      forgetStudentData(client);
      auth.signOut();
      client.clearCache();
      files.forget();
      statuses.forget();
      beats.clear();
      archived.clear();
      left.clear();
      ops.current.value = null;
      env = null;
      st.user.value = null;
      st.estate.value = null;
      st.restoring.value = false;
    },
    toggleNav() {
      st.navOpen.value = !st.navOpen.value;
      document.body.classList.toggle('nav-open', st.navOpen.value);
    },
  };
  auth.onLost = () => st.signOut();
  return st;
}
