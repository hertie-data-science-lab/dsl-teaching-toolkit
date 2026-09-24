// The frame every screen sits in: top bar, context switcher and nav, footer.

import { useEffect, useState } from 'preact/hooks';
import type { GhUser } from '../github/client';
import { cohortName, type Course, type CohortRef } from '../model/discovery';
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

export function HeaderLinks({ course, cohort }: { course?: Course; cohort?: CohortRef }) {
  if (!course) return null;
  return (
    <>
      {!course.write ? <span class="ro-chip">read only</span> : null}
      {cohort ? <a href={`https://${cohort.org}.github.io`} target="_blank" rel="noopener">Student site <Ext /></a> : null}
      {cohort ? <a href={ghUrl(cohort.org)} target="_blank" rel="noopener">Cohort on GitHub <Ext /></a> : null}
      <a href={ghUrl(course.org)} target="_blank" rel="noopener">Course on GitHub <Ext /></a>
    </>
  );
}

export function Topbar({ user, course, cohort, onSignOut, navOpen, onMenu }: {
  user: GhUser | null;
  course?: Course;
  cohort?: CohortRef;
  onSignOut?: () => void;
  navOpen: boolean;
  onMenu: () => void;
}) {
  const [dark, toggle] = useTheme();
  return (
    <header class="topbar">
      <div class="topbar-inner">
        {user ? <button class="pill-ghost menu-btn" type="button" aria-expanded={navOpen} aria-controls="sidenav-wrap" onClick={onMenu}>Menu</button> : null}
        <a class="app-name" href="#home">Instructor Console <small>Data Science Lab</small></a>
        <nav class="hdr-links" aria-label="Open on the web"><HeaderLinks course={course} cohort={cohort} /></nav>
        <div class="topbar-right">
          {user ? (
            <div class="who">
              <span class="avatar" aria-hidden="true">{user.avatar_url ? <img src={user.avatar_url} alt="" /> : initials(user)}</span>
              <span class="who-name">{user.name || user.login}</span>
            </div>
          ) : null}
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
        <div><h2>{course ? course.name : 'Instructor Console'}</h2><p>{cohort ? cohort.termLabel : course ? 'Course' : 'All courses'}</p></div>
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

function Switcher({ courses, course, cohort, cohortStates }: {
  courses: Course[];
  course?: Course;
  cohort?: CohortRef;
  cohortStates: Record<string, Loaded>;
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
  const label = course ? (cohort ? cohortName({ course, cohort }) : course.name) : 'All courses';
  const sw = (l: Loaded | undefined, c: Course): string => {
    if (!c.write) return 'read only';
    if (!l || l.kind !== 'ready') return l?.kind === 'absent' ? 'not computed yet' : '';
    if (l.status.cohort?.live === false) return 'archived';
    const n = (l.status.problems ?? []).length;
    return n ? `${n} problem${n > 1 ? 's' : ''}` : 'no problems';
  };
  return (
    <div class="switcher">
      <button type="button" aria-expanded={open} aria-haspopup="true" onClick={() => setOpen(!open)}>
        <span>{label}</span><span class="caret" aria-hidden="true" />
      </button>
      <div class="popmenu" hidden={!open} role="menu" onClick={(e) => (e.target as Element).closest('a') && setOpen(false)}>
        <a href="#home" role="menuitem">All courses</a>
        <a href="#new-course-1" role="menuitem">New course</a>
        {!course ? (
          <span class="disabled" role="menuitem" aria-disabled="true">New cohort<small>Select a course first</small></span>
        ) : !course.write ? (
          <span class="disabled" role="menuitem" aria-disabled="true">New cohort of {course.name}<small>You have no write access</small></span>
        ) : (
          <a href={`?course=${course.org}#new-cohort-1`} role="menuitem">New cohort of {course.name}</a>
        )}
        <hr />
        {courses.map((c) => (
          <>
            <button type="button" class="sub-toggle" aria-expanded={sub === c.org} onClick={() => setSub(sub === c.org ? null : c.org)}>
              {c.name}<span class="arrow" aria-hidden="true" />
            </button>
            <div class="submenu" hidden={sub !== c.org}>
              <a href={`?course=${c.org}#course`} class={`${c.write ? '' : 'ro'}${course?.org === c.org && !cohort ? ' cur' : ''}`}>Course overview</a>
              {c.cohorts.map((k) => (
                <a href={`?cohort=${k.org}#cohort`} class={`${c.write ? '' : 'ro'}${cohort?.org === k.org ? ' cur' : ''}`}>
                  {k.termLabel}<span class="pm-sub">{sw(cohortStates[k.org], c)}</span>
                </a>
              ))}
            </div>
          </>
        ))}
      </div>
    </div>
  );
}

/** A cohort's problem count and whether it is archived, from its loaded status. */
export function cohortFlags(l: Loaded | undefined): { problems: number | null; archived: boolean } {
  if (!l || l.kind !== 'ready') return { problems: null, archived: false };
  return { problems: (l.status.problems ?? []).length, archived: l.status.cohort?.live === false };
}

/**
 * The course nav's Cohorts group: each cohort by term with its problems count. No entry
 * carries aria-current: in a cohort, This week above already marks the page.
 */
function CohortsNav({ course, cohortStates }: { course: Course; cohortStates: Record<string, Loaded> }) {
  if (!course.cohorts.length) return null;
  return (
    <>
      <div class="nav-h">Cohorts</div>
      <ul>
        {course.cohorts.map((k) => {
          const f = cohortFlags(cohortStates[k.org]);
          return (
            <li>
              <a href={`?cohort=${k.org}#cohort`} class={f.archived ? 'archived' : undefined}>
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

export function Sidenav({ courses, course, cohort, cohortStates, current, problems }: {
  courses: Course[];
  course?: Course;
  cohort?: CohortRef;
  cohortStates: Record<string, Loaded>;
  current: string;
  problems: number;
}) {
  const item = (href: string, t: string, key: string, extra?: preact.ComponentChildren) => (
    <li><a href={href} aria-current={key === current ? 'page' : undefined}>{t}{extra}</a></li>
  );
  return (
    <nav>
      <Switcher courses={courses} course={course} cohort={cohort} cohortStates={cohortStates} />
      {course && cohort && course.write ? (
        <>
          <ul>
            {item('#cohort', 'This week', 'week', problems ? <span class="n-count" aria-label={`${problems} problems`}>{problems}</span> : null)}
            {item('#schedule', 'Schedule', 'schedule')}
            {item('#assignments', 'Assignments', 'assignments')}
            {item('#students', 'Students', 'students')}
            {item('#teams', 'Teams', 'teams')}
            {item('#staff', 'Staff', 'staff')}
            {item('#site', 'Site', 'site')}
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
            {item('#materials', 'Materials', 'materials')}
            {item('#templates', 'Assignment templates', 'templates')}
            {item('#website', 'Public website', 'website')}
          </ul>
          <CohortsNav course={course} cohortStates={cohortStates} />
        </>
      ) : null}
      {course && cohort && course.write ? (
        <>
          <hr />
          <ul>{item('#operations', 'All operations', 'operations')}{item('#archive', 'Archive', 'archive')}</ul>
        </>
      ) : null}
      {course && !course.write ? <p class="footnote" style="padding:8px 10px">Read only: other pages need write access.</p> : null}
      {!course ? <p class="footnote" style="padding:4px 10px">Choose a course and term to see its pages.</p> : null}
      {course ? (
        <div class="nav-links">
          <hr />
          <ul>
            {cohort ? <li><a href={`https://${cohort.org}.github.io`} target="_blank" rel="noopener">Student site <Ext /></a></li> : null}
            {cohort ? <li><a href={ghUrl(cohort.org)} target="_blank" rel="noopener">Cohort on GitHub <Ext /></a></li> : null}
            <li><a href={ghUrl(course.org)} target="_blank" rel="noopener">Course on GitHub <Ext /></a></li>
          </ul>
        </div>
      ) : null}
    </nav>
  );
}
