// The student shell's screens (decision 0011 rule 2): This week, Schedule, Assignments,
// Marks, Materials, Join and Instructors of one semester, and This week across every
// semester on Home. The semester's shared facts come through `StudentData` (the public site
// today, `student-status.json` after WP-D4); the student's own repos, team, receipts and
// marks come from GitHub with their own token (`model/mine.ts`). In an instructor's Student
// view (rule 7) the same screens render with the instructor's own identity and never read
// anyone's repos or marks. An archived semester is history: a link to the org, nothing read.

import { useEnv } from '../env';
import type { GitHubClient } from '../github/client';
import { semesterName, type Semester } from '../model/discovery';
import { dayKey, fmtDay, fmtTime, fmtWhen, sortKey } from '../model/format';
import { gradebookUrl, isMarked, readMine, readReceipts, repoUrl, type Gradebook, type MarkEntry, type Mine, type Receipts } from '../model/mine';
import { DEFAULT_TZ, MY_STATE_WORD, SiteSource, instant, myState, sortedRows, type InstructorCard, type ScheduleRow, type SemesterAssignment, type SemesterFacts, type StudentData } from '../model/student';
import { weekItems, type WeekItem } from '../model/week';
import { STUDENT_SCREENS, studentHref } from '../router';
import { CheckLine, Crumbs, Loading, Md, ghUrl } from '../ui/bits';
import { useLoad } from '../ui/load';
import { Ext } from '../ui/icons';
import { JoinScreen } from './StudentJoin';
import { MaterialsView } from './StudentMaterials';

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

/** The one `StudentData` the console reads a semester's shared facts through. */
export function studentData(client: GitHubClient): StudentData {
  let s = sources.get(client);
  if (!s) {
    s = new SiteSource(client);
    sources.set(client, s);
  }
  return s;
}

export function StudentViewBanner({ semester }: { semester: Semester }) {
  return (
    <div class="ro-banner" role="status">
      <b>Student view.</b>
      <span>What a student of {semesterName(semester)} sees, shown with your own account: no student’s repos or marks.</span>
      <a href={`?cohort=${semester.org}#cohort`}>Back to the instructor screens</a>
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
      {semester.archived ? <ArchivedSemester semester={semester} /> : <SemesterBody semester={semester} screen={screen} studentView={studentView} entry={entry} now={now} />}
    </>
  );
}

function SemesterBody({ semester, screen, studentView, entry, now }: Required<Omit<StudentProps, 'entry'>> & { entry?: string }) {
  const env = useEnv();
  const org = semester.org;
  const facts = useLoad(env ? () => studentData(env.client).facts(org) : null, [org]);
  const f = facts.kind === 'ready' ? facts.value : null;
  const mine = useLoad(env && f && !studentView ? () => readMine(env.client, org, env.user.login, f.assignments) : null, [org, f]);
  if (facts.kind === 'loading') return <Loading what="Reading the semester" />;
  if (facts.kind === 'failed') return <CheckLine cls="bad">The semester’s schedule could not be read: {facts.error}</CheckLine>;
  if (!f) return <NoFacts org={org} />;
  const m: Mine | null = mine.kind === 'ready' ? mine.value : null;
  const mineNote = studentView ? null : mine.kind === 'loading' ? <Loading what="Reading your repos and marks" /> : mine.kind === 'failed' ? <CheckLine cls="warn">Your repos and marks could not be read: {mine.error}</CheckLine> : null;
  const tz = f.timezone || DEFAULT_TZ;
  const login = env?.user.login ?? '';
  const body =
    screen === 'schedule' ? <ScheduleView facts={f} mine={m} now={now} org={org} />
    : screen === 'assignments' ? <AssignmentsLoader org={org} facts={f} mine={m} now={now} studentView={studentView} />
    : screen === 'marks' ? <MarksView org={org} login={login} facts={f} gradebook={m?.gradebook ?? null} studentView={studentView} loaded={mine.kind !== 'loading'} />
    : screen === 'materials' ? <MaterialsView org={org} repos={f.materialsRepos} entry={entry} />
    : screen === 'join' ? <JoinScreen org={org} facts={f} mine={m} studentView={studentView} />
    : screen === 'instructors' ? <InstructorsView facts={f} />
    : <WeekList items={weekItems(f, m, now)} tz={tz} org={org} />;
  return (
    <div class="stack">
      {f.archive ? <ArchiveNotice when={f.archive} tz={tz} now={now} /> : null}
      {screen !== 'instructors' && screen !== 'materials' ? mineNote : null}
      {body}
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

/** History: an archived semester is listed with a link to its org, and nothing of it is read. */
export function ArchivedSemester({ semester }: { semester: Semester }) {
  return (
    <section class="panel section">
      <p>{semesterName(semester)} is archived: every repository in it is read-only, and you keep read access to what was yours.</p>
      <p><a class="btn outline" href={ghUrl(semester.org)} target="_blank" rel="noopener">Open the semester on GitHub <Ext /></a></p>
    </section>
  );
}

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
            <span class="d">{i.when ? fmtDay(i.when, tz) : 'now'}{i.when && fmtTime(i.when, tz) && !/T00:00(:00)?$/.test(i.when) ? <span>{fmtTime(i.when, tz)}</span> : null}</span>
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
            return weekItems(f, m, now).map((i) => ({ ...i, org: s.org, semester: semesterName(s) }));
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
        <WeekList items={load.value} tz={DEFAULT_TZ} org={live[0].org} semesterOf={(i) => (i as WeekLine).semester ?? ''} />
      )}
    </section>
  );
}

// --------------------------------------------------------------------------- Schedule

export const ROW_CLASS: Record<string, string> = { lecture: 'lec', lab: 'lab', assignment: 'asg', due: 'asg', exam: 'exam', special_event: 'evt', term_date: 'term' };
export const ROW_WORD: Record<string, string> = { lecture: 'lecture', lab: 'lab', assignment: 'hand out', due: 'due', exam: 'exam', special_event: 'event', term_date: 'term' };

/** Monday of the week `iso` falls in, as yyyy-mm-dd. */
function mondayOf(iso: string, tz: string): string {
  const day = dayKey(iso, tz);
  const [y, m, d] = day.split('-').map(Number);
  const dow = (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
  return new Date(Date.UTC(y, m - 1, d - dow)).toISOString().slice(0, 10);
}

export function ScheduleView({ facts, mine, now, org }: { facts: SemesterFacts; mine: Mine | null; now: number; org: string }) {
  const tz = facts.timezone || DEFAULT_TZ;
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
              const st = a ? myState(a, isMarked(mine?.gradebook ?? null, a.slug), now, tz) : null;
              const yours = a && mine ? mine.units[a.slug] : undefined;
              out.push(
                <li class={`trow ${ROW_CLASS[r.kind] ?? 'evt'}${yours?.repo ? ' mine' : ''}`}>
                  <span class="k">{ROW_WORD[r.kind] ?? r.kind}</span>
                  <span class="d">{fmtDay(r.when, tz, year)}{!r.allDay && fmtTime(r.when, tz) ? <span>{fmtTime(r.when, tz)}</span> : null}</span>
                  <span class="ttl">
                    <b>{r.title}</b>{r.subtitle ? `: ${r.subtitle}` : ''}
                    {r.details ? <Md class="t-details" src={r.details} /> : null}
                  </span>
                  <span class="st">
                    {st ? <a class="st-chip" href={studentHref(org, 'assignments')}>{MY_STATE_WORD[st]}</a> : null}
                    {yours?.repo ? <span class="st-chip">yours</span> : null}
                    {r.links.length ? <a class="st-chip" href={studentHref(org, 'materials')}>{r.links.length} file{r.links.length > 1 ? 's' : ''}</a> : !r.released ? <span class="st-chip">not released yet</span> : null}
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

function AssignmentsLoader(p: { org: string; facts: SemesterFacts; mine: Mine | null; now: number; studentView: boolean }) {
  const env = useEnv();
  const repos = p.mine ? p.facts.assignments.filter((a) => a.privateRepo).map((a) => p.mine!.units[a.slug]?.repo).filter((r): r is string => !!r) : [];
  const receipts = useLoad<Record<string, Receipts | null>>(
    env && repos.length ? async () => Object.fromEntries(await Promise.all(repos.map(async (r) => [r, await readReceipts(env.client, p.org, r).catch(() => null)] as const))) : null,
    [p.org, repos.join(',')],
  );
  return <AssignmentsView {...p} receipts={receipts.kind === 'ready' ? receipts.value : undefined} />;
}

const SUBMIT_WORD: Record<string, string> = {
  assignment_repo: 'Push to your repo', shared_dropbox_repo: 'Push to your folder in the shared repo', external: 'Handed in outside GitHub',
};

export function AssignmentsView({ org, facts, mine, now, studentView, receipts }: {
  org: string; facts: SemesterFacts; mine: Mine | null; now: number; studentView: boolean;
  /** Receipts per repo; undefined while they are read. */
  receipts?: Record<string, Receipts | null>;
}) {
  const tz = facts.timezone || DEFAULT_TZ;
  const year = new Date(now).getFullYear();
  if (!facts.assignments.length) return <p class="footnote">No assignments are planned yet.</p>;
  const list = [...facts.assignments].sort((a, b) => (a.due ? instant(a.due, tz) : Infinity) - (b.due ? instant(b.due, tz) : Infinity));
  return (
    <div class="stack">
      {list.map((a) => {
        const st = myState(a, isMarked(mine?.gradebook ?? null, a.slug), now, tz);
        const u = mine?.units[a.slug];
        const rc = u?.repo ? receipts?.[u.repo] : undefined;
        return (
          <section class="panel section a-card" aria-label={a.title}>
            <div class="a-head">
              <h2>{a.title}{a.subtitle ? <span>{a.subtitle}</span> : null}</h2>
              <span class={`chip ${st === 'returned' ? 'ok' : st === 'late_window' ? 'amber' : st === 'open' ? 'asg' : ''}`}>{MY_STATE_WORD[st]}</span>
            </div>
            <dl class="kv">
              {a.handout ? <><dt>Handed out</dt><dd>{fmtWhen(a.handout, tz, year)}</dd></> : null}
              {a.due ? <><dt>Due</dt><dd>{fmtWhen(a.due, tz, year)}</dd></> : null}
              {a.lateCutoff && a.lateCutoff !== a.due ? <><dt>Late cutoff</dt><dd>{fmtWhen(a.lateCutoff, tz, year)}</dd></> : null}
              {a.lateRule ? <><dt>Late work</dt><dd>{a.lateRule}</dd></> : null}
              {a.solutionShown ? <><dt>Solution shown</dt><dd>{fmtWhen(a.solutionShown, tz, year)}</dd></> : null}
              {a.submitVia ? <><dt>How to hand in</dt><dd>{SUBMIT_WORD[a.submitVia]}{a.submitVia === 'external' && a.submitUrl ? <>: <a href={a.submitUrl} target="_blank" rel="noopener">{hostOf(a.submitUrl)} <Ext /></a></> : null}</dd></> : null}
              {studentView ? <><dt>Yours</dt><dd class="footnote">A student’s repo, team and receipts show here.</dd></> : <MyUnitRows org={org} a={a} mine={mine} receipts={rc} loading={u?.repo ? receipts === undefined : false} tz={tz} year={year} />}
            </dl>
            {a.cutoffSentence && a.submitVia !== 'external' ? <p class="footnote">{a.cutoffSentence}</p> : null}
          </section>
        );
      })}
    </div>
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

export function MarksView({ org, login, facts, gradebook, studentView, loaded = true }: { org: string; login: string; facts: SemesterFacts; gradebook: Gradebook | null; studentView: boolean; loaded?: boolean }) {
  if (studentView) return <p class="footnote">A student’s marks show here, from their private gradebook; your own account has none in this semester.</p>;
  if (!loaded) return null;
  if (!gradebook) return <p class="footnote">No marks yet. They appear here when your instructors return them.</p>;
  const titles = new Map(facts.assignments.map((a) => [a.slug, a.subtitle ? `${a.title}: ${a.subtitle}` : a.title]));
  const slugs = Object.keys(gradebook.entries);
  return (
    <div class="stack">
      {gradebook.total ? <p class="lede"><b>Term total:</b> {gradebook.total}</p> : null}
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

export function InstructorsView({ facts }: { facts: SemesterFacts }) {
  if (!facts.instructors.length) return <p class="footnote">No instructors are listed yet.</p>;
  const card = (c: InstructorCard) => (
    <li class="person">
      <span class="p-avatar" aria-hidden="true">{c.picture ? <img src={c.picture} alt="" /> : initials(c.name)}</span>
      <div>
        <b>{c.webpage ? <a href={c.webpage} target="_blank" rel="noopener">{c.name}</a> : c.name}</b>
        <div class="footnote">{c.role === 'instructor' ? 'Instructor' : 'Teaching assistant'}{c.title ? `; ${c.title}` : ''}</div>
      </div>
    </li>
  );
  return <ul class="people-grid">{facts.instructors.map(card)}</ul>;
}
