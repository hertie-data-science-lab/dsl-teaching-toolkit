// New assignment (`#new-assignment-1..4`): three questions, then a check (design/inputs.md
// "New assignment", revision brief v2 section 7). What the `assignment.create` operation
// accepts goes in its request; the Advanced marking values it does not accept are written
// into the new template's grading_config.yml straight after, as its Settings form would.

import Ajv2020 from 'ajv/dist/2020';
import { useState } from 'preact/hooks';
import gradingSchema from '../../schemas/grading_config.schema.json';
import { useEnv } from '../env';
import { useSave } from '../edit/save';
import { YamlText, deepEqual } from '../edit/yamlText';
import { SchemaForm, effective, fieldErrors } from '../forms/Form';
import { createAssignment } from '../ops/defs';
import { FORMATS, VISIBILITY, toConfig } from '../tiers/grading';
import type { Tiers, Values } from '../tiers/types';
import { assignmentMarking, assignmentWhat, assignmentWork } from '../tiers/wizard';
import { Crumbs, Help } from '../ui/bits';
import { SaveLine } from '../ui/edit';
import { Ext } from '../ui/icons';
import { useDraft } from '../wizards/drafts';
import {
  assignmentArgs, autogradeBlock, contentTerms, formatBlock, formatError, nextFreeNumber, openAt, signature, templateRepo, termLabel, toggleFormat,
} from '../wizards/model';
import { allOk, checkFree, checkRepoExists, checkTemplate, useLive, type Check } from '../wizards/verify';
import { Checks, Rail, StepCard, Verified, WizError } from '../wizards/Wizard';
import { courseDefaults, courseView } from './Course';
import { courseScope } from './CourseEdit';
import type { CourseProps } from './types';

const STEPS = [
  { t: 'What is it', s: 'Name and number' },
  { t: 'How students work', s: 'Teams, where they submit' },
  { t: 'How it is marked', s: 'Format, tests' },
  { t: 'Check', s: 'Verified against the course', check: true },
];

export const S1 = ['name', 'number', 'term', 'copy_from'];
export const S2 = ['type', 'team_formation', 'max_team_size', 'submit_via', 'submit_url', 'visibility'];
export const S3 = ['formats', 'autograde', 'tests', 'completion_check', 'grader_pdf', 'late_window_days', 'late_penalty_per_day'];
/** grading_config.yml keys the create request cannot carry. */
export const EXTRA_KEYS = ['max_team_size', 'submit_url', 'tests', 'completion_check', 'grader_pdf', 'late_window_days', 'late_penalty_per_day'];

export interface NaDraft {
  v: Values;
  verified: Record<string, string>;
  extrasSaved?: string;
}

/** Fresh answers: the course's defaults where it has them, else the toolkit's. */
export function initialValues(meta: Record<string, unknown> | null, term: string): Values {
  const ad = ((meta?.assignment_defaults ?? {}) as Record<string, unknown>);
  const s = (k: string, d: string) => (typeof ad[k] === 'string' && ad[k] ? String(ad[k]) : d);
  return {
    term, type: 'individual', team_formation: s('team_formation', 'self_select'), submit_via: s('submit_via', 'assignment_repo'), visibility: s('visibility', 'private'),
    formats: [s('format', 'ipynb')], autograde: 'false', completion_check: 'auto', grader_pdf: false,
  };
}

/** Steps done: each stays done only while its answers match what was verified; copying skips 2 and 3. */
export function naDone(d: NaDraft, v: Values, created: boolean): boolean[] {
  if (created) return [true, true, true, false];
  const one = d.verified['1'] === signature(v, S1);
  const copying = !!v.copy_from;
  const two = one && (copying || d.verified['2'] === signature(v, S2));
  const three = two && (copying || d.verified['3'] === signature(v, S3));
  return [one, two, three, false];
}

/** The Advanced values the wizard writes into grading_config.yml after creation, key by key. */
export function extrasOf(v: Values): Record<string, unknown> {
  const c = toConfig(v);
  return Object.fromEntries(EXTRA_KEYS.filter((k) => c[k] !== undefined).map((k) => [k, c[k]]));
}

const gradingValid = new Ajv2020({ allErrors: true, strict: false }).compile(gradingSchema);

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

export function FormatPicker({ v, set }: { v: Values; set: (v: Values) => void }) {
  const formats = (v.formats as string[] | undefined) ?? [];
  const auto = v.autograde === 'true' && !autogradeBlock(v);
  const offs = FORMATS.map(([f]) => formatBlock(formats, f, auto)).filter((x): x is string => !!x);
  const err = formatError(v);
  return (
    <div class="field">
      <span class="label">What students hand in <span class="default">default: Jupyter notebook</span></span>
      <div class="fmt-grid">
        {FORMATS.map(([f, label]) => {
          const why = formatBlock(formats, f, auto);
          return (
            <label class={`check${why ? ' off' : ''}`} title={why ?? undefined}>
              <input type="checkbox" id={`na-fmt-${f}`} checked={formats.includes(f)} disabled={!!why} onChange={() => set({ ...v, formats: toggleFormat(formats, f) })} />
              <span>{label}</span>
            </label>
          );
        })}
      </div>
      {offs.length ? <p class="off-why">{[...new Set(offs)].join(' ')}</p> : null}
      {err ? <span class="invalid-msg"><span>{err}</span></span> : null}
      <p class="why">Seeds the starter files and decides how markers see submissions.</p>
    </div>
  );
}

export function NewAssignmentScreen(p: CourseProps & { step?: number }) {
  const { course, now, step: asked } = p;
  const env = useEnv();
  const status = courseView(p).course;
  const terms = contentTerms(course.cohorts.map((c) => c.term), now);
  const known = [
    ...(status?.templates ?? []).map((t) => t.repo),
    ...Object.values(p.cohortStates).flatMap((l) => (l.kind === 'ready' ? (l.status.assignments ?? []).map((a) => a.template) : [])),
  ].filter((r, i, a) => r && a.indexOf(r) === i);
  const [d, set, clear] = useDraft<NaDraft>(`new-assignment:${course.org}`, () => ({ v: initialValues(course.meta, terms[0]), verified: {} }));
  const v = d.v;
  const term = String(v.term ?? terms[0]);
  const next = nextFreeNumber(known, term);
  const number = (v.number as number | undefined) ?? next;
  const vv: Values = { ...v, number };
  const repo = templateRepo(number, term);
  const title = `Assignment ${number}${v.name ? `: ${String(v.name)}` : ''}`;
  const copying = !!v.copy_from;
  const defaults = courseDefaults(course.meta);
  const runs = env?.ops.runs.value.length ?? 0;
  const tpl = useLive(env ? async () => ({ repo, r: await checkTemplate(env.client, course.org, repo) }) : null, [repo, runs, asked]);
  const tplNow = tpl.value?.repo === repo ? tpl.value.r : null;
  // Ours only when step 1 found the name free: a repo that was already there is a clash, not a creation.
  const created = d.verified['1'] === signature(vv, S1) && !!tplNow && tplNow.checks[0].ok === true;
  const done = naDone(d, vv, created);
  const step = openAt(done, asked);
  const [s1, setS1] = useState<{ busy: boolean; list: Check[] | null }>({ busy: false, list: null });
  const [save, runSave, setSave] = useSave(env);
  const setV = (nv: Values) => set({ v: { ...nv, number: nv.number === next && v.number === undefined ? undefined : nv.number } });
  const go = (k: number) => {
    if (typeof location !== 'undefined') location.hash = `#new-assignment-${k}`;
  };
  const tiers1 = assignmentWhat(terms, next, known);
  const tiers2 = assignmentWork(defaults);
  const tiers3 = assignmentMarking(defaults);
  const errsOf = (t: Tiers) => fieldErrors(null, t, vv);
  const cohort = course.cohorts.find((c) => c.term === term);

  const writeExtras = async () => {
    if (!tplNow?.config) return;
    const text = withExtras(tplNow.config.text, extrasOf(effective(tiers3, effective(tiers2, vv))));
    if (text === null) return set({ extrasSaved: repo });
    const y = new YamlText(text);
    if (!gradingValid(y.toJS())) return setSave({ kind: 'bad', text: `Not saved: grading_config.yml would not be valid (${(gradingValid.errors ?? []).map((e) => `${e.instancePath} ${e.message}`).slice(0, 2).join('; ')}).` });
    if (await runSave({ owner: course.org, repo, path: 'grading_config.yml', branch: 'solution' }, text, tplNow.config.sha, { message: 'template: settings from the New assignment wizard, from the Instructor Console', statusRepo: [course.org, '.github'] }))
      set({ extrasSaved: repo });
  };

  let heading = '', body, foot;
  const createdNote = created && step < 4 ? <p class="note">{repo} is created. Change its settings on the <a href={`#template-${repo.replace(/-[fs]\d{4}$/, '')}`}>template’s settings</a>.</p> : null;
  if (step === 1) {
    heading = 'What is the assignment?';
    const errs = errsOf(tiers1);
    body = (
      <>
        {createdNote}
        <SchemaForm id="na1" schema={null} tiers={tiers1} values={vv} onChange={setV} advancedOpen={copying} />
        <p class="footnote">Will create <code>{repo}</code> in the course.</p>
        {s1.list || s1.busy ? <Checks list={s1.list} busy={s1.busy} pending={[`Assignment ${number} and ${repo} are free in the course`]} /> : null}
      </>
    );
    foot = (
      <button class="btn" type="button" disabled={!env || s1.busy || Object.keys(errs).length > 0} onClick={async () => {
        if (!env) return;
        setS1({ busy: true, list: null });
        const list = [await checkFree(env.client, course.org, repo, `Assignment ${number} and ${repo}`)];
        if (copying) list.push(await checkRepoExists(env.client, course.org, String(v.copy_from), `${String(v.copy_from)} is there to copy`));
        setS1({ busy: false, list });
        if (allOk(list)) {
          set({ verified: { ...d.verified, '1': signature(vv, S1) }, v: { ...v, number } });
          go(copying ? 4 : 2);
        }
      }}>Continue</button>
    );
  } else if (step === 2 || step === 3) {
    heading = step === 2 ? 'How do students work on it?' : 'How is it marked?';
    const tiers = step === 2 ? tiers2 : tiers3;
    const errs = { ...errsOf(tiers), ...(step === 3 && formatError(vv) ? { formats: formatError(vv)! } : {}) };
    body = copying ? (
      <p class="note">Copied from {String(v.copy_from)}: its settings come with it, so this question is skipped. Change them on the template’s settings after creation.</p>
    ) : (
      <>
        {createdNote}
        {step === 3 ? <FormatPicker v={vv} set={setV} /> : null}
        <SchemaForm id={`na${step}`} schema={null} tiers={tiers} values={vv} onChange={setV} />
      </>
    );
    foot = (
      <button class="btn" type="button" disabled={!copying && Object.keys(errs).length > 0} onClick={() => {
        set({ verified: { ...d.verified, [String(step)]: signature(vv, step === 2 ? S2 : S3) } });
        go(step + 1);
      }}>Continue</button>
    );
  } else {
    heading = created ? 'Created' : 'Check and create';
    const w = effective(tiers3, effective(tiers2, vv));
    const fmts = ((w.formats as string[]) ?? []).map((f) => FORMATS.find((x) => x[0] === f)?.[1] ?? f).join(' + ');
    const submit = { assignment_repo: `Their own repo, ${(VISIBILITY[String(w.visibility)] ?? 'Private').toLowerCase()}`, shared_dropbox_repo: 'A shared drop box, private', external: `Elsewhere: ${String(w.submit_url ?? 'no link')}, private` }[String(w.submit_via)] ?? '';
    const def = createAssignment(courseScope(p), repo, title, assignmentArgs(vv));
    const extrasPending = created && !copying && Object.keys(extrasOf(w)).length > 0 && d.extrasSaved !== repo;
    const ready = created && allOk(tplNow?.checks) && !extrasPending;
    body = (
      <>
        <dl class="summary-dl">
          <dt>Name</dt><dd>{title}</dd><dd><a href="#new-assignment-1">Change</a></dd>
          <dt>Students</dt><dd>{copying ? 'As copied' : w.type === 'group' ? `In teams, ${w.team_formation === 'assigned' ? 'you assign' : 'students choose'}, up to ${String(w.max_team_size ?? defaults.teamSize)}` : 'Alone'}</dd><dd>{copying ? null : <a href="#new-assignment-2">Change</a>}</dd>
          <dt>Submit</dt><dd>{copying ? 'As copied' : submit}</dd><dd>{copying ? null : <a href="#new-assignment-2">Change</a>}</dd>
          <dt>Marking</dt><dd>{copying ? 'As copied' : `${fmts}; tests ${w.autograde === 'true' ? `on (${String(w.tests ?? 'tests')})` : 'off'}`}</dd><dd>{copying ? null : <a href="#new-assignment-3">Change</a>}</dd>
        </dl>
        <Checks list={tplNow?.checks ?? null} busy={tpl.busy} pending={[`Template ${repo} created`]} />
        {extrasPending ? <p class="footnote">The marking settings the create step does not take are written to grading_config.yml next.</p> : null}
        <SaveLine state={save} />
        {ready ? (
          <>
            <Verified>Created. Nothing reaches students until you add it to a schedule.</Verified>
            <div class="actions">
              <a class="btn" href={`https://github.com/${course.org}/${repo}/edit/main/README.md`} target="_blank" rel="noopener">Write the brief <Ext /></a>
              {cohort ? <a class="btn outline" href={`?cohort=${cohort.org}&template=${encodeURIComponent(repo)}#schedule-new`}>Add to {cohort.termLabel} schedule</a> : <a class="btn outline" href={`?course=${course.org}#new-cohort-1`}>Add to {termLabel(term)} schedule: set up the cohort first</a>}
            </div>
            <div><button class="btn small quiet" type="button" onClick={() => { clear(); go(1); }}>Start another assignment</button></div>
          </>
        ) : created ? null : <p class="footnote">Creating makes a template with a main branch for the brief and a solution branch for marking.</p>}
        {tplNow && !tpl.busy && tplNow.checks.some((c) => c.ok === false) && created ? <WizError>The template is not complete yet. Check again in a minute; if it stays like this, open the run from All operations.</WizError> : null}
      </>
    );
    foot = !created ? (
      <button class="btn" type="button" disabled={!env} onClick={() => env?.ops.open(def, 'run')}>Create assignment</button>
    ) : extrasPending ? (
      <button class="btn" type="button" disabled={save.kind === 'busy' || !tplNow?.config} onClick={() => void writeExtras()}>Save the marking settings</button>
    ) : (
      <button class="btn small outline" type="button" disabled={tpl.busy} onClick={() => tpl.run()}>Check again</button>
    );
  }
  const back = step > 1 ? <a class="btn quiet" href={`#new-assignment-${step === 4 && copying ? 1 : step - 1}`}>Back</a> : <a class="btn quiet" href="#templates">Cancel</a>;
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Templates', href: '#templates' }, { t: 'New assignment' }]} />
      <div class="page-head"><div><h1>New assignment</h1></div></div>
      <Help title="What a template is" doc="03-add-assignment-to-course.md">
        <p>One template per assignment. Students get a copy at hand out; marking reads its solution branch. Everything here has a default and can be changed later on the template’s settings.</p>
      </Help>
      <div class="wizard">
        <Rail steps={STEPS} cur={step} done={done} heading="Three questions, then a check" base="new-assignment-" />
        <StepCard ctx={`For ${course.name}`} of={step < 4 ? `Question ${step} of 3` : 'The check'} title={heading} back={back} foot={foot}>{body}</StepCard>
      </div>
    </>
  );
}
