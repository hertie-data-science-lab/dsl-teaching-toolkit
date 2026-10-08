// The student shell's screens (decision 0011 rule 2), the semester site's tabs (decision 0035
// rule 3): Home, This week, Schedule, one tab per kind, Assignments, All materials and
// Instructors of one semester, each title with its `?` (decision 0029 rule 5), the side nav
// with the semester's kind tabs, and the course banner's week line and dates (rule 2). Each
// screen lives in its own `Student<Screen>.tsx`. The semester's shared facts come
// through `StudentData` (the engine's public `student-status.json`, else the site); the
// student's own repos, team, role, receipts and marks come from GitHub with their own token
// (`model/mine.ts`). In an instructor's Student view (rule 7) the same screens render with the
// instructor's own identity and never read anyone's repos or marks. An auditor sees the
// materials and the schedule, and nothing that promises a repo, a team or marks. An archived
// semester is history: the student's own repos and marks, read-only, and nothing is run.

import { DEFAULT_TIMEZONE } from '../model/policy';
import { useEffect } from 'preact/hooks';
import { useEnv } from '../env';
import type { GitHubClient } from '../github/client';
import type { Semester } from '../model/discovery';
import { ago } from '../model/format';
import { knownAuditor, patchLines, readAllReceipts, readMine, repoUrl, type Mine, type Receipts } from '../model/mine';
import { lastVisit, markVisit } from '../model/prefs';
import { StatusFileSource, type SemesterFacts, type StudentData } from '../model/student';
import { semesterLine, weekItems } from '../model/week';
import { STUDENT_SCREENS, studentHref, studentScreens, tabWord } from '../router';
import { CheckLine, Loading, ghUrl } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { useLoad } from '../ui/load';
import { Ext } from '../ui/icons';
import { CourseBanner, StudentNav } from '../ui/shell';
import { AssignmentsView, MarksView } from './StudentAssignments';
import { HomeView } from './StudentHome';
import { KindView } from './StudentKind';
import { InstructorsView } from './StudentInstructors';
import { MaterialsView, ReadingsView } from './StudentMaterials';
import { ScheduleView } from './StudentSchedule';
import { WeekList } from './StudentWeek';

export interface StudentProps {
  semester: Semester;
  /** The screen key from STUDENT_SCREENS, or a kind tab's `kind-<key>`. */
  screen: string;
  /** An instructor looking at the semester as a student would. */
  studentView: boolean;
  /** What the hash names after the screen (a materials path). */
  entry?: string;
  now?: number;
}

/** The screen a hash names (a kind tab's whether or not the semester has it), Home for anything else. */
export const studentScreen = (key: string) => (STUDENT_SCREENS.some(([k]) => k === key) || /^kind-[\w-]+$/.test(key) ? key : 'home');

/** A semester's shared facts, read once per session (`studentData`); none for an archived semester. */
function useFacts(semester: Semester) {
  const env = useEnv();
  return useLoad(env && !semester.archived ? () => studentData(env.client).facts(semester.org) : null, [semester.org]);
}

// --------------------------------------------------------------------------- loading

const sources = new WeakMap<GitHubClient, StudentData>();

/** Drop the session's shared facts (sign-out). */
export const forgetStudentData = (client: GitHubClient) => sources.delete(client);

/** The one `StudentData` the console reads a semester's shared facts through. */
export function studentData(client: GitHubClient): StudentData {
  let s = sources.get(client);
  if (!s) {
    s = new StatusFileSource(client);
    sources.set(client, s);
  }
  return s;
}

export function StudentViewBanner() {
  return (
    <div class="ro-banner" role="status">
      <b>Student view.</b>
      <span>What a student of this semester sees, shown with your own account: no student’s repos or marks.</span>
    </div>
  );
}

/** The `?` beside each student page title (decision 0029 rule 5): one or two sentences. */
export const STUDENT_HINTS: Record<string, string> = {
  home: 'The course’s own page: news, the syllabus, who teaches it.',
  week: 'What is due, handed out or released this week, and news from your instructors.',
  schedule: 'Every session, assignment and due date of the semester, with its files and readings once they are released.',
  assignments: 'Your repo, team, deadlines and Submission receipts for each assignment. Receipts are comments the automation leaves in your repo when it collects your work.',
  materials: 'The files your instructors have released to this semester, read with your own account.',
  instructors: 'Who teaches this semester.',
};

/** A kind tab's `?`. */
export const kindHint = (label: string) => `${label} by session, with their files once they are released.`;

/** The student's side nav with the open semester's kind tabs, once its facts are read (decision 0035 rule 3). */
export function StudentSideNav(props: Omit<Parameters<typeof StudentNav>[0], 'facts'>) {
  const facts = useFacts(props.semester);
  return <StudentNav {...props} facts={facts.kind === 'ready' ? facts.value : null} />;
}

/**
 * The course banner in the student console (decision 0031 rule 11): the instructor's banner,
 * the course name as the h1 and the semester's line under it, its week and dates from the shared
 * facts (decision 0029 rule 2), which the screen below reads anyway (`studentData` keeps one read
 * per semester). Until they are read, and for a source without the dates, the line is the name
 * and the chip. The crumbs follow the student's tree: the landing page (`root`), the semester
 * (no page of its own, so plain) and the course, a link to its Home, plain on Home.
 */
export function StudentBanner({ root, screen, semester, studentView, chip, now = Date.now() }: { root: string; screen: string; semester: Semester; studentView: boolean; chip?: preact.ComponentChildren; now?: number }) {
  const facts = useFacts(semester);
  const line = facts.kind === 'ready' && facts.value ? semesterLine(facts.value, now) : {};
  const name = semester.courseName || semester.org;
  return (
    <CourseBanner crumbs={[{ t: root, href: '?#home' }, { t: semester.termLabel }, { t: name, href: screen === 'home' ? undefined : studentHref(semester.org) }]} name={name}
      semester={{ org: semester.org, termLabel: semester.termLabel, chip, view: studentView ? 'back' : undefined, ...line }} />
  );
}

export function StudentScreen({ semester, screen, studentView, entry, now = Date.now() }: StudentProps) {
  const facts = useFacts(semester);
  const f = facts.kind === 'ready' ? facts.value : null;
  // A kind tab's label from the policy; its key until the facts are read (or for a kind the semester lacks).
  const key = screen.startsWith('kind-') ? screen.slice(5) : '';
  const kind = key ? studentScreens(f).find(([k]) => k === screen)?.[1] ?? tabWord(key.charAt(0).toUpperCase() + key.slice(1)) : '';
  // Home's head is the site's h1: the course with "/ <semester>" (decision 0035 rule 4).
  const label = kind || (screen === 'home'
    ? <>{f?.courseName || semester.courseName || semester.org} <span class="h-sub">/ {semester.termLabel}</span></>
    : STUDENT_SCREENS.find(([k]) => k === screen)?.[1] ?? 'Home');
  const hint = kind ? kindHint(kind) : STUDENT_HINTS[screen];
  return (
    <>
      {studentView ? <StudentViewBanner /> : null}
      <div class="page-head"><div><h2 class="h1">{label}{hint ? <> <Hint>{hint}</Hint></> : null}</h2></div></div>
      {semester.archived ? <ArchivedSemester semester={semester} studentView={studentView} /> : <SemesterBody semester={semester} screen={screen} studentView={studentView} entry={entry} now={now} />}
    </>
  );
}

function SemesterBody({ semester, screen, studentView, entry, now }: Required<Omit<StudentProps, 'entry'>> & { entry?: string }) {
  const env = useEnv();
  const org = semester.org;
  const facts = useLoad(env ? () => studentData(env.client).facts(org) : null, [org]);
  const f = facts.kind === 'ready' ? facts.value : null;
  const mine = useLoad(env && f && !studentView ? () => readMine(env.client, org, env.user.login, f.assignments) : null, [org, f]);
  const m: Mine | null = mine.kind === 'ready' ? mine.value : null;
  // The Submission receipts issues feed Assignments and This week's "your instructors updated files" line.
  const threads = screen === 'week' || screen === 'assignments';
  const receipts = useLoad<Record<string, Receipts | null>>(env && f && m && threads && !m.auditor ? () => readAllReceipts(env.client, org, f.assignments, m) : null, [org, f, m, threads]);
  const login = env?.user.login ?? '';
  // The visit is stored only once the threads it is compared against were read.
  useEffect(() => {
    if (receipts.kind === 'ready' && login && !studentView) markVisit(login, org, now);
  }, [receipts.kind, org, login]);
  if (facts.kind === 'loading') return <Loading what="Reading the semester" />;
  if (facts.kind === 'failed') return <CheckLine cls="bad">The semester’s schedule could not be read: {facts.error}</CheckLine>;
  if (!f) return <NoFacts org={org} />;
  // The role could not be read: nothing below promises a repo, a team or marks.
  const unknownRole = !studentView && mine.kind === 'failed';
  const mineNote = studentView ? null
    : mine.kind === 'loading' ? (knownAuditor(org) ? null : <Loading what="Reading your repos and marks" />)
    : unknownRole ? <CheckLine cls="warn">Could not read your role in this semester ({mine.error}), so your repos, team and marks are not shown. Reload the page to try again.</CheckLine>
    : null;
  const tz = f.timezone || DEFAULT_TIMEZONE;
  const rc = receipts.kind === 'ready' ? receipts.value : undefined;
  const hasThreads = !!m && f.assignments.some((a) => a.privateRepo && m.units[a.slug]?.repo);
  const body =
    screen === 'schedule' ? <ScheduleView facts={f} mine={m} now={now} org={org} />
    : screen === 'assignments' ? <AssignmentsView org={org} facts={f} mine={m} now={now} studentView={studentView} receipts={hasThreads ? rc : {}} unknownRole={unknownRole} />
    : screen === 'materials' ? <div class="stack"><MaterialsView org={org} repos={f.materialsRepos} entry={entry} />{entry ? null : <ReadingsView org={org} facts={f} now={now} />}</div>
    : screen === 'instructors' ? <InstructorsView facts={f} org={org} />
    : screen === 'home' ? <HomeView facts={f} org={org} now={now} />
    : screen.startsWith('kind-') ? <KindView facts={f} kind={screen.slice(5)} org={org} now={now} />
    : <WeekList items={weekItems(f, m, now, patchLines(f.assignments, m, rc), studentView || !login ? null : lastVisit(login, org)).filter((i) => !(unknownRole && i.kind === 'teams'))} tz={tz} org={org} />;
  return (
    <div class="stack">
      {screen === 'week' && f.generatedAt ? <p class="footnote updated">Updated {ago(f.generatedAt, now)}</p> : null}
      {m?.auditor ? <AuditorNote /> : null}
      {['week', 'schedule', 'assignments'].includes(screen) ? mineNote : null}
      {body}
    </div>
  );
}

/** What an auditor gets, said once on every screen. */
export function AuditorNote() {
  return (
    <div class="note" role="note">
      <b>You audit this semester.</b> As an auditor you read the materials and follow the schedule; you hand in no work, join no team and get no marks.
    </div>
  );
}

function NoFacts({ org }: { org: string }) {
  return (
    <section class="panel section stub">
      <p>This semester publishes no schedule yet. <a href={ghUrl(org)} target="_blank" rel="noopener">Semester on GitHub <Ext /></a></p>
    </section>
  );
}

/**
 * History: an archived semester shows the student's own repos and marks, read-only, from the
 * same reads as a live one (its repos stay readable); nothing is run against it. The Student
 * view reads no one's.
 */
export function ArchivedSemester({ semester, studentView = false }: { semester: Semester; studentView?: boolean }) {
  const env = useEnv();
  const org = semester.org;
  const load = useLoad(
    env && !studentView
      ? async () => {
          const f = await studentData(env.client).facts(org).catch(() => null);
          return { f, m: await readMine(env.client, org, env.user.login, f?.assignments ?? []) };
        }
      : null,
    [org],
  );
  const head = (
    <section class="panel section">
      <p>This semester is archived: every repository in it is read-only, and you keep read access to what was yours.</p>
      <p><a class="btn outline" href={ghUrl(org)} target="_blank" rel="noopener">Semester on GitHub <Ext /></a></p>
    </section>
  );
  if (!env || studentView) return head;
  if (load.kind === 'loading') return <div class="stack">{head}{knownAuditor(org) ? null : <Loading what="Reading your repos and marks" />}</div>;
  if (load.kind === 'failed') return <div class="stack">{head}<CheckLine cls="warn">Your repos and marks could not be read: {load.error}</CheckLine></div>;
  const { f, m } = load.value;
  const facts: SemesterFacts = f ?? EMPTY_FACTS;
  const repos = facts.assignments.flatMap((a) => (m.units[a.slug]?.repo ? [[a, m.units[a.slug].repo!] as const] : []));
  return (
    <div class="stack">
      {head}
      <section class="panel section" aria-labelledby="h-history-repos">
        <h2 id="h-history-repos">Your repos</h2>
        {repos.length ? (
          <ul class="plain-list">{repos.map(([a, r]) => <li><a href={repoUrl(org, r)} target="_blank" rel="noopener">{r} <Ext /></a> <span class="footnote">{a.title}{m.units[a.slug].team ? `, team ${m.units[a.slug].team}` : ''}; read-only</span></li>)}</ul>
        ) : <p class="footnote">No assignment repo of yours was found in this semester.</p>}
      </section>
      <section class="section" aria-labelledby="h-history-marks">
        <h2 id="h-history-marks">Your marks</h2>
        <MarksView org={org} login={env.user.login} facts={facts} gradebook={m.gradebook} studentView={false} auditor={m.auditor} />
      </section>
    </div>
  );
}

const EMPTY_FACTS: SemesterFacts = {
  courseName: '', timezone: DEFAULT_TIMEZONE, rows: [], assignments: [], instructors: [], archive: null, latePolicy: [], materialsRepos: [], homeMarkdown: '', announcements: [], syllabus: null, courseDescription: '', previousOfferings: [],
};
