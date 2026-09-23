// Hash routes are the mockup's tokens (`#cohort`, `#schedule-s5`, `#template-assignment-3`);
// a problem's fix `{screen, entry}` is the route `#<screen>-<entry>`. Which course or cohort
// the page is about rides in the query string (`?cohort=<org>` or `?course=<org>`), so a link
// from a fault mail can name both.

import type { Course, CohortRef } from './model/discovery';

export interface Route {
  screen: string;
  entry?: string;
}

const ENTRY_SCREENS = ['schedule', 'assignment', 'release', 'template'];

export function parseHash(hash: string): Route {
  const r = decodeURIComponent(hash.replace(/^#/, ''));
  if (!r) return { screen: '' };
  for (const s of ENTRY_SCREENS) if (r.startsWith(`${s}-`)) return { screen: s, entry: r.slice(s.length + 1) };
  return { screen: r };
}

export interface Selection {
  cohort?: string;
  course?: string;
}

export function parseSearch(search: string): Selection {
  const q = new URLSearchParams(search);
  return { cohort: q.get('cohort') ?? undefined, course: q.get('course') ?? undefined };
}

/** Screens that need a cohort, and the nav key each lights up. */
export const COHORT_SCREENS: Record<string, string> = {
  cohort: 'week', schedule: 'schedule', release: 'schedule', assignments: 'assignments', assignment: 'assignments',
  students: 'students', roster: 'students', teams: 'teams', staff: 'staff', site: 'site', operations: 'operations', archive: 'archive',
};
/** Screens about the course. */
export const COURSE_SCREENS: Record<string, string> = {
  course: 'course', materials: 'materials', templates: 'templates', template: 'templates', details: 'details', website: 'website',
};

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
  if (route.screen === 'home') return {};
  const first = courses.find((c) => c.write && c.cohorts.length) ?? courses.find((c) => c.cohorts.length) ?? courses[0];
  return first ? { course: first, cohort: first.cohorts[0] } : {};
}

/** Where sign-in lands: This week when there is exactly one writable course with a cohort, else Home. */
export function landing(courses: Course[]): string {
  const writable = courses.filter((c) => c.write && c.cohorts.length);
  return writable.length === 1 ? 'cohort' : 'home';
}
