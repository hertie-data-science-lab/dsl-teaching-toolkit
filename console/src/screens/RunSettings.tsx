// How a semester runs its assignments (decisions 0009 and 0039): the semester's `defaults:`
// (semester.yml) on the Assignments index, and each assignment's Overview form, which edits its
// schedule entry, timings and inline run settings, as one form (decision 0038 rule 9). Every
// run setting shows its effective value and where it comes from; a save writes only the keys
// the instructor set.

import { useRef, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { invalidText, useSave } from '../edit/save';
import { YamlText, compact, deepEqual } from '../edit/yamlText';
import { Field, SchemaForm, fieldErrors } from '../forms/Form';
import { Modal } from '../ui/Modal';
import {
  LATE_PAIR, REPO_NAME_RE, RUN_KEYS, semesterName, SOURCE_WORD, courseDefaults, effectiveOf, effectiveWord, lateWord, over, scheduleFile, semesterDefaults, semesterFile, usableBlock,
  fileValue, validSchedule, validSemesterFile, valueWord, type Block, type Effectives, type RunKey, type YamlFile,
} from '../model/settings';
import { defaultsOf, semesterInheritance, statedDefaults, withDefaults } from '../model/config';
import { addDays, fmtWhen } from '../model/format';
import { CONFIG_REPO, SEMESTER_FILE } from '../model/names';
import { defaultsTiers } from '../tiers/course';
import { draftErrors, readDraft, writeDraft, type AssignmentDraft } from '../model/scheduleEdit';
import { SOLUTION_WARNING } from '../model/labels';
import type { Assignment } from '../model/types';
import { pastDue } from '../model/assignment';
import { penaltyRate } from '../model/policy';
import { round } from '../model/marks';
import { RUN_LABEL, lateError, runTier } from '../tiers/runSettings';
import type { FieldTier, Values } from '../tiers/types';
import { CheckLine, EditFile, Loading } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { SaveBar } from '../ui/edit';
import { gradingConfig, tzOf, yearOf } from './common';
import type { ReadyProps } from './types';



/** What an assignment's own settings fall back to: the semester's effective defaults (status.json). */
export const entryBelow = (p: Pick<ReadyProps, 'status'>): Effectives => semesterDefaults(p.status);

/** An assignment's effective run settings, each with its source, as the engine resolved them (status.json). */
export function assignmentSettings(p: Pick<ReadyProps, 'status'>, key: string): Effectives | null {
  return p.status.assignments?.find((a) => a.slug === key)?.settings ?? null;
}

/** The semester-side name of the assignment with schedule key `key` (`schedule.semester_name`): what its mark
 *  sheet and Return marks key on. The engine's answer (status.json), else the schedule entry's `repo_name`, else the key. */
export function sheetName(p: Pick<ReadyProps, 'files' | 'cohort'> & { status?: ReadyProps['status'] }, key: string): string {
  const said = p.status?.assignments?.find((a) => a.slug === key)?.semester_name;
  if (said) return said;
  const f = scheduleFile(p.files, p.cohort.org);
  return f && f !== 'loading' ? semesterName(f.doc, key) : key;
}

const toValues = (b: Block): Values => Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v === null ? undefined : v]));

// ------------------------------------------------------------------ the semester's defaults

/** "Defaults: 5 days, 5%, teams of 4." from the semester's effective defaults (status.json). */
export function defaultsLine(defaults: Effectives): string {
  const days = effectiveOf(defaults, 'late_window_days').value;
  const pen = effectiveOf(defaults, 'late_penalty_per_day').value;
  const size = effectiveOf(defaults, 'max_team_size').value;
  const rate = penaltyRate(pen);
  const late = typeof days === 'number' && days >= 1 ? [valueWord('late_window_days', days), rate ? `${round(rate * 100)}%` : 'no penalty'] : ['no late work'];
  return `Defaults: ${[...late, ...(size ? [`teams of ${String(size)}`] : [])].join(', ')}.`;
}

/** The semester's defaults as one line below the Assignments list; Change opens the form as a modal. */
export function SemesterDefaults({ p }: { p: ReadyProps }) {
  const f = semesterFile(p.files, p.cohort.org);
  const [open, setOpen] = useState(false);
  const [draft, setDraft] = useState<Values | null>(null);
  const opener = useRef<HTMLButtonElement>(null);
  if (f === 'loading') return <Loading what={`Reading ${SEMESTER_FILE}`} />;
  if (f === null) return <CheckLine cls="bad">Could not read {SEMESTER_FILE}.</CheckLine>;
  const close = () => {
    setOpen(false);
    opener.current?.focus();
  };
  return (
    <>
      <p class="defaults-line" id="semester-defaults">
        {defaultsLine(entryBelow(p))}{' '}
        <button class="textlink" type="button" ref={opener} aria-haspopup="dialog" onClick={() => setOpen(true)}>Change</button>
      </p>
      {open ? <Modal title="Defaults for this semester" onClose={close}><DefaultsForm p={p} f={f} draft={draft} setDraft={setDraft} /></Modal> : null}
    </>
  );
}

/** What this semester's own defaults fall back to: the course's effective values (its status), else the semester's answer where it is not the semester's own. */
export const semesterInherited = (p: Pick<ReadyProps, 'status' | 'courseLoaded'>): Effectives =>
  semesterInheritance(p.courseLoaded?.kind === 'ready' ? courseDefaults(p.courseLoaded.status) : undefined, semesterDefaults(p.status));

/** semester.yml with its `defaults:` stating `after` (form values), or why it would not be valid. */
export function semesterDefaultsAfter(f: YamlFile, after: Values, inherited: Effectives): { text: string } | { error: string } {
  const text = withDefaults(f.text, statedDefaults(after), inherited);
  return validSemesterFile(new YamlText(text).toJS() ?? {}) ? { text } : { error: invalidText(SEMESTER_FILE, validSemesterFile) };
}

function DefaultsForm({ p, f, draft, setDraft }: { p: ReadyProps; f: YamlFile; draft: Values | null; setDraft: (v: Values | null) => void }) {
  // Absent, the form edits the stub, and the edit link goes to GitHub's new-file page.
  const exists = p.files.file(p.cohort.org, CONFIG_REPO, SEMESTER_FILE).kind !== 'absent';
  const env = useEnv();
  const [save, runSave, setSave] = useSave(env);
  const before = toValues(defaultsOf(f.doc));
  const inherited = semesterInherited(p);
  const tiers = defaultsTiers(inherited, before);
  const cur = draft ?? before;
  const errors = fieldErrors(null, tiers, cur);
  const dirty = draft !== null && !deepEqual(compact(draft), compact(before));
  const doSave = async () => {
    if (f.error) return;
    if (Object.keys(errors).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const out = semesterDefaultsAfter(f, cur, inherited);
    if ('error' in out) return setSave({ kind: 'bad', text: out.error });
    if (await runSave({ owner: p.cohort.org, repo: CONFIG_REPO, path: SEMESTER_FILE }, out.text, f.sha, { message: 'semester: edit the semester’s defaults, from the DSL Teaching Console', statusRepo: [p.cohort.org, CONFIG_REPO] })) setDraft(null);
  };
  return (
    <>
      <p class="footnote">Every assignment this semester runs on these unless its own page says otherwise. Left empty, the value in grey applies: the course’s default, else the institution’s. <a class="textlink" href={`?course=${p.course.org}#details`}>Change the course’s defaults</a></p>
      {f.error ? <CheckLine cls="bad">{SEMESTER_FILE} does not parse ({f.error}); fix it with Edit the file directly.</CheckLine> : (
        <>
          <SchemaForm id="sd" schema={null} tiers={tiers} values={cur} onChange={(v) => { setDraft(v); setSave({ kind: 'idle' }); }} />
          <SaveBar state={save} onSave={() => void doSave()} disabled={!dirty} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: SEMESTER_FILE, exists }} />
        </>
      )}
      {f.error ? <div class="savebar"><EditFile org={p.cohort.org} repo={CONFIG_REPO} path={SEMESTER_FILE} exists={exists} /></div> : null}
    </>
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
  /** What this assignment's own settings fall back to: the semester's effective defaults. */
  below: Effectives;
  /** The assignment's effective settings as the engine resolved them (status.json), shown while the form states nothing; none for a new entry. */
  current?: Effectives | null;
  draft: Values;
  setDraft: (v: Values) => void;
  errors: Record<string, string>;
  defaultsHref: string;
  teamsHref?: string;
  forced?: string | null;
}

/** Each run setting as its effective value and source, with "Change for this assignment" opening the field. */
export function RunRows({ id, keys, below, current, draft, setDraft, errors, defaultsHref, teamsHref, forced }: RunRowsProps) {
  const [opened, setOpened] = useState<string[]>([]);
  const own = usableBlock(draft);
  const mine = (k: RunKey) => over(k, own, 'entry', current ?? below);
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
        const eff = mine(r.keys[0]);
        const effText = late ? `${lateWord(eff.value, mine('late_penalty_per_day').value)}, ${SOURCE_WORD[eff.source]}` : effectiveWord(r.keys[0], eff);
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
                  <Field id={`${id}-${k}`} k={k} t={runTier(k, effectiveOf(below, k), { override: true }) as FieldTier} value={draft[k]} values={draft} error={errors[k]}
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
export function runErrors(keys: RunKey[], below: Effectives, draft: Values): Record<string, string> {
  const out: Record<string, string> = {};
  for (const k of keys) {
    const t = runTier(k, effectiveOf(below, k));
    const msg = t.check?.(draft[k], draft);
    if (msg && (draft[k] !== undefined || (k === 'late_window_days' && lateError(draft)))) out[k] = msg;
  }
  return out;
}

// ------------------------------------------------------------------ the Overview form

/** `repo_name` as `settings.parse_instance` checks it, in its words. */
export function repoNameError(v: unknown): Record<string, string> {
  const t = typeof v === 'string' ? v.trim() : v == null ? '' : String(v);
  return t && !REPO_NAME_RE.test(t) ? { repo_name: 'Not a repo name (letters, digits, `.`, `_`, `-`); the schedule key names the repos instead.' } : {};
}

/** The late cutoff a due date and a late window give: due + N days, as the engine computes it. */
export function cutoffOf(dueDate: string, dueTime: string, days: unknown): string | null {
  if (!dueDate || typeof days !== 'number' || !Number.isInteger(days)) return null;
  const day = addDays(dueDate, days);
  return dueTime ? `${day}T${dueTime}` : day;
}

/** What showing the solution does, said beside every switch that schedules it, with the ? on reuse and returning marks. */
export function SolutionNote() {
  return (
    <p class="note">
      {SOLUTION_WARNING}{' '}
      <Hint small label="About pushing the solution" doc="07-schedule-releases.md#releasing-the-model-solution">
        Copies the template’s solution branch, the model answer and rubric, into every student’s repo, where it stays: nothing takes it back. Reuse this assignment template in a later semester and its answers are already out with this semester’s students. Marks go back without it: Return marks, on the assignment’s page, sends marks and feedback only.
      </Hint>
    </p>
  );
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
              <SolutionNote />
              <div class="row-2">{F('solutionDate', T('Solution shown on', 'date', { reason: 'After the hand out, and not before the late cutoff.' }), errors.solution)}{F('solutionTime', T('At', 'time'))}</div>
            </div>
          ) : null}
        </>
      )}
    </>
  );
}

export const LATE_MOVES = 'The due date has passed: this moves the late cutoff and the late penalties of this assignment, and the engine recomputes them.';

/** An entry's inline run settings and repo name, as the Overview form edits them. */
export const runValues = (d: AssignmentDraft): Values => toValues({ ...(d.run ?? {}), ...(d.repoName ? { repo_name: d.repoName } : {}) });

/** The draft with the form's run settings and repo name written back, each as the file spells it. */
export function withRun(d: AssignmentDraft, r: Values): AssignmentDraft {
  const run = Object.fromEntries(RUN_KEYS.filter((k) => r[k] !== undefined && r[k] !== '' && r[k] !== null).map((k) => [k, fileValue(k, r[k])]));
  return { ...d, run, repoName: typeof r.repo_name === 'string' ? r.repo_name.trim() : '' };
}

/**
 * The schedule's text with one inline run setting of assignment `key` set to `value`, or null
 * when its entry already says so. Throws when the file cannot be written as it stands.
 */
export function withRunSetting(f: YamlFile, key: string, setting: RunKey, value: unknown): string | null {
  if (f.error) throw new Error(`schedule.yml does not parse (${f.error}); nothing was saved`);
  const d = readDraft(f.doc, key);
  if (!d || d.kind !== 'assignments') throw new Error(`${key} is not in the schedule; nothing was saved`);
  if (deepEqual(d.run?.[setting], value)) return null;
  const y = new YamlText(f.text);
  writeDraft(y, { ...d, run: { ...(d.run ?? {}), [setting]: value } }, f.doc);
  if (!validSchedule(y.toJS() ?? {})) throw new Error('schedule.yml would not be valid; nothing was saved');
  return y.text === f.text ? null : y.text;
}

/** The assignment's Overview form: its schedule entry, dates and inline run settings, saved as one commit. */
export function AssignmentRun({ p, a, group }: { p: ReadyProps; a: Assignment; group: boolean }) {
  const env = useEnv();
  const tz = tzOf(p.status), year = yearOf(p.now, tz);
  const [timing, setTiming] = useState<AssignmentDraft | null>(null);
  const [run, setRun] = useState<Values | null>(null);
  const [save, runSave, setSave] = useSave(env);
  const sf = scheduleFile(p.files, p.cohort.org);
  if (sf === 'loading') return <section class="panel section"><h2>How this semester runs it</h2><Loading what="Reading the schedule" /></section>;
  if (!sf || sf.error) return <section class="panel section"><h2>How this semester runs it</h2><CheckLine cls="bad">The schedule could not be read{sf?.error ? ` (${sf.error})` : ''}; fix it with Edit the file directly.</CheckLine></section>;
  const baseT = readDraft(sf.doc, a.slug);
  if (!baseT || baseT.kind !== 'assignments') return <section class="panel section"><h2>How this semester runs it</h2><p class="footnote">{a.slug} is not in the schedule.</p></section>;
  const cfg = gradingConfig(p, a.template);
  const keys = applicableKeys(cfg, group);
  const beforeRun = runValues(baseT);
  const d = timing ?? baseT;
  const r = run ?? beforeRun;
  const below = entryBelow(p);
  const current = run === null ? a.settings : null;
  const own = usableBlock(r);
  const days = over('late_window_days', own, 'entry', current ?? below).value;
  const vis = forcedVisibility(cfg) ? 'private' : over('visibility', own, 'entry', current ?? below).value;
  const solutionOff = vis !== 'private' ? 'Not available: student repos are not private, so the solution cannot be pushed automatically.' : d.manual ? 'Needs a hand out time; the solution must follow it.' : null;
  const tErr = draftErrors(d, undefined, cutoffOf(d.dueDate, d.dueTime, typeof days === 'number' ? days : 0));
  const rErr = { ...runErrors(keys, below, r), ...repoNameError(r.repo_name) };
  const dirtyT = timing !== null && !deepEqual(timing, baseT);
  const dirtyR = run !== null && !deepEqual(compact(run), compact(beforeRun));
  // Past the due date the engine recomputes the cutoff and every late deduction off a changed late rule.
  const lateMoved = dirtyR && pastDue(a) && LATE_PAIR.some((k) => !deepEqual(compact({ k: r[k] }), compact({ k: beforeRun[k] })));
  const change = (patch: Partial<AssignmentDraft>) => { setTiming({ ...d, ...patch }); setSave({ kind: 'idle' }); };
  const doSave = async () => {
    if (Object.keys(tErr).length || Object.keys(rErr).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const y = new YamlText(sf.text);
    writeDraft(y, withRun({ ...d, solutionOn: d.solutionOn && !solutionOff }, r), sf.doc);
    if (!validSchedule(y.toJS())) return setSave({ kind: 'bad', text: invalidText('the schedule', validSchedule) });
    if (await runSave({ owner: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml' }, y.text, sf.sha, { message: `schedule: edit ${a.slug}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] })) {
      setTiming(null);
      setRun(null);
    }
  };
  const repoName = typeof r.repo_name === 'string' ? r.repo_name : '';
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
          <RunRows id="ov" keys={keys} below={below} current={current} draft={r} setDraft={(v) => { setRun(v); setSave({ kind: 'idle' }); }} errors={rErr}
            defaultsHref="#assignments" teamsHref={group ? `#assignment-${a.slug}/teams` : undefined} forced={forcedVisibility(cfg)} />
          <details class="fold" open={!!repoName}>
            <summary>Advanced <span class={`cnt${repoName ? ' changed' : ''}`}>({repoName ? '1 changed' : 'none changed'})</span></summary>
            <div class="fold-body">
              <Field id="ov-repo" k="repo_name" values={r} value={r.repo_name} set={(k, v) => { setRun({ ...r, [k]: v }); setSave({ kind: 'idle' }); }}
                error={rErr.repo_name}
                t={{ tier: 'advanced', label: 'Repo name in this semester', placeholder: a.slug, defaultLabel: `default: ${a.slug}`, reason: 'What this semester’s repos are called. Change it before the first hand out.' }} />
            </div>
          </details>
        </div>
        <div class="form-section">
          {lateMoved ? <CheckLine cls="warn">{LATE_MOVES}</CheckLine> : null}
          <SaveBar state={save} onSave={() => void doSave()} disabled={!dirtyT && !dirtyR} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml' }} />
          <p class="lives">Dates and run settings live in <code>{CONFIG_REPO}/schedule.yml</code>.</p>
        </div>
      </div>
    </section>
  );
}
