// The console's root: sign-in, discovery, the route, and which screen renders: the
// instructor screens, or a semester's student screens (a student's own, or an instructor's
// Student view). Every course and semester page, in either console, opens with the course
// banner (decision 0031 rule 11): crumbs, the course name as the h1, the semester's line.

import { computed, signal } from '@preact/signals';
import { EnvCtx, type Env } from './env';
import { useEffect } from 'preact/hooks';
import { createAuth, type ConsoleAuth } from './auth/console';
import { GitHubClient, type GhUser } from './github/client';
import { discoverEstate, isInstructor, studentSemesters, type Estate, type Mode } from './model/discovery';
import { semesterOver } from './model/catalogue';
import { LiveFiles } from './model/files';
import { forgetMine, forgetMyTeams } from './model/mine';
import { forgetStudentPrefs } from './model/prefs';
import { courseLeftovers, semesterLeftovers, type Leftover } from './model/migration';
import { loadHeartbeat, type Heartbeat } from './model/heartbeat';
import { StatusStore, type Loaded } from './model/status';
import { DispatchAdapter, outcomePath } from './ops/adapter';
import { OpPanel } from './ops/Panel';
import { OpsSession } from './ops/session';
import { ArchiveScreen } from './screens/Archive';
import { DetailsScreen, MaterialsScreen, WebsiteScreen } from './screens/CourseEdit';
import { takeInstallReturn } from './wizards/drafts';
import { COHORT_SCREENS, COURSE_SCREENS, WIZARD_NAV, installReturn, modeOf, movedHash, parseHash, replaceHash, parseSearch, resolveContext, studentContext, studentLanding, wizardOf } from './router';
import { AssignmentScreen, AssignmentsScreen } from './screens/Assignments';
import { CohortScreen } from './screens/Cohort';
import { CourseHeaderActions, CourseHint, CourseScreen, TemplateScreen, courseView, semesterChip, templateTitle } from './screens/Course';
import { MaterialsIndexScreen, TemplatesIndexScreen, otherRepos } from './screens/CourseIndex';
import { HomeScreen, Invitations, ReadonlyScreen, SignInScreen } from './screens/Home';
import { InstructorsScreen, StudentsScreen } from './screens/People';
import { ReleaseScreen, ScheduleScreen } from './screens/Schedule';
import { OperationsScreen, SiteScreen } from './screens/Site';
import { HelpScreen } from './screens/Help';
import { SetupScreen } from './screens/Setup';
import { MarksOverviewScreen } from './screens/Marking';
import { NewAssignmentScreen } from './screens/NewAssignment';
import { NewCohortScreen } from './screens/NewCohort';
import { NewCourseScreen } from './screens/NewCourse';
import { NewMaterialsScreen } from './screens/NewMaterials';
import { MigrationUnknownScreen, NotMigratedScreen } from './screens/NotMigrated';
import { StudentBanner, StudentScreen, forgetStudentData, studentScreen } from './screens/Student';
import { JoinCourseScreen } from './screens/StudentJoin';
import type { CohortProps, CourseProps } from './screens/types';
import { Loading, ghUrl } from './ui/bits';
import { ScreenBoundary } from './ui/boundary';
import { forgetRendered } from './ui/rendered';
import { forgetShown } from './model/materials';
import { CourseBanner, Footer, Sidenav, StudentNav, Topbar, type CourseSubPages, type SubWanted } from './ui/shell';
import { fmtDay } from './model/format';
import { DEFAULT_TIMEZONE } from './model/policy';
import type { SemesterStatus } from './model/types';
import type { Files } from './model/files';
import type { Course } from './model/discovery';
import { CONFIG_REPO, COURSE_REPO, STATUS_PATH } from './model/names';

/** App-level pages: about no course or semester. */
const APP_SCREENS = ['home', 'help', 'profile'];
/** App-level pages that render full width, without the side nav. */
const FULL_WIDTH = ['help', 'profile'];

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

  // A semester's student screens keep their own `#setup` (Set up); only instructor links rename.
  // Until discovery answers, a `?semester=` URL is taken as the student screens' (nothing is
  // rewritten yet); after, only when the person holds a role in that semester.
  const asked = parseSearch(s.search.value);
  const studentHash = !!asked.semester && (!s.estate.value || !!studentContext(s.estate.value, asked));
  const route = parseHash(s.hash.value, studentHash);
  // An old `#teams-<slug>` / `#marks-<slug>` link: show the tab, and write its new hash.
  const moved = movedHash(s.hash.value, studentHash);
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
        <Topbar user={null} />
        <div class="shell" style="grid-template-columns:minmax(0,1fr)">
          <main>{s.restoring.value ? <Loading what={s.offline.value ? OFFLINE_RETRY : 'Signing in'} /> : <SignInScreen auth={auth} onSignedIn={s.signedIn} />}</main>
        </div>
        <Footer />
      </>
    );
  }

  const estate = s.estate.value;
  if (!estate) {
    return (
      <>
        <Topbar user={user} onSignOut={s.signOut} />
        <div class="shell" style="grid-template-columns:minmax(0,1fr)">
          <main>{s.error.value ? <div class="check-line bad"><span>{s.error.value}</span></div> : <Loading what="Finding your courses" />}</main>
        </div>
        <Footer />
      </>
    );
  }

  const courses = estate.courses;
  const semesters = studentSemesters(estate);
  const asks = parseSearch(s.search.value);
  // A URL naming no page or org lands a person who teaches nothing in their one live semester.
  const one = !route.screen && !asks.semester && !asks.cohort && !asks.course && !asks.join ? studentLanding(estate, s.now.value) : null;
  const sel = one ? { ...asks, semester: one } : asks;
  const title = s.mode.value === 'student' ? 'Student view' : 'Instructor view';
  // Guide explains the instructor console: only a person with an instructor role sees it.
  const guide = courses.length > 0 || isInstructor(estate);
  // The landing page's name: All courses for anyone with a course, else Your semesters.
  const root = courses.length ? 'All courses' : 'Your semesters';

  if (sel.join) {
    return (
      <EnvCtx.Provider value={s.env(user)}>
        <Topbar user={user} title="Student view" onSignOut={s.signOut} guide={guide} />
        <div class="shell" style="grid-template-columns:minmax(0,1fr)"><main id="view" tabindex={-1}><ScreenBoundary key={s.search.value}><JoinCourseScreen org={sel.join} root={root} /></ScreenBoundary></main></div>
        <Footer sub={root} />
      </EnvCtx.Provider>
    );
  }

  // Profile and Guide are app-level: a semester in the query does not turn them into student screens.
  const stu = FULL_WIDTH.includes(route.screen) ? null : studentContext(estate, sel, s.archivedOf);
  if (stu?.pending) {
    return (
      <>
        <Topbar user={user} title={title} onSignOut={s.signOut} guide={guide} />
        <div class="shell" style="grid-template-columns:minmax(0,1fr)"><main><Loading what="Opening the semester" /></main></div>
        <Footer sub={root} />
      </>
    );
  }
  if (stu) {
    const key = studentScreen(route.screen);
    const back = `?cohort=${stu.semester.org}#dashboard`;
    return (
      <EnvCtx.Provider value={s.env(user)}>
        <Topbar user={user} title={stu.studentView ? 'Student view (preview)' : title} titleHref={stu.studentView ? back : undefined} onSignOut={s.signOut} navOpen={s.navOpen.value} onMenu={s.toggleNav} guide={guide} />
        <div class="shell">
          <aside class="sidenav" id="sidenav-wrap" aria-label="Semester navigation">
            <StudentNav root={root} semesters={semesters} semester={stu.semester} current={key} studentView={stu.studentView} now={s.now.value} />
          </aside>
          <main id="view" tabindex={-1}>
            {estate.invited?.length ? <Invitations invited={estate.invited} kind={estate.kind} /> : null}
            <StudentBanner root={root} screen={key} semester={stu.semester} studentView={stu.studentView} chip={semesterChip({ live: !stu.semester.archived, ended: semesterOver(stu.semester, s.now.value) })} now={s.now.value} />
            <ScreenBoundary key={s.search.value + s.hash.value}><StudentScreen semester={stu.semester} screen={key} studentView={stu.studentView} entry={route.entry} now={s.now.value} /></ScreenBoundary>
          </main>
        </div>
        <Footer title={stu.semester.courseName || undefined} sub={stu.semester.termLabel} />
      </EnvCtx.Provider>
    );
  }

  // A URL naming no page lands an instructor on All courses (decision 0030 rule 1).
  const screen = route.screen || 'home';
  const r = { ...route, screen };
  const ctx = resolveContext(courses, sel, r);
  const wiz = wizardOf(screen);
  // An org still on retired names gets one screen, before anything of it is read. Only the
  // orgs the page is about are checked: a semester only when one of its screens opens.
  const nav = s.search.value + s.hash.value;
  const aboutCourse = !!ctx.course && !APP_SCREENS.includes(screen) && wiz?.name !== 'new-course';
  const semesterPage = !!ctx.cohort && !!ctx.course?.write && !wiz && !APP_SCREENS.includes(screen) && !(screen in COURSE_SCREENS);
  const courseLeft = aboutCourse ? s.leftovers('course', ctx.course!.org, nav) : [];
  const semLeft = semesterPage ? s.leftovers('semester', ctx.cohort!.org, nav) : [];
  const failed = courseLeft === 'failed' ? { what: 'course' as const, org: ctx.course!.org } : semLeft === 'failed' ? { what: 'semester' as const, org: ctx.cohort!.org } : null;
  const pending = courseLeft === undefined || semLeft === undefined;
  const unmigrated = Array.isArray(courseLeft) && courseLeft.length ? { what: 'course' as const, org: ctx.course!.org, list: courseLeft } : Array.isArray(semLeft) && semLeft.length ? { what: 'semester' as const, org: ctx.cohort!.org, list: semLeft } : null;
  const blocked = !!unmigrated || !!failed || pending;
  const cohortStates: Record<string, Loaded> = {};
  const wanted = blocked ? [] : screen === 'home' ? courses.filter((c) => c.write).flatMap((c) => c.cohorts) : ctx.course?.write ? ctx.course.cohorts : [];
  // Only the open semester's pages show staleness: every other semester read skips the tree.
  for (const k of wanted) cohortStates[k.org] = s.statuses.cohort(k.org, k.org === ctx.cohort?.org).value;
  const cohortLoaded = ctx.cohort && ctx.course?.write && !blocked ? s.statuses.cohort(ctx.cohort.org).value : undefined;
  const navKey = COHORT_SCREENS[screen] ?? COURSE_SCREENS[screen] ?? (wiz ? WIZARD_NAV[wiz.name] : undefined) ?? screen;
  const navCourse = !blocked && !APP_SCREENS.includes(screen) ? ctx.course : undefined;
  const sub = navCourse ? (wanted: SubWanted) => subPages(navCourse, s.statuses.course(navCourse.org).value, cohortStates, s.files, { ...wanted, titles: !ctx.cohort }) : undefined;

  let body;
  let banner = null;
  // The banner's crumbs follow the tree: All courses › Course (› Semester); the open page's is plain.
  const home = { t: root, href: '?#home' };
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
  } else if (screen === 'profile') {
    body = <SetupScreen org={ctx.course?.org} courses={[...courses.map((c) => ({ org: c.org, name: c.name })), ...semesters.map((k) => ({ org: k.org, name: k.courseName || k.termLabel }))]} />;
  } else if (screen === 'home') {
    body = <HomeScreen courses={courses} semesters={semesters} invited={estate.invited} kind={estate.kind} cohortStates={cohortStates} now={s.now.value} user={user} />;
  } else if (!ctx.course) {
    body = <HomeScreen courses={courses} semesters={semesters} invited={estate.invited} kind={estate.kind} cohortStates={cohortStates} now={s.now.value} user={user} />;
  } else if (!ctx.course.write) {
    body = <ReadonlyScreen course={ctx.course} cohort={ctx.cohort} />;
    // Read only: the same banner, the semester's line without the Student view (no role to preview).
    banner = (
      <CourseBanner crumbs={ctx.cohort ? [home, { t: ctx.course.name, href: `?course=${ctx.course.org}#course` }, { t: ctx.cohort.termLabel }] : [home, { t: ctx.course.name }]}
        name={ctx.course.name} semester={ctx.cohort ? { org: ctx.cohort.org, termLabel: ctx.cohort.termLabel } : undefined} />
    );
  } else if (wiz || screen in COURSE_SCREENS || !ctx.cohort) {
    const cp: CourseProps = { migrated: Array.isArray(courseLeft) && !courseLeft.length, course: ctx.course, loaded: s.statuses.course(ctx.course.org).value, cohortStates, files: s.files, now: s.now.value, entry: route.entry };
    body = wiz?.name === 'new-semester' ? <NewCohortScreen {...cp} step={wiz.step} />
      : wiz?.name === 'new-assignment' ? <NewAssignmentScreen {...cp} step={wiz.step} />
      : wiz?.name === 'new-materials' ? <NewMaterialsScreen {...cp} />
      : screen === 'template' ? <TemplateScreen {...cp} />
      : screen === 'details' ? <DetailsScreen {...cp} />
      : screen === 'website' ? <WebsiteScreen {...cp} />
      : screen === 'materials' && route.entry ? <MaterialsScreen key={route.entry} {...cp} />
      : screen === 'materials' ? <MaterialsIndexScreen {...cp} />
      : screen === 'templates' ? <TemplatesIndexScreen {...cp} />
      : <CourseScreen {...cp} />;
    // The overview: the banner is its head, with New semester; other course pages have no right side.
    const overview = !wiz && !['template', 'details', 'website', 'materials', 'templates'].includes(screen);
    banner = (
      <CourseBanner crumbs={[home, { t: ctx.course.name, href: overview ? undefined : `?course=${ctx.course.org}#course` }]} name={ctx.course.name}
        hint={overview ? <CourseHint /> : undefined} side={overview ? <CourseHeaderActions course={ctx.course} /> : undefined} />
    );
  } else {
    const cp: CohortProps = {
      course: ctx.course, cohort: ctx.cohort, loaded: cohortLoaded ?? { kind: 'loading' }, files: s.files, now: s.now.value, entry: route.entry, tab: route.tab,
      heartbeat: s.heartbeat(ctx.course.org), prefill: sel.template,
    };
    const screens: Record<string, () => preact.JSX.Element> = {
      dashboard: () => <CohortScreen {...cp} />,
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
    body = (screens[screen] ?? screens.dashboard)();
    const sem = cohortLoaded?.kind === 'ready' ? cohortLoaded.status.semester : undefined;
    const dashboard = `?cohort=${ctx.cohort.org}#dashboard`;
    banner = (
      <CourseBanner crumbs={[home, { t: ctx.course.name, href: `?course=${ctx.course.org}#course` }, { t: ctx.cohort.termLabel, href: navKey === 'dashboard' ? undefined : dashboard }]}
        name={ctx.course.name} semester={{ org: ctx.cohort.org, termLabel: ctx.cohort.termLabel, view: 'student', ...bannerLine(sem) }} />
    );
  }

  // Two levels (decision 0021): Profile and Guide are app-level, full width without the side nav.
  const appLevel = FULL_WIDTH.includes(screen);
  return (
    <EnvCtx.Provider value={s.env(user)}>
      <Topbar user={user} title={title} onSignOut={s.signOut} navOpen={s.navOpen.value} onMenu={appLevel ? undefined : s.toggleNav} guide={guide} />
      <div class="shell" style={appLevel ? 'grid-template-columns:minmax(0,1fr)' : undefined}>
        {appLevel ? null : (
          <aside class="sidenav" id="sidenav-wrap" aria-label="Console navigation">
            <Sidenav courses={courses} course={ctx.course} cohort={semesterPage ? ctx.cohort : undefined} site={ctx.cohort} cohortStates={cohortStates} current={navKey} sub={sub} entry={route.entry} now={s.now.value} />
          </aside>
        )}
        <main id="view" tabindex={-1}>
          {sel.wizard && ctx.course && !wiz ? <p class="note" style="margin-bottom:18px"><a href={`?course=${ctx.course.org}#${sel.wizard}`}>Back to the {sel.wizard.startsWith('new-semester') ? 'New semester' : 'wizard'}</a> when you are done here.</p> : null}
          {banner}
          <ScreenBoundary key={s.search.value + s.hash.value}>{body}</ScreenBoundary>
        </main>
      </div>
      {appLevel || !ctx.course ? <Footer sub={root} /> : <Footer title={ctx.course.name} sub={ctx.cohort ? ctx.cohort.termLabel : 'Course'} />}
      <OpPanel />
    </EnvCtx.Provider>
  );
}

/** A semester page's banner line from the semester's status: state, week and dates, each only when known. */
export function bannerLine(sem: SemesterStatus | undefined): { chip?: preact.ComponentChildren; week?: string; dates?: string } {
  if (!sem) return {};
  const tz = sem.timezone || DEFAULT_TIMEZONE;
  const year = sem.start ? Number(sem.start.slice(0, 4)) : undefined;
  return {
    chip: semesterChip(sem),
    week: sem.week && sem.weeks ? `Week ${sem.week} of ${sem.weeks}` : undefined,
    dates: sem.start && sem.end ? `${fmtDay(sem.start, tz, year)} to ${fmtDay(sem.end, tz, year)}` : undefined,
  };
}

/**
 * The course nav's sub-pages, from the course status (or a semester's copy of it): every
 * materials repo, then the other releasable repos (on GitHub), and every template. Only a
 * shown group reads more than the status: the other repos for Materials, and each template's
 * title when `titles` (else, and until it is read, the repo name). Nothing for a read-only course.
 */
export function subPages(course: Course, loaded: Loaded, cohortStates: Record<string, Loaded>, files: Files, want: SubWanted & { titles: boolean }): CourseSubPages | undefined {
  if (!course.write) return undefined;
  const c = courseView({ loaded, cohortStates }).course;
  if (!c) return undefined;
  const materials = c.materials ?? [], templates = c.templates ?? [];
  const repos = want.materials ? files.repos(course.org) : null;
  const known = [...materials.map((m) => m.repo), ...templates.map((t) => t.repo)];
  const others = repos?.kind === 'ready' ? otherRepos(course.org, repos.repos, known) : [];
  const title = (repo: string) => (want.templates && want.titles ? templateTitle(files, course.org, repo) : '') || repo;
  return {
    materials: [
      ...materials.map((m) => ({ repo: m.repo, label: m.repo, href: `#materials-${m.repo}` })),
      ...others.map((r) => ({ repo: r.name, label: r.name, href: r.html_url || ghUrl(course.org, r.name), ext: true })),
    ],
    templates: templates.map((t) => ({ repo: t.repo, label: title(t.repo), href: `#template-${t.repo}` })),
  };
}

// --------------------------------------------------------------------------- state

export type AppState = ReturnType<typeof createState>;

/** The sign-in screen's line while the saved token waits for GitHub to answer. */
export const OFFLINE_RETRY = 'Offline: GitHub is not answering, retrying the sign-in';

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
      files.refresh(def.courseOrg, COURSE_REPO, STATUS_PATH);
    },
  });
  const estate = signal<Estate | null>(null);
  // Back from installing the console app: drop GitHub's two parameters and reopen the wizard's
  // first step, before anything reads the URL. Discovery then runs as on any load, and finds
  // the installation.
  const back = typeof location !== 'undefined' ? installReturn(location.search, () => takeInstallReturn()) : null;
  if (back) history.replaceState(null, '', `${location.pathname}${back}`);
  const search = signal(typeof location !== 'undefined' ? location.search : '');
  const st = {
    auth,
    user: signal<GhUser | null>(null),
    restoring: signal(true),
    /** The saved token's check at reload got no answer: it is kept and tried again. */
    offline: signal(false),
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
      if (!env || env.user !== user) env = { client, user, ops, statuses, files, rediscover: st.rediscover, kind: auth.kind() ?? 'classic' };
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
      st.offline.value = false;
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
      forgetMine(client);
      forgetShown(client);
      forgetStudentData(client);
      auth.signOut();
      client.clearCache();
      files.forget();
      statuses.forget();
      beats.clear();
      archived.clear();
      left.clear();
      ops.reset();
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
  auth.onRetry = () => (st.offline.value = true);
  return st;
}
