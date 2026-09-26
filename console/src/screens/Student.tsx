// The student shell's screens (decision 0011 rule 2): This week (with the course's About
// block), Schedule, Assignments, Marks, Materials, Set up, Join and Instructors of one
// semester, and This week across every semester on Home. The semester's shared facts come
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
import { semesterName, type Semester } from '../model/discovery';
import { dayKey, fmtDay, fmtTime, fmtWhen, sortKey } from '../model/format';
import { gradebookUrl, isMarked, knownAuditor, patchLines, patchNotes, readAllReceipts, readMine, repoUrl, type Gradebook, type MarkEntry, type Mine, type Receipts, type ThreadKind } from '../model/mine';
import { lastVisit, markVisit } from '../model/prefs';
import { IMG_HOSTS, MY_STATE_WORD, STUDENT_CHOICE, StatusFileSource, instant, myState, sortedRows, type FileLink, type InstructorCard, type ScheduleRow, type SemesterAssignment, type SemesterFacts, type StudentData } from '../model/student';
import { weekItems, type WeekItem } from '../model/week';
import { STUDENT_SCREENS, studentHref } from '../router';
import { CheckLine, Crumbs, Loading, Md, ghUrl } from '../ui/bits';
import { useLoad } from '../ui/load';
import { Ext } from '../ui/icons';
import { GhMd, LazyFold } from '../ui/rendered';
import { JoinScreen } from './StudentJoin';
import { MaterialsView, ReadingsView, materialHref } from './StudentMaterials';
import { SetupView } from './StudentSetup';

export interface StudentProps {
  semester: Semester;
  /** The screen key from STUDENT_SCREENS. */
  screen: string;
  /** An instructor looking at the semester as a student would. */
  studentView: boolean;
  /** What the hash names after the screen (a materials path). */
  entry?: string;
  now?: number;
}

/** The screen a hash names, This week for anything else. */
export const studentScreen = (key: string) => (STUDENT_SCREENS.some(([k]) => k === key) ? key : 'week');

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

export function StudentViewBanner({ semester }: { semester: Semester }) {
  return (
    <div class="ro-banner" role="status">
      <b>Student view.</b>
      <span>What a student of {semesterName(semester)} sees, shown with your own account: no student’s repos or marks.</span>
      <a href={`?cohort=${semester.org}#semester`}>Back to the instructor screens</a>
    </div>
  );
}

export function StudentScreen({ semester, screen, studentView, entry, now = Date.now() }: StudentProps) {
  const label = STUDENT_SCREENS.find(([k]) => k === screen)?.[1] ?? 'This week';
  return (
    <>
      <Crumbs items={[{ t: 'Your semesters', href: '#home' }, { t: semesterName(semester), href: studentHref(semester.org) }, { t: label }]} />
      {studentView ? <StudentViewBanner semester={semester} /> : null}
      <div class="page-head"><div><h1>{label}</h1><p class="lede">{semesterName(semester)}{semester.archived ? '; archived' : ''}</p></div></div>
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
    : unknownRole && (screen === 'marks' || screen === 'join') ? null
    : screen === 'marks' ? <MarksView org={org} login={login} facts={f} gradebook={m?.gradebook ?? null} studentView={studentView} loaded={mine.kind !== 'loading'} auditor={m?.auditor} />
    : screen === 'materials' ? <div class="stack"><MaterialsView org={org} repos={f.materialsRepos} entry={entry} />{entry ? null : <ReadingsView org={org} facts={f} now={now} />}</div>
    : screen === 'setup' ? <SetupView org={org} facts={f} mine={m} studentView={studentView} />
    : screen === 'join' ? <JoinScreen org={org} facts={f} mine={m} studentView={studentView} />
    : screen === 'instructors' ? <InstructorsView facts={f} org={org} />
    : (
      <>
        <WeekList items={weekItems(f, m, now, patchLines(f.assignments, m, rc), studentView || !login ? null : lastVisit(login, org)).filter((i) => !(unknownRole && i.kind === 'teams'))} tz={tz} org={org} />
        <AboutView facts={f} org={org} tz={tz} now={now} />
      </>
    );
  return (
    <div class="stack">
      {f.archive ? <ArchiveNotice when={f.archive} tz={tz} now={now} /> : null}
      {m?.auditor ? <AuditorNote /> : null}
      {screen !== 'instructors' && screen !== 'materials' ? mineNote : null}
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
      <p>This semester publishes no schedule yet. <a href={ghUrl(org)} target="_blank" rel="noopener">Open it on GitHub <Ext /></a></p>
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
      <p>{semesterName(semester)} is archived: every repository in it is read-only, and you keep read access to what was yours.</p>
      <p><a class="btn outline" href={ghUrl(org)} target="_blank" rel="noopener">Open the semester on GitHub <Ext /></a></p>
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
  courseName: '', timezone: DEFAULT_TIMEZONE, rows: [], assignments: [], instructors: [], archive: null, latePolicy: [], materialsRepos: [], homeMarkdown: '', announcements: [], syllabus: null,
};

export function ArchiveNotice({ when, tz, now }: { when: string; tz: string; now: number }) {
  const past = instant(when, tz) <= now;
  return (
    <div class="note" role="note">
      <b>Archive{past ? 'd' : ''}.</b>{' '}
      {past
        ? <>This semester was archived on {fmtDay(when, tz)}: its repositories are read-only.</>
        : <>This semester is archived on {fmtDay(when, tz, new Date(now).getFullYear())}: every repository in it becomes read-only. You keep read access, so you can fork or clone anything you want to keep working on into your own account.</>}
    </div>
  );
}

// --------------------------------------------------------------------------- This week

export interface WeekLine extends WeekItem {
  /** The semester the line is about, on Home where several are merged. */
  semester?: string;
  org: string;
}

export function WeekList({ items, tz, org, semesterOf }: { items: WeekItem[]; tz: string; org: string; semesterOf?: (i: WeekItem) => string }) {
  if (!items.length) return <p class="footnote">Nothing is due, handed out or released this week.</p>;
  return (
    <ul class="timeline week-list">
      {items.map((i) => {
        const l = i as WeekLine;
        const target = l.org ?? org;
        return (
          <li class={`trow ${i.cls}`}>
            <span class="k">{i.label}</span>
            <span class="d">{i.when ? fmtDay(i.when, i.tz ?? tz) : 'now'}{i.when && fmtTime(i.when, i.tz ?? tz) && !/T00:00(:00)?$/.test(i.when) ? <span>{fmtTime(i.when, i.tz ?? tz)}</span> : null}</span>
            <span class="ttl"><a href={studentHref(target, i.screen)}>{i.text}</a>{i.note ? <span class="w-note">; {i.note}</span> : null}</span>
            <span class="st">{semesterOf ? <span class="st-chip">{semesterOf(i)}</span> : null}</span>
          </li>
        );
      })}
    </ul>
  );
}

/** Home for a student: this week in every semester they chose to show, one line each with the semester. */
export function StudentWeekHome({ semesters, now }: { semesters: Semester[]; now: number }) {
  const env = useEnv();
  const live = semesters.filter((s) => !s.archived);
  const key = live.map((s) => s.org).join(',');
  const load = useLoad<WeekLine[]>(
    env && live.length
      ? async () => {
          const lists = await Promise.all(live.map(async (s) => {
            const f = await studentData(env.client).facts(s.org).catch(() => null);
            if (!f) return [];
            const m = await readMine(env.client, s.org, env.user.login, f.assignments).catch(() => null);
            const rc = m && !m.auditor ? await readAllReceipts(env.client, s.org, f.assignments, m).catch(() => null) : null;
            const seen = lastVisit(env.user.login, s.org);
            if (rc) markVisit(env.user.login, s.org, now);
            // Each line keeps its own semester's timezone (weekItems sets it).
            return weekItems(f, m, now, patchLines(f.assignments, m, rc), seen).map((i) => ({ ...i, org: s.org, semester: semesterName(s) }));
          }));
          return lists.flat().sort((a, b) => a.at - b.at);
        }
      : null,
    [key, Math.floor(now / 36e5)],
  );
  if (!live.length) return null;
  return (
    <section class="section" aria-labelledby="h-week">
      <h2 id="h-week">This week</h2>
      {load.kind === 'loading' ? <Loading what="Reading your semesters" /> : load.kind === 'failed' ? <CheckLine cls="bad">This week could not be read: {load.error}</CheckLine> : (
        <WeekList items={load.value} tz={DEFAULT_TIMEZONE} org={live[0].org} semesterOf={(i) => (i as WeekLine).semester ?? ''} />
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- About

/** The course's own words on This week: its name, the syllabus pinned, the instructors' welcome text and their announcements. */
export function AboutView({ facts, org, tz, now }: { facts: SemesterFacts; org: string; tz: string; now: number }) {
  const { courseName, syllabus, homeMarkdown, announcements } = facts;
  if (!courseName && !syllabus && !homeMarkdown && !announcements.length) return null;
  const year = new Date(now).getFullYear();
  const syl = syllabus ? fileHref(org, facts.materialsRepos, syllabus) : null;
  return (
    <section class="panel section" aria-labelledby="h-about">
      <h2 id="h-about">{courseName ? `About ${courseName}` : 'About the course'}</h2>
      {syllabus && syl ? <p><a class="btn outline small" href={syl.href} {...(syl.ext ? { target: '_blank', rel: 'noopener' } : {})}>Syllabus{syl.ext ? <> <Ext /></> : null}</a></p> : null}
      {homeMarkdown ? <GhMd src={homeMarkdown} context={`${org}/${org}.github.io`} /> : null}
      {announcements.length ? (
        <div class="fb">
          <h3>Announcements</h3>
          <ul class="plain-list">
            {announcements.map((n) => <li><span class="footnote">{fmtDay(n.when, tz, year)}</span> {n.title ? <b>{n.title}</b> : null}{n.details ? <Md src={n.details} /> : null}</li>)}
          </ul>
        </div>
      ) : null}
    </section>
  );
}

/** Where a released file opens: inside the console when it is in a materials repo, else on GitHub. */
export function fileHref(org: string, repos: string[], l: FileLink): { href: string; ext: boolean } {
  return l.repo && l.path && repos.includes(l.repo) ? { href: materialHref(org, l.repo, l.path), ext: false } : { href: l.url, ext: true };
}

function FileChips({ org, repos, links }: { org: string; repos: string[]; links: FileLink[] }) {
  return (
    <>
      {links.map((l) => {
        const h = fileHref(org, repos, l);
        return <a class="st-chip" href={h.href} {...(h.ext ? { target: '_blank', rel: 'noopener' } : {})}>{l.name || 'file'}</a>;
      })}
    </>
  );
}

// --------------------------------------------------------------------------- Schedule

export const ROW_CLASS: Record<string, string> = { lecture: 'lec', lab: 'lab', assignment: 'asg', due: 'asg', exam: 'exam', special_event: 'evt', term_date: 'term' };
export const ROW_WORD: Record<string, string> = { lecture: 'lecture', lab: 'lab', assignment: 'hand out', due: 'due', exam: 'exam', special_event: 'event', term_date: 'term date' };

/** Monday of the week `iso` falls in, as yyyy-mm-dd. */
function mondayOf(iso: string, tz: string): string {
  const day = dayKey(iso, tz);
  const [y, m, d] = day.split('-').map(Number);
  const dow = (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
  return new Date(Date.UTC(y, m - 1, d - dow)).toISOString().slice(0, 10);
}

export function ScheduleView({ facts, mine, now, org }: { facts: SemesterFacts; mine: Mine | null; now: number; org: string }) {
  const tz = facts.timezone || DEFAULT_TIMEZONE;
  const rows = sortedRows(facts.rows, tz);
  if (!rows.length) return <p class="footnote">The schedule has no entries yet.</p>;
  const year = new Date(now).getFullYear();
  const byAssignment = new Map(facts.assignments.map((a) => [a.slug, a]));
  const weeks: [string, ScheduleRow[]][] = [];
  for (const r of rows) {
    const w = mondayOf(r.when, tz);
    const last = weeks[weeks.length - 1];
    if (last && last[0] === w) last[1].push(r);
    else weeks.push([w, [r]]);
  }
  const nowKey = sortKey(new Date(now).toISOString(), tz);
  let todayDone = false;
  return (
    <div class="stack">
      {weeks.map(([monday, list], n) => (
        <section aria-label={`Week ${n + 1}`}>
          <h2 class="week-h">Week {n + 1} <span>from {fmtDay(monday, tz, year)}</span></h2>
          <ul class="timeline">
            {list.flatMap((r) => {
              const out = [];
              if (!todayDone && sortKey(r.when, tz) >= nowKey) {
                todayDone = true;
                out.push(<li class="today-line">Today, {fmtDay(new Date(now).toISOString(), tz, year)}</li>);
              }
              const a = r.assignment ? byAssignment.get(r.assignment) : undefined;
              const st = a && !mine?.auditor ? myState(a, isMarked(mine?.gradebook ?? null, a.slug), now, tz) : null;
              const yours = a && mine ? mine.units[a.slug] : undefined;
              const files = r.links.filter((l) => !r.readings.includes(l));
              // A kind the console has no class for (a policy kind: readings, drop-in) takes its policy colours.
              const k = ROW_CLASS[r.kind] ? undefined : facts.kinds?.[r.kind];
              out.push(
                <li class={`trow ${ROW_CLASS[r.kind] ?? 'evt'}${yours?.repo ? ' mine' : ''}`} style={k?.background ? { background: k.background } : undefined}>
                  <span class="k" style={k?.colour ? { color: k.colour } : undefined}>{ROW_WORD[r.kind] ?? k?.label.toLowerCase() ?? r.kind}</span>
                  <span class="d">{fmtDay(r.when, tz, year)}{!r.allDay && fmtTime(r.when, tz) ? <span>{fmtTime(r.when, tz)}</span> : null}{r.tbc ? <span class="tbc">TBC</span> : null}</span>
                  <span class="ttl">
                    <b>{r.title}</b>{r.subtitle ? `: ${r.subtitle}` : ''}
                    {r.details ? <Md class="t-details" src={r.details} /> : null}
                    {r.readings.length || r.readingList ? (
                      <span class="t-readings">Readings: <FileChips org={org} repos={facts.materialsRepos} links={r.readings} />{r.readingList ? <a class="st-chip" href={studentHref(org, 'materials')}>reading list</a> : null}</span>
                    ) : r.readingsPending ? <span class="t-readings footnote">Readings to come.</span> : null}
                  </span>
                  <span class="st">
                    {st ? <a class="st-chip" href={studentHref(org, 'assignments')}>{MY_STATE_WORD[st]}</a> : null}
                    {yours?.repo ? <span class="st-chip">yours</span> : null}
                    {files.length ? <FileChips org={org} repos={facts.materialsRepos} links={files} /> : !r.released ? <span class="st-chip">not released yet</span> : null}
                  </span>
                </li>,
              );
              return out;
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}

// --------------------------------------------------------------------------- Assignments

const SUBMIT_WORD: Record<string, string> = {
  assignment_repo: 'Push to your repo', shared_dropbox_repo: 'Push to your folder in the shared repo', external: 'Handed in outside GitHub',
};

export function AssignmentsView({ org, facts, mine, now, studentView, receipts, unknownRole = false }: {
  org: string; facts: SemesterFacts; mine: Mine | null; now: number; studentView: boolean;
  /** The person's role could not be read: promise them nothing. */
  unknownRole?: boolean;
  /** Receipts per repo; undefined while they are read. */
  receipts?: Record<string, Receipts | null>;
}) {
  const tz = facts.timezone || DEFAULT_TIMEZONE;
  const year = new Date(now).getFullYear();
  if (!facts.assignments.length) return <p class="footnote">No assignments are planned yet.</p>;
  const list = [...facts.assignments].sort((a, b) => (a.due ? instant(a.due, tz) : Infinity) - (b.due ? instant(b.due, tz) : Infinity));
  return (
    <div class="stack">
      {list.map((a) => {
        const auditor = mine?.auditor === true || unknownRole;
        const st = myState(a, isMarked(mine?.gradebook ?? null, a.slug), now, tz);
        const u = mine?.units[a.slug];
        const rc = u?.repo ? receipts?.[u.repo] : undefined;
        const tbc = a.tbc ? ' (TBC)' : '';
        const patch = patchNotes(rc).at(-1);
        const past = st === 'marking' || st === 'returned';
        return (
          <section class="panel section a-card" aria-label={a.title}>
            <div class="a-head">
              <h2>{a.title}{a.subtitle ? <span>{a.subtitle}</span> : null}</h2>
              {auditor ? null : <span class={`chip ${st === 'returned' ? 'ok' : st === 'late_window' ? 'amber' : st === 'open' ? 'asg' : ''}`}>{MY_STATE_WORD[st]}</span>}
            </div>
            {patch && !auditor ? <div class="note" role="note"><b>Pull before you continue.</b> <span class="footnote">{fmtWhen(patch.when, tz, year)}</span><Md src={patch.text} /></div> : null}
            <dl class="kv">
              {a.handout ? <><dt>Handed out</dt><dd>{fmtWhen(a.handout, tz, year)}{tbc}</dd></> : null}
              {a.due ? <><dt>Due</dt><dd>{fmtWhen(a.due, tz, year)}{tbc}</dd></> : null}
              {a.lateCutoff && a.lateCutoff !== a.due ? <><dt>Late cutoff</dt><dd>{fmtWhen(a.lateCutoff, tz, year)}{tbc}</dd></> : null}
              {a.lateRule ? <><dt>Late work</dt><dd>{a.lateRule}</dd></> : null}
              {a.maxPoints ? <><dt>Out of</dt><dd>{a.maxPoints} points</dd></> : null}
              {a.solutionShown ? <><dt>Solution shown</dt><dd>{fmtWhen(a.solutionShown, tz, year)}</dd></> : null}
              {a.submitVia && !auditor ? <><dt>How to hand in</dt><dd>{SUBMIT_WORD[a.submitVia]}{a.submitVia === 'external' && a.submitUrl ? <>: <a href={a.submitUrl} target="_blank" rel="noopener">{hostOf(a.submitUrl)} <Ext /></a></> : null}</dd></> : null}
              {studentView ? <><dt>Yours</dt><dd class="footnote">A student’s repo, team and receipts show here.</dd></>
                : unknownRole ? <><dt>Yours</dt><dd class="footnote">Could not read your role: your repo, team and receipts are not shown.</dd></>
                : auditor ? <><dt>Yours</dt><dd class="footnote">As an auditor you hand in no work for this assignment.</dd></>
                : <MyUnitRows org={org} a={a} mine={mine} receipts={rc} loading={u?.repo ? receipts === undefined : false} tz={tz} year={year} />}
            </dl>
            {a.shapeNote && !auditor ? <p class="footnote shape-note">{a.shapeNote}</p> : null}
            {a.shape === STUDENT_CHOICE && past && u?.repo && !auditor ? (
              <p class="footnote">The late cutoff has passed: you may make {u.repo} public, if you want it in your portfolio, under <a href={`${repoUrl(org, u.repo)}/settings`} target="_blank" rel="noopener">Settings, Danger zone <Ext /></a>.</p>
            ) : null}
            {a.cutoffSentence && a.submitVia !== 'external' && !auditor ? <p class="footnote">{a.cutoffSentence}</p> : null}
            {a.brief ? <LazyFold summary="The brief">{() => <GhMd src={a.brief} context={u?.repo ? `${org}/${u.repo}` : undefined} />}</LazyFold> : null}
            {rc && rc.thread.length ? <ThreadView receipts={rc} tz={tz} year={year} /> : null}
          </section>
        );
      })}
      {facts.latePolicy.length ? (
        <section class="section" aria-labelledby="h-late">
          <h2 id="h-late">Late work in this course</h2>
          <ul class="plain-list">{facts.latePolicy.map((l) => <li>{l}</li>)}</ul>
        </section>
      ) : null}
    </div>
  );
}

const THREAD_WORD: Record<ThreadKind, string> = { receipt: 'Receipt', patch: 'Files updated', marks: 'Marks', comment: 'Comment' };

/** All of the Submission receipts issue: its own text, then every comment. */
export function ThreadView({ receipts, tz, year }: { receipts: Receipts; tz: string; year: number }) {
  return (
    <details class="fold">
      <summary>Every comment on Submission receipts <span class="cnt">{receipts.thread.length}</span></summary>
      <div class="fold-body">
        {receipts.body ? <div class="receipt"><Md src={receipts.body} /></div> : null}
        {receipts.thread.map((c) => (
          <div class={`receipt r-${c.kind}`}><span class="footnote"><b>{THREAD_WORD[c.kind]}</b>, {fmtWhen(c.when, tz, year)}</span><Md src={c.text} /></div>
        ))}
        <p><a href={receipts.url} target="_blank" rel="noopener">Open Submission receipts on GitHub <Ext /></a></p>
      </div>
    </details>
  );
}

const hostOf = (url: string) => {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
};

function MyUnitRows({ org, a, mine, receipts, loading, tz, year }: { org: string; a: SemesterAssignment; mine: Mine | null; receipts: Receipts | null | undefined; loading: boolean; tz: string; year: number }) {
  if (!mine || a.submitVia === 'external') return null;
  const u = mine.units[a.slug];
  const out = [];
  if (u?.repo) out.push(<><dt>{u.shared ? 'Shared repo' : 'Your repo'}</dt><dd><a href={repoUrl(org, u.repo)} target="_blank" rel="noopener">{u.repo} <Ext /></a>{u.shared ? <span class="footnote"> (your work goes in a folder named after {a.group ? 'your team' : 'you'})</span> : null}</dd></>);
  else if (a.handedOut && !(a.group && a.teamFormation)) out.push(<><dt>Your repo</dt><dd class="footnote">Not there yet: your instructors hand it out.</dd></>);
  if (a.group) {
    out.push(<><dt>Your team</dt><dd>{u?.team ? <>{u.team}{u.members?.length ? <span class="footnote">: {u.members.join(', ')}</span> : null}</> : a.teamFormation ? <a href={studentHref(org, 'join')}>You have no team yet: join or create one</a> : <span class="footnote">No team yet.</span>}</dd></>);
  }
  if (a.group && u?.repo && !u.shared) {
    out.push(<><dt>Contributions</dt><dd>Fill in <a href={`${repoUrl(org, u.repo)}/blob/HEAD/CONTRIBUTIONS.md`} target="_blank" rel="noopener">CONTRIBUTIONS.md <Ext /></a> in your team repo before the deadline.</dd></>);
  }
  if (u?.repo && a.privateRepo) {
    out.push(
      <><dt>Submission receipts</dt><dd>
        {loading ? <span class="footnote">Reading…</span> : receipts ? (
          <>
            <a href={receipts.url} target="_blank" rel="noopener">Open the thread <Ext /></a>
            {receipts.last ? <div class="receipt"><span class="footnote">{fmtWhen(receipts.last.when, tz, year)}</span><Md src={receipts.last.text} /></div> : <span class="footnote"> No receipt yet: the first comes at the deadline.</span>}
          </>
        ) : <span class="footnote">No thread in the repo yet.</span>}
      </dd></>,
    );
  }
  return <>{out}</>;
}

// --------------------------------------------------------------------------- Marks

function questionsOf(e: MarkEntry): string[] {
  const keys = new Set([...Object.keys(typeof e.score === 'object' && e.score ? e.score : {}), ...Object.keys(e.questionFeedback)]);
  return [...keys];
}

export function MarksView({ org, login, facts, gradebook, studentView, loaded = true, auditor = false }: { org: string; login: string; facts: SemesterFacts; gradebook: Gradebook | null; studentView: boolean; loaded?: boolean; auditor?: boolean }) {
  if (studentView) return <p class="footnote">A student’s marks show here, from their private gradebook; your own account has none in this semester.</p>;
  if (!loaded) return null;
  if (auditor && !gradebook) return <p class="footnote">As an auditor you get no marks in this semester.</p>;
  if (!gradebook) return <p class="footnote">No marks yet. They appear here when your instructors return them.</p>;
  const titles = new Map(facts.assignments.map((a) => [a.slug, a.subtitle ? `${a.title}: ${a.subtitle}` : a.title]));
  const slugs = Object.keys(gradebook.entries);
  return (
    <div class="stack">
      {gradebook.total ? <p class="lede"><b>Semester total:</b> {gradebook.total}</p> : null}
      {!slugs.length ? <p class="footnote">No marks yet. They appear here when your instructors return them.</p> : null}
      {slugs.map((slug) => {
        const e = gradebook.entries[slug];
        const qs = questionsOf(e);
        return (
          <section class="panel section" aria-label={titles.get(slug) ?? slug}>
            <div class="a-head">
              <h2>{titles.get(slug) ?? slug}</h2>
              {e.finalGrade ? <span class="mark-big">{e.finalGrade}{e.maxPoints ? <span> / {e.maxPoints}</span> : null}</span> : <span class="chip">marking</span>}
            </div>
            <dl class="kv">
              {e.submitted ? <><dt>Submitted</dt><dd>{e.submitted}</dd></> : null}
              {e.daysLate && e.daysLate !== '0' ? <><dt>Days late</dt><dd>{e.daysLate}</dd></> : null}
              {e.penalty ? <><dt>Late penalty</dt><dd>{e.penalty}</dd></> : null}
              {typeof e.score === 'string' && e.score !== e.finalGrade ? <><dt>Marks</dt><dd>{e.score}</dd></> : null}
              {e.team ? <><dt>Team</dt><dd>{e.team}</dd></> : null}
            </dl>
            {e.feedback ? <div class="fb"><h3>Feedback</h3><Md src={e.feedback} /></div> : null}
            {qs.length ? (
              <div class="fb">
                <h3>By question</h3>
                <ul class="q-list">
                  {qs.map((q) => {
                    const s = typeof e.score === 'object' && e.score ? e.score[q] : '';
                    return <li><b>{q}</b>{s ? <span class="q-score">{s}</span> : null}{e.questionFeedback[q] ? <Md src={e.questionFeedback[q]} /> : null}</li>;
                  })}
                </ul>
              </div>
            ) : null}
            {e.teamFeedback ? <div class="fb"><h3>Team feedback</h3><Md src={e.teamFeedback} /></div> : null}
          </section>
        );
      })}
      <p class="footnote"><a href={gradebookUrl(org, login)} target="_blank" rel="noopener">Your gradebook on GitHub <Ext /></a>: private to you.</p>
    </div>
  );
}

// --------------------------------------------------------------------------- Instructors

const initials = (name: string) => {
  const n = name.replace(/^(Prof\.|Dr\.)\s+/g, '').split(/[\s,]+/).filter((w) => /^[A-Z]/.test(w));
  return (n.length > 1 ? n[0][0] + n[n.length - 1][0] : (n[0] ?? name).slice(0, 2)).toUpperCase();
};

/** A card's picture: a GitHub avatar straight, else whatever `StudentData` can show (a site-hosted one as data:), else initials. */
function CardPicture({ card, org }: { card: InstructorCard; org: string }) {
  const env = useEnv();
  const direct = IMG_HOSTS.test(card.picture);
  const load = useLoad(env && card.picture && !direct ? () => studentData(env.client).picture(org, card.picture) : null, [card.picture]);
  const src = direct ? card.picture : load.kind === 'ready' ? load.value : '';
  return <span class="p-avatar" aria-hidden="true">{src ? <img src={src} alt="" /> : initials(card.name)}</span>;
}

export function InstructorsView({ facts, org = '' }: { facts: SemesterFacts; org?: string }) {
  if (!facts.instructors.length) return <p class="footnote">No instructors are listed yet.</p>;
  const card = (c: InstructorCard) => (
    <li class="person">
      <CardPicture card={c} org={org} />
      <div>
        <b>{c.webpage ? <a href={c.webpage} target="_blank" rel="noopener">{c.name}</a> : c.name}</b>
        <div class="footnote">{c.role === 'instructor' ? 'Instructor' : 'Teaching assistant'}{c.title ? `; ${c.title}` : ''}</div>
        {c.email ? <div class="footnote"><a href={`mailto:${c.email}`}>{c.email}</a></div> : null}
      </div>
    </li>
  );
  return <ul class="people-grid">{facts.instructors.map(card)}</ul>;
}
