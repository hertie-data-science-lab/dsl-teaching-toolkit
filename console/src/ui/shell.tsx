// The frame every screen sits in: the app-level top bar, the course-level switcher and side
// nav, the footer.

import { useEffect, useState } from 'preact/hooks';
import type { GhUser } from '../github/client';
import { cohortName, semesterName, type Course, type CohortRef, type Semester } from '../model/discovery';
import { knownAuditor } from '../model/mine';
import { STUDENT_SCREENS, studentHref } from '../router';
import type { Loaded } from '../model/status';
import { ghUrl } from './bits';
import { Bldg, Ext, Gh, Pin } from './icons';

function initials(u: GhUser): string {
  const n = (u.name || u.login).split(/\s+/).filter(Boolean);
  return (n.length > 1 ? n[0][0] + n[n.length - 1][0] : n[0].slice(0, 2)).toUpperCase();
}

function useTheme(): [boolean, () => void] {
  const root = typeof document !== 'undefined' ? document.documentElement : null;
  const effective = () => {
    const s = root?.getAttribute('data-theme');
    if (s) return s === 'dark';
    return typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-color-scheme: dark)').matches;
  };
  const [dark, setDark] = useState(effective);
  return [
    dark,
    () => {
      const next = effective() ? 'light' : 'dark';
      root?.setAttribute('data-theme', next);
      try {
        localStorage.setItem('console-theme', next);
      } catch {
        /* storage unavailable */
      }
      setDark(next === 'dark');
    },
  ];
}

/**
 * The app-level bar (decision 0021): the product name (a Home link) with the view, the person
 * (a link to Profile), Guide, theme, Sign out. Nothing course- or semester-specific; the Menu
 * button only where there is a side nav to open. Its links start `?`: app-level pages are about
 * no course or semester, so they clear the query.
 */
export function Topbar({ user, onSignOut, navOpen = false, onMenu, title }: {
  user: GhUser | null;
  /** The view after sign-in: Instructor view or Student view. */
  title?: string;
  onSignOut?: () => void;
  navOpen?: boolean;
  onMenu?: () => void;
}) {
  const [dark, toggle] = useTheme();
  return (
    <header class="topbar">
      <div class="topbar-inner">
        {user && onMenu ? <button class="pill-ghost menu-btn" type="button" aria-expanded={navOpen} aria-controls="sidenav-wrap" onClick={onMenu}>Menu</button> : null}
        <a class="app-name" href="?#home">DSL Teaching Console{user && title ? <small>{title}</small> : null}</a>
        <div class="topbar-right">
          {user ? (
            <a class="who" href="?#profile" aria-label="Your profile">
              <span class="avatar" aria-hidden="true">{user.avatar_url ? <img src={user.avatar_url} alt="" /> : initials(user)}</span>
              <span class="who-name">{user.name || user.login}</span>
            </a>
          ) : null}
          {user ? <a class="pill-ghost" href="?#help" aria-label="Guide: how the console is organised">Guide</a> : null}
          <button class="pill-ghost" type="button" aria-label="Switch colour theme" onClick={toggle}>{dark ? 'Light' : 'Dark'}</button>
          {user && onSignOut ? <button class="pill-ghost" type="button" onClick={onSignOut}>Sign out</button> : null}
        </div>
      </div>
    </header>
  );
}

export function Footer({ course, cohort }: { course?: Course; cohort?: CohortRef }) {
  return (
    <footer class="site-footer">
      <div class="f-inner">
        <div><h2>{course ? course.name : 'DSL Teaching Console'}</h2><p>{cohort ? cohort.termLabel : course ? 'Course' : 'All courses'}</p></div>
        <div>
          <ul>
            <li><a href="https://github.com/hertie-data-science-lab" target="_blank" rel="noopener"><Gh />Hertie School Data Science Lab</a></li>
            <li><a href="https://www.hertie-school.org" target="_blank" rel="noopener"><Bldg />Hertie School</a></li>
            <li>
              <span style="display:inline-flex;gap:8px;align-items:flex-start"><Pin />
                <address style="padding:0">Data Science Lab<br />Hertie School<br />Friedrichstraße 180<br />10117 Berlin, Germany</address>
              </span>
            </li>
          </ul>
        </div>
        <div><p><a href="https://hertie-school.org/en/datasciencelab" target="_blank" rel="noopener">Part of the Hertie Data Science Lab.</a></p></div>
      </div>
    </footer>
  );
}

function Switcher({ courses, course, cohort, cohortStates, semesters = [], semester }: {
  courses: Course[];
  course?: Course;
  cohort?: CohortRef;
  cohortStates: Record<string, Loaded>;
  /** The semesters the person is a student of, listed after the courses. */
  semesters?: Semester[];
  /** The semester whose student screens are open. */
  semester?: Semester;
}) {
  const [open, setOpen] = useState(false);
  const [sub, setSub] = useState<string | null>(course?.org ?? null);
  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (!(e.target as Element)?.closest?.('.switcher')) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === 'Escape' && setOpen(false);
    document.addEventListener('click', close);
    document.addEventListener('keydown', esc);
    return () => {
      document.removeEventListener('click', close);
      document.removeEventListener('keydown', esc);
    };
  }, [open]);
  const label = semester ? semesterName(semester) : course ? (cohort ? cohortName({ course, cohort }) : course.name) : courses.length ? 'All courses' : 'Your semesters';
  const sw = (l: Loaded | undefined, c: Course): string => {
    if (!c.write) return 'read only';
    if (!l || l.kind !== 'ready') return l?.kind === 'absent' ? 'not computed yet' : '';
    if (l.status.semester?.live === false) return 'archived';
    const n = (l.status.problems ?? []).length;
    return n ? `${n} problem${n > 1 ? 's' : ''}` : 'no problems';
  };
  return (
    <div class="switcher">
      <button type="button" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen(!open)}>
        <span>{label}</span><span class="caret" aria-hidden="true" />
      </button>
      <div class="popmenu" hidden={!open} role="menu" onClick={(e) => (e.target as Element).closest('a') && setOpen(false)}>
        {/* `?#home` clears the course and semester from the query: All courses is about none. */}
        <a href="?#home" role="menuitem">{courses.length ? 'All courses' : 'Your semesters'}</a>
        {courses.map((c) => (
          <>
            <button type="button" class="sub-toggle" aria-expanded={sub === c.org} onClick={() => setSub(sub === c.org ? null : c.org)}>
              {c.name}<span class="arrow" aria-hidden="true" />
            </button>
            <div class="submenu" hidden={sub !== c.org}>
              <a href={`?course=${c.org}#course`} class={`${c.write ? '' : 'ro'}${course?.org === c.org && !cohort ? ' cur' : ''}`}>Course overview</a>
              {c.cohorts.map((k) => (
                <a href={`?cohort=${k.org}#dashboard`} class={`${c.write ? '' : 'ro'}${cohort?.org === k.org ? ' cur' : ''}`}>
                  {k.termLabel}<span class="pm-sub">{sw(cohortStates[k.org], c)}</span>
                </a>
              ))}
            </div>
          </>
        ))}
        {semesters.length ? (
          <>
            <hr />
            <div class="nav-h">Your semesters</div>
            {semesters.map((k) => (
              <a href={studentHref(k.org)} role="menuitem" class={`${k.archived ? 'ro' : ''}${semester?.org === k.org ? ' cur' : ''}`}>
                {semesterName(k)}<span class="pm-sub">{k.archived ? 'archived' : 'student'}</span>
              </a>
            ))}
          </>
        ) : null}
      </div>
    </div>
  );
}

/** A semester's problem count and whether it is archived, from its loaded status. */
export function cohortFlags(l: Loaded | undefined): { problems: number | null; archived: boolean } {
  if (!l || l.kind !== 'ready') return { problems: null, archived: false };
  return { problems: (l.status.problems ?? []).length, archived: l.status.semester?.live === false };
}

/**
 * The course nav's Semesters group: each semester by name with its problems count. No entry
 * carries aria-current: in a semester, Dashboard above already marks the page.
 */
function CohortsNav({ course, cohortStates }: { course: Course; cohortStates: Record<string, Loaded> }) {
  if (!course.cohorts.length && !course.settingUp?.length) return null;
  return (
    <>
      <div class="nav-h">Semesters</div>
      <ul>
        {(course.settingUp ?? []).map((k) => (
          <li><span class="nav-pending">{k.termLabel}<span class="n-soon"> being set up</span></span></li>
        ))}
        {course.cohorts.map((k) => {
          const f = cohortFlags(cohortStates[k.org]);
          return (
            <li>
              <a href={`?cohort=${k.org}#dashboard`} class={f.archived ? 'archived' : undefined}>
                <span>{k.termLabel}{f.archived ? <span class="n-soon"> archived</span> : null}</span>
                {f.problems ? <span class="n-count" aria-label={`${f.problems} problems`}>{f.problems}</span> : null}
              </a>
            </li>
          );
        })}
      </ul>
    </>
  );
}

/** One sub-page under a course nav item: a repo's settings page, or a repo on GitHub (`ext`). */
export interface SubPage {
  repo: string;
  label: string;
  href: string;
  ext?: boolean;
}

/** The course's sub-pages, from its status: materials and other releasable repos, and templates. */
export interface CourseSubPages {
  materials: SubPage[];
  templates: SubPage[];
}

/** Which nav groups are shown: only those read anything beyond the course status. */
export type SubWanted = { materials: boolean; templates: boolean };

/**
 * A course nav item with its sub-pages: the link goes to the index; the chevron button opens a
 * nested list. Collapsed unless the open page is one of them, or until the chevron is pressed.
 */
function NavGroup({ href, t, here, inside, shown, onToggle, pages, entry }: {
  href: string; t: string; here: boolean; inside: boolean; shown: boolean; onToggle: () => void; pages: SubPage[]; entry?: string;
}) {
  const id = `nav-${href.slice(1)}`;
  return (
    <li class="nav-group">
      <span class="nav-row">
        <a href={href} aria-current={here && !inside ? 'page' : undefined}>{t}</a>
        {pages.length ? <button class="nav-chev" type="button" aria-expanded={shown} aria-controls={id} aria-label={`${shown ? 'Hide' : 'Show'} the ${t.toLowerCase()} pages`} onClick={onToggle}><span class="arrow" aria-hidden="true" /></button> : null}
      </span>
      {pages.length ? (
        <ul class="nav-sub" id={id} hidden={!shown}>
          {shown ? pages.map((x) => (
            <li>
              {x.ext ? <a href={x.href} target="_blank" rel="noopener">{x.label} <Ext /></a>
                : <a href={x.href} aria-current={inside && x.repo === entry ? 'page' : undefined}>{x.label}</a>}
            </li>
          )) : null}
        </ul>
      ) : null}
    </li>
  );
}

export function Sidenav({ courses, course, cohort, cohortStates, current, problems, semesters, sub, entry }: {
  courses: Course[];
  semesters?: Semester[];
  course?: Course;
  cohort?: CohortRef;
  cohortStates: Record<string, Loaded>;
  current: string;
  problems: number;
  /** The course's materials and templates for the nav's sub-pages, read for the groups shown; none until its status is read. */
  sub?: (wanted: SubWanted) => CourseSubPages | undefined;
  /** The open page's entry (a repo on `#materials-<repo>` or `#template-<repo>`). */
  entry?: string;
}) {
  const item = (href: string, t: string, key: string, extra?: preact.ComponentChildren) => (
    <li><a href={href} aria-current={key === current ? 'page' : undefined}>{t}{extra}</a></li>
  );
  // A group opens on a page inside it, or when its chevron is pressed (kept here only).
  const [open, setOpen] = useState<Partial<Record<keyof SubWanted, boolean>>>({});
  const inside = (k: keyof SubWanted) => current === k && !!entry;
  const shown = (k: keyof SubWanted) => open[k] ?? inside(k);
  const pages = sub?.({ materials: shown('materials'), templates: shown('templates') });
  const group = (k: keyof SubWanted, href: string, t: string) => (
    <NavGroup href={href} t={t} here={current === k} inside={inside(k) && !!pages?.[k].some((x) => !x.ext && x.repo === entry)} shown={shown(k)}
      onToggle={() => setOpen({ ...open, [k]: !shown(k) })} pages={pages?.[k] ?? []} entry={entry} />
  );
  return (
    <nav>
      <Switcher courses={courses} course={course} cohort={cohort} cohortStates={cohortStates} semesters={semesters} />
      {course && cohort && course.write ? (
        <>
          <ul>
            {item('#dashboard', 'Dashboard', 'dashboard', problems ? <span class="n-count" aria-label={`${problems} problems`}>{problems}</span> : null)}
            {item('#schedule', 'Schedule', 'schedule')}
            {item('#assignments', 'Assignments', 'assignments')}
            {item('#marks', 'Marks', 'marks')}
            {item('#students', 'Students', 'students')}
            {item('#instructors', 'Instructors', 'instructors')}
            {item('#site', 'Site', 'site')}
            {item('#archive', 'Archive', 'archive')}
            {item('#operations', 'Operations', 'operations')}
          </ul>
          <hr />
        </>
      ) : null}
      {course && course.write ? (
        <>
          <div class="nav-h">Course</div>
          <ul>
            {!cohort ? item('#course', 'Overview', 'course') : null}
            {item('#details', 'Course details', 'details')}
            {group('materials', '#materials', 'Materials')}
            {group('templates', '#templates', 'Assignment templates')}
            {item('#website', 'Public website', 'website')}
          </ul>
          <CohortsNav course={course} cohortStates={cohortStates} />
        </>
      ) : null}
      {course && !course.write ? <p class="footnote" style="padding:8px 10px">Read only: other pages need write access.</p> : null}
      {!course ? <p class="footnote" style="padding:4px 10px">{courses.length ? 'Choose a course and semester to see its pages.' : 'Choose a semester to see its pages.'}</p> : null}
      {course ? (
        <div>
          <hr />
          <ul>
            {cohort && course.write ? <li><a href={studentHref(cohort.org)}>Student view</a></li> : null}
            {cohort ? <li><a href={`https://${cohort.org}.github.io`} target="_blank" rel="noopener">Public site <Ext /></a></li> : null}
            {cohort ? <li><a href={ghUrl(cohort.org)} target="_blank" rel="noopener">Semester on GitHub <Ext /></a></li> : null}
            <li><a href={ghUrl(course.org)} target="_blank" rel="noopener">Course on GitHub <Ext /></a></li>
          </ul>
        </div>
      ) : null}
    </nav>
  );
}

/** Screens an auditor has no use for: they get no marks and join no team. */
const AUDITOR_HIDDEN = ['marks', 'join'];

/** The student shell's nav: the switcher, one semester's screens, and the semester on GitHub. An auditor's omits Marks and Join. */
export function StudentNav({ courses, cohortStates, semesters, semester, current }: {
  courses: Course[];
  cohortStates: Record<string, Loaded>;
  semesters: Semester[];
  semester: Semester;
  current: string;
}) {
  return (
    <nav>
      <Switcher courses={courses} cohortStates={cohortStates} semesters={semesters} semester={semester} />
      <ul>
        {STUDENT_SCREENS.filter(([k]) => !(knownAuditor(semester.org) && AUDITOR_HIDDEN.includes(k))).map(([k, t]) => <li><a href={studentHref(semester.org, k)} aria-current={k === current ? 'page' : undefined}>{t}</a></li>)}
      </ul>
      <div>
        <hr />
        <ul><li><a href={ghUrl(semester.org)} target="_blank" rel="noopener">Semester on GitHub <Ext /></a></li></ul>
      </div>
    </nav>
  );
}
