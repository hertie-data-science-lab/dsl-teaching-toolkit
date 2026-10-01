// S2 Course overview and S17 Template settings (read).

import type { ComponentChildren } from 'preact';
import { useState } from 'preact/hooks';
import gradingSchema from '../../schemas/grading_config.schema.json';
import { useEnv } from '../env';
import { invalidText, useSave } from '../edit/save';
import { YamlText, deepEqual } from '../edit/yamlText';
import { Invalid, SchemaForm, effective, fieldErrors } from '../forms/Form';
import { KIND_LABEL, STAGE_WORD, ago, templateName } from '../model/format';
import { checkNow, derive, publishWebsite } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { FormatPicker } from '../forms/FormatPicker';
import { courseBlock, institutionLayer, lateWord, resolve, valueWord, type Layers } from '../model/cascade';
import { DEFAULT_FORMATS, POLICY } from '../model/policy';
import { formatsList, fromConfig, questionFileError, questionRows, questionsValue, settingsTiers, toConfig, type QuestionRow } from '../tiers/grading';
import type { Tiers, Values } from '../tiers/types';
import { SaveBar } from '../ui/edit';
import type { CourseStatus, MaterialsCheck, MaterialsState, Problem, SemesterStatus, Todo } from '../model/types';
import { validator } from '../model/validate';
import { CheckLine, Crumbs, Lives, Loading, ProblemCards, Probs, Soon, ghUrl } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { Check, Ext, Fail } from '../ui/icons';
import { OpenButton } from '../ui/OpenButton';
import { formatError } from '../wizards/model';
import { courseScope, detailsOf, newestScope, websiteUrl, websiteWord } from './CourseEdit';
import type { CourseProps } from './types';
import { COURSE_REPO, STATUS_PATH } from '../model/names';
import { REFRESH_HINT } from './common';

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

/** The course's setup steps (C1-C6). The first three are what a new semester needs (decision 0019). */
export const SETUP_STEPS: { id: string; name: string; need?: 'required' }[] = [
  { id: 'C1', name: 'Course org read', need: 'required' },
  { id: 'C2', name: 'Course set up on GitHub', need: 'required' },
  { id: 'C3', name: 'Course details filled in', need: 'required' },
  { id: 'C4', name: 'First materials repo' },
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
    case 'C4': return c.materials?.length ? { href: '#materials', label: 'Open materials' } : { href: `?course=${org}#new-materials`, label: 'New materials' };
    case 'C5': return c.templates?.length ? { href: '#templates', label: 'Open templates' } : { href: `?course=${org}#new-assignment-1`, label: 'New assignment' };
    default: return { href: '#website', label: 'Set up the public website' };
  }
}

/** Setup: a calm checklist. A tick when done; else a grey line, why, and where to do it. */
export function SetupList({ course }: { course: CourseStatus }) {
  return (
    <ul class="setup">
      {SETUP_STEPS.map((s) => {
        const state = course.stages[s.id] ?? 'todo';
        const done = state === 'done';
        const link = done ? null : stepLink(s.id, course);
        return (
          <li class={done ? 'done' : 'open'}>
            <span class="s-mark" aria-hidden="true">{done ? <Check /> : null}</span>
            <span class="s-name">{s.name}{s.need ? <span class="s-need">{s.need}</span> : null}<span class="sr">: {STAGE_WORD[state]}</span></span>
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

/** The open to-dos, one line each with where it is done. */
export function TodoList({ todo }: { todo: Todo[] }) {
  if (!todo.length) return <p class="footnote">Nothing to do.</p>;
  return (
    <ul class="todo">
      {todo.map((t) => (
        <li><span class="slug">{t.repo}</span> {t.text} <a class="textlink" href={todoHref(t)} aria-label={`Open ${t.repo} settings`}>Open settings</a></li>
      ))}
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

/** What each materials check is, for its `?` (decision 0024 rule 8). */
const CHECK_HINT: Record<string, string> = {
  all_mapped: 'The student site and the public website show materials by kind (lectures, labs, readings…), so every top folder a release can copy needs one. Folder names like lectures/ set it; anything else you set under Folder kinds.',
  kind_folder: 'A repo with nothing of a content kind has nothing to release.',
  syllabus: 'The file the student site pins as the syllabus. Still the template text until you write it.',
  withheld: 'The whole repo is released as it stands unless a line here withholds it. Saving the list once, even empty, marks it reviewed.',
  sessions: 'A paste-ready block of sessions and readings built from the semester schedule; optional.',
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
                {c.detail.map((d) => <li><b>{KIND_LABEL[d.kind] ?? d.kind}</b>: {d.folders.length ? d.folders.map((f) => `${f}/`).join(', ') : 'none'}</li>)}
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

/** A semester row's state: live, ended but not archived, or archived. */
export function semesterChip(s: SemesterStatus | undefined): ComponentChildren {
  if (s?.live === false) return <span class="chip">Archived</span>;
  if (s?.ended) return <span class="chip amber">Ended, not archived</span>;
  return <span class="chip ok">Live</span>;
}

/** A template's or materials repo's state as a chip: only `problem` is bad; `todo` is neutral. */
export function StateChip({ state, todo }: { state: string; todo: string }) {
  return state === 'problem' ? <span class="chip bad">Has a problem</span> : state === 'ready' ? <span class="chip ok">Ready</span> : <span class="chip">{todo}</span>;
}

/** The overview's head: New semester, the one primary action. */
export function CourseHeaderActions({ course, ready }: { course: CourseProps['course']; ready: boolean }) {
  return (
    <div class="actions">
      <a class={ready ? 'btn' : 'btn quiet'} href={`?course=${course.org}#new-semester-1`}>New semester</a>
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

/** The course layer over the institution's: what an assignment gets when its semester says nothing. */
export function courseLayers(p: Pick<CourseProps, 'course' | 'files'>): Layers {
  return { assignment: {}, semester: {}, course: courseBlock(p.files, p.course.org, p.course.meta), institution: institutionLayer() };
}

/** Where a course-level default comes from, in plain words. */
const whose = (s: string) => (s === 'course' ? 'this course' : 'institution');

export function CourseScreen(p: CourseProps) {
  const { course } = p;
  const v = courseView(p);
  const ready = v.course ? v.course.ready : false;
  const layers = courseLayers(p);
  const lateDays = resolve('late_window_days', layers), latePen = resolve('late_penalty_per_day', layers);
  const team = resolve('max_team_size', layers);
  const pub = v.course?.stages?.C6 === 'done';
  const about = detailsOf(course.meta ?? {}).about;
  const description = typeof about.course_description === 'string' ? about.course_description.trim() : '';
  const fallback = (value: unknown, institution: string) => (value ? String(value) : <span class="footnote">{institution}, from the institution</span>);
  return (
    <>
      <Crumbs items={[{ t: 'All courses', href: '#home' }, { t: course.name }]} />
      <div class="page-head">
        <div>
          <h1>{course.name} <Hint doc="02-add-materials-to-course.md">Materials are staged here privately until a release copies them in whole or in part to a semester. Selected materials can also be published on the course’s optional public website.</Hint></h1>
        </div>
        <CourseHeaderActions course={course} ready={ready} />
      </div>
      <CourseSubActions course={course} loaded={p.loaded} files={p.files} now={p.now} computed={v.computed} />
      <p class="page-note">Materials and assignment templates are prepared here, for every semester. Students get only what a semester releases or hands out, from that semester’s page.</p>
      <Verdict course={v.course} />
      {!course.write ? <div class="ro-banner"><b>Read only.</b><span>You cannot change this course on GitHub, so the console shows what your account can see and offers no buttons.</span></div> : null}
      <div class="grid-2 cols">
        <div class="stack">
          <section class="panel section">
            <h2>Setup &amp; To do <Hint label="Setup, to-dos and problems">Setup steps are the one-time things a course needs. To-dos are work started but not finished. Problems, on the right, are things that broke.</Hint></h2>
            {v.course ? (
              <>
                <SetupList course={v.course} />
                <h3 class="todo-head">To do</h3>
                <TodoList todo={v.course.todo ?? []} />
              </>
            ) : <p class="footnote">Status not computed yet.</p>}
          </section>
          <section class="panel section">
            <h2>Semesters</h2>
            {course.cohorts.length ? (
              <ul class="rows">
                {course.cohorts.map((c) => {
                  const l = p.cohortStates[c.org];
                  const n = problemsOf(p, c.org);
                  const sem = l && l.kind === 'ready' ? l.status.semester : undefined;
                  return (
                    <li>
                      <span class="r-title">{c.termLabel} {semesterChip(sem)}</span>
                      <span class="r-sub">{l && l.kind === 'ready' && l.status.semester ? `Week ${l.status.semester.week} of ${l.status.semester.weeks}` : l?.kind === 'absent' ? 'Status not computed yet' : c.termLabel}</span>
                      <span class="r-side">{n !== null ? <Probs n={n} /> : null}<a class="btn small quiet" href={`?cohort=${c.org}#dashboard`}>Open</a></span>
                    </li>
                  );
                })}
              </ul>
            ) : <p class="footnote">No semesters yet.</p>}
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
        </div>
        <div class="stack">
          <section class="panel section" id="course-problems">
            <div class="problems-head"><h2>Problems <Hint label="About course problems">Things that broke and need fixing. They also appear on every semester they will affect. Unfinished work is a to-do on the left, not a problem.</Hint></h2></div>
            {!v.computed ? <p class="footnote">Status not computed yet.</p> : v.problems.length ? <ProblemCards list={v.problems} /> : <div class="no-problems"><Check /><span>No problems.</span></div>}
          </section>
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
              <dt>Public website <Hint small label="About the public website setting">Whether the open version of your materials is published. In opencourse.yml.</Hint></dt><dd>{websiteWord(p.files, course.org)} <a class="textlink" href="#website">Manage</a></dd>
            </dl>
            <Lives org={course.org} repo={COURSE_REPO} path="dsl-course.yml" />
          </section>
          <section class="panel section">
            <div class="section-head"><h2>Public website <Hint label="About the public website">Optional: an open version of your materials for anyone on the internet, updated daily.</Hint></h2><a class="btn small quiet" href="#website">Edit website details</a></div>
            <p><span class={`chip ${pub ? 'ok' : ''}`}>{pub ? 'Published' : 'Not published'}</span> {pub ? <a class="textlink" href={websiteUrl(course.org)} target="_blank" rel="noopener">{course.org}.github.io <Ext /></a> : null}</p>
            {course.write ? <div class="actions"><OpButtons def={publishWebsite(courseScope({ course }), pub)} small verbCls="btn small outline" /></div> : null}
          </section>
          <section class="panel section" id="sec-materials">
            <div class="section-head"><h2>Materials</h2><a class="btn small outline" href={`?course=${course.org}#new-materials`}>New materials</a></div>
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
        </div>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- S17

const gradingValid = validator(gradingSchema);

export function Questions({ rows, set, files }: { rows: QuestionRow[]; set: (r: QuestionRow[]) => void; files: string[] }) {
  const total = rows.reduce((n, r) => n + (Number(r.points) || 0), 0);
  const edit = (i: number, patch: Partial<QuestionRow>) => set(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div class="field">
      <span class="label">Points per question <Hint label="About points per question">Optional. With questions set, the mark sheet has one column per question with its maximum, totals add up for you, and students see their score per question. Leave it empty for one flat score.</Hint></span>
      {rows.length ? (
        <table class="qtable">
          <thead><tr><th>Question</th><th>Points</th><th>Marked from <span class="default">optional</span></th><th /></tr></thead>
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
      <p class="why">A question marked from another file (a LaTeX write-up, say) names it; the mark sheet shows it beside the maximum.</p>
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
  const read = fromConfig(cfg);
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
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Assignment templates', href: '#templates' }, { t: heading }]} />
      <div class="page-head">
        <div><h1>{heading} <Hint doc="03-add-assignment-to-course.md">Students get a copy of the assignment template at hand out; marking reads its solution branch. These settings apply to every semester, and after hand out they reach students only through Update every copy.</Hint></h1><p class="lede">This page sets up how the assignment is worked and marked, not its content. <span class="slug">{repo}</span></p></div>
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
              <Lives org={course.org} repo={repo} path="README.md" />
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
              <SchemaForm id="g3" schema={null} tiers={pick(tiers, ['autograde', 'tests'])} values={cur} onChange={change} />
              <Questions rows={q} set={(r) => { setQdraft(r); setSave({ kind: 'idle' }); }} files={files} />
              <SchemaForm id="g4" schema={null} tiers={pick(tiers, ['completion_check', 'grader_pdf'])} values={cur} onChange={change} />
              <p class="lives"><a href={ghUrl(course.org, repo, 'grading_config.yml', 'solution')} target="_blank" rel="noopener">Lives in {`${course.org}/${repo}/grading_config.yml`}</a> on the solution branch.</p>
            </div>
            <div class="form-section">
              <h3>Student version <Hint label="About the student version">Derive builds the main branch, the copy students get, from the solution branch, removing the marked answers. Run it after every change to the solution.</Hint></h3>
              <div class="actions"><OpButtons def={derive(scope, repo, repo, heading)} small /></div>
            </div>
            <div class="form-section">
              <SaveBar state={save} onSave={() => void doSave()} disabled={!dirty} file={{ org: course.org, repo, path: 'grading_config.yml', branch: 'solution' }} />
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
