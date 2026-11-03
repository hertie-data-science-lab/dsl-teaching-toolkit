// How a semester runs its assignments (decisions 0009 and 0039): the semester's `defaults:`
// (semester.yml) on the Assignments index, and each assignment's Overview form, which edits its
// schedule entry, timings and inline run settings, as one form (decision 0038 rule 9). Every
// run setting shows its effective value and where it comes from; a save writes only the keys
// the instructor set.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { invalidText, useSave } from '../edit/save';
import { YamlText, compact, deepEqual } from '../edit/yamlText';
import { Field } from '../forms/Form';
import { MODAL_TEXT_ID, Modal } from '../ui/Modal';
import {
  REPO_NAME_RE, RUN_KEYS, semesterName, SOURCE_WORD, courseDefaults, effectiveOf, effectiveWord, lateWord, over, scheduleFile, semesterDefaults, usableBlock,
  fileValue, validSchedule, validSemesterBlocks, validSemesterFile, valueWord, type Block, type Effectives, type RunKey, type YamlFile,
} from '../model/settings';
import { semesterInheritance, statedDefaults, withDefaults } from '../model/config';
import { addDays, assignmentTitle, fmtDay, fmtWhen } from '../model/format';
import { CONFIG_REPO, SEMESTER_FILE } from '../model/names';
import { draftErrors, newlyManual, readDraft, writeDraft, type AssignmentDraft, type Draft } from '../model/scheduleEdit';
import { handout, type AsgRef } from '../ops/defs';
import type { OpDef } from '../ops/session';
import { OpButtons } from '../ops/Panel';
import { SOLUTION_WARNING } from '../model/labels';
import type { Assignment } from '../model/types';
import { isGroup, pastDue } from '../model/assignment';
import { penaltyRate } from '../model/policy';
import { round } from '../model/marks';
import { RUN_LABEL, runTier } from '../tiers/runSettings';
import type { FieldTier, Values } from '../tiers/types';
import { CheckLine, Loading } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { SaveBar } from '../ui/edit';
import { cohortScope, gradingConfig, tzOf, yearOf } from './common';
import type { ReadyProps } from './types';



/** What an assignment's own settings fall back to: the semester's effective defaults (status.json). */
export const entryBelow = (p: Pick<ReadyProps, 'status'>): Effectives => semesterDefaults(p.status);

/** The semester-side name of the assignment with schedule key `key` (`schedule.semester_name`): what its mark
 *  sheet and Return marks key on. The schedule entry's `repo_name`, else the key while the schedule is read. */
export function sheetName(p: Pick<ReadyProps, 'files' | 'cohort'>, key: string): string {
  const f = scheduleFile(p.files, p.cohort.org);
  return f && f !== 'loading' ? semesterName(f.doc, key) : key;
}

/** The assignment as Hand out now names it, wherever it is offered. */
export function handoutRef(p: Pick<ReadyProps, 'files' | 'cohort'>, a: Pick<Assignment, 'slug' | 'title' | 'number' | 'template' | 'units' | 'handout'>, group: boolean, tz: string, year: number): AsgRef {
  return { slug: a.slug, title: assignmentTitle(a), template: a.template, units: a.units, group, when: a.handout ? `Scheduled ${fmtDay(a.handout, tz, year)}` : 'Hand out by hand', name: sheetName(p, a.slug) };
}

/** Whether an assignment set to hand out by hand is still waiting for Hand out now. */
export const waitsByHand = (a: Pick<Assignment, 'state' | 'handout'>) => a.state === 'declared' && !a.handout;

/** The tag on an assignment that `waitsByHand` (Schedule, Assignments). */
export const ManualWaiting = () => <span class="chip amber">Manual hand-out: not handed out yet</span>;

/** Hand out now for an assignment not yet handed out: one button, its panel the confirmation. */
export function HandOutNow({ p, a, small }: { p: ReadyProps; a: Assignment; small?: boolean }) {
  const tz = tzOf(p.status), year = yearOf(p.now, tz);
  return <OpButtons def={handout(cohortScope(p), handoutRef(p, a, isGroup(a), tz, year))} label="Hand out now" small={small} />;
}

/**
 * The assignment to offer Hand out now for beside an entry's hand out choice: only a saved entry
 * handed out by hand, its choice unchanged in the draft, that the engine still reports declared.
 * Never a draft: the op hands out what schedule.yml says.
 */
export function waitingHandOut(a: Assignment | undefined, saved: Draft | null, d: Draft): Assignment | null {
  return a?.state === 'declared' && saved?.kind === 'assignments' && saved.manual && d.kind === 'assignments' && d.manual ? a : null;
}

/**
 * Hand out for an entry just saved, from its draft: status.json lags the save, and the op
 * needs only the template (the engine reads the entry from schedule.yml as committed).
 */
export function draftHandout(p: ReadyProps, d: AssignmentDraft): OpDef {
  const cfg = gradingConfig(p, d.template);
  const tz = tzOf(p.status), year = yearOf(p.now, tz);
  const a = { slug: d.id, title: String(cfg.title ?? ''), number: typeof d.number === 'number' ? d.number : null, template: d.template, units: 0, handout: null };
  return handout(cohortScope(p), handoutRef(p, a, cfg.type === 'group', tz, year));
}

/**
 * The question a Save asks when it newly leaves an entry with no hand out date, on the
 * Schedule and the Overview alike. `one` (exactly one such entry) offers Save and hand out
 * now; `onSave(true)` saves, then opens Hand out on it.
 */
export function HandOutDialog({ one, onSave, onClose }: { one: boolean; onSave: (handOut: boolean) => void; onClose: () => void }) {
  const it = one ? 'it is' : 'they are';
  return (
    <Modal small title="Hand out now?" onClose={onClose}>
      <p id={MODAL_TEXT_ID}>You chose to hand out by hand. Until {it} handed out students see no brief and there is no mark sheet.</p>
      <div class="actions">
        {/* Several at once: each is handed out from its own page. */}
        {one ? <button class="btn" type="button" onClick={() => { onClose(); onSave(true); }}>Save and hand out now</button> : null}
        <button class={one ? 'btn outline' : 'btn'} type="button" onClick={() => { onClose(); onSave(false); }}>Save, hand out later</button>
        <button class="btn quiet" type="button" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/** A `defaults:` block as form values: a null (stated empty) is unset. */
export const toValues = (b: Block): Values => Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v === null ? undefined : v]));

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

/** What this semester's own defaults fall back to: the course's effective values (the status's course block), else the semester's answer where it is not the semester's own. */
export const semesterInherited = (p: Pick<ReadyProps, 'status'>): Effectives => semesterInheritance(courseDefaults(p.status), semesterDefaults(p.status));

/** semester.yml with its `defaults:` stating `after` (form values), or why it would not be valid. */
export function semesterDefaultsAfter(f: YamlFile, after: Values, inherited: Effectives): { text: string } | { error: string } {
  const text = withDefaults(f.text, statedDefaults(after), inherited);
  return validSemesterBlocks(new YamlText(text).toJS(), ['defaults']) ? { text } : { error: invalidText(SEMESTER_FILE, validSemesterFile) };
}

// ------------------------------------------------------------------ one assignment's run settings

/** Where the work is handed in: the entry's `submit_via` override, else the template's. */
const routeOf = (cfg: Record<string, unknown>, submitVia?: string) => submitVia || cfg.submit_via || 'assignment_repo';

/** Whether the work is handed in outside GitHub (`submit_via: external`, `submitVia` the entry's override): no repo, so no late window and no visibility. */
export const isExternal = (cfg: Record<string, unknown>, submitVia?: string) => routeOf(cfg, submitVia) === 'external';

/** `course.can_hold_solution`: a unit's own private repo, or the shared drop box (always private). */
export function canHoldSolution(cfg: Record<string, unknown>, visibility: unknown, submitVia?: string): boolean {
  const via = routeOf(cfg, submitVia);
  return via === 'shared_dropbox_repo' || (via === 'assignment_repo' && visibility === 'private');
}

/** The late window that sets the cutoff: none for external work, whatever the cascade says. */
export const windowDays = (cfg: Record<string, unknown>, days: unknown, submitVia?: string) => (isExternal(cfg, submitVia) ? 0 : days);

/** The late line for external work, wherever its cutoff would be. */
export const NO_LATE_WINDOW = 'No late window: handed in outside GitHub, so the cutoff is the due date.';

/**
 * Which run settings apply to an assignment of this template: teams only for a team assignment;
 * for external work the link, the late penalty without a window, and no visibility (no repo).
 */
export function applicableKeys(cfg: Record<string, unknown>, group: boolean, submitVia?: string): RunKey[] {
  const ext = isExternal(cfg, submitVia);
  return RUN_KEYS.filter((k) => (k === 'team_formation' || k === 'max_team_size' ? group : k === 'submit_url' ? ext : k === 'late_window_days' || k === 'visibility' ? !ext : true));
}

/** Why the template's shape fixes the visibility, or null. */
export function forcedVisibility(cfg: Record<string, unknown>): string | null {
  return cfg.submit_via === 'shared_dropbox_repo' ? 'Private: a shared drop box is always private.' : null;
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
  teamsHref?: string;
  forced?: string | null;
}

/**
 * Each run setting as its effective value and source, with "Change for this assignment" opening
 * the field. The external link has no default: its row is the field. The semester's defaults
 * are changed from the Assignments page, never per row.
 */
export function RunRows({ id, keys, below, current, draft, setDraft, errors, teamsHref, forced }: RunRowsProps) {
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
        const field = (k: RunKey) => (
          <Field id={`${id}-${k}`} k={k} t={runTier(k, effectiveOf(below, k), { override: true }) as FieldTier} value={draft[k]} values={draft} error={errors[k]}
            set={(key, v) => setDraft({ ...draft, [key]: v })} />
        );
        if (r.name === 'submit_url') return <div class="run-row">{field('submit_url')}</div>;
        const open = opened.includes(r.name) || r.keys.some(has);
        const late = r.name === 'late';
        // A penalty with no window: work handed in outside GitHub, its days late entered by the marker.
        const marked = r.name === 'late_penalty_per_day';
        const eff = mine(r.keys[0]);
        // The window and the penalty inherit each on its own: a penalty from another layer says so.
        const pen = mine('late_penalty_per_day');
        const penFrom = pen.value && pen.source !== eff.source && lateWord(eff.value, pen.value) !== 'no late work' ? ` (penalty: ${SOURCE_WORD[pen.source]})` : '';
        const effText = late ? `${lateWord(eff.value, pen.value)}, ${SOURCE_WORD[eff.source]}${penFrom}` : effectiveWord(r.keys[0], eff);
        const label = late ? 'Late work' : marked ? 'Late penalty' : RUN_LABEL[r.keys[0]];
        const teams = r.name === 'team_formation' ? teamsHref : undefined;
        if (r.name === 'visibility' && forced)
          return <div class="field"><span class="label">{label}</span><div class="readonly">{forced}</div></div>;
        return (
          <div class="field run-row">
            <span class="label">{label}</span>
            <div class="eff" data-key={r.name}>{effText}</div>
            {marked ? <p class="why">Applied to the days late you enter on the Marks tab. No late window: work is handed in outside GitHub.</p> : null}
            {open ? (
              <div class="cond">
                {r.keys.map(field)}
                <button class="textlink" type="button" onClick={() => { setOpened(opened.filter((x) => x !== r.name)); setDraft(Object.fromEntries(Object.entries(draft).filter(([k]) => !r.keys.includes(k as RunKey)))); }}>Use the default</button>
              </div>
            ) : null}
            {!open || teams ? (
              <p class="run-links">
                {!open ? <button class="textlink" type="button" onClick={() => setOpened([...opened, r.name])}>Change for this assignment</button> : null}
                {teams ? <a class="textlink" href={teams}>Open Teams</a> : null}
              </p>
            ) : null}
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
    if (msg && draft[k] !== undefined) out[k] = msg;
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
function Timings({ d, set, errors, cutoff, days, lateOk, external, solutionOff, tz, year, handOut }: {
  d: AssignmentDraft; set: (patch: Partial<AssignmentDraft>) => void; errors: Record<string, string>; cutoff: string | null; days: unknown; lateOk: boolean; external: boolean; solutionOff: string | null; tz: string; year: number; handOut?: preact.ComponentChildren;
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
          <label class="choice"><input type="radio" name="ov-ho" checked={d.manual} onChange={() => set({ manual: true })} /><b>I will hand out manually</b><span>Hand out now when you save, or later from this page.</span></label>
        </div>
        {handOut}
      </div>
      {!d.manual ? <div class="cond"><div class="row-2">{F('handoutDate', T('Hand out on', 'date'), errors.handout)}{F('handoutTime', T('At', 'time'))}</div></div> : null}
      <div class="row-2">{F('dueDate', T('Due', 'date', { tier: 'ask' }), errors.due)}{F('dueTime', T('At', 'time', { tier: 'ask' }))}</div>
      {external ? (
        <div class="field"><span class="label">Late cutoff</span><div class="readonly">{NO_LATE_WINDOW}</div></div>
      ) : (
        <div class="field">
          <span class="label">Late cutoff <span class="default">computed</span></span>
          <div class="readonly">{!lateOk ? 'No late work: the cutoff is the due date.' : cutoff ? `${fmtWhen(cutoff, tz, year)} (due + ${String(days)} day${days === 1 ? '' : 's'})` : 'Set the due date first.'}</div>
          <p class="why">What is on main at the late cutoff is what is marked. Change it with the late window below.</p>
        </div>
      )}
      {external ? null : solutionOff ? (
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
  const [askManual, setAskManual] = useState(false);
  const sf = scheduleFile(p.files, p.cohort.org);
  if (sf === 'loading') return <section class="panel section"><h2>How this semester runs it</h2><Loading what="Reading the schedule" /></section>;
  if (!sf || sf.error) return <section class="panel section"><h2>How this semester runs it</h2><CheckLine cls="bad">The schedule could not be read{sf?.error ? ` (${sf.error})` : ''}; fix it with Edit the file directly.</CheckLine></section>;
  const baseT = readDraft(sf.doc, a.slug);
  if (!baseT || baseT.kind !== 'assignments') return <section class="panel section"><h2>How this semester runs it</h2><p class="footnote">{a.slug} is not in the schedule.</p></section>;
  const cfg = gradingConfig(p, a.template);
  const d = timing ?? baseT;
  // The entry's own route as saved: the run settings that apply follow it.
  const keys = applicableKeys(cfg, group, baseT.submitVia);
  const beforeRun = runValues(baseT);
  const r = run ?? beforeRun;
  const below = entryBelow(p);
  const current = run === null ? a.settings : null;
  const own = usableBlock(r);
  const external = isExternal(cfg, d.submitVia);
  const days = windowDays(cfg, over('late_window_days', own, 'entry', current ?? below).value, d.submitVia);
  const vis = forcedVisibility({ ...cfg, submit_via: d.submitVia || cfg.submit_via }) ? 'private' : over('visibility', own, 'entry', current ?? below).value;
  const solutionOff = !canHoldSolution(cfg, vis, d.submitVia) ? 'Not available: student repos are not private, so the solution cannot be pushed automatically.' : d.manual ? 'Needs a hand out time; the solution must follow it.' : null;
  const tErr = draftErrors(d, undefined, cutoffOf(d.dueDate, d.dueTime, typeof days === 'number' ? days : 0));
  const rErr = { ...runErrors(keys, below, r), ...repoNameError(r.repo_name) };
  const dirtyT = timing !== null && !deepEqual(timing, baseT);
  const dirtyR = run !== null && !deepEqual(compact(run), compact(beforeRun));
  // Past the due date the engine recomputes the cutoff and every late deduction off a changed late rule.
  const lateMoved = dirtyR && pastDue(a) && (['late_window_days', 'late_penalty_per_day'] as const).some((k) => !deepEqual(compact({ [k]: r[k] }), compact({ [k]: beforeRun[k] })));
  const change = (patch: Partial<AssignmentDraft>) => { setTiming({ ...d, ...patch }); setSave({ kind: 'idle' }); };
  const hasErrors = Object.keys(tErr).length > 0 || Object.keys(rErr).length > 0;
  /** Save the entry; `handOut` then opens Hand out on it once the save lands. */
  const doSave = async (handOut = false) => {
    if (hasErrors) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const y = new YamlText(sf.text);
    writeDraft(y, withRun({ ...d, solutionOn: d.solutionOn && !solutionOff }, r), sf.doc);
    if (!validSchedule(y.toJS())) return setSave({ kind: 'bad', text: invalidText('the schedule', validSchedule) });
    if (!(await runSave({ owner: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml' }, y.text, sf.sha, { message: `schedule: edit ${a.slug}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] }))) return;
    setTiming(null);
    setRun(null);
    if (handOut) env?.ops.open(draftHandout(p, d));
  };
  // An entry newly handed out by hand asks first, as the Schedule's Save does.
  const requestSave = () => (dirtyT && !hasErrors && newlyManual(d, baseT, a.state) ? setAskManual(true) : void doSave());
  const repoName = typeof r.repo_name === 'string' ? r.repo_name : '';
  return (
    <section class="panel section" aria-label="How this semester runs it" id="run-settings">
      <div class="section-head"><h2>How this semester runs it</h2><a class="textlink" href={`#schedule-${a.slug}`}>Details and the site row</a></div>
      <div class="form">
        <div class="form-section">
          <h3>Dates</h3>
          <Timings d={d} set={change} errors={tErr} cutoff={cutoffOf(d.dueDate, d.dueTime, days)} days={days} lateOk={typeof days === 'number' && days > 0} external={external} solutionOff={solutionOff} tz={tz} year={year}
            handOut={waitingHandOut(a, baseT, d) ? <div class="actions"><HandOutNow p={p} a={a} small /></div> : null} />
        </div>
        <div class="form-section">
          <h3>Run settings</h3>
          <RunRows id="ov" keys={keys} below={below} current={current} draft={r} setDraft={(v) => { setRun(v); setSave({ kind: 'idle' }); }} errors={rErr}
            teamsHref={group ? `#assignment-${a.slug}/teams` : undefined} forced={forcedVisibility({ ...cfg, submit_via: d.submitVia || cfg.submit_via })} />
          <details class="fold" open={!!repoName}>
            <summary>Advanced <span class={`cnt${repoName ? ' changed' : ''}`}>({repoName ? '1 changed' : 'none changed'})</span></summary>
            <div class="fold-body">
              <Field id="ov-repo" k="repo_name" values={r} value={r.repo_name} set={(k, v) => { setRun({ ...r, [k]: v }); setSave({ kind: 'idle' }); }}
                error={rErr.repo_name}
                t={{
                  tier: 'advanced', placeholder: a.slug, defaultLabel: `default: ${a.slug}`,
                  label: external ? 'Name in this semester (sheet and site page)' : 'Repo name in this semester',
                  reason: `What this semester’s ${external ? 'mark sheet, site page and gradebook entries are' : 'repos are'} called. Change it before the first hand out.`,
                }} />
            </div>
          </details>
        </div>
        <div class="form-section">
          {lateMoved ? <CheckLine cls="warn">{LATE_MOVES}</CheckLine> : null}
          <SaveBar state={save} onSave={requestSave} disabled={!dirtyT && !dirtyR} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml' }} />
          <p class="lives">Dates and run settings live in <code>{CONFIG_REPO}/schedule.yml</code>.</p>
        </div>
      </div>
      {askManual ? <HandOutDialog one onSave={(h) => void doSave(h)} onClose={() => setAskManual(false)} /> : null}
    </section>
  );
}
