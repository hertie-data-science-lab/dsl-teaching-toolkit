// The frame every screen sits in: the app-level top bar, the side nav (one tree, decision 0031
// rule 11: course-anchored for an instructor, semester-anchored for a student), the course
// banner heading every course and semester page, the footer.

import type { ComponentChildren } from 'preact';
import { useState } from 'preact/hooks';
import { readText, safeStorage, writeText } from '../auth/types';
import type { GhUser } from '../github/client';
import type { Course, CohortRef, Semester } from '../model/discovery';
import { semesterOver, termRank } from '../model/catalogue';
import { problemCount } from '../model/readiness';
import type { SemesterFacts } from '../model/student';
import { studentHref, studentScreens } from '../router';
import type { Loaded } from '../model/status';
import { Crumbs, ghUrl } from './bits';
import { Bldg, Ext, FaIcon, Gh, HertieMark, Pin } from './icons';

function initials(u: GhUser): string {
  const n = (u.name || u.login).split(/\s+/).filter(Boolean);
  return (n.length > 1 ? n[0][0] + n[n.length - 1][0] : n[0].slice(0, 2)).toUpperCase();
}

const THEME_KEY = 'console-theme';

/** The theme chosen in this browser ('dark' or 'light'), or null to follow the system. */
export function savedTheme(): 'dark' | 'light' | null {
  const t = readText(safeStorage('local'), THEME_KEY);
  return t === 'dark' || t === 'light' ? t : null;
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
      writeText(safeStorage('local'), THEME_KEY, next);
      setDark(next === 'dark');
    },
  ];
}

/**
 * The app-level bar (decision 0021): the Hertie mark and the product name (a Home link,
 * decision 0035 rule 2) with the view, the person
 * (a link to Profile), Guide, theme, Sign out. Nothing course- or semester-specific; the Menu
 * button only where there is a side nav to open. Its links start `?`: app-level pages are about
 * no course or semester, so they clear the query. With `titleHref` (an instructor previewing a
 * semester's Student view, decision 0025) the view is a link of its own, back to that semester.
 * Guide only for a person with an instructor role (`guide`): it explains the instructor console
 * (decision 0029 rule 5).
 */
export function Topbar({ user, onSignOut, navOpen = false, onMenu, title, titleHref, guide = false }: {
  user: GhUser | null;
  /** Show the Guide link. */
  guide?: boolean;
  /** The view after sign-in: Instructor view, Student view, or Student view (preview). */
  title?: string;
  /** Where the view links to; plain text without it. */
  titleHref?: string;
  onSignOut?: () => void;
  navOpen?: boolean;
  onMenu?: () => void;
}) {
  const [dark, toggle] = useTheme();
  return (
    <header class="topbar">
      <div class="topbar-inner">
        {user && onMenu ? <button class="pill-ghost menu-btn" type="button" aria-expanded={navOpen} aria-controls="sidenav-wrap" onClick={onMenu}>Menu</button> : null}
        <a class="app-name" href="?#home"><HertieMark /><span class="app-words">DSL Teaching Console</span>{user && title && !titleHref ? <small>{title}</small> : null}</a>
        {user && title && titleHref ? <a class="app-view" href={titleHref}><span class="long">{title}</span><span class="short">Preview</span></a> : null}
        <div class="topbar-right">
          {user ? (
            <a class="who" href="?#profile" aria-label="Your profile">
              <span class="avatar" aria-hidden="true">{user.avatar_url ? <img src={user.avatar_url} alt="" /> : initials(user)}</span>
              <span class="who-name">{user.name || user.login}</span>
            </a>
          ) : null}
          {user && guide ? <a class="pill-ghost" href="?#help" aria-label="Guide: how the console is organised">Guide</a> : null}
          <button class="pill-ghost" type="button" aria-label="Switch colour theme" onClick={toggle}>{dark ? 'Light' : 'Dark'}</button>
          {user && onSignOut ? <button class="pill-ghost" type="button" onClick={onSignOut}>Sign out</button> : null}
        </div>
      </div>
    </header>
  );
}

/** The footer, naming what the page is about: a course and its semester, or the console (`sub` then names the landing page). */
export function Footer({ title = 'DSL Teaching Console', sub = 'All courses' }: { title?: string; sub?: string }) {
  return (
    <footer class="site-footer">
      <div class="f-inner">
        <div><h2>{title}</h2><p>{sub}</p></div>
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

// --------------------------------------------------------------------------- the nav tree

/** A page under a tree node: a link, current or not; `ext` opens GitHub in a new tab. */
export interface NavLeaf {
  href: string;
  t: ComponentChildren;
  current?: boolean;
  count?: number | null;
  past?: boolean;
  ext?: boolean;
}

/** A tree node: a name (a link to its home, or a toggle without `href`), a mark, a count and its pages. */
export interface NavNode {
  id: string;
  label: ComponentChildren;
  href?: string;
  /** Ended or archived: muted. */
  past?: boolean;
  count?: number | null;
  /** Being set up: a muted row with nothing under it. */
  pending?: boolean;
  /** What the chevron shows, for its label: "the Fall 2026 pages". */
  what: string;
  pages: NavLeaf[];
}

const Count = ({ n }: { n?: number | null }) => (n ? <span class="n-count" aria-label={`${n} problems`}>{n}</span> : null);

/** The chevron in the 22px gutter before a node's name (decision 0031 rule 11). */
export function Chev({ open, controls, what, onToggle }: { open: boolean; controls: string; what: string; onToggle: () => void }) {
  return (
    <button class="chev" type="button" aria-expanded={open} aria-controls={controls} aria-label={`${open ? 'Hide' : 'Show'} ${what}`} onClick={onToggle}>
      <span class="arrow" aria-hidden="true" />
    </button>
  );
}

/** A row with nothing to expand: its label keeps the chevron gutter, so labels align. */
function Leaf({ l, cls = 'leaf' }: { l: NavLeaf; cls?: string }) {
  return (
    <li>
      {l.ext ? <a class={cls || undefined} href={l.href} target="_blank" rel="noopener">{l.t} <Ext /></a>
        : <a class={[cls, l.past ? 'past' : ''].filter(Boolean).join(' ') || undefined} href={l.href} aria-current={l.current ? 'page' : undefined}>{l.t}<Count n={l.count} /></a>}
    </li>
  );
}

/** One node: the chevron, the name, and its pages one level in. The name opens the node's home and expands it. */
export function TreeNode({ node, open, onToggle, onPick, current }: { node: NavNode; open: boolean; onToggle: () => void; onPick?: () => void; current?: boolean }) {
  if (node.pending) return <li><span class="leaf nav-pending">{node.label}</span></li>;
  const id = `nav-${node.id}`;
  const has = node.pages.length > 0;
  return (
    <li>
      <div class="row">
        {has || node.href === undefined ? <Chev open={open} controls={id} what={node.what} onToggle={onToggle} /> : <span />}
        {node.href !== undefined
          ? <a href={node.href} class={node.past ? 'past' : undefined} aria-current={current ? 'page' : undefined} onClick={onPick}><span class="node-name">{node.label}</span><Count n={node.count} /></a>
          : <span class={`nav-fold${node.past ? ' past' : ''}`} onClick={onToggle}><span class="node-name">{node.label}</span></span>}
      </div>
      {has || node.href === undefined ? (
        <ul class="tree lvl2" id={id} hidden={!open}>
          {open ? node.pages.map((l) => <Leaf l={l} cls="" />) : null}
        </ul>
      ) : null}
    </li>
  );
}

/**
 * Nodes one at a time (an accordion), the first `shown` always visible and the rest under
 * "Older semesters (n)", which unfolds in place. `open` is the node expanded by default; a
 * chevron or name pressed overrides it until the default or `scope` changes (another semester
 * or page opened, another anchor), so the open page is always shown after a move. The fold
 * starts open when the expanded node is inside it. State is the component's, never the URL's.
 */
export function NodeList({ nodes, open = null, shown = nodes.length, scope = '' }: { nodes: NavNode[]; open?: string | null; shown?: number; scope?: string }) {
  const base = `${open ?? ''}|${scope}`;
  const [pick, setPick] = useState<{ base: string; id: string | null } | null>(null);
  const cur = pick && pick.base === base ? pick.id : open;
  const [fold, setFold] = useState<{ base: string; v: boolean } | null>(null);
  const head = nodes.slice(0, shown), rest = nodes.slice(shown);
  const inRest = rest.some((n) => n.id === cur);
  const unfolded = fold && fold.base === base ? fold.v : inRest;
  const node = (n: NavNode) => (
    <TreeNode node={n} open={cur === n.id} onToggle={() => setPick({ base, id: cur === n.id ? null : n.id })} onPick={() => setPick({ base, id: n.id })} />
  );
  const foldId = `nav-older-${nodes[0]?.id ?? ''}`;
  return (
    <ul class="tree">
      {head.map(node)}
      {rest.length ? (
        <li>
          <div class="row">
            <Chev open={unfolded} controls={foldId} what="the older semesters" onToggle={() => setFold({ base, v: !unfolded })} />
            <span class="nav-fold" onClick={() => setFold({ base, v: !unfolded })}>Older semesters ({rest.length})</span>
          </div>
          <ul class="tree" id={foldId} hidden={!unfolded}>{rest.map(node)}</ul>
        </li>
      ) : null}
    </ul>
  );
}

/**
 * The side nav's frame, one for both consoles (decision 0031 rule 11): a quiet root link to the
 * landing page (All courses, or Your semesters for a person with no course), then the anchor
 * and the tree under it.
 */
export function NavTree({ root, rootCurrent = false, anchor, children }: { root: string; rootCurrent?: boolean; anchor?: ComponentChildren; children?: ComponentChildren }) {
  return (
    <nav>
      {/* `?#home` clears the course and semester from the query: the landing page is about none. */}
      <a class="nav-root" href="?#home" aria-current={rootCurrent ? 'page' : undefined}><span aria-hidden="true">‹ </span>{root}</a>
      {anchor}
      {children}
    </nav>
  );
}

/**
 * The anchor: bold, a link when it has a page (a course), plain text when not (a student's
 * semester). It never carries aria-current: the page it opens is also the first leaf under it
 * (Dashboard), which does.
 */
export function NavAnchor({ href, children }: { href?: string; children: ComponentChildren }) {
  return href ? <a class="nav-anchor" href={href}>{children}</a> : <span class="nav-anchor">{children}</span>;
}

/** A semester's name in the tree: the live dot before it, "ended" or "archived" after it. */
function semLabel(name: string, live: boolean, mark?: string) {
  return (
    <>
      {live ? <span class="nav-dot" aria-hidden="true" /> : null}{name}{live ? <span class="sr"> (live)</span> : null}
      {mark ? <span class="n-soon"> {mark}</span> : null}
    </>
  );
}

/** Newest first by the semester key. */
const newestFirst = <T extends { org: string }>(list: T[]) => [...list].sort((a, b) => termRank(b.org) - termRank(a.org));

/** Live first, then past ones, newest first in each; how many show before the fold: every live one, at least three. */
function liveThenPast<T extends { org: string }>(list: T[], isLive: (x: T) => boolean): { ordered: T[]; shown: number } {
  const live = newestFirst(list.filter(isLive)), past = newestFirst(list.filter((x) => !isLive(x)));
  return { ordered: [...live, ...past], shown: Math.max(live.length, 3) };
}

/** A semester's problem count (now or soon, decision 0034) and whether it is archived, from its loaded status. */
export function cohortFlags(l: Loaded | undefined, now = Date.now()): { problems: number | null; archived: boolean } {
  if (!l || l.kind !== 'ready') return { problems: null, archived: false };
  return { problems: problemCount(l.status, now), archived: l.status.semester?.live === false };
}

/** The nine semester pages, in nav order. */
const SEMESTER_PAGES: [string, string][] = [
  ['dashboard', 'Dashboard'], ['schedule', 'Schedule'], ['assignments', 'Assignments'], ['marks', 'Marks'], ['students', 'Students'],
  ['instructors', 'Instructors'], ['site', 'Site'], ['archive', 'Archive'], ['operations', 'Operations'],
];

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
 * The instructor's side nav (decision 0031 rule 11): one course, anchored. The root link (All
 * courses), the course as the anchor (a link to its dashboard), the course's pages (Dashboard first,
 * where the anchor goes, as a semester's Dashboard is where its name goes; Handout materials and
 * Assignment templates open on a page inside them, or with their chevron), then its semesters as
 * nodes: being set up first, every live one, then past ones newest first to three rows and the
 * rest under Older semesters. The open semester is expanded to its pages, else on a course page
 * the newest live one. The person's student semesters are not here: they are on All courses.
 * `cohort` is the semester whose page is open, none on a course page; `site` the semester whose
 * public site the external links name (the URL's, on any page of the course, read only too).
 */
export function Sidenav({ courses, course, cohort, site, cohortStates, current, sub, entry, now = Date.now() }: {
  courses: Course[];
  course?: Course;
  cohort?: CohortRef;
  site?: CohortRef;
  cohortStates: Record<string, Loaded>;
  current: string;
  /** The course's materials and templates for the nav's sub-pages, read for the groups shown; none until its status is read. */
  sub?: (wanted: SubWanted) => CourseSubPages | undefined;
  /** The open page's entry (a repo on `#materials-<repo>` or `#template-<repo>`). */
  entry?: string;
  now?: number;
}) {
  // A group opens on a page inside it, or when its chevron is pressed (kept here only).
  const [open, setOpen] = useState<Partial<Record<keyof SubWanted, boolean>>>({});
  const root = courses.length ? 'All courses' : 'Your semesters';
  if (!course) {
    return (
      <NavTree root={root} rootCurrent={current === 'home'}>
        <p class="footnote" style="padding:4px 10px">{courses.length ? 'Choose a course to see its pages.' : 'Choose a semester to see its pages.'}</p>
      </NavTree>
    );
  }
  const inside = (k: keyof SubWanted) => current === k && !!entry;
  const shownGroup = (k: keyof SubWanted) => open[k] ?? inside(k);
  const pages = course.write ? sub?.({ materials: shownGroup('materials'), templates: shownGroup('templates') }) : undefined;
  const group = (k: keyof SubWanted, href: string, t: string, what: string) => {
    const list = pages?.[k] ?? [];
    const onPage = inside(k) && list.some((x) => !x.ext && x.repo === entry);
    const node: NavNode = { id: k, label: t, href, what, pages: list.map((x) => ({ href: x.href, t: x.label, ext: x.ext, current: onPage && x.repo === entry })) };
    return <TreeNode node={node} open={shownGroup(k)} current={current === k && !onPage} onToggle={() => setOpen({ ...open, [k]: !shownGroup(k) })} />;
  };
  const leaf = (key: string, href: string, t: string) => <Leaf l={{ href, t, current: key === current }} />;
  // Semesters: live is neither archived nor past its end (the status's, else the key's).
  const flags = (k: CohortRef) => {
    const l = cohortStates[k.org];
    const f = cohortFlags(l, now);
    const sem = l?.kind === 'ready' ? l.status.semester : undefined;
    const over = f.archived || sem?.ended === true || semesterOver({ org: k.org, termLabel: k.termLabel, archived: f.archived }, now, sem?.end ?? undefined, sem?.timezone);
    return { ...f, over };
  };
  const { ordered, shown } = liveThenPast(course.cohorts, (k) => !flags(k).over);
  const settingUp = course.settingUp ?? [];
  const nodes: NavNode[] = [
    ...settingUp.map((k) => ({ id: `su-${k.org}`, label: <>{k.termLabel}<span class="n-soon"> being set up</span></>, pending: true, what: '', pages: [] })),
    ...ordered.map((k): NavNode => {
      const f = flags(k);
      // The open semester's pages keep the query (a wizard's way back rides on it); another's name its org.
      const base = cohort?.org === k.org ? '' : `?cohort=${k.org}`;
      return {
        id: k.org,
        label: semLabel(k.termLabel, !f.over, f.archived ? 'archived' : f.over ? 'ended' : undefined),
        href: `?cohort=${k.org}#dashboard`,
        past: f.over,
        count: f.problems,
        what: `the ${k.termLabel} pages`,
        // The count sits on the semester node only, not again on its Dashboard leaf.
        pages: SEMESTER_PAGES.map(([key, t]) => ({ href: `${base}#${key}`, t, current: cohort?.org === k.org && key === current })),
      };
    }),
  ];
  const expanded = cohort?.org ?? ordered.find((k) => !flags(k).over)?.org ?? null;
  return (
    <NavTree root={root} anchor={<NavAnchor href={`?course=${course.org}#course`}>{course.name}</NavAnchor>}>
      {course.write ? (
        <>
          <ul class="tree">
            {leaf('course', `?course=${course.org}#course`, 'Dashboard')}
            {leaf('details', '#details', 'Course details')}
            {group('materials', '#materials', 'Handout materials', 'the handout materials repos')}
            {group('templates', '#templates', 'Assignment templates', 'the assignment templates')}
            {leaf('website', '#website', 'Public website')}
          </ul>
          {nodes.length ? (
            <>
              <div class="nav-h">Semesters</div>
              <NodeList nodes={nodes} open={expanded} shown={settingUp.length + shown} scope={current} />
            </>
          ) : null}
        </>
      ) : (
        // Read only (decision 0032): the dashboard the course name opens, highlighted; the edit pages stay hidden.
        <>
          <ul class="tree">{leaf('course', `?course=${course.org}#course`, 'Dashboard')}</ul>
          <p class="footnote" style="padding:8px 10px">Read only: other pages need write access.</p>
        </>
      )}
      <hr />
      <ul>
        {site ? <li><a href={`https://${site.org}.github.io`} target="_blank" rel="noopener">Public site <Ext /></a></li> : null}
        <li><a href={ghUrl(course.org)} target="_blank" rel="noopener">Course on GitHub <Ext /></a></li>
      </ul>
    </NavTree>
  );
}

/** The site's glyph for each student tab (decision 0035 rule 3); a kind without one of its own gets the folder. */
const SCREEN_ICON: Record<string, string> = {
  home: 'home', week: 'calendar-week', schedule: 'calendar-alt', 'kind-lecture': 'book-reader', 'kind-lab': 'flask', 'kind-readings': 'book',
  assignments: 'user-graduate', materials: 'folder-open', instructors: 'chalkboard-teacher',
};

/**
 * The student's side nav (decision 0031 rule 11), the same tree inverted: a student takes each
 * course once, so the anchor is the open semester (its term, plain text), its nodes the courses
 * the person studies that term (each a link to its Home), the open one expanded to its pages:
 * the site's tabs with their icons, its kind tabs from `facts` once they are read (decision
 * 0035 rule 3). Another live term sits under them with its dot; Past semesters lists the rest, each
 * expanding to its courses as links (no third level), with the same fold as the instructor's.
 * Opening a course of another term re-anchors the tree on that term. In a Student view the tree
 * is that one semester's (decision 0025 rule 5). Semesters the person teaches are not here.
 */
export function StudentNav({ root, semesters, semester, facts = null, current, studentView = false, now = Date.now() }: {
  root: string;
  /** The semesters the person is a student (or auditor) of. */
  semesters: Semester[];
  /** The semester whose student screens are open. */
  semester: Semester;
  /** That semester's shared facts, for its kind tabs; null while they are read. */
  facts?: SemesterFacts | null;
  current: string;
  studentView?: boolean;
  now?: number;
}) {
  const own = studentView ? [semester] : semesters.some((s) => s.org === semester.org) ? semesters : [semester, ...semesters];
  const terms = new Map<string, Semester[]>();
  for (const s of own) terms.set(s.term, [...(terms.get(s.term) ?? []), s]);
  const byName = (list: Semester[]) => [...list].sort((a, b) => (a.courseName || a.org).localeCompare(b.courseName || b.org));
  const termLive = (list: Semester[]) => list.some((s) => !semesterOver(s, now));
  const termMark = (list: Semester[]) => (list.every((s) => s.archived) ? 'archived' : 'ended');
  const here = terms.get(semester.term) ?? [semester];
  const hereLive = termLive(here);
  const courseNodes: NavNode[] = byName(here).map((s) => ({
    id: `c-${s.org}`,
    label: s.courseName || s.org,
    href: studentHref(s.org),
    what: `the ${s.courseName || s.org} pages`,
    pages: studentScreens(s.org === semester.org ? facts : null).map(([k, t]) => ({
      href: studentHref(s.org, k), t: <span class="with-icon"><FaIcon name={SCREEN_ICON[k] ?? 'folder'} />{t}</span>, current: s.org === semester.org && k === current,
    })),
  }));
  const termNode = (list: Semester[], live: boolean): NavNode => {
    const courses = byName(list);
    return {
      id: `t-${list[0].term}`,
      label: semLabel(list[0].termLabel, live, live ? undefined : termMark(list)),
      // A live term links back to its first course; a past one only unfolds.
      href: live ? studentHref(courses[0].org) : undefined,
      past: !live,
      what: `the ${list[0].termLabel} courses`,
      pages: courses.map((s) => ({ href: studentHref(s.org), t: s.courseName || s.org, past: !live })),
    };
  };
  const others = [...terms.values()].filter((list) => list[0].term !== semester.term).map((list) => ({ org: list[0].org, list, live: termLive(list) }));
  const liveOthers = newestFirst(others.filter((o) => o.live)).map((o) => termNode(o.list, true));
  const past = newestFirst(others.filter((o) => !o.live)).map((o) => termNode(o.list, false));
  return (
    <NavTree root={root} anchor={<NavAnchor>{semLabel(semester.termLabel, hereLive, hereLive ? undefined : termMark(here))}</NavAnchor>}>
      <NodeList nodes={courseNodes} open={`c-${semester.org}`} scope={current} />
      {liveOthers.length ? <NodeList nodes={liveOthers} scope={semester.term} /> : null}
      {past.length ? (
        <>
          <div class="nav-h">Past semesters</div>
          <NodeList nodes={past} shown={3} scope={semester.term} />
        </>
      ) : null}
    </NavTree>
  );
}

// --------------------------------------------------------------------------- the banner

/** A breadcrumb: a link to its home, plain text for the open page or a crumb with no page. */
export type Crumb = { t: string; href?: string };

/**
 * The head of every course and semester page, instructor and student alike (decision 0031
 * rule 11): crumbs that follow the tree, the course name as the page's one h1, and on a
 * semester page the semester's line under it (its name, the state chip, "Week N of M" and the
 * dates, each left out when not known) with the semester's links on the right: the Student view
 * pill on an instructor's page, "Back to instructor view" in a preview, and the semester on
 * GitHub. `side` is the course dashboard's (New semester); other course pages have none.
 */
export function CourseBanner({ crumbs, name, hint, semester, side }: {
  crumbs: Crumb[];
  name: string;
  hint?: ComponentChildren;
  semester?: { org: string; termLabel: string; chip?: ComponentChildren; week?: string; dates?: string; view?: 'student' | 'back' };
  side?: ComponentChildren;
}) {
  const s = semester;
  const right = s ? (
    <>
      {s.view === 'student' ? <a class="btn small quiet" href={studentHref(s.org)}>Student view</a> : null}
      {s.view === 'back' ? <a class="textlink" href={`?cohort=${s.org}#dashboard`}>Back to instructor view</a> : null}
      <a class="textlink" href={ghUrl(s.org)} target="_blank" rel="noopener">Semester on GitHub <Ext /></a>
    </>
  ) : side;
  return (
    <>
      <Crumbs items={crumbs} />
      <div class={`course-banner${right ? '' : ' plain'}`}>
        <div class="cb-main">
          <h1>{name}{hint ? <> {hint}</> : null}</h1>
          {s ? <p class="cb-sem"><span class="sem-title">{s.termLabel}</span>{s.chip}{s.week ? <span>{s.week}</span> : null}{s.dates ? <span>{s.dates}</span> : null}</p> : null}
        </div>
        {right ? <div class="cb-side">{right}</div> : null}
      </div>
    </>
  );
}
