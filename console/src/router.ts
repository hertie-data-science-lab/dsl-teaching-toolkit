// Hash routes are the mockup's tokens (`#cohort`, `#schedule-s5`, `#template-assignment-3`);
// a problem's fix `{screen, entry}` is the route `#<screen>-<entry>`. An assignment's tabs
// ride after a slash (`#assignment-assignment-2/marks`); the retired `#teams-<slug>` and
// `#marks-<slug>` screens parse to those tabs, so old links still land. Which course or cohort
// the page is about rides in the query string (`?cohort=<org>` or `?course=<org>`), so a link
// from a fault mail can name both. `?semester=<org>` opens that semester's student screens:
// a student's own, or an instructor's Student view.

import { isInstructor, roleOf, type Course, type CohortRef, type Estate, type Mode, type Semester } from './model/discovery';

export interface Route {
  screen: string;
  entry?: string;
  /** The assignment hub's tab, when the hash names one. */
  tab?: AssignmentTab;
}

export type AssignmentTab = 'overview' | 'teams' | 'marks';
const TABS: AssignmentTab[] = ['overview', 'teams', 'marks'];

const ENTRY_SCREENS = ['schedule', 'assignment', 'release', 'template', 'marks', 'teams', 'materials'];

export function parseHash(hash: string): Route {
  const r = decodeURIComponent(hash.replace(/^#/, ''));
  if (!r) return { screen: '' };
  if (r === 'teams') return { screen: 'assignments' };
  for (const s of ENTRY_SCREENS) {
    if (!r.startsWith(`${s}-`)) continue;
    const entry = r.slice(s.length + 1);
    if (s === 'teams' || s === 'marks') return { screen: 'assignment', entry, tab: s };
    if (s === 'assignment') {
      const [slug, tab] = entry.split('/');
      return TABS.includes(tab as AssignmentTab) ? { screen: s, entry: slug, tab: tab as AssignmentTab } : { screen: s, entry: slug };
    }
    return { screen: s, entry };
  }
  return { screen: r };
}

/** The hash a route is canonically written as (the old Teams and Marks hashes rewrite to the tab). */
export function hashOf(r: Route): string {
  return `#${r.screen}${r.entry ? `-${r.entry}` : ''}${r.tab ? `/${r.tab}` : ''}`;
}

/** The hash to write instead of `hash` (an old Teams or Marks link), or null when it is already canonical. */
export function movedHash(hash: string): string | null {
  if (!hash || hash === '#') return null;
  const canonical = hashOf(parseHash(hash));
  return decodeURIComponent(hash) === canonical ? null : canonical;
}

/** Rewrite the address bar's hash in place (no history entry), keeping the query string. */
export function replaceHash(hash: string): void {
  history.replaceState(null, '', `${location.search}${hash}`);
}

/** The link to one tab of an assignment. */
export function tabHref(slug: string, tab: AssignmentTab): string {
  return `#assignment-${slug}/${tab}`;
}

export interface Selection {
  cohort?: string;
  course?: string;
  /** A semester whose student screens to show. */
  semester?: string;
  /** A wizard step to come back to from an editor it opened (`new-cohort-3`). */
  wizard?: string;
  /** A template to prefill a new schedule entry with (New assignment's last step). */
  template?: string;
}

export function parseSearch(search: string): Selection {
  const q = new URLSearchParams(search);
  const wizard = q.get('wizard') ?? undefined;
  return {
    cohort: q.get('cohort') ?? undefined,
    course: q.get('course') ?? undefined,
    semester: q.get('semester') ?? undefined,
    wizard: wizard && WIZARD_RE.test(wizard) ? wizard : undefined,
    template: q.get('template') ?? undefined,
  };
}

/** Screens that need a cohort, and the nav key each lights up. */
export const COHORT_SCREENS: Record<string, string> = {
  cohort: 'week', schedule: 'schedule', release: 'schedule', assignments: 'assignments', assignment: 'assignments',
  students: 'students', roster: 'students', marks: 'marks', staff: 'staff', site: 'site', operations: 'operations', archive: 'archive',
};
/** Screens about the course. */
export const COURSE_SCREENS: Record<string, string> = {
  course: 'course', materials: 'materials', templates: 'templates', template: 'templates', details: 'details', website: 'website',
};

const WIZARD_RE = /^new-(course|cohort|assignment)-[1-4]$/;

/** A wizard route: `new-assignment-2` is New assignment at step 2; `new-materials` is one page. */
export function wizardOf(screen: string): { name: string; step?: number } | null {
  const m = /^(new-(?:course|cohort|assignment))(?:-(\d))?$/.exec(screen);
  if (m) return { name: m[1], step: m[2] ? Number(m[2]) : undefined };
  return screen === 'new-materials' ? { name: screen } : null;
}

/** The nav key each wizard lights up. */
export const WIZARD_NAV: Record<string, string> = { 'new-course': 'home', 'new-cohort': 'details', 'new-assignment': 'templates', 'new-materials': 'materials' };

export interface Context {
  course?: Course;
  cohort?: CohortRef;
}

/** The course and cohort a URL is about, falling back to the newest cohort of the first writable course. */
export function resolveContext(courses: Course[], sel: Selection, route: Route): Context {
  if (sel.cohort) {
    for (const c of courses) {
      const k = c.cohorts.find((x) => x.org === sel.cohort);
      if (k) return { course: c, cohort: k };
    }
  }
  const byCourse = sel.course ? courses.find((c) => c.org === sel.course) : undefined;
  if (byCourse) return { course: byCourse, cohort: route.screen in COHORT_SCREENS ? byCourse.cohorts[0] : undefined };
  if (route.screen === 'home' || wizardOf(route.screen)?.name === 'new-course') return {};
  const first = courses.find((c) => c.write && c.cohorts.length) ?? courses.find((c) => c.cohorts.length) ?? courses[0];
  return first ? { course: first, cohort: first.cohorts[0] } : {};
}

/** Where sign-in lands: This week when there is exactly one writable course with a cohort, else Home. */
export function landing(courses: Course[]): string {
  const writable = courses.filter((c) => c.write && c.cohorts.length);
  return writable.length === 1 ? 'cohort' : 'home';
}

/** The student screens, in nav order, with their labels; `week` is where a semester opens. */
export const STUDENT_SCREENS: [string, string][] = [
  ['week', 'This week'], ['schedule', 'Schedule'], ['assignments', 'Assignments'], ['marks', 'Marks'], ['materials', 'Materials'], ['instructors', 'Instructors'],
];

/** The link to a semester's student screens. */
export const studentHref = (org: string, screen = 'week') => `?semester=${org}#${screen}`;

/**
 * The semester whose student screens the URL asks for, when the person holds a role there: a
 * student sees their own; an instructor gets the Student view, the same screens with no
 * student's data (decision 0011 rule 7). Org names compare whatever their case. A semester
 * known only from a writable course's registry is an instructor's, and whether it is archived
 * comes from `archivedOf` (its `.github` read on opening); `pending` while that is unknown.
 */
export function studentContext(
  estate: Estate,
  sel: Selection,
  archivedOf: (org: string) => boolean | undefined = () => undefined,
): { semester: Semester; studentView: boolean; pending: boolean } | null {
  const org = sel.semester?.toLowerCase();
  if (!org) return null;
  const role = roleOf(estate, org);
  if (!role) return null;
  const same = (x: { org: string }) => x.org.toLowerCase() === org;
  const known = estate.semesters.find(same);
  if (known) return { semester: known, studentView: role === 'instructor', pending: false };
  const course = estate.courses.find((c) => c.cohorts.some(same));
  const k = course?.cohorts.find(same);
  if (!course || !k) return null;
  const archived = archivedOf(k.org);
  return {
    semester: { ...k, courseOrg: course.org, courseName: course.name, archived: archived === true, role },
    studentView: role === 'instructor',
    pending: archived === undefined,
  };
}

/** Which shell the URL renders: a semester's student screens, else instructor screens for anyone who is an instructor anywhere. */
export function modeOf(estate: Estate, sel: Selection): Mode {
  if (studentContext(estate, sel)) return 'student';
  return isInstructor(estate) ? 'instructor' : 'student';
}
