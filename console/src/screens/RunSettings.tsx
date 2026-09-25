// How a semester runs its assignments (decisions 0009 and 0010 rule 5): the semester's
// defaults block on the Assignments index, and each assignment's Overview form, which edits
// the schedule entry (timings) and the assignment's block in assignments.yml as one form.
// Every run setting shows its effective value and where it comes from; a save writes only
// the keys the instructor set.

import { useState } from 'preact/hooks';
import scheduleSchema from '../../schemas/schedule.schema.json';
import { useEnv } from '../env';
import { invalidText, saveSteps, useSave, type SaveState, type Step } from '../edit/save';
import { YamlText, compact, deepEqual, obj, type Path } from '../edit/yamlText';
import { Field, SchemaForm, fieldErrors } from '../forms/Form';
import {
  REPO_NAME_RE, RUN_KEYS, semesterName, SOURCE_WORD, assignmentsFile, below, courseBlock, effectiveWord, lateWord, layersOf, rawBlock, resolve, scheduleFile, usableBlock,
  validAssignments, writeBlock, type Block, type Layers, type RunKey, type YamlFile,
} from '../model/cascade';
import { addDays, fmtWhen } from '../model/format';
import { ASSIGNMENTS_FILE, CONFIG_REPO } from '../model/names';
import { draftErrors, readDraft, writeDraft, type AssignmentDraft } from '../model/scheduleEdit';
import type { Assignment } from '../model/types';
import { validator } from '../model/validate';
import { RUN_LABEL, lateError, runTier, runTiers } from '../tiers/runSettings';
import type { FieldTier, Values } from '../tiers/types';
import { CheckLine, Lives, Loading } from '../ui/bits';
import { SaveBar } from '../ui/edit';
import { gradingConfig, tzOf, yearOf } from './common';
import type { ReadyProps } from './types';

const validSchedule = validator(scheduleSchema);

/** What the engine does with a solution date: the hand-out's warning (course.SOLUTION_WARNING). */
export const SOLUTION_WARNING = 'Pushes the model answer and rubric into every student’s repo. This is not returning marks, and cannot be undone for reuse.';

/** The semester's layers for `key` (empty for its defaults), read from the files as they are now. */
export function semesterLayers(p: Pick<ReadyProps, 'files' | 'course' | 'cohort'>, doc: Block, key = ''): Layers {
  return layersOf(courseBlock(p.files, p.course.org, p.course.meta), doc, key);
}

/** The effective value of every run key for an assignment, with its source, from the files. */
export function assignmentSettings(p: Pick<ReadyProps, 'files' | 'course' | 'cohort'>, key: string): Layers | null {
  const f = assignmentsFile(p.files, p.cohort.org);
  if (f === 'loading' || f === null) return f === null ? semesterLayers(p, {}, key) : null;
  return semesterLayers(p, f.doc, key);
}

/** The semester-side name of the assignment with schedule key `key` (`schedule.semester_name`): what its mark
 *  sheet and Return marks key on. The key while assignments.yml is still being read. */
export function sheetName(p: Pick<ReadyProps, 'files' | 'cohort'>, key: string): string {
  const f = assignmentsFile(p.files, p.cohort.org);
  return f && f !== 'loading' ? semesterName(f.doc, key) : key;
}

const toValues = (b: Block): Values => Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v === null ? undefined : v]));

// ------------------------------------------------------------------ the semester's defaults

/** "Defaults for this semester's assignments": assignments.yml `defaults:`, over the course's and the institution's. */
export function SemesterDefaults({ p }: { p: ReadyProps }) {
  const env = useEnv();
  const f = assignmentsFile(p.files, p.cohort.org);
  const [draft, setDraft] = useState<Values | null>(null);
  const [save, runSave, setSave] = useSave(env);
  if (f === 'loading') return <section class="panel section"><h2>Defaults for this semester’s assignments</h2><Loading what={`Reading ${ASSIGNMENTS_FILE}`} /></section>;
  if (f === null) return <section class="panel section"><h2>Defaults for this semester’s assignments</h2><CheckLine cls="bad">Could not read {ASSIGNMENTS_FILE}.</CheckLine></section>;
  const layers = semesterLayers(p, f.doc);
  const tiers = runTiers((k) => resolve(k, layers, 'course'));
  const before = toValues(rawBlock(f.doc, ['defaults']));
  const cur = draft ?? before;
  const errors = fieldErrors(null, tiers, cur);
  const dirty = draft !== null && !deepEqual(compact(draft), compact(before));
  const doSave = async () => {
    if (f.error) return;
    if (Object.keys(errors).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const y = new YamlText(f.text);
    writeBlock(y, ['defaults'], before, cur, RUN_KEYS);
    if (!validAssignments(y.toJS() ?? {})) return setSave({ kind: 'bad', text: invalidText(ASSIGNMENTS_FILE, validAssignments) });
    if (await runSave({ owner: p.cohort.org, repo: CONFIG_REPO, path: ASSIGNMENTS_FILE }, y.text, f.sha, { message: 'assignments: edit the semester’s defaults, from the Instructor Console', statusRepo: [p.cohort.org, CONFIG_REPO] })) setDraft(null);
  };
  return (
    <section class="panel section" id="semester-defaults">
      <div class="section-head"><h2>Defaults for this semester’s assignments</h2><a class="textlink" href={`?course=${p.course.org}#details`}>Change the course’s defaults</a></div>
      <p class="footnote">Every assignment this semester runs on these unless its own page says otherwise. Left empty, the value in grey applies: the course’s default, else the institution’s.</p>
      {f.error ? <CheckLine cls="bad">{ASSIGNMENTS_FILE} does not parse ({f.error}); fix it with Edit the file.</CheckLine> : (
        <>
          <SchemaForm id="sd" schema={null} tiers={tiers} values={cur} onChange={(v) => { setDraft(v); setSave({ kind: 'idle' }); }} />
          <SaveBar state={save} onSave={() => void doSave()} disabled={!dirty} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: ASSIGNMENTS_FILE }} />
        </>
      )}
      <Lives org={p.cohort.org} repo={CONFIG_REPO} path={ASSIGNMENTS_FILE} />
    </section>
  );
}

// ------------------------------------------------------------------ one assignment's run settings

/** Which run settings apply to an assignment of this template: teams only for a team assignment, the link only for one submitted elsewhere. */
export function applicableKeys(cfg: Record<string, unknown>, group: boolean): RunKey[] {
  return RUN_KEYS.filter((k) => (k === 'team_formation' || k === 'max_team_size' ? group : k === 'submit_url' ? cfg.submit_via === 'external' : true));
}

/** Why the template's shape fixes the visibility, or null. */
export function forcedVisibility(cfg: Record<string, unknown>): string | null {
  if (cfg.submit_via === 'shared_dropbox_repo') return 'Private: a shared drop box is always private.';
  if (cfg.submit_via === 'external') return 'Private: the repo holds the brief only.';
  return null;
}

export interface RunRowsProps {
  id: string;
  keys: RunKey[];
  /** The layers under this assignment's block (semester, course, institution). */
  layers: Layers;
  draft: Values;
  setDraft: (v: Values) => void;
  errors: Record<string, string>;
  defaultsHref: string;
  teamsHref?: string;
  forced?: string | null;
}

/** Each run setting as its effective value and source, with "Change for this assignment" opening the field. */
export function RunRows({ id, keys, layers, draft, setDraft, errors, defaultsHref, teamsHref, forced }: RunRowsProps) {
  const [opened, setOpened] = useState<string[]>([]);
  const mine: Layers = { ...layers, assignment: usableBlock(draft) };
  const has = (k: string) => draft[k] !== undefined && draft[k] !== '' && draft[k] !== null;
  const rows: { name: string; keys: RunKey[] }[] = [];
  for (const k of keys) {
    if (k === 'late_penalty_per_day' && keys.includes('late_window_days')) continue;
    rows.push(k === 'late_window_days' ? { name: 'late', keys: ['late_window_days', 'late_penalty_per_day'] } : { name: k, keys: [k] });
  }
  return (
    <>
      {rows.map((r) => {
        const open = opened.includes(r.name) || r.keys.some(has);
        const late = r.name === 'late';
        const eff = resolve(r.keys[0], mine);
        const effText = late ? `${lateWord(eff.value, resolve('late_penalty_per_day', mine).value)}, ${SOURCE_WORD[eff.source]}` : effectiveWord(r.keys[0], eff);
        const label = late ? 'Late work' : RUN_LABEL[r.keys[0]];
        if (r.name === 'visibility' && forced)
          return <div class="field"><span class="label">{label}</span><div class="readonly">{forced}</div></div>;
        return (
          <div class="field run-row">
            <span class="label">{label}</span>
            <div class="eff" data-key={r.name}>{effText}</div>
            {open ? (
              <div class="cond">
                {r.keys.map((k) => (
                  <Field id={`${id}-${k}`} k={k} t={runTier(k, resolve(k, layers, below('assignment'))) as FieldTier} value={draft[k]} values={draft} error={errors[k]}
                    set={(key, v) => setDraft({ ...draft, [key]: v })} />
                ))}
                <button class="textlink" type="button" onClick={() => { setOpened(opened.filter((x) => x !== r.name)); setDraft(Object.fromEntries(Object.entries(draft).filter(([k]) => !r.keys.includes(k as RunKey)))); }}>Use the default</button>
              </div>
            ) : null}
            <p class="run-links">
              {!open ? <button class="textlink" type="button" onClick={() => setOpened([...opened, r.name])}>Change for this assignment</button> : null}
              <a class="textlink" href={defaultsHref}>Change the default</a>
              {r.name === 'team_formation' && teamsHref ? <a class="textlink" href={teamsHref}>Open Teams</a> : null}
            </p>
            {r.name === 'visibility' ? <p class="why">Applies to copies handed out after this change; existing copies keep theirs.</p> : null}
          </div>
        );
      })}
    </>
  );
}

/** The run settings' errors as typed at the assignment layer. */
export function runErrors(keys: RunKey[], layers: Layers, draft: Values): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of keys) {
    const t = runTier(k, resolve(k, layers, 'semester'));
    const msg = t.check?.(draft[k], draft);
    if (msg && (draft[k] !== undefined || (k === 'late_window_days' && lateError(draft)))) out[k] = msg;
  }
  return out;
}

/**
 * assignments.yml after a schedule save: a new entry's run settings as its block (only the
 * keys set; none, no block), and no block left for a removed entry, which the engine would
 * fault. Null when nothing changes.
 */
export function assignmentsAfterSchedule(af: YamlFile, added: { key: string; run: Values } | null, removed: string[]): string | null {
  const y = new YamlText(af.text);
  if (added) writeBlock(y, ['assignments', added.key], {}, added.run, RUN_KEYS);
  const blocks = obj(af.doc.assignments);
  for (const id of removed) if (id in blocks) writeBlock(y, ['assignments'], { [id]: true }, {}, [id]);
  return y.text === af.text ? null : y.text;
}

// ------------------------------------------------------------------ the Overview form

/** `semester_dest_repo` as `settings.parse_instance` checks it, in its words. */
export function repoNameError(v: unknown): Record<string, string> {
  const t = typeof v === 'string' ? v.trim() : v == null ? '' : String(v);
  return t && !REPO_NAME_RE.test(t) ? { semester_dest_repo: 'Not a repo name (letters, digits, `.`, `_`, `-`); the schedule key names the repos instead.' } : {};
}

/** The late cutoff a due date and a late window give: due + N days, as the engine computes it. */
export function cutoffOf(dueDate: string, dueTime: string, days: unknown): string | null {
  if (!dueDate || typeof days !== 'number' || !Number.isInteger(days)) return null;
  const day = addDays(dueDate, days);
  return dueTime ? `${day}T${dueTime}` : day;
}

const T = (label: string, widget: FieldTier['widget'], extra: Partial<FieldTier> = {}): FieldTier => ({ tier: 'default', label, widget, ...extra });

/** Timings as the Overview form edits them: hand out, due, the computed late cutoff, solution shown. */
function Timings({ d, set, errors, cutoff, days, lateOk, solutionOff, tz, year }: {
  d: AssignmentDraft; set: (patch: Partial<AssignmentDraft>) => void; errors: Record<string, string>; cutoff: string | null; days: unknown; lateOk: boolean; solutionOff: string | null; tz: string; year: number;
}) {
  const F = (k: keyof AssignmentDraft & string, t: FieldTier, error?: string) => (
    <Field id={`ov-${k}`} k={k} t={t} value={d[k]} values={d as unknown as Values} error={error} set={(key, v) => set({ [key]: v ?? (t.widget === 'checkbox' ? false : '') } as Partial<AssignmentDraft>)} />
  );
  return (
    <>
      <div class="field">
        <span class="label">Hand out</span>
        <div class="choices">
          <label class="choice"><input type="radio" name="ov-ho" checked={!d.manual} onChange={() => set({ manual: false })} /><b>At a time</b><span>Automation hands out then.</span></label>
          <label class="choice"><input type="radio" name="ov-ho" checked={d.manual} onChange={() => set({ manual: true })} /><b>I will hand out manually</b><span>From this page.</span></label>
        </div>
      </div>
      {!d.manual ? <div class="cond"><div class="row-2">{F('handoutDate', T('Hand out on', 'date'), errors.handout)}{F('handoutTime', T('At', 'time'))}</div></div> : null}
      <div class="row-2">{F('dueDate', T('Due', 'date', { tier: 'ask' }), errors.due)}{F('dueTime', T('At', 'time', { tier: 'ask' }))}</div>
      <div class="field">
        <span class="label">Late cutoff <span class="default">computed</span></span>
        <div class="readonly">{!lateOk ? 'No late work: the cutoff is the due date.' : cutoff ? `${fmtWhen(cutoff, tz, year)} (due + ${String(days)} day${days === 1 ? '' : 's'})` : 'Set the due date first.'}</div>
        <p class="why">What is on main at the late cutoff is what is marked. Change it with the late window below.</p>
      </div>
      {solutionOff ? (
        <div class="field"><span class="label">Solution shown</span><div class="readonly">{solutionOff}</div></div>
      ) : (
        <>
          {F('solutionOn', T('Show the solution', 'checkbox', { defaultLabel: 'default: off' }))}
          {d.solutionOn ? (
            <div class="cond">
              <p class="note">{SOLUTION_WARNING}</p>
              <div class="row-2">{F('solutionDate', T('Solution shown on', 'date', { reason: 'Must be after the hand out.' }), errors.solution)}{F('solutionTime', T('At', 'time'))}</div>
            </div>
          ) : null}
        </>
      )}
    </>
  );
}

/** The assignment's Overview form: its schedule entry and its assignments.yml block, saved together. */
export function AssignmentRun({ p, a, group }: { p: ReadyProps; a: Assignment; group: boolean }) {
  const env = useEnv();
  const tz = tzOf(p.status), year = yearOf(p.now, tz);
  const [timing, setTiming] = useState<AssignmentDraft | null>(null);
  const [run, setRun] = useState<Values | null>(null);
  const [save, setSave] = useState<SaveState>({ kind: 'idle' });
  const sf = scheduleFile(p.files, p.cohort.org);
  const af = assignmentsFile(p.files, p.cohort.org);
  if (sf === 'loading' || af === 'loading') return <section class="panel section"><h2>How this semester runs it</h2><Loading what="Reading the schedule and assignments.yml" /></section>;
  if (!sf || sf.error) return <section class="panel section"><h2>How this semester runs it</h2><CheckLine cls="bad">The schedule could not be read{sf?.error ? ` (${sf.error})` : ''}; fix it with Edit the file.</CheckLine></section>;
  const baseT = readDraft(sf.doc, a.slug);
  if (!baseT || baseT.kind !== 'assignments') return <section class="panel section"><h2>How this semester runs it</h2><p class="footnote">{a.slug} is not in the schedule.</p></section>;
  const afOk = af !== null && !af.error;
  const doc = afOk ? af!.doc : {};
  const cfg = gradingConfig(p, a.template);
  const keys = applicableKeys(cfg, group);
  const path: Path = ['assignments', a.slug];
  const beforeRun = toValues(rawBlock(doc, path));
  const d = timing ?? baseT;
  const r = run ?? beforeRun;
  const layers = semesterLayers(p, doc, a.slug);
  const mine: Layers = { ...layers, assignment: usableBlock(r) };
  const days = resolve('late_window_days', mine).value;
  const vis = forcedVisibility(cfg) ? 'private' : resolve('visibility', mine).value;
  const solutionOff = vis !== 'private' ? 'Not available: student repos are not private, so the solution cannot be pushed automatically.' : d.manual ? 'Needs a hand out time; the solution must follow it.' : null;
  const tErr = draftErrors(d);
  const rErr = { ...runErrors(keys, layers, r), ...repoNameError(r.semester_dest_repo) };
  const dirtyT = timing !== null && !deepEqual(timing, baseT);
  const dirtyR = run !== null && !deepEqual(compact(run), compact(beforeRun));
  const change = (patch: Partial<AssignmentDraft>) => { setTiming({ ...d, ...patch }); setSave({ kind: 'idle' }); };
  const doSave = async () => {
    if (Object.keys(tErr).length || Object.keys(rErr).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    // Both files are built and checked before either is written; then the schedule goes
    // first, since a block in assignments.yml for a key the schedule does not name is a problem.
    let first: Step | null = null, second: Step | null = null;
    if (dirtyT) {
      const y = new YamlText(sf.text);
      writeDraft(y, { ...d, solutionOn: d.solutionOn && !solutionOff }, sf.doc);
      if (!validSchedule(y.toJS())) return setSave({ kind: 'bad', text: invalidText('the schedule', validSchedule) });
      first = { target: { owner: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml' }, text: y.text, sha: sf.sha, opts: { message: `schedule: edit ${a.slug}, from the Instructor Console`, statusRepo: [p.cohort.org, CONFIG_REPO] } };
    }
    if (dirtyR) {
      if (!afOk) return setSave({ kind: 'bad', text: `Not saved: ${ASSIGNMENTS_FILE} could not be read, so nothing was written.` });
      const y = new YamlText(af!.text);
      writeBlock(y, path, beforeRun, r, [...RUN_KEYS, 'semester_dest_repo']);
      if (!validAssignments(y.toJS() ?? {})) return setSave({ kind: 'bad', text: invalidText(ASSIGNMENTS_FILE, validAssignments) });
      second = { target: { owner: p.cohort.org, repo: CONFIG_REPO, path: ASSIGNMENTS_FILE }, text: y.text, sha: af!.sha, opts: { message: `assignments: edit ${a.slug}, from the Instructor Console`, statusRepo: [p.cohort.org, CONFIG_REPO] } };
    }
    if (await saveSteps(env, setSave, first, second, 'Dates saved; run settings not saved', () => setTiming(null))) setRun(null);
  };
  const repoName = typeof r.semester_dest_repo === 'string' ? r.semester_dest_repo : '';
  return (
    <section class="panel section" aria-label="How this semester runs it" id="run-settings">
      <div class="section-head"><h2>How this semester runs it</h2><a class="textlink" href={`#schedule-${a.slug}`}>Details and the site row</a></div>
      <div class="form">
        <div class="form-section">
          <h3>Dates</h3>
          <Timings d={d} set={change} errors={tErr} cutoff={cutoffOf(d.dueDate, d.dueTime, days)} days={days} lateOk={typeof days === 'number' && days > 0} solutionOff={solutionOff} tz={tz} year={year} />
        </div>
        <div class="form-section">
          <h3>Run settings</h3>
          {af === null ? <CheckLine cls="bad">Could not read {ASSIGNMENTS_FILE}; the defaults below may not be this semester’s.</CheckLine> : af.error ? <CheckLine cls="bad">{ASSIGNMENTS_FILE} does not parse ({af.error}); fix it with Edit the file.</CheckLine> : null}
          <RunRows id="ov" keys={keys} layers={layers} draft={r} setDraft={(v) => { setRun(v); setSave({ kind: 'idle' }); }} errors={rErr}
            defaultsHref="#assignments" teamsHref={group ? `#assignment-${a.slug}/teams` : undefined} forced={forcedVisibility(cfg)} />
          <details class="fold" open={!!repoName}>
            <summary>Advanced <span class={`cnt${repoName ? ' changed' : ''}`}>({repoName ? '1 changed' : 'none changed'})</span></summary>
            <div class="fold-body">
              <Field id="ov-repo" k="semester_dest_repo" values={r} value={r.semester_dest_repo} set={(k, v) => { setRun({ ...r, [k]: v }); setSave({ kind: 'idle' }); }}
                error={rErr.semester_dest_repo}
                t={{ tier: 'advanced', label: 'Repo name in this semester', placeholder: a.slug, defaultLabel: `default: ${a.slug}`, reason: 'What this semester’s repos are called. Change it before the first hand out.' }} />
            </div>
          </details>
        </div>
        <div class="form-section">
          <SaveBar state={save} onSave={() => void doSave()} disabled={!dirtyT && !dirtyR} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: dirtyR && !dirtyT ? ASSIGNMENTS_FILE : 'schedule.yml' }} />
          <p class="lives">Dates live in <code>{CONFIG_REPO}/schedule.yml</code>; run settings in <code>{CONFIG_REPO}/{ASSIGNMENTS_FILE}</code>.</p>
        </div>
      </div>
    </section>
  );
}
