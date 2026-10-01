// New assignment (`#new-assignment-1..6`): five questions, then a check (design/inputs.md
// "New assignment", decision 0014 rules 6 and 7). Question 4 asks how students get the
// starter (decision 0028), after the tests answer its default follows. What the `assignment.create` operation
// accepts goes in its request; the marking values it does not accept are written into the new
// template's grading_config.yml straight after, as its Settings form would. An import is the
// console's: once the template exists and checks out, the ticked files are copied onto it by
// themselves with the signed-in user's token, one commit per branch; a button retries a branch
// the copy missed.

import { useEffect, useRef, useState } from 'preact/hooks';
import gradingSchema from '../../schemas/grading_config.schema.json';
import { useEnv } from '../env';
import { invalidText, useSave } from '../edit/save';
import { YamlText, deepEqual } from '../edit/yamlText';
import { SchemaForm, effective, fieldErrors } from '../forms/Form';
import { authorOf, copyFiles, type CopyResult } from '../github/client';
import { validator } from '../model/validate';
import { createAssignment } from '../ops/defs';
import { FormatPicker } from '../forms/FormatPicker';
import { labelOf } from '../model/labels';
import { DEFAULT_FORMATS, SUBMIT_VIA_DEFAULT } from '../model/policy';
import { STARTERS, STARTER_COPY, formatWord, formatsList, questionFileError, questionsValue, toConfig, type QuestionRow } from '../tiers/grading';
import type { Tiers, Values } from '../tiers/types';
import { assignmentMarking, assignmentName, assignmentStart, assignmentWork } from '../tiers/wizard';
import { CheckLine, Crumbs, Loading } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { SaveLine } from '../ui/edit';
import { Ext } from '../ui/icons';
import { OpenButton } from '../ui/OpenButton';
import { PatternTree } from '../ui/PatternTree';
import { useDraft } from '../wizards/drafts';
import {
  APP_SLUG, IMPORT_UNTICKED_MAIN, IMPORT_UNTICKED_SOLUTION, ORDINAL_WARNING, assignmentArgs, formatError, installUrl, liveSemesters, openAt, ordinalInName,
  parseSource, signature, sourceFixed, starterChoice, templateRepo, tickedEntries, type SourceRepo,
} from '../wizards/model';
import { allOk, checkFree, checkTemplate, readSource, useLive, type Check, type SourceBranch, type SourceRead } from '../wizards/verify';
import { Checks, Rail, StepCard, Verified, WizError } from '../wizards/Wizard';
import { Questions, STARTER_DOC, courseView } from './Course';
import { courseScope } from './CourseEdit';
import type { CourseProps } from './types';
import { COURSE_REPO } from '../model/names';

const STEPS = [
  { t: 'What is it', s: 'Name, what it starts from' },
  { t: 'How students work', s: 'Teams, where they submit' },
  { t: 'How it is marked', s: 'Format, tests' },
  { t: 'How students get the starter', s: 'Derived or by hand' },
  { t: 'Points per question', s: 'Optional' },
  { t: 'Check', s: 'Verified against the course', check: true },
];

export const S1 = ['name', 'keep_number', 'start', 'source_template', 'source_repo'];
export const S2 = ['type', 'submit_via'];
export const S3 = ['formats', 'autograde', 'tests', 'completion_check', 'grader_pdf'];
export const S4 = ['starter'];
export const S5 = ['questions'];
/** grading_config.yml keys the create request cannot carry. */
export const EXTRA_KEYS = ['tests', 'completion_check', 'grader_pdf', 'questions'];

/** Which files an import leaves out, per branch: ignore-file lines over the source's tree. */
export interface ImportLines {
  /** The source these lines are for; lines for another source are not used. */
  source: string;
  main: string[];
  solution: string[];
}

export interface NaDraft {
  v: Values;
  verified: Record<string, string>;
  extrasSaved?: string;
  lines?: ImportLines;
  /** The branches of `repo` the import has copied to. */
  imported?: { repo: string; branches: string[] };
}

/** Fresh answers: the course's defaults where it has them, else the institution's. */
export function initialValues(meta: Record<string, unknown> | null): Values {
  const ad = ((meta?.assignment_defaults ?? {}) as Record<string, unknown>);
  const s = (k: string, d: string) => (typeof ad[k] === 'string' && ad[k] ? String(ad[k]) : d);
  const course = formatsList(ad.formats);
  return {
    start: 'fresh', type: 'individual', submit_via: s('submit_via', SUBMIT_VIA_DEFAULT),
    formats: course.length ? course : [...DEFAULT_FORMATS], autograde: 'false', completion_check: 'auto', grader_pdf: false,
  };
}

/** The repo an import copies from, or null for a fresh start (or a source not given yet). */
export function sourceOf(v: Values, courseOrg: string): SourceRepo | null {
  if (v.start === 'template') return v.source_template ? { owner: courseOrg, repo: String(v.source_template) } : null;
  if (v.start === 'repo') return parseSource(v.source_repo);
  return null;
}

const imports = (v: Values) => v.start === 'repo' || v.start === 'template';

/**
 * `next` after a change on step 1. Starting from a repo makes its files the starter, so the
 * starter format becomes "No starter file"; going back to fresh restores `fresh`'s formats
 * unless someone chose others since. Either way it can still be changed on step 3.
 */
export function withStart(prev: Values, next: Values, fresh: Values): Values {
  if (imports(next) && !imports(prev)) return { ...next, formats: ['none'] };
  if (!imports(next) && imports(prev) && deepEqual(next.formats, ['none'])) return { ...next, formats: fresh.formats };
  return next;
}

/** Step 1 cannot continue while the name carries a number nobody said to keep. */
export const ordinalUnconfirmed = (v: Values) => ordinalInName(v.name) && v.keep_number !== true;

/** Steps done: each stays done only while its answers match what was verified. */
export function naDone(d: NaDraft, v: Values, created: boolean): boolean[] {
  if (created) return [true, true, true, true, true, false];
  const one = d.verified['1'] === signature(v, S1);
  const two = one && d.verified['2'] === signature(v, S2);
  const three = two && d.verified['3'] === signature(v, S3);
  const four = three && d.verified['4'] === signature(v, S4);
  const five = four && d.verified['5'] === signature(v, S5);
  return [one, two, three, four, five, false];
}

/** Question 4's radio: the two ways a starter is written, each described in full (decision 0028 rule 4). */
const STARTER_TIERS: Tiers = {
  starter: {
    tier: 'ask', label: 'How students get the starter', widget: 'radio',
    options: STARTERS.map((k) => ({ value: k, label: k === 'derived' ? `${STARTER_COPY[k].label} (recommended when tests mark it)` : STARTER_COPY[k].label, sub: STARTER_COPY[k].hint })),
  },
};

/** The values the wizard writes into grading_config.yml after creation, key by key. */
export function extrasOf(v: Values): Record<string, unknown> {
  const c: Record<string, unknown> = { ...toConfig(v), questions: questionsValue((v.questions as QuestionRow[] | undefined) ?? [], undefined) };
  return Object.fromEntries(EXTRA_KEYS.filter((k) => c[k] !== undefined).map((k) => [k, c[k]]));
}

const gradingValid = validator(gradingSchema);

/** grading_config.yml with `extras` applied, or null when nothing changes. */
export function withExtras(text: string, extras: Record<string, unknown>): string | null {
  const y = new YamlText(text);
  if (y.errors.length) return null;
  let changed = false;
  for (const [k, val] of Object.entries(extras))
    if (!deepEqual(y.get([k]), val)) {
      y.assign([k], val);
      changed = true;
    }
  return changed ? y.text : null;
}

/** The lines in force for `source`: the draft's when they are for it, else the defaults. */
export function linesFor(lines: ImportLines | undefined, source: string): ImportLines {
  return lines?.source === source ? lines : { source, main: [...IMPORT_UNTICKED_MAIN], solution: [...IMPORT_UNTICKED_SOLUTION] };
}

/** What a finished (or partial) copy says, one short sentence per branch. */
export function copySentences(results: CopyResult[]): { ok: string[]; bad: string[] } {
  const files = (n: number) => `${n} file${n === 1 ? '' : 's'}`;
  return {
    ok: results.filter((r) => !r.error).map((r) => (r.copied ? `Copied ${files(r.copied)} to ${r.branch}.` : `Nothing to copy to ${r.branch}.`)),
    bad: results.filter((r) => r.error).map((r) => `Not copied to ${r.branch}. ${r.error}`),
  };
}

/** Which repos "A repo the console can read" covers, and where an owner installs the app for another. */
const REPO_HINT = (
  <>
    The console reads public repos, and private repos in organisations where the DSL console app is installed. For another organisation, an owner installs the app there first.
    {APP_SLUG ? <> <a href={installUrl(APP_SLUG, null)} target="_blank" rel="noopener">Install the console app <Ext /></a></> : null}
  </>
);

function BranchPicker({ title, note, b, lines, set, fixed }: { title: string; note: string; b: SourceBranch; lines: string[]; set: (l: string[]) => void; fixed: (p: string) => string | null }) {
  const n = tickedEntries(b.entries, lines, fixed).length;
  return (
    <div class="field">
      <span class="label">{title} <span class="default">{n} of {b.entries.length} files ticked</span></span>
      <p class="why">{note}</p>
      <PatternTree mode="select" files={b.entries.map((e) => e.path)} patterns={lines} onChange={set} withheldWord="left out" releasedWord="copied" fixed={fixed} />
      {b.truncated ? <p class="footnote">GitHub listed only part of this branch. Only the files shown are copied.</p> : null}
    </div>
  );
}

function ImportPicker({ src, read, busy, lines, set }: { src: SourceRepo; read: SourceRead | null; busy: boolean; lines: ImportLines; set: (l: ImportLines) => void }) {
  if (!read) return busy ? <Loading what={`Reading ${src.owner}/${src.repo}`} /> : null;
  if (!read.main) return <CheckLine cls="bad">{read.check.text.replace(' can be read', '')}: {read.check.hint}</CheckLine>;
  return (
    <>
      <BranchPicker
        title={`Files from ${read.main.branch}, for students`} b={read.main} lines={lines.main} set={(main) => set({ ...lines, main })} fixed={sourceFixed('main', read.main.entries)}
        note="These files go on the template's main branch, which students receive at hand out. Solutions, tests, grading files and .env start unticked."
      />
      {read.solution ? (
        <BranchPicker
          title="Files from solution, for marking" b={read.solution} lines={lines.solution} set={(solution) => set({ ...lines, solution })} fixed={sourceFixed('solution', read.solution.entries)}
          note="The model answer and tests. grading_config.yml is written from your answers in the next steps."
        />
      ) : <p class="footnote">{src.owner}/{src.repo} has no solution branch, so only its brief is copied.</p>}
    </>
  );
}

export function NewAssignmentScreen(p: CourseProps & { step?: number }) {
  const { course, step: asked } = p;
  const env = useEnv();
  const status = courseView(p).course;
  const templates = (status?.templates ?? []).map((t) => t.repo);
  const [d, set, clear] = useDraft<NaDraft>(`new-assignment:${course.org}`, () => ({ v: initialValues(course.meta), verified: {} }));
  const v = d.v;
  const repo = templateRepo(v.name);
  const name = String(v.name ?? '').trim();
  const courseFormats = formatsList(((course.meta?.assignment_defaults ?? {}) as Record<string, unknown>).formats);
  const runs = env?.ops.runs.value.length ?? 0;
  const tpl = useLive(env && repo ? async () => ({ repo, r: await checkTemplate(env.client, course.org, repo) }) : null, [repo, runs, asked]);
  const tplNow = tpl.value?.repo === repo ? tpl.value.r : null;
  // Ours only when step 1 found the name free: a repo that was already there is a clash, not a creation.
  const created = !!repo && d.verified['1'] === signature(v, S1) && !!tplNow && tplNow.checks[0].ok === true;
  const done = naDone(d, v, created);
  const step = openAt(done, asked);
  const [s1, setS1] = useState<{ busy: boolean; list: Check[] | null }>({ busy: false, list: null });
  const [save, runSave, setSave] = useSave(env);
  const [copy, setCopy] = useState<{ busy: boolean; line?: string; results?: CopyResult[] }>({ busy: false });
  const [semester, setSemester] = useState('');
  const setV = (nv: Values) => set({ v: nv });
  const go = (k: number) => {
    if (typeof location !== 'undefined') location.hash = `#new-assignment-${k}`;
  };
  const tiersName = assignmentName();
  const tiersStart = assignmentStart(templates, REPO_HINT);
  const tiers2 = assignmentWork();
  const tiers3 = assignmentMarking();
  const errsOf = (t: Tiers) => fieldErrors(null, t, v);

  const src = sourceOf(v, course.org);
  const srcKey = src ? `${src.owner}/${src.repo}` : '';
  const source = useLive(env && src ? async () => ({ key: srcKey, r: await readSource(env.client, src.owner, src.repo) }) : null, [srcKey]);
  const read = source.value?.key === srcKey ? source.value.r : null;
  const lines = linesFor(d.lines, srcKey);
  const toCopy = read?.main ? [
    { branch: 'main', entries: tickedEntries(read.main.entries, lines.main, sourceFixed('main', read.main.entries)) },
    ...(read.solution ? [{ branch: 'solution', entries: tickedEntries(read.solution.entries, lines.solution, sourceFixed('solution', read.solution.entries)) }] : []),
  ] : [];
  const importDone = d.imported?.repo === repo ? d.imported.branches : [];
  // A source counts as pending until it has been read: an unread or unreadable source is never "nothing to copy".
  const importPending = !!src && (!read?.main || toCopy.some((c) => !importDone.includes(c.branch)));
  const readFailed = !!src && !!read && !read.main;
  /** The copy has run for this template (in full or in part): what is missing now waits for a retry. */
  const attempted = d.imported?.repo === repo;
  const missing = toCopy.filter((c) => !importDone.includes(c.branch)).map((c) => c.branch);
  const checksOk = created && allOk(tplNow?.checks);

  const w = effective(tiers3, effective(tiers2, v));
  const writeExtras = async () => {
    if (!tplNow?.config) return;
    const text = withExtras(tplNow.config.text, extrasOf(w));
    if (text === null) return set({ extrasSaved: repo });
    const y = new YamlText(text);
    if (!gradingValid(y.toJS())) return setSave({ kind: 'bad', text: invalidText('grading_config.yml', gradingValid) });
    if (await runSave({ owner: course.org, repo, path: 'grading_config.yml', branch: 'solution' }, text, tplNow.config.sha, { message: 'template: settings from the New assignment wizard, from the DSL Teaching Console', statusRepo: [course.org, COURSE_REPO] }))
      set({ extrasSaved: repo });
  };
  const runCopy = async () => {
    if (!env || !src) return;
    const results: CopyResult[] = [];
    const branches = [...importDone];
    for (const c of toCopy.filter((x) => !importDone.includes(x.branch))) {
      setCopy({ busy: true, line: `Copying to ${c.branch}` });
      const r = await copyFiles(env.client, src, { owner: course.org, repo, branch: c.branch }, c.entries, {
        message: `template: files from ${srcKey}, from the DSL Teaching Console`, author: authorOf(env.user),
        progress: (n, of) => setCopy({ busy: true, line: `Copying to ${c.branch}: ${n} of ${of} files` }),
      });
      results.push(r);
      if (!r.error) branches.push(r.branch);
    }
    setCopy({ busy: false, results });
    set({ imported: { repo, branches } });
  };
  // The copy runs by itself, once, as soon as the template exists and checks out.
  const autoCopied = useRef('');
  const copyNow = checksOk && !!read?.main && importPending && !attempted && !copy.busy && autoCopied.current !== repo;
  useEffect(() => {
    if (!copyNow) return;
    autoCopied.current = repo;
    void runCopy();
  }, [copyNow]);

  let heading = '', body, foot;
  const createdNote = created && step < 6 ? <p class="note">{repo} is created. Change its settings on the <a href={`#template-${repo}`}>assignment template's settings</a>.</p> : null;
  if (step === 1) {
    heading = 'What is the assignment?';
    const warn = ordinalInName(v.name);
    const errs = { ...errsOf(tiersName), ...errsOf(tiersStart) };
    const sourceBad = !!src && (!read || !read.main);
    body = (
      <>
        {createdNote}
        <SchemaForm id="na1" schema={null} tiers={tiersName} values={v} onChange={setV} />
        {repo ? <p class="footnote">Repo: <code>{repo}</code></p> : null}
        {warn ? (
          <div class="field">
            <p class="why">{ORDINAL_WARNING}</p>
            <label class="check"><input type="checkbox" id="na-keep-number" checked={v.keep_number === true} onChange={(e) => setV({ ...v, keep_number: (e.target as HTMLInputElement).checked })} /> Keep the number</label>
          </div>
        ) : null}
        <SchemaForm id="na1s" schema={null} tiers={tiersStart} values={v} onChange={(nv) => setV(withStart(v, nv, initialValues(course.meta)))} />
        {src ? <ImportPicker src={src} read={read} busy={source.busy} lines={lines} set={(l) => set({ lines: l })} /> : null}
        {s1.list || s1.busy ? <Checks list={s1.list} busy={s1.busy} pending={[`${repo} is free in the course`]} /> : null}
      </>
    );
    foot = (
      <button class="btn" type="button" disabled={!env || s1.busy || Object.keys(errs).length > 0 || ordinalUnconfirmed(v) || sourceBad} onClick={async () => {
        if (!env) return;
        setS1({ busy: true, list: null });
        const list = [await checkFree(env.client, course.org, repo, repo)];
        setS1({ busy: false, list });
        if (allOk(list)) {
          set({ verified: { ...d.verified, '1': signature(v, S1) } });
          go(2);
        }
      }}>Continue</button>
    );
  } else if (step === 2 || step === 3) {
    heading = step === 2 ? 'How do students work on it?' : 'How is it marked?';
    const tiers = step === 2 ? tiers2 : tiers3;
    const errs = { ...errsOf(tiers), ...(step === 3 && formatError(v) ? { formats: formatError(v)! } : {}) };
    body = (
      <>
        {createdNote}
        {step === 3 ? <FormatPicker v={v} set={setV} fallback={courseFormats.length ? { formats: courseFormats, source: 'course' } : undefined} /> : null}
        {step === 3 && imports(v) ? <p class="why">The imported files are the starter, so No starter file starts ticked.</p> : null}
        <SchemaForm id={`na${step}`} schema={null} tiers={tiers} values={v} onChange={setV} />
      </>
    );
    foot = (
      <button class="btn" type="button" disabled={Object.keys(errs).length > 0} onClick={() => {
        set({ verified: { ...d.verified, [String(step)]: signature(v, step === 2 ? S2 : S3) } });
        go(step + 1);
      }}>Continue</button>
    );
  } else if (step === 4) {
    heading = 'How do students get the starter?';
    const sv = { ...v, starter: starterChoice(v) };
    body = (
      <>
        {createdNote}
        <p class="note">Students receive the main branch at hand out. <Hint label="About the starter" doc={STARTER_DOC}>The default follows the tests answer: with tests on, derived, so the tests always match the starter. You can change it later in the template’s settings.</Hint></p>
        <SchemaForm id="na4" schema={null} tiers={STARTER_TIERS} values={sv} onChange={setV} />
      </>
    );
    foot = (
      <button class="btn" type="button" onClick={() => {
        set({ v: sv, verified: { ...d.verified, '4': signature(sv, S4) } });
        go(5);
      }}>Continue</button>
    );
  } else if (step === 5) {
    heading = 'Points per question';
    const rows = (v.questions as QuestionRow[] | undefined) ?? [];
    const bad = rows.map((r) => questionFileError(r.file)).find(Boolean);
    const files = toCopy.find((c) => c.branch === 'main')?.entries.map((e) => e.path) ?? [];
    const next = (nv: Values) => {
      set({ v: nv, verified: { ...d.verified, '5': signature(nv, S5) } });
      go(6);
    };
    body = (
      <>
        {createdNote}
        <p class="note">Optional. Skip it if the assignment is not written yet. You can fill it in later on the template's settings. With none set, each student gets one mark. The marks page then shows no total.</p>
        <Questions rows={rows} set={(r) => setV({ ...v, questions: r })} files={files} />
      </>
    );
    foot = (
      <>
        <button class="btn quiet" type="button" onClick={() => next({ ...v, questions: undefined })}>Skip</button>
        <button class="btn" type="button" disabled={!!bad} onClick={() => next(v)}>Continue</button>
      </>
    );
  } else {
    const fmts = ((w.formats as string[]) ?? []).map(formatWord).join(' + ');
    const submit = w.submit_via ? labelOf('submit_via', String(w.submit_via)) : '';
    const rows = ((v.questions as QuestionRow[] | undefined) ?? []).filter((r) => r.name.trim());
    const total = rows.reduce((n, r) => n + (Number(r.points) || 0), 0);
    const def = createAssignment(courseScope(p), repo, name, assignmentArgs(v), src ? srcKey : undefined);
    const extrasPending = created && Object.keys(extrasOf(w)).length > 0 && d.extrasSaved !== repo;
    const ready = checksOk && !importPending && !extrasPending;
    heading = ready ? 'Created' : 'Check and create';
    const live = liveSemesters(course.cohorts, (c) => {
      const l = p.cohortStates[c.org];
      return l?.kind !== 'ready' || l.status.semester?.live !== false;
    });
    const pick = live.find((c) => c.org === semester) ?? live[0];
    const said = copy.results ? copySentences(copy.results) : null;
    body = (
      <>
        <dl class="summary-dl">
          <dt>Name</dt><dd>{name}</dd><dd><a href="#new-assignment-1">Change</a></dd>
          <dt>Starts from</dt><dd>{!src ? 'Fresh starter files' : read?.main ? `${toCopy.map((c) => `${c.entries.length} files from ${c.branch}`).join(', ')} of ${srcKey}` : srcKey}</dd><dd><a href="#new-assignment-1">Change</a></dd>
          <dt>Students</dt><dd>{w.type === 'group' ? 'In teams' : 'Alone'}</dd><dd><a href="#new-assignment-2">Change</a></dd>
          <dt>Submit</dt><dd>{submit}</dd><dd><a href="#new-assignment-2">Change</a></dd>
          <dt>Marking</dt><dd>{`${fmts}; tests ${w.autograde === 'true' ? `on (${String(w.tests ?? 'tests')})` : 'off'}`}</dd><dd><a href="#new-assignment-3">Change</a></dd>
          <dt>Starter</dt><dd>{STARTER_COPY[starterChoice(v)].label}</dd><dd><a href="#new-assignment-4">Change</a></dd>
          <dt>Points</dt><dd>{rows.length ? `${rows.length} question${rows.length === 1 ? '' : 's'}, total ${total}` : 'Not set yet'}</dd><dd><a href="#new-assignment-5">Change</a></dd>
        </dl>
        <Checks list={tplNow?.checks ?? null} busy={tpl.busy} pending={[`Assignment template ${repo} created`]} />
        {src && !read ? <CheckLine cls="busy">Reading {srcKey}</CheckLine> : null}
        {readFailed ? <Checks list={[read!.check]} busy={source.busy} /> : null}
        {copy.busy ? <CheckLine cls="busy">{copy.line}</CheckLine> : null}
        {said?.ok.map((t) => <CheckLine cls="ok">{t}</CheckLine>)}
        {said?.bad.length ? <WizError>{said.bad.join(' ')} Copy again to try only what is missing.</WizError> : null}
        {attempted && missing.length && !said && !copy.busy ? <WizError>Not copied to {missing.join(' or ')} yet. Copy again to try only what is missing.</WizError> : null}
        {checksOk && !importPending && extrasPending ? <p class="footnote">The marking settings the create step does not take are written to grading_config.yml next.</p> : null}
        <SaveLine state={save} />
        {ready ? (
          <>
            <Verified>Created. Nothing reaches students until you add it to a schedule.</Verified>
            <div class="actions">
              <OpenButton org={course.org} repo={repo} /><a class="btn outline" href={`https://github.com/${course.org}/${repo}/upload/main`} target="_blank" rel="noopener">Upload files on GitHub <Ext /></a>
            </div>
            <div class="actions">
              {pick ? (
                <>
                  {live.length > 1 ? <select id="na-sem" aria-label="Semester" onChange={(e) => setSemester((e.target as HTMLSelectElement).value)}>{live.map((c) => <option value={c.org} selected={c.org === pick.org}>{c.termLabel}</option>)}</select> : null}
                  <a class="btn outline" href={`?cohort=${pick.org}&template=${encodeURIComponent(repo)}#schedule-new`}>{live.length > 1 ? 'Add to a schedule' : `Add to the ${pick.termLabel} schedule`}</a>
                </>
              ) : <a class="btn outline" href={`?course=${course.org}#new-semester-1`}>Set up a semester first</a>}
            </div>
            <div><button class="btn small quiet" type="button" onClick={() => { clear(); go(1); }}>Start another assignment</button></div>
          </>
        ) : created ? null : <p class="footnote">Creating makes an assignment template with a main branch for the brief and a solution branch for marking.{src ? ' The ticked files are copied next with your GitHub account.' : ''}</p>}
        {tplNow && !tpl.busy && tplNow.checks.some((c) => c.ok === false) && created ? <WizError>The assignment template is not complete yet. Check again in a minute; if it stays like this, open the run from All operations.</WizError> : null}
      </>
    );
    foot = readFailed ? (
      <button class="btn" type="button" disabled={source.busy} onClick={() => source.run()}>Read it again</button>
    ) : !created ? (
      <button class="btn" type="button" disabled={!env || !repo || (!!src && !read)} onClick={() => env?.ops.open(def, 'run')}>Create assignment</button>
    ) : checksOk && importPending && read?.main ? (
      attempted ? <button class="btn" type="button" disabled={copy.busy} onClick={() => void runCopy()}>Copy again</button> : null
    ) : checksOk && extrasPending ? (
      <button class="btn" type="button" disabled={save.kind === 'busy' || !tplNow?.config} onClick={() => void writeExtras()}>Save the marking settings</button>
    ) : (
      <button class="btn small outline" type="button" disabled={tpl.busy} onClick={() => tpl.run()}>Check again</button>
    );
  }
  const back = step > 1 ? <a class="btn quiet" href={`#new-assignment-${step - 1}`}>Back</a> : <a class="btn quiet" href="#templates">Cancel</a>;
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Assignment templates', href: '#templates' }, { t: 'New assignment' }]} />
      <div class="page-head"><div><h1>New assignment <Hint doc="03-add-assignment-to-course.md">Students get a copy of the assignment template at hand out; marking reads its solution branch. Everything here can be changed later in the template’s settings.</Hint></h1></div></div>
      <div class="wizard">
        <Rail steps={STEPS} cur={step} done={done} heading="Five questions, then a check" base="new-assignment-" />
        <StepCard ctx={`For ${course.name}`} of={step < 6 ? `Question ${step} of 5` : 'The check'} title={heading} back={back} foot={foot}>{body}</StepCard>
      </div>
    </>
  );
}
