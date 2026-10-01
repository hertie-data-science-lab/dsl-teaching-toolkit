// S2 Course overview (a status board, decision 0025) and S17 Template settings (read).

import { Fragment, type ComponentChildren } from 'preact';
import { useState } from 'preact/hooks';
import gradingSchema from '../../schemas/grading_config.schema.json';
import { useEnv } from '../env';
import { invalidText, saveText, useSave } from '../edit/save';
import { YamlText, deepEqual } from '../edit/yamlText';
import { Invalid, SchemaForm, effective, fieldErrors } from '../forms/Form';
import { KIND_LABEL, STAGE_WORD, ago, opLabel, templateName } from '../model/format';
import { checkNow, derive, publishWebsite } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { FormatPicker } from '../forms/FormatPicker';
import { courseBlock, institutionLayer, lateWord, resolve, valueWord, type Layers } from '../model/cascade';
import { DEFAULT_FORMATS, POLICY } from '../model/policy';
import { formatsList, fromConfig, questionFileError, questionRows, questionsValue, settingsTiers, toConfig, type QuestionRow } from '../tiers/grading';
import type { Tiers, Values } from '../tiers/types';
import { SaveBar } from '../ui/edit';
import type { CourseStatus, MaterialsCheck, MaterialsState, Operation, Outcome, Problem, SemesterStatus, Status, Todo } from '../model/types';
import { nextEvent, nextEventWords, recentActivity, rollUpProblems, whoWord, type Activity } from '../model/status';
import type { CohortRef } from '../model/discovery';
import { outcomePath } from '../ops/adapter';
import { validator } from '../model/validate';
import { CheckLine, Lives, Loading, OpMark, ProblemCards, Probs, Soon, ghUrl, runUrl } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { Check, Ext, Fail } from '../ui/icons';
import { OpenButton } from '../ui/OpenButton';
import { formatError } from '../wizards/model';
import { courseScope, detailsOf, newestScope, websiteUrl } from './CourseEdit';
import type { CourseProps } from './types';
import { CONFIG_REPO, COURSE_REPO, STATUS_PATH } from '../model/names';
import { AsideFold, COURSE_FILE, Circle, SetAsideDialog, asideList, setAsideText, stepAside, todoAside, type Ask } from './SetAside';
import { REFRESH_HINT, tzOf, yearOf } from './common';

/** The course block and course-scoped problems: from the course's own status, else a semester's. */
export function courseView(p: Pick<CourseProps, 'loaded' | 'cohortStates'>): { course: CourseStatus | null; problems: Problem[]; computed: boolean } {
  if (p.loaded.kind === 'ready' && p.loaded.status.course)
    return { course: p.loaded.status.course, problems: (p.loaded.status.problems ?? []).filter((x) => x.scope === 'course'), computed: true };
  for (const l of Object.values(p.cohortStates))
    if (l.kind === 'ready' && l.status.course)
      return { course: l.status.course, problems: (l.status.problems ?? []).filter((x) => x.scope === 'course'), computed: true };
  return { course: null, problems: [], computed: false };
}

function problemsOf(p: CourseProps, cohortOrg: string): number | null {
  const l = p.cohortStates[cohortOrg];
  return l && l.kind === 'ready' ? (l.status.problems ?? []).length : null;
}

/** The course's live semesters whose status is read, newest first: what the overview rolls up. */
export function liveSemesters(p: Pick<CourseProps, 'course' | 'cohortStates'>): { ref: CohortRef; status: Status }[] {
  return p.course.cohorts.flatMap((ref) => {
    const l = p.cohortStates[ref.org];
    return l?.kind === 'ready' && l.status.semester?.live !== false ? [{ ref, status: l.status }] : [];
  });
}

/** The course's setup steps (C1-C6). The first three are what a new semester needs (decision 0019). */
export const SETUP_STEPS: { id: string; name: string; need?: 'required' }[] = [
  { id: 'C1', name: 'Course org read', need: 'required' },
  { id: 'C2', name: 'Course set up on GitHub', need: 'required' },
  { id: 'C3', name: 'Course details filled in', need: 'required' },
  { id: 'C4', name: 'First handout materials repo' },
  { id: 'C5', name: 'First assignment template' },
  { id: 'C6', name: 'Public website' },
];

/** How many required setup steps are not done. */
export function stepsLeft(c: CourseStatus): number {
  return SETUP_STEPS.filter((s) => s.need === 'required' && c.stages[s.id] !== 'done').length;
}

/** The one-line readiness verdict: never a problem count. */
export function readyWords(c: CourseStatus): string {
  if (c.ready) return 'Ready for a new semester.';
  const n = stepsLeft(c);
  return n ? `Not ready: ${n === 1 ? '1 setup step' : `${n} setup steps`} left.` : 'Not ready: a problem below needs fixing.';
}

/** The step each one waits for (the engine's `PREREQUISITES`). */
const WAITS_FOR: Record<string, string> = { C2: 'C1', C3: 'C2', C4: 'C2', C5: 'C2', C6: 'C4' };

/** Where an open setup step is done: one link. A problem links to its card, so a fault is listed once;
 * a blocked step links to where the step it waits for is done. */
export function stepLink(id: string, c: CourseStatus): { href: string; label: string; ext?: boolean } {
  const org = c.org;
  const state = c.stages[id];
  if (state === 'problem') return { href: '#course-problems', label: 'See the problem' };
  if (state === 'blocked' && WAITS_FOR[id]) return stepLink(WAITS_FOR[id], c);
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

/** Initial setup is complete once every step not set aside is done (decision 0032 rule 5). */
export function setupComplete(c: CourseStatus, list: string[] | null = null): boolean {
  return SETUP_STEPS.every((s) => c.stages[s.id] === 'done' || stepAside(c, s.id, list));
}

/** Where a required step is done, for its can't-be-set-aside dialog: the step's own page, whatever its state. */
const STEP_PAGE: Record<string, string> = { C1: 'Open on GitHub', C2: 'Open .github', C3: 'Open course details' };
function stepPage(id: string, c: CourseStatus): { href: string; label: string; ext?: boolean } {
  const own = stepLink(id, { ...c, stages: { ...c.stages, [id]: 'todo' } });
  return { ...own, label: STEP_PAGE[id] ?? own.label };
}

/** What the circle before a setup step asks: may it be set aside, or why not. */
export function stepAsk(id: string, c: CourseStatus): Ask {
  const label = SETUP_STEPS.find((s) => s.id === id)?.name ?? id;
  if (c.stage_optional?.[id]) return { kind: 'optional', id, label };
  return { kind: 'step', id, label, why: c.stage_why?.[id] ?? 'Not done yet.', page: stepPage(id, c) };
}

/** What the circle before a to-do asks. */
export function todoAsk(t: Todo, c: CourseStatus): Ask {
  const label = todoLine(t, c).label;
  return t.optional ? { kind: 'optional', id: t.id, label } : { kind: 'todo', id: t.id, label, todo: t, href: todoHref(t) };
}

/** Setup: a calm checklist. A tick when done; else a grey line, why, and where to do it. Steps set aside are left out
 * (the panel lists them in its fold); `onCircle`, for a viewer with write access, makes an open line's circle a button. */
export function SetupList({ course, list = null, onCircle }: { course: CourseStatus; list?: string[] | null; onCircle?: (id: string) => void }) {
  return (
    <ul class="setup">
      {SETUP_STEPS.filter((s) => !stepAside(course, s.id, list)).map((s) => {
        const state = course.stages[s.id] ?? 'todo';
        const done = state === 'done';
        const link = done ? null : stepLink(s.id, course);
        // Only a status that says which steps are optional offers the circle.
        const circle = !done && onCircle && course.stage_optional && s.id in course.stage_optional;
        return (
          <li class={done ? 'done' : 'open'}>
            {circle ? <Circle label={s.name} onClick={() => onCircle(s.id)} /> : <span class="s-mark" aria-hidden="true">{done ? <Check /> : null}</span>}
            <span class="s-name">{s.name}{s.need ? <span class="s-need">{s.need}</span> : null}<span class="sr">: {STAGE_WORD[state]}</span> <Hint label="About this step">{STEP_HINT[s.id]}</Hint></span>
            {link ? (
              <span class="s-why">
                {course.stage_why?.[s.id] ?? 'Not done yet.'}{' '}
                <a class="textlink" href={link.href} {...(link.ext ? { target: '_blank', rel: 'noopener' } : {})}>{link.label}{link.ext ? <Ext /> : null}</a>
              </span>
            ) : null}
          </li>
        );
      })}
    </ul>
  );
}

/** The readiness verdict under the page note: a tick when ready, a red cross when not. */
export function Verdict({ course }: { course: CourseStatus | null }) {
  if (!course) return <p class="verdict">Status not computed yet.</p>;
  return <p class={`verdict ${course.ready ? 'ok' : 'bad'}`}>{course.ready ? <Check /> : <Fail />}<span>{readyWords(course)}</span></p>;
}

/** Where a to-do is done: the repo's settings page. */
export function todoHref(t: Todo): string {
  return `#${t.kind === 'materials' ? 'materials' : 'template'}-${t.repo}`;
}

/** A template to-do's line and `?`, by the last part of its id (`template:<repo>:brief`). */
const TEMPLATE_TODO: Record<string, { label: string; hint: string }> = {
  brief: { label: 'Brief written', hint: 'The README.md students get as their instructions. Still the template text until you write it.' },
  starter: { label: 'Student version derived', hint: 'Derive builds the starter students get by blanking the marked answers. Run it after every change to the solution.' },
};

/** A to-do's line and `?`: a materials one reads as the same check on its settings checklist. */
export function todoLine(t: Todo, c: CourseStatus): { label: string; hint?: string } {
  const id = t.id.split(':').pop() ?? '';
  if (t.kind === 'template') return TEMPLATE_TODO[id] ?? { label: t.text };
  const check = c.materials?.find((m) => m.repo === t.repo)?.checks?.find((k) => k.id === id);
  return { label: check?.label ?? t.text, hint: CHECK_HINT[id] };
}

/** The to-dos not set aside. */
export function openTodos(c: CourseStatus, list: string[] | null = null): Todo[] {
  return (c.todo ?? []).filter((t) => !todoAside(t, list));
}

/** The open to-dos as a checklist styled like Initial setup: a grey line, why, and where to do it. */
export function TodoList({ course, list = null, onCircle }: { course: CourseStatus; list?: string[] | null; onCircle?: (t: Todo) => void }) {
  const todo = openTodos(course, list);
  if (!todo.length) return <p class="footnote">Nothing to do.</p>;
  return (
    <ul class="setup">
      {todo.map((t) => {
        const line = todoLine(t, course);
        return (
          <li class="open" key={t.id}>
            {onCircle && typeof t.optional === 'boolean' ? <Circle label={line.label} onClick={() => onCircle(t)} /> : <span class="s-mark" aria-hidden="true" />}
            <span class="s-name">{line.label}<span class="s-need slug">{t.repo}</span><span class="sr">: To do</span>{line.hint ? <> <Hint label="About this to-do">{line.hint}</Hint></> : null}</span>
            <span class="s-why">
              {line.label === t.text ? null : <>{t.text}{' '}</>}
              <a class="textlink" href={todoHref(t)} aria-label={`Open ${t.repo} settings`}>Open settings</a>
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** What stops a materials repo being ready: its unmet blocking checks. A ready repo has none. */
export function materialsWhys(m: MaterialsState): string[] {
  if (m.state === 'ready' || m.state === 'problem') return [];
  const unmet = (m.checks ?? []).filter((c) => c.blocks && !c.done).map((c) => c.why ?? c.label);
  return unmet.length ? unmet : ['Not ready yet.'];
}

/** The unmet checks under a materials row, one per line. */
export function Whys({ m }: { m: MaterialsState }) {
  const whys = materialsWhys(m);
  return whys.length ? <ul class="r-sub unmet">{whys.map((w) => <li>{w}</li>)}</ul> : null;
}

/** What each materials check is, for its `?`, in the checklist's order. */
const CHECK_HINT: Record<string, string> = {
  kind_folder: 'A repo with nothing of a content kind has nothing to release.',
  syllabus: 'The file the student site pins as the syllabus. Still the template text until you write it.',
  sessions: 'Every session with its date and readings, built from the semester schedule. Write puts it into your syllabus. Optional.',
  withheld: 'The whole repo is released as it stands unless a line here withholds it. Saving the list once, even empty, marks it reviewed.',
};

/** A materials repo's whole checklist, ticks included: the settings screen's head. */
export function MaterialsChecklist({ checks }: { checks: MaterialsCheck[] }) {
  return (
    <ul class="setup">
      {checks.map((c) => (
        <li class={c.done ? 'done' : 'open'}>
          <span class="s-mark" aria-hidden="true">{c.done ? <Check /> : null}</span>
          <span class="s-name">{c.label}{c.blocks ? <span class="s-need">required</span> : null}<span class="sr">: {c.done ? 'Done' : 'To do'}</span>{CHECK_HINT[c.id] ? <> <Hint label="About this check">{CHECK_HINT[c.id]}</Hint></> : null}</span>
          {!c.done && c.why ? <span class="s-why">{c.why}</span> : null}
          {c.detail ? (
            <details class="fold s-kinds">
              <summary>Kinds found</summary>
              <ul class="fold-body">
                {/* Every content kind: a tick and its folders when present, nothing when not. */}
                {c.detail.map((d) => (
                  <li class={d.folders.length ? 'found' : undefined}>
                    <span class="k-mark" aria-hidden="true">{d.folders.length ? <Check /> : null}</span>
                    <b>{KIND_LABEL[d.kind] ?? d.kind}</b>{d.folders.length ? `: ${d.folders.map((f) => `${f}/`).join(', ')}` : <span class="sr">: none</span>}
                  </li>
                ))}
              </ul>
            </details>
          ) : null}
        </li>
      ))}
    </ul>
  );
}

/** A template's title from its grading_config.yml, as loaded; '' until then. */
export function templateTitle(files: CourseProps['files'], org: string, repo: string): string {
  const f = files.file(org, repo, 'grading_config.yml', 'solution');
  if (f.kind !== 'ready') return '';
  const y = new YamlText(f.text);
  const t = y.errors.length ? undefined : (y.toJS() as Record<string, unknown> | null)?.title;
  return typeof t === 'string' ? t : '';
}

/** A semester's state: live, ended but not archived, or archived. */
export function semesterChip(s: Pick<SemesterStatus, 'live' | 'ended'> | undefined): ComponentChildren {
  if (s?.live === false) return <span class="chip">Archived</span>;
  if (s?.ended) return <span class="chip amber">Ended, not archived</span>;
  return <span class="chip ok">Live</span>;
}

/** A template's or materials repo's state as a chip: only `problem` is bad; `todo` is neutral. */
export function StateChip({ state, todo }: { state: string; todo: string }) {
  return state === 'problem' ? <span class="chip bad">Has a problem</span> : state === 'ready' ? <span class="chip ok">Ready</span> : <span class="chip">{todo}</span>;
}

/** The `?` on the course overview's h1, in the course banner. */
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
 * The overview's two columns (decision 0031 rule 4): Setup & To do heads the left, Problems the
 * right, and every other block goes where the two columns come out closest in height, keeping
 * the given order within each. Deterministic, so the page does not reflow between renders; on
 * a tie the earlier block stays left.
 */
export function splitColumns([setup, problems, ...rest]: Block[]): [string[], string[]] {
  let best: [string[], string[]] = [[setup.key], [problems.key]];
  let gap = Infinity;
  for (let mask = 0; mask < 1 << rest.length; mask++) {
    const cols: [Block[], Block[]] = [[setup], [problems]];
    rest.forEach((b, i) => cols[(mask >> i) & 1].push(b));
    const [l, r] = cols.map((c) => c.reduce((n, b) => n + b.h, 0));
    if (Math.abs(l - r) < gap) [gap, best] = [Math.abs(l - r), [cols[0].map((b) => b.key), cols[1].map((b) => b.key)]];
  }
  return best;
}

/** How many problems count towards the Problems panel's estimated height. */
const PROBLEMS_WEIGHED = 4;

/** Until every status has loaded, the overview keeps this layout, so panels do not move as each arrives. */
export const SETTLING_COLUMNS: [string[], string[]] = [['setup', 'semesters', 'handouts'], ['problems', 'details', 'activity']];

/** Whether the course's and every semester's status has finished loading (present, absent or failed). */
export function statusesSettled(p: Pick<CourseProps, 'course' | 'loaded' | 'cohortStates'>): boolean {
  return p.loaded.kind !== 'loading' && p.course.cohorts.every((c) => p.cohortStates[c.org] && p.cohortStates[c.org].kind !== 'loading');
}

/** Each overview panel's height, estimated from what it lists: a heading and one or two lines a row. */
export function overviewHeights(o: { course: CourseStatus | null; problems: number; semesters: number; description: string; activity: number }): Block[] {
  const HEAD = 3; // the heading and the panel's padding
  const c = o.course;
  const steps = c && !setupComplete(c) ? SETUP_STEPS.reduce((n, s) => n + (stepAside(c, s.id, null) ? 0 : c.stages[s.id] === 'done' ? 1 : 2), 0) : 0;
  const todo = c ? openTodos(c).length : 0;
  const materials = (c?.materials ?? []).reduce((n, m) => n + 1 + materialsWhys(m).length, 0);
  return [
    { key: 'setup', h: HEAD + (c ? 2 + steps + 2 * todo : 1) },
    // Capped: a day's new problem must not reshuffle the page.
    { key: 'problems', h: HEAD + 5 * Math.min(Math.max(1, o.problems), PROBLEMS_WEIGHED) },
    { key: 'semesters', h: HEAD + Math.max(1, 3 * o.semesters) },
    { key: 'details', h: HEAD + 9 + Math.ceil(o.description.length / 50) + 5 },
    { key: 'activity', h: HEAD + Math.max(1, 2 * o.activity) },
    { key: 'handouts', h: 2 * HEAD + Math.max(1, materials) + Math.max(1, 2 * (c?.templates?.length ?? 0)) },
  ];
}

/** The Setup & To do panel: Initial setup and To do, each with its Set aside fold (decision 0032). The circle before an
 * open line asks whether it can be set aside; the answer is saved into dsl-course.yml through the same save path as
 * Course details, and Bring back removes the id at once. A read-only viewer gets neither. */
export function SetupPanel({ p, c }: { p: CourseProps; c: CourseStatus | null }) {
  const env = useEnv();
  const { course } = p;
  const [ask, setAsk] = useState<Ask | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const file = p.files.file(course.org, COURSE_REPO, COURSE_FILE);
  const list = asideList(file);
  const change = async (id: string, on: boolean) => {
    setError('');
    const refuse = (text: string) => setError(text);
    if (!env) return refuse('Sign in to save.');
    if (p.migrated === false) return refuse('Not saved: the console has not yet confirmed this course uses the current names.');
    if (file.kind !== 'ready') return refuse(`Not saved: ${COURSE_FILE} is not read yet.`);
    const out = setAsideText(file.text, id, on);
    if ('error' in out) return refuse(out.error);
    setBusy(true);
    const ok = await saveText(env, { owner: course.org, repo: COURSE_REPO, path: COURSE_FILE }, out.text, file.sha, {
      message: `course: ${on ? 'set aside' : 'bring back'} ${id}, from the DSL Teaching Console`,
      statusRepo: [course.org, COURSE_REPO],
      onCommit: () => {
        setBusy(false);
        setAsk(null);
      },
    }, (st) => setError(st.kind === 'bad' ? st.text : ''));
    if (!ok) setBusy(false);
  };
  const write = course.write;
  const close = () => {
    setAsk(null);
    setError('');
  };
  const steps = c ? SETUP_STEPS.filter((x) => !stepAside(c, x.id, list)) : [];
  const stepRows = c ? SETUP_STEPS.filter((x) => stepAside(c, x.id, list)).map((x) => ({ id: x.id, label: x.name })) : [];
  const open = c ? openTodos(c, list) : [];
  const todoRows = c ? (c.todo ?? []).filter((t) => todoAside(t, list)).map((t) => ({ id: t.id, label: todoLine(t, c).label, repo: t.repo })) : [];
  const back = write ? (id: string) => void change(id, false) : undefined;
  return (
    <section class="panel section">
      <h2>Setup &amp; To do <Hint label="Setup, to-dos and problems">Setup steps are the one-time things a course needs. To-dos are work started but not finished. Problems, on the right, are things that broke.{write ? ' Optional items can be set aside: click the circle before them.' : ''}</Hint></h2>
      {c ? (
        <>
          <details class="fold setup-fold" open={!setupComplete(c, list)}>
            <summary><span class="fold-title">Initial setup</span><span class="cnt">{setupComplete(c, list) ? 'Complete' : `${steps.filter((x) => c.stages[x.id] === 'done').length} of ${steps.length} done`}</span></summary>
            <SetupList course={c} list={list} onCircle={write ? (id) => setAsk(stepAsk(id, c)) : undefined} />
            <AsideFold rows={stepRows} busy={busy} onBack={back} />
          </details>
          <details class="fold setup-fold" open={!!open.length}>
            <summary><span class="fold-title">To do</span><span class="cnt">{open.length ? `${open.length} open` : 'Nothing to do'}</span></summary>
            <TodoList course={c} list={list} onCircle={write ? (t) => setAsk(todoAsk(t, c)) : undefined} />
            <AsideFold rows={todoRows} busy={busy} onBack={back} />
          </details>
          {error && !ask ? <CheckLine cls="bad">{error}</CheckLine> : null}
          {ask ? <SetAsideDialog ask={ask} busy={busy} error={error} onSetAside={() => void change(ask.id, true)} onClose={close} /> : null}
        </>
      ) : <p class="footnote">Status not computed yet.</p>}
    </section>
  );
}

export function CourseScreen(p: CourseProps) {
  const { course } = p;
  const env = useEnv();
  const v = courseView(p);
  const live = liveSemesters(p);
  const problems = rollUpProblems(v.problems, live.map(({ ref, status }) => ({ org: ref.org, label: ref.termLabel, problems: status.problems ?? [] })));
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
  // Every panel but the two anchors goes in whichever column keeps the two about even; the
  // materials and templates panels travel as one block, so they stay together (0031 rule 4).
  const panels: Record<string, ComponentChildren> = {
    setup: <SetupPanel p={p} c={v.course} />,
    problems: (
      <section class="panel section" id="course-problems">
        <div class="problems-head"><h2>Problems <Hint label="About course problems">Things that broke and need fixing: the course’s first, then each live semester’s, tagged with the semester. Unfinished work is a to-do on the left, not a problem.</Hint></h2>{problems.length ? <span class="count-badge" aria-label={`${problems.length} problems`}>{problems.length}</span> : null}</div>
        {!v.computed && !live.length ? <p class="footnote">Status not computed yet.</p> : problems.length ? <ProblemCards list={problems} /> : <div class="no-problems"><Check /><span>No problems.</span></div>}
      </section>
    ),
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
                  <span class="r-sub">{sem ? `Week ${sem.week} of ${sem.weeks}` : l?.kind === 'absent' ? 'Status not computed yet' : c.termLabel}</span>
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
              {v.course.materials.map((m) => (
                <li>
                  <span class="r-title">{m.repo} <StateChip state={m.state} todo="Not ready yet" /></span>
                  <Whys m={m} />
                  <span class="r-side"><a class="btn small quiet" href={`#materials-${m.repo}`}>Settings</a></span>
                </li>
              ))}
            </ul>
          ) : <p class="footnote">{v.computed ? 'No materials repos yet.' : 'Materials appear once the course has been checked.'}</p>}
        </section>
        <section class="panel section" id="sec-templates">
          <div class="section-head"><h2>Assignment templates</h2><a class="btn small outline" href={`?course=${course.org}#new-assignment-1`}>New assignment</a></div>
          {v.course?.templates?.length ? (
            <ul class="rows">
              {v.course.templates.map((t) => {
                const bad = t.state === 'problem';
                return (
                  <li>
                    <span class="r-title">{templateName(templateTitle(p.files, course.org, t.repo))} <StateChip state={t.state} todo="Not written yet" /></span>
                    <span class={`r-sub${bad ? ' flag' : ''}`}>{bad ? v.problems.find((x) => x.fix?.entry === t.repo)?.stops ?? 'Has a problem.' : t.state === 'ready' ? 'Brief written. Settings check out.' : 'The brief (README.md) is not written yet.'} <span class="slug">{t.repo}</span></span>
                    <span class="r-side"><a class={`btn small ${bad ? '' : 'quiet'}`} href={`#template-${t.repo}`}>{bad ? 'Fix' : 'Settings'}</a></span>
                  </li>
                );
              })}
            </ul>
          ) : <p class="footnote">{v.computed ? 'No assignment templates yet.' : 'Assignment templates appear once the course has been checked.'}</p>}
        </section>
      </>
    ),
  };
  const [left, right] = statusesSettled(p) ? splitColumns(overviewHeights({ course: v.course, problems: problems.length, semesters: course.cohorts.length, description, activity: recentActivity(ops).length })) : SETTLING_COLUMNS;
  return (
    <>
      <CourseSubActions course={course} loaded={p.loaded} files={p.files} now={p.now} computed={v.computed} />
      <p class="page-note">Materials and assignment templates are prepared here, for every semester. Students get only what a semester releases or hands out, from that semester’s page.</p>
      <Verdict course={v.course} />
      {!course.write ? <div class="ro-banner"><b>Read only.</b><span>You cannot change this course on GitHub, so the console shows what your account can see and offers no buttons.</span></div> : null}
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
  const v = courseView(p);
  const file = p.files.file(course.org, repo, 'grading_config.yml', 'solution');
  const gradingExists = file.kind !== 'absent';
  const tree = p.files.tree(course.org, repo);
  const problems = v.problems.filter((x) => x.fix?.entry === repo);
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
        <div class="actions"><span class={`chip ${problems.length ? 'bad' : 'ok'}`}>{problems.length ? 'Has a problem' : 'Ready'}</span><OpenButton org={course.org} repo={repo} /></div>
      </div>
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
            {cur.starter === 'derived' ? (
              <div class="form-section">
                <h3>Student version <Hint label="About the student version" doc={STARTER_DOC}>Write the solution once and mark the answers; Derive builds the starter students get by blanking them. Run it after every change to the solution.</Hint></h3>
                <div class="actions"><OpButtons def={derive(scope, repo, repo, heading)} small /></div>
              </div>
            ) : (
              <div class="form-section">
                <h3>Student version</h3>
                <p class="footnote">Starter written by hand on main; nothing is derived.</p>
              </div>
            )}
            <div class="form-section">
              <SaveBar state={save} onSave={() => void doSave()} disabled={!dirty} file={{ org: course.org, repo, path: 'grading_config.yml', branch: 'solution', exists: gradingExists }} />
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

function pick(t: Tiers, keys: string[]): Tiers {
  return Object.fromEntries(keys.filter((k) => t[k]).map((k) => [k, t[k]]));
}
