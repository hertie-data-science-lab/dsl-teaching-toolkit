// S2 Course overview (a status board, decision 0025) and S17 Template settings (read).

import { Fragment, type ComponentChildren } from 'preact';
import { useMemo, useState } from 'preact/hooks';
import gradingSchema from '../../schemas/grading_config.schema.json';
import { useEnv } from '../env';
import { invalidText, useSave } from '../edit/save';
import { YamlText, deepEqual } from '../edit/yamlText';
import { Invalid, SchemaForm, effective, fieldErrors } from '../forms/Form';
import { KIND_LABEL, ago, fmtDay, fmtShort, opLabel, templateName } from '../model/format';
import {
  SYLLABUS_HINT, SYLLABUS_LABEL, materialsReadiness, problemCount, problemFromDay, standing, stepItems, suggestionsCount, templateReadiness, tier, todoAside, verdictOf,
  type Tiered, type Verdict as VerdictT,
} from '../model/readiness';
import { checkNow, derive, publishWebsite } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { FormatPicker } from '../forms/FormatPicker';
import { courseBlock, institutionLayer, lateWord, resolve, valueWord, type Layers } from '../model/cascade';
import { DEFAULT_FORMATS, DEFAULT_TIMEZONE, POLICY } from '../model/policy';
import { formatsList, fromConfig, questionFileError, questionRows, questionsValue, settingsTiers, toConfig, type QuestionRow } from '../tiers/grading';
import { pick, type Values } from '../tiers/types';
import { SaveBar } from '../ui/edit';
import type { CourseStatus, MaterialsCheck, Operation, Outcome, Problem, SemesterStatus, Status, Todo } from '../model/types';
import { nextEvent, nextEventWords, recentActivity, whoWord, type Activity } from '../model/status';
import type { CohortRef } from '../model/discovery';
import { outcomePath } from '../ops/adapter';
import { validator } from '../model/validate';
import { CheckLine, Lives, Loading, OpMark, ProblemCards, Probs, Soon, fixHref, ghUrl, runUrl } from '../ui/bits';
import { ItemRow, RepoChip, RepoWhy, TemplateSub, Verdict, readinessTabs, type Link, type SetupItem } from '../ui/SetupPanel';
import { DASH_PANEL, DashboardTabs, showTab, type DashTab } from '../ui/DashboardTabs';
import { Hint } from '../ui/Hint';
import { Check, Ext } from '../ui/icons';
import { OpenButton } from '../ui/OpenButton';
import { formatError } from '../wizards/model';
import { detailsOf, newestScope, websiteUrl } from './CourseEdit';
import type { CourseProps } from './types';
import { CONFIG_REPO, COURSE_REPO, STATUS_PATH } from '../model/names';
import { useSetAside } from './SetAside';
import { REFRESH_HINT, courseScope, todayOf, tzOf, yearOf } from './common';
import { weekWords } from '../model/week';

/** The status that carries the course block: the course's own, else a semester's. */
function courseStatus(p: Pick<CourseProps, 'loaded' | 'cohortStates'>): Status | null {
  if (p.loaded.kind === 'ready' && p.loaded.status.course) return p.loaded.status;
  for (const l of Object.values(p.cohortStates)) if (l.kind === 'ready' && l.status.course) return l.status;
  return null;
}

/** The course block, for a caller that needs no problems; null until a status carries it. */
export const courseOf = (p: Pick<CourseProps, 'loaded' | 'cohortStates'>): CourseStatus | null => courseStatus(p)?.course ?? null;

/** The course block and its course-scope problems with when each bites (`tiered`), and those now or soon (`problems`). */
export interface CourseView {
  course: CourseStatus | null;
  tiered: Tiered[];
  problems: Problem[];
}

export function courseView(p: Pick<CourseProps, 'loaded' | 'cohortStates'>, now: number): CourseView {
  const st = courseStatus(p);
  const tiered = tier(st ?? undefined, now, 'course');
  return { course: st?.course ?? null, tiered, problems: standing(tiered) };
}

/** `courseView` once per render. */
export const useCourseView = (p: Pick<CourseProps, 'loaded' | 'cohortStates' | 'now'>): CourseView => useMemo(() => courseView(p, p.now), [p.loaded, p.cohortStates, p.now]);

/** A template's problems in a course view. */
const templateProblems = (v: CourseView, repo: string) => v.tiered.filter((x) => x.p.fix?.entry === repo);

/** A semester's problem count for its row: those now or soon. */
function problemsOf(p: CourseProps, cohortOrg: string): number | null {
  const l = p.cohortStates[cohortOrg];
  return l && l.kind === 'ready' ? problemCount(l.status, p.now) : null;
}

/** The course's live semesters whose status is read, newest first: what the overview rolls up. */
export function liveSemesters(p: Pick<CourseProps, 'course' | 'cohortStates'>): { ref: CohortRef; status: Status }[] {
  return p.course.cohorts.flatMap((ref) => {
    const l = p.cohortStates[ref.org];
    return l?.kind === 'ready' && l.status.semester?.live !== false ? [{ ref, status: l.status }] : [];
  });
}

/** The course's setup steps (C1-C6). C1-C3 are needed; C4-C6 suggested (decision 0034). */
export const SETUP_STEPS: { id: string; name: string }[] = [
  { id: 'C1', name: 'Course org read' },
  { id: 'C2', name: 'Course set up on GitHub' },
  { id: 'C3', name: 'Course details filled in' },
  { id: 'C4', name: 'Handout materials repo' },
  { id: 'C5', name: 'Assignment template' },
  { id: 'C6', name: 'Public website' },
];

/** Where an open setup step is done: one link. */
export function stepLink(id: string, c: CourseStatus): Link {
  const org = c.org;
  switch (id) {
    case 'C1': return { href: ghUrl(org), label: 'Open on GitHub', ext: true };
    case 'C2': return { href: ghUrl(org, COURSE_REPO), label: 'Open .github', ext: true };
    case 'C3': return { href: '#details', label: 'Edit course details' };
    case 'C4': return c.materials?.length ? { href: '#materials', label: 'Open handout materials' } : { href: `?course=${org}#new-materials`, label: 'New handout materials' };
    case 'C5': return c.templates?.length ? { href: '#templates', label: 'Open templates' } : { href: `?course=${org}#new-assignment-1`, label: 'New assignment' };
    default: return { href: '#website', label: 'Set up the public website' };
  }
}

/** What each setup step is, for its `?`. */
const STEP_HINT: Record<string, string> = {
  C1: 'The console and the nightly check can read the course’s GitHub org.',
  C2: 'The course’s .github repo holds its details file and the workflows behind every button.',
  C3: 'A name, code, description and at least one course admin, in Course details.',
  C4: 'A repo of lectures, labs and readings that semesters release from. Done once any one is ready.',
  C5: 'A repo that semesters hand assignments out from. Done once any one is ready.',
  C6: 'Optional: an open version of your materials for anyone on the internet.',
};

/** Where a to-do is done: the repo's settings page. */
export function todoHref(t: Todo): string {
  if (t.kind === 'materials' || t.kind === 'template') return `#${t.kind}-${t.repo}`;
  return `#${t.screen ?? 'details'}${t.entry ? `-${t.entry}` : ''}`;
}

/** A template to-do's line and `?`, by its check. */
const TEMPLATE_TODO: Record<string, { label: string; hint: string }> = {
  brief: { label: 'Brief written', hint: 'The README.md students get as their instructions. Still the template text until you write it.' },
  starter: { label: 'Student version derived', hint: 'Derive builds the starter students get by blanking the marked answers. Run it after every change to the solution.' },
};

/** A to-do's line and `?`: a materials one reads as the same check on its settings checklist. */
export function todoLine(t: Todo, c: CourseStatus): { label: string; hint?: string } {
  const check = t.check ?? '';
  if (t.kind === 'template') return TEMPLATE_TODO[check] ?? { label: t.text };
  if (check === 'syllabus') return { label: SYLLABUS_LABEL, hint: SYLLABUS_HINT };
  const row = c.materials?.find((m) => m.repo === t.repo)?.checks?.find((k) => k.id === check);
  return { label: row?.label ?? t.text, hint: CHECK_HINT[check] };
}

/**
 * "Hands out Wed 4 Nov; a problem from 28 Oct.": a template to-do's hand-out, from its problem
 * (`BRIEF` / `STARTER`) when a dated hand-out cites the template; none while it is a problem now.
 */
export function handoutWords(tiered: Tiered[], repo: string, check: string, now: number): string | undefined {
  const x = tiered.find((t) => t.p.fix?.entry === repo && t.p.kind === check.toUpperCase() && t.b !== 'now' && t.p.when);
  if (!x) return undefined;
  const tz = DEFAULT_TIMEZONE, when = x.p.when!;
  const from = problemFromDay(when, undefined, tz);
  return `Hands out ${fmtDay(when, tz, yearOf(now, tz))}${from > todayOf(now, tz) ? `; a problem from ${fmtShort(from)}` : ''}.`;
}

/** Where an open course step is done, and what it is (its `?`). */
const STEP_OPTS = (c: CourseStatus) => ({ hint: STEP_HINT, link: (id: string) => stepLink(id, c), fixHref });

/**
 * The course's Setup & To do items: each step, then each to-do. A step with a problem now or
 * soon is a red row whose Fix opens the problem's screen; `list` is the set-aside ids as read.
 */
export function courseItems(c: CourseStatus, tiered: Tiered[], list: string[] | null, now = 0): SetupItem[] {
  const steps = stepItems(c, SETUP_STEPS, tiered, list, STEP_OPTS(c));
  const todos: SetupItem[] = (c.todo ?? []).map((t) => {
    const line = todoLine(t, c);
    return {
      id: t.id, label: line.label, kind: 'todo', need: t.need ?? 'needed', state: 'open', repo: t.repo, hint: line.hint,
      hintLabel: line.label === SYLLABUS_LABEL ? 'About the syllabus' : 'About this to-do',
      why: line.label === t.text ? undefined : t.text,
      when: t.kind === 'template' && t.check ? handoutWords(tiered, t.repo, t.check, now) : undefined,
      link: { href: todoHref(t), label: 'Open settings', aria: `Open ${t.repo} settings` },
      aside: todoAside(t, list),
    };
  });
  return [...steps, ...todos];
}

/** What each materials check is, for its `?`, in the checklist's order. */
const CHECK_HINT: Record<string, string> = {
  kind_folder: 'A repo with nothing of a content kind has nothing to release.',
  syllabus: 'The file the student site pins as the syllabus. Still the template text until you write it.',
  withheld: 'The whole repo is released as it stands unless a line here withholds it. Saving the list once, even empty, marks it reviewed.',
};

/** A repo's checklist split by need (decision 0034): "Needed", then "Suggested". */
export function RepoChecklist({ items }: { items: SetupItem[] }) {
  const needed = items.filter((i) => i.need === 'needed'), suggested = items.filter((i) => i.need === 'suggested');
  return (
    <>
      {needed.length ? <><p class="sub-h">Needed</p><ul class="setup">{needed.map((it) => <ItemRow it={it} />)}</ul></> : null}
      {suggested.length ? <><p class="sub-h">Suggested</p><ul class="setup">{suggested.map((it) => <ItemRow it={it} />)}</ul></> : null}
    </>
  );
}

/** A materials repo's whole checklist, ticks included: the settings screen's head. */
export function MaterialsChecklist({ checks }: { checks: MaterialsCheck[] }) {
  const items: SetupItem[] = checks.map((c) => ({
    id: c.id, label: c.label, kind: 'step', need: c.need ?? 'needed', state: c.done ? 'done' : 'open', why: c.done ? undefined : c.why ?? undefined, hint: c.id === 'syllabus' ? SYLLABUS_HINT : CHECK_HINT[c.id],
    hintLabel: c.id === 'syllabus' ? 'About the syllabus' : 'About this check',
  }));
  const kinds = checks.find((c) => c.detail)?.detail;
  return (
    <>
      <RepoChecklist items={items} />
      {kinds ? (
        <details class="fold s-kinds">
          <summary>Kinds found</summary>
          <ul class="fold-body">
            {/* Every content kind: a tick and its folders when present, nothing when not. */}
            {kinds.map((d) => (
              <li class={d.folders.length ? 'found' : undefined}>
                <span class="k-mark" aria-hidden="true">{d.folders.length ? <Check /> : null}</span>
                <b>{KIND_LABEL[d.kind] ?? d.kind}</b>{d.folders.length ? `: ${d.folders.map((f) => `${f}/`).join(', ')}` : <span class="sr">: none</span>}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </>
  );
}

/** A template's needed checklist (the brief, the student version), from the course's open to-dos. */
export function templateItems(repo: string, todo: Todo[], starter: 'derived' | 'handwritten' | undefined, tiered: Tiered[], now: number): SetupItem[] {
  const row = (k: 'brief' | 'starter', label: string): SetupItem => {
    const t = todo.find((x) => x.kind === 'template' && x.repo === repo && x.check === k);
    const when = t ? handoutWords(tiered, repo, k, now) : undefined;
    return { id: k, label, kind: 'step', need: 'needed', state: t ? 'open' : 'done', why: t?.text, when, hint: TEMPLATE_TODO[k].hint, hintLabel: 'About this check' };
  };
  return [row('brief', TEMPLATE_TODO.brief.label), row('starter', starter === 'handwritten' ? 'Starter files on main' : TEMPLATE_TODO.starter.label)];
}

/** Each grading_config.yml text read so far -> its title, so a re-render parses no YAML. */
const titles = new Map<string, string>();

/** A template's title from its grading_config.yml, as loaded; '' until then. */
export function templateTitle(files: CourseProps['files'], org: string, repo: string): string {
  const f = files.file(org, repo, 'grading_config.yml', 'solution');
  if (f.kind !== 'ready') return '';
  let title = titles.get(f.text);
  if (title === undefined) {
    const y = new YamlText(f.text);
    const t = y.errors.length ? undefined : (y.toJS() as Record<string, unknown> | null)?.title;
    titles.set(f.text, (title = typeof t === 'string' ? t : ''));
  }
  return title;
}

/** A semester's state: live, ended but not archived, or archived. */
export function semesterChip(s: Pick<SemesterStatus, 'live' | 'ended'> | undefined): ComponentChildren {
  if (s?.live === false) return <span class="chip">Archived</span>;
  if (s?.ended) return <span class="chip amber">Ended, not archived</span>;
  return <span class="chip ok">Live</span>;
}

/** The `?` on the course dashboard's title (decision 0034: both pages are called Dashboard). */
export function CourseHint() {
  return <Hint doc="02-add-materials-to-course.md">Materials are staged here privately until a release copies them in whole or in part to a semester. Selected materials can also be published on the course’s optional public website.</Hint>;
}

/** The overview's head, on the right of the course banner: New semester, the one primary action, always the black button. */
export function CourseHeaderActions({ course }: { course: CourseProps['course'] }) {
  return (
    <div class="actions">
      <a class="btn" href={`?course=${course.org}#new-semester-1`}>New semester</a>
    </div>
  );
}

/** The newer of two ISO times; either may be missing. */
const newer = (a: string | null | undefined, b: string | null | undefined) => (!a ? b : !b ? a : Date.parse(a) >= Date.parse(b) ? a : b);

/**
 * Under the overview's head, quiet: how old the course's status is, Refresh, and the course on
 * GitHub. The age is the newer of the status file's last change and this session's last
 * Refresh that finished, so a Refresh that changed nothing still reads "just now". A read-only
 * viewer gets the GitHub link alone.
 */
export function CourseSubActions({ course, loaded, files, now, computed }: Pick<CourseProps, 'course' | 'loaded' | 'files' | 'now'> & { computed: boolean }) {
  const env = useEnv();
  const scope = newestScope({ course });
  const refreshed = (env?.ops.runs.value ?? []).find((r) => r.op === 'semester.check' && r.course === course.org && r.conclusion !== 'failed')?.finished;
  const changed = newer(loaded.kind === 'ready' ? files.lastChange(course.org, COURSE_REPO, STATUS_PATH) : null, refreshed);
  const age = !computed ? 'Not computed yet' : changed ? `Updated ${ago(changed, now)}` : null;
  return (
    <div class="actions sub-actions">
      {course.write ? (
        <>
          {age ? <span class="footnote">{age}</span> : null}
          {age ? <span aria-hidden="true">·</span> : null}
          {scope ? <OpButtons def={{ ...checkNow(scope), where: course.name }} verbCls="textlink" /> : <Soon label="Refresh" cls="textlink" title="Refresh runs on a semester; this course has none yet." />}
          <Hint label="About Refresh">{REFRESH_HINT}</Hint>
          <span class="sep" aria-hidden="true">|</span>
        </>
      ) : null}
      <a class="textlink" href={ghUrl(course.org)} target="_blank" rel="noopener">Course on GitHub <Ext /></a>
    </div>
  );
}

/** The public website's indicator: what the last publish did, and whether one is running. */
export type SiteLiveState = 'live' | 'publishing' | 'failed' | 'off';

export function siteLiveState(published: boolean, last: Operation | undefined, running: boolean): SiteLiveState {
  if (running) return 'publishing';
  if (last?.conclusion === 'failed') return 'failed';
  return published ? 'live' : 'off';
}

const SITE_DOT: Record<SiteLiveState, string> = { live: 'ok', publishing: 'amber', failed: 'bad', off: 'idle' };

/**
 * The website block's indicator and address: green "Live · updated 3 h ago" (the age of the
 * last publish that succeeded; "Live" alone when this session and the statuses hold none),
 * amber while a publish runs, red linking to the run when the last one failed, else grey.
 */
export function SiteLive({ org, published, last, running, now }: { org: string; published: boolean; last?: Operation; running: boolean; now: number }) {
  const st = siteLiveState(published, last, running);
  const says = st === 'live' ? (last?.conclusion === 'done' ? `Live · updated ${ago(last.finished, now)}` : 'Live')
    : st === 'publishing' ? 'Publishing…'
    : st === 'failed' ? <a class="textlink" href={runUrl(`${org}/${COURSE_REPO}`, last!.run_id)} target="_blank" rel="noopener">Last publish failed <Ext /></a>
    : 'Not published';
  return (
    <>
      <p class="site-live"><span class={`dot ${SITE_DOT[st]}`} aria-hidden="true" /><span>{says}</span></p>
      {published ? <p class="site-address"><a class="textlink" href={websiteUrl(org)} target="_blank" rel="noopener">{org}.github.io <Ext /></a></p> : null}
    </>
  );
}

/** Who started an operation, from its outcome file in `owner/repo` when that is this run's; undefined while not known. */
function actorOf(files: CourseProps['files'], owner: string, repo: string, o: Operation): string | undefined {
  const f = files.file(owner, repo, outcomePath(o.op));
  if (f.kind !== 'ready') return undefined;
  try {
    const oc = JSON.parse(f.text) as Outcome;
    return oc.run_id === o.run_id ? oc.actor : undefined;
  } catch {
    return undefined;
  }
}

/** Every operation the overview knows of: this session's runs, the course status's, each live semester's. */
function courseOperations(p: CourseProps, live: { ref: CohortRef; status: Status }[], runs: (Operation & { cohort?: string; course: string })[], login: string): Activity[][] {
  const where = (org?: string) => (org ? p.course.cohorts.find((k) => k.org === org)?.termLabel ?? org : 'course');
  const session = runs.filter((r) => r.course === p.course.org).map((r) => ({ ...r, org: r.cohort, where: where(r.cohort), actor: login }));
  const course = (p.loaded.kind === 'ready' ? p.loaded.status.operations ?? [] : []).map((o) => ({ ...o, where: 'course' }));
  return [session, course, ...live.map(({ ref, status }) => (status.operations ?? []).map((o) => ({ ...o, org: ref.org, where: ref.termLabel })))];
}

/** The last five operations across the course and its live semesters, each linking to its run. */
export function RecentActivity({ p, lists, newest }: { p: CourseProps; lists: Activity[][]; newest?: string }) {
  const env = useEnv();
  const login = env?.user.login ?? '';
  const rows = recentActivity(lists).map((a) => (a.actor !== undefined ? a : { ...a, actor: a.org ? actorOf(p.files, a.org, CONFIG_REPO, a) : actorOf(p.files, p.course.org, COURSE_REPO, a) }));
  return (
    <section class="panel section">
      <div class="section-head"><h2>Recent activity</h2>{newest ? <a class="textlink" href={`?cohort=${newest}#operations`}>All operations</a> : null}</div>
      {rows.length ? (
        <ul class="ops activity">
          {rows.map((a) => (
            <li key={a.run_id}>
              <OpMark conclusion={a.conclusion} />
              <div class="o-head">
                <a href={runUrl(`${p.course.org}/${COURSE_REPO}`, a.run_id)} target="_blank" rel="noopener"><b>{opLabel(a.op)}</b></a>
                <span>{ago(a.finished, p.now)}</span>
              </div>
              <p class="o-who">{[a.where, whoWord(a.actor, login)].filter(Boolean).join(' · ')}</p>
            </li>
          ))}
        </ul>
      ) : <p class="footnote">Nothing has run yet.</p>}
    </section>
  );
}

/** The course layer over the institution's: what an assignment gets when its semester says nothing. */
export function courseLayers(p: Pick<CourseProps, 'course' | 'files'>): Layers {
  return { assignment: {}, semester: {}, course: courseBlock(p.files, p.course.org, p.course.meta), institution: institutionLayer() };
}

/** Where a course-level default comes from, in plain words. */
const whose = (s: string) => (s === 'course' ? 'this course' : 'institution');

/** A panel of the overview and its estimated height, in lines of text. */
export interface Block {
  key: string;
  h: number;
}

/**
 * The dashboard's two columns under its tabbed panel (decision 0031 rule 4): the first block
 * heads the left, the second the right, and every other block goes where the two columns come
 * out closest in height, keeping the given order within each. Deterministic, so the page does
 * not reflow between renders; on a tie the earlier block stays left.
 */
export function splitColumns([first, second, ...rest]: Block[]): [string[], string[]] {
  let best: [string[], string[]] = [[first.key], [second.key]];
  let gap = Infinity;
  for (let mask = 0; mask < 1 << rest.length; mask++) {
    const cols: [Block[], Block[]] = [[first], [second]];
    rest.forEach((b, i) => cols[(mask >> i) & 1].push(b));
    const [l, r] = cols.map((c) => c.reduce((n, b) => n + b.h, 0));
    if (Math.abs(l - r) < gap) [gap, best] = [Math.abs(l - r), [cols[0].map((b) => b.key), cols[1].map((b) => b.key)]];
  }
  return best;
}

/** Until every status has loaded, the dashboard keeps this layout, so panels do not move as each arrives. */
export const SETTLING_COLUMNS: [string[], string[]] = [['handouts', 'details'], ['semesters', 'activity']];

/** Whether the course's and every semester's status has finished loading (present, absent or failed). */
export function statusesSettled(p: Pick<CourseProps, 'course' | 'loaded' | 'cohortStates'>): boolean {
  return p.loaded.kind !== 'loading' && p.course.cohorts.every((c) => p.cohortStates[c.org] && p.cohortStates[c.org].kind !== 'loading');
}

/** Each panel's height, estimated from what it lists: a heading and one or two lines a row. */
export function overviewHeights(o: { course: CourseStatus | null; semesters: number; description: string; activity: number }): Block[] {
  const HEAD = 3; // the heading and the panel's padding
  const c = o.course;
  const materials = (c?.materials ?? []).reduce((n, m) => n + 1 + (materialsReadiness(m).state === 'not_ready' ? 1 : 0), 0);
  return [
    { key: 'handouts', h: 2 * HEAD + Math.max(1, materials) + Math.max(1, 2 * (c?.templates?.length ?? 0)) },
    { key: 'semesters', h: HEAD + Math.max(1, 3 * o.semesters) },
    { key: 'details', h: HEAD + 9 + Math.ceil(o.description.length / 50) + 5 },
    { key: 'activity', h: HEAD + Math.max(1, 2 * o.activity) },
  ];
}

/** The course's Setup & To do rows and its verdict, from the set-aside list as the console last read it (`aside.list`). */
export function courseReadiness(c: CourseStatus, tiered: Tiered[], list: string[] | null, now: number): { items: SetupItem[]; verdict: VerdictT | null } {
  const items = courseItems(c, tiered, list, now);
  return { items, verdict: verdictOf(c.verdict, 'course', suggestionsCount(items)) };
}

/**
 * The course Dashboard's tabbed panel (decisions 0032 and 0034): Problems (course-scope only),
 * Suggestions, Set aside and Setup. The circle before a suggestion asks whether to set it aside;
 * the answer is saved into dsl-course.yml through the same save path as Course details, and
 * Bring back removes the id at once. A read-only viewer gets neither.
 */
export function CourseTabs({ items, problems: list, aside, tab, onTab }: { items: SetupItem[] | null; problems: Problem[]; aside: ReturnType<typeof useSetAside>; tab?: string; onTab?: (key: string) => void }) {
  if (!items) return <section class="panel section dash-tabs" id={DASH_PANEL}><p class="footnote">Status not computed yet.</p></section>;
  const n = list.length;
  const problems: DashTab = {
    key: 'problems', label: 'Problems', count: n || 'done', bad: true,
    body: n ? <ProblemCards list={list} /> : (
      <>
        <div class="no-problems"><Check /><span>No problems on the course.</span></div>
        <p class="footnote">{COURSE_PROBLEMS_NOTE}</p>
      </>
    ),
  };
  return (
    <div ref={aside.ref}>
      <DashboardTabs selected={tab} onSelect={onTab} tabs={[problems, ...readinessTabs({ items, scope: 'course', busy: aside.busy, onCircle: aside.onCircle, onBack: aside.onBack(items) })]} />
      {aside.after}
    </div>
  );
}

/** What the course's Problems tab holds, said once under its empty state. */
export const COURSE_PROBLEMS_NOTE = 'A problem here is something on the course itself that automation cannot act on: a settings file that does not parse, an org setting. What each semester needs by when is on that semester’s dashboard.';

export function CourseScreen(p: CourseProps) {
  const { course } = p;
  const env = useEnv();
  const v = useCourseView(p);
  const computed = v.course !== null;
  const live = liveSemesters(p);
  const [tab, setTab] = useState('problems');
  const aside = useSetAside({ org: p.course.org, files: p.files, migrated: p.migrated, write: p.course.write });
  const ready = v.course ? courseReadiness(v.course, v.tiered, aside.list, p.now) : null;
  const ops = courseOperations(p, live, env?.ops.runs.value ?? [], env?.user.login ?? '');
  // A previewed run published nothing: the age is the last real publish's.
  const lastPublish = recentActivity(ops.map((l) => l.filter((o) => o.op === 'course.publish_website' && o.conclusion !== 'previewed')), 1)[0];
  const cur = env?.ops.current.value;
  const publishing = cur?.phase === 'running' && cur.def.op === 'course.publish_website' && cur.def.courseOrg === course.org;
  const layers = courseLayers(p);
  const lateDays = resolve('late_window_days', layers), latePen = resolve('late_penalty_per_day', layers);
  const team = resolve('max_team_size', layers);
  const pub = v.course?.stages?.C6 === 'done';
  const about = detailsOf(course.meta ?? {}).about;
  const description = typeof about.course_description === 'string' ? about.course_description.trim() : '';
  const fallback = (value: unknown, institution: string) => (value ? String(value) : <span class="footnote">{institution}, from the institution</span>);
  // Under the tabbed panel, two columns (0031 rule 4): Handout materials and Assignment templates
  // (one block) head the left, Semesters the right, and the rest go where the columns come out
  // closest in height. A semester's problems are on its own Dashboard (decision 0034).
  const panels: Record<string, ComponentChildren> = {
    semesters: (
      <section class="panel section">
        <h2>Semesters</h2>
        {course.cohorts.length ? (
          <ul class="rows">
            {course.cohorts.map((c) => {
              const l = p.cohortStates[c.org];
              const n = problemsOf(p, c.org);
              const st = l && l.kind === 'ready' ? l.status : undefined;
              const sem = st?.semester;
              return (
                <li>
                  <span class="r-title">{c.termLabel} {semesterChip(sem)}</span>
                  <span class="r-sub">{sem ? weekWords(sem) : l?.kind === 'absent' ? 'Status not computed yet' : c.termLabel}</span>
                  {st && sem?.live !== false ? <span class="r-sub next-event">{nextEventWords(nextEvent(st, p.now), tzOf(st), yearOf(p.now, tzOf(st)))}</span> : null}
                  <span class="r-side">{n !== null ? <Probs n={n} /> : null}<a class="btn small quiet" href={`?cohort=${c.org}#dashboard`}>Open</a></span>
                </li>
              );
            })}
          </ul>
        ) : <p class="footnote">No semesters yet.</p>}
      </section>
    ),
    details: (
      <section class="panel section">
        <div class="section-head"><h2>Course details</h2><a class="btn small quiet" href="#details">Edit course details</a></div>
        <dl class="kv">
          <dt>Name <Hint small label="About the name">The course’s name, as the console and the student site show it. In dsl-course.yml.</Hint></dt><dd>{course.name}</dd>
          <dt>Code <Hint small label="About the code">The course’s code in the catalogue, as students know it. In dsl-course.yml.</Hint></dt><dd>{course.code || 'not set'}</dd>
          <dt>Description <Hint small label="About the description">One paragraph about the course, shown on the public website. In dsl-course.yml.</Hint></dt><dd>{description || <span class="footnote">Not set</span>}</dd>
          <dt>Contact <Hint small label="About the contact">Who students and the lab write to about the course. In dsl-course.yml; the institution’s contact when unset.</Hint></dt><dd>{fallback(about.contact, POLICY.contact)}</dd>
          <dt>Licence <Hint small label="About the licence">The licence the public website shows for your materials. In dsl-course.yml; the institution’s default when unset.</Hint></dt><dd>{fallback(about.licence, POLICY.licences[0].name)}</dd>
          <dt>Admins <Hint small label="Course admins, instructors and teaching assistants">Course admins can change everything in the course, every semester. A semester’s instructors and TAs are set on that semester’s Instructors page. In dsl-course.yml.</Hint></dt><dd>{course.admins.join(', ') || 'none'}</dd>
          <dt>Late work <Hint small label="About these defaults">Late work and max team size apply to every assignment unless its semester or the assignment sets its own. Each comes from this course, or from the institution when the course sets none.</Hint></dt><dd>{lateWord(lateDays.value, latePen.value)}, {whose(lateDays.source)}</dd>
          <dt>Max team size <Hint small label="About max team size">This course’s default. Each assignment can set its own.</Hint></dt><dd>{valueWord('max_team_size', team.value)}, {whose(team.source)}</dd>
        </dl>
        <Lives org={course.org} repo={COURSE_REPO} path="dsl-course.yml" exists={p.files.file(course.org, COURSE_REPO, 'dsl-course.yml').kind !== 'absent'} />
        <div class="website-block">
          <h3>Public website <Hint label="About the public website">Optional: an open course version of your materials accessible to anyone on the internet, updated daily.</Hint></h3>
          <SiteLive org={course.org} published={pub} last={lastPublish} running={publishing} now={p.now} />
          <div class="actions">
            {course.write ? <OpButtons def={publishWebsite(courseScope({ course }), pub)} small verbCls="btn small outline" /> : null}
            <a class="btn small quiet" href="#website">Edit website details</a>
          </div>
        </div>
      </section>
    ),
    activity: <RecentActivity p={p} lists={ops} newest={live[0]?.ref.org} />,
    handouts: (
      <>
        <section class="panel section" id="sec-materials">
          <div class="section-head"><h2>Handout materials</h2><a class="btn small outline" href={`?course=${course.org}#new-materials`}>New handout materials</a></div>
          {v.course?.materials?.length ? (
            <ul class="rows">
              {v.course.materials.map((m) => {
                const r = materialsReadiness(m);
                return (
                  <li>
                    <span class="r-title">{m.repo} <RepoChip r={r} row /></span>
                    <RepoWhy r={r} />
                    <span class="r-side"><a class="btn small quiet" href={`#materials-${m.repo}`}>Settings</a></span>
                  </li>
                );
              })}
            </ul>
          ) : <p class="footnote">{computed ? 'No materials repos yet.' : 'Materials appear once the course has been checked.'}</p>}
        </section>
        <section class="panel section" id="sec-templates">
          <div class="section-head"><h2>Assignment templates</h2><a class="btn small outline" href={`?course=${course.org}#new-assignment-1`}>New assignment</a></div>
          {v.course?.templates?.length ? (
            <ul class="rows">
              {v.course.templates.map((t) => {
                const r = templateReadiness(t, v.course?.todo ?? [], templateProblems(v, t.repo));
                const bad = r.state === 'problem';
                return (
                  <li>
                    <span class="r-title">{templateName(templateTitle(p.files, course.org, t.repo))} <RepoChip r={r} row /></span>
                    <TemplateSub r={r} repo={t.repo} stops={v.problems.find((x) => x.fix?.entry === t.repo)?.stops} />
                    <span class="r-side"><a class={`btn small ${bad ? '' : 'quiet'}`} href={`#template-${t.repo}`}>{bad ? 'Fix' : 'Settings'}</a></span>
                  </li>
                );
              })}
            </ul>
          ) : <p class="footnote">{computed ? 'No assignment templates yet.' : 'Assignment templates appear once the course has been checked.'}</p>}
        </section>
      </>
    ),
  };
  const [left, right] = statusesSettled(p) ? splitColumns(overviewHeights({ course: v.course, semesters: course.cohorts.length, description, activity: recentActivity(ops).length })) : SETTLING_COLUMNS;
  return (
    <>
      <div class="page-head"><div><h2 class="h1">Dashboard <CourseHint /></h2></div></div>
      <CourseSubActions course={course} loaded={p.loaded} files={p.files} now={p.now} computed={computed} />
      <p class="page-note">Materials and assignment templates are prepared here, for every semester. Students get only what a semester releases or hands out, from that semester’s page.</p>
      <Verdict v={ready?.verdict ?? null} onOpen={() => showTab(setTab, 'problems')} />
      {!course.write ? <div class="ro-banner"><b>Read only.</b><span>You cannot change this course on GitHub, so the console shows what your account can see and offers no buttons.</span></div> : null}
      <CourseTabs items={ready?.items ?? null} problems={v.problems} aside={aside} tab={tab} onTab={setTab} />
      <div class="grid-2 cols">
        <div class="stack">{left.map((k) => <Fragment key={k}>{panels[k]}</Fragment>)}</div>
        <div class="stack">{right.map((k) => <Fragment key={k}>{panels[k]}</Fragment>)}</div>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- S17

const gradingValid = validator(gradingSchema);
/** The page that explains derived and hand-written starters (decision 0028 rule 5). */
export const STARTER_DOC = 'assignment-starter.md';

/** The `?` on "Marked from" (decision 0031 rule 7), after `questions: Q: {file:}` in the engine (`setting_readers.question_files`). */
export const MARKED_FROM_HINT =
  'The file in the student’s repo this question is marked from, when it is not the runnable one: a LaTeX write-up, say. Type its path from the top of the repo, like report.tex (for a shared drop box, from the student’s or team’s folder); the mark sheet names it beside the question. Leave it blank and the question is marked from the runnable file.';

export function Questions({ rows, set, files }: { rows: QuestionRow[]; set: (r: QuestionRow[]) => void; files: string[] }) {
  const total = rows.reduce((n, r) => n + (Number(r.points) || 0), 0);
  const edit = (i: number, patch: Partial<QuestionRow>) => set(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div class="field">
      <span class="label">Points per question <Hint label="About points per question">Optional. With questions set, the mark sheet has one column per question with its maximum, totals add up for you, and students see their score per question. Leave it empty for one flat score.</Hint></span>
      {rows.length ? (
        <table class="qtable">
          <thead><tr><th>Question</th><th>Points</th><th>Marked from <span class="default">optional</span> <Hint small label="About marked from">{MARKED_FROM_HINT}</Hint></th><th /></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr>
                <td><input type="text" value={r.name} aria-label={`Question ${i + 1} name`} onInput={(e) => edit(i, { name: (e.target as HTMLInputElement).value })} /></td>
                <td><input type="number" min="0" value={r.points} aria-label={`Question ${i + 1} points`} style="max-width:90px" onInput={(e) => edit(i, { points: (e.target as HTMLInputElement).value })} /></td>
                <td>
                  <input type="text" list="q-files" value={r.file} placeholder="the runnable file" aria-label={`Question ${i + 1} file`} aria-invalid={questionFileError(r.file) ? 'true' : undefined} onInput={(e) => edit(i, { file: (e.target as HTMLInputElement).value })} />
                  {questionFileError(r.file) ? <Invalid>{questionFileError(r.file)}</Invalid> : null}
                </td>
                <td><button class="x" type="button" aria-label={`Remove question ${i + 1}`} onClick={() => set(rows.filter((_, j) => j !== i))}>&times;</button></td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr><td>Total</td><td>{total}</td><td /><td /></tr></tfoot>
        </table>
      ) : <div class="readonly">Not set: the mark sheet takes one flat score.</div>}
      <datalist id="q-files">{files.map((f) => <option value={f} />)}</datalist>
      <div><button class="btn small quiet" type="button" onClick={() => set([...rows, { name: `Q${rows.length + 1}`, points: '', file: '' }])}>Add a question</button></div>
    </div>
  );
}

export function TemplateScreen(p: CourseProps) {
  const { course, entry } = p;
  const env = useEnv();
  // The entry is the template's repo name (`status.json` `course.templates[].slug` is the repo).
  const repo = entry ?? '';
  const v = useCourseView(p);
  const file = p.files.file(course.org, repo, 'grading_config.yml', 'solution');
  const gradingExists = file.kind !== 'absent';
  const tree = p.files.tree(course.org, repo);
  const problems = v.problems.filter((x) => x.fix?.entry === repo);
  const tpl = v.course?.templates?.find((t) => t.repo === repo);
  const [values, setValues] = useState<Values | null>(null);
  const [qdraft, setQdraft] = useState<QuestionRow[] | null>(null);
  const [save, runSave, setSave] = useSave(env);
  let cfg: Record<string, unknown> = {};
  let parseError = '';
  if (file.kind === 'ready') {
    const y = new YamlText(file.text);
    if (y.errors.length) parseError = y.errors[0];
    else cfg = (y.toJS() ?? {}) as Record<string, unknown>;
  }
  const tiers = settingsTiers();
  // A template that lists no formats runs on the course's, else the institution's: shown ticked, written only when changed.
  const read = fromConfig(cfg, v.course?.templates?.find((t) => t.repo === repo)?.starter);
  const fallbackFormats = formatsList(courseBlock(p.files, course.org, course.meta).formats);
  const base: Values = { ...read, formats: (read.formats as string[]).length ? read.formats : fallbackFormats.length ? fallbackFormats : [...DEFAULT_FORMATS] };
  const cur = values ?? base;
  const baseQ = questionRows(cfg.questions);
  const q = qdraft ?? baseQ;
  const fileErr = q.map((r) => questionFileError(r.file)).find(Boolean);
  const errors = { ...fieldErrors(null, tiers, cur), ...(formatError(cur) ? { formats: formatError(cur)! } : {}), ...(fileErr ? { questions: fileErr } : {}) };
  const dirty = (values !== null && !deepEqual(effective(tiers, values), effective(tiers, base))) || (qdraft !== null && !deepEqual(qdraft, baseQ));
  const title = String(cfg.title ?? '');
  const heading = templateName(title);
  const scope = courseScope(p);
  const files = tree.kind === 'ready' ? tree.paths.filter((x) => !x.dir && !x.path.startsWith('.')).map((x) => x.path) : [];
  const newest = course.cohorts[0];
  const nl = newest ? p.cohortStates[newest.org] : undefined;
  // The newest semester's key for this template, when its schedule has it.
  const inNewest = nl?.kind === 'ready' ? (nl.status.assignments ?? []).find((a) => a.template === repo)?.slug : undefined;
  const change = (nv: Values) => { setValues({ ...cur, ...nv }); setSave({ kind: 'idle' }); };
  const doSave = async () => {
    if (file.kind !== 'ready') return;
    if (Object.keys(errors).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const y = new YamlText(file.text);
    const was = toConfig(effective(tiers, base)), now = toConfig(effective(tiers, cur));
    for (const k of Object.keys({ ...was, ...now })) if (!deepEqual(was[k], now[k])) y.assign([k], now[k]);
    if (qdraft !== null && !deepEqual(qdraft, baseQ)) y.assign(['questions'], questionsValue(qdraft, cfg.questions));
    if (!gradingValid(y.toJS())) return setSave({ kind: 'bad', text: invalidText('grading_config.yml', gradingValid) });
    if (await runSave({ owner: course.org, repo, path: 'grading_config.yml', branch: 'solution' }, y.text, file.sha, { message: `template: edit the settings, from the DSL Teaching Console`, statusRepo: [course.org, COURSE_REPO] })) {
      setValues(null);
      setQdraft(null);
    }
  };
  return (
    <>
      <div class="page-head">
        <div><h2 class="h1">{heading} <Hint doc="03-add-assignment-to-course.md">Students get a copy of the assignment template at hand out; marking reads its solution branch. These settings apply to every semester, and after hand out they reach students only through Update every copy.</Hint></h2><p class="lede">This page sets up how the assignment is worked and marked, not its content. <span class="slug">{repo}</span></p></div>
        <div class="actions">{tpl ? <RepoChip r={templateReadiness(tpl, v.course?.todo ?? [], templateProblems(v, repo))} /> : null}<OpenButton org={course.org} repo={repo} /></div>
      </div>
      {tpl ? <section class="panel section" style="margin-bottom:18px"><RepoChecklist items={templateItems(repo, v.course?.todo ?? [], tpl.starter, v.tiered, p.now)} /></section> : null}
      {problems.length ? <div style="margin-bottom:18px"><ProblemCards list={problems} /></div> : null}
      {file.kind === 'loading' ? <Loading what="Reading grading_config.yml" /> : null}
      {file.kind === 'absent' ? <CheckLine cls="bad">There is no grading_config.yml on the solution branch of {repo}.</CheckLine> : null}
      {parseError ? <CheckLine cls="bad">grading_config.yml does not parse: {parseError}</CheckLine> : null}
      {file.kind === 'ready' && !parseError ? (
        <div class="panel">
          <div class="form">
            <div class="form-section">
              <h3>What it is</h3>
              <SchemaForm id="g1" schema={null} tiers={pick(tiers, ['title'])} values={cur} onChange={change} />
              <Lives org={course.org} repo={repo} path="README.md" exists={tree.kind !== 'ready' || tree.paths.some((x) => x.path === 'README.md')} />
            </div>
            <div class="form-section">
              <h3>How students work on it</h3>
              <SchemaForm id="g2" schema={null} tiers={pick(tiers, ['type', 'submit_via'])} values={cur} onChange={change} />
              <p class="footnote">
                How teams form, the max team size, late work, who can see each repo and the submit link are each semester’s.{' '}
                {newest ? (inNewest ? <a class="textlink" href={`?cohort=${newest.org}#assignment-${inNewest}/overview`}>Set them for {newest.termLabel}</a> : <a class="textlink" href={`?cohort=${newest.org}&template=${encodeURIComponent(repo)}#schedule-new`}>Add it to the {newest.termLabel} schedule</a>) : null}
              </p>
            </div>
            <div class="form-section">
              <h3>How it is marked <Hint label="About marking">Marking builds a mark sheet per student or team from these settings: the formats to read, whether tests run, and the questions below. You fill the sheet in Marks; totals and late penalties are worked out.</Hint></h3>
              <FormatPicker id="g-fmt" v={cur} set={change} fallback={fallbackFormats.length ? { formats: fallbackFormats, source: 'course' } : undefined} />
              <SchemaForm id="g3" schema={null} tiers={pick(tiers, ['autograde', 'tests', 'starter'])} values={cur} onChange={change} />
              <Questions rows={q} set={(r) => { setQdraft(r); setSave({ kind: 'idle' }); }} files={files} />
              <SchemaForm id="g4" schema={null} tiers={pick(tiers, ['completion_check', 'grader_pdf'])} values={cur} onChange={change} />
              <Lives org={course.org} repo={repo} path="grading_config.yml" branch="solution" exists={gradingExists} />
            </div>
            {/* A hand-written starter has nothing to derive: the radio's `?` says so (decision 0028 rule 4). */}
            {cur.starter === 'derived' ? (
              <div class="form-section">
                <h3>Student version <Hint label="About the student version" doc={STARTER_DOC}>Write the solution once and mark the answers; Derive builds the starter students get by blanking them. Run it after every change to the solution.</Hint></h3>
                <div class="actions"><OpButtons def={derive(scope, repo, repo, heading)} small /></div>
              </div>
            ) : null}
            <div class="form-section">
              <SaveBar state={save} onSave={() => void doSave()} disabled={!dirty} file={{ org: course.org, repo, path: 'grading_config.yml', branch: 'solution', exists: gradingExists }} />
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

