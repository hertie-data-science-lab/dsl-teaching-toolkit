// Readiness (decision 0034): every item has a need (needed or suggested), a state (done, open,
// problem, waiting), and a problem has a time (now, soon, later). The engine writes `need`,
// `bites`, `horizon` and `verdict` into status.json; a status the previous engine wrote lacks
// them, and these helpers derive each one then (contract C). Screens read the fields only here.

import type { Bites, CourseStatus, Horizon, MaterialsCheck, Need, Problem, StageState, Status, Todo, VerdictState } from './types';

/** Each step's need when the status does not say (contract B): C1-C3 and K1-K6 needed. */
const STEP_NEED: Record<string, Need> = {
  C1: 'needed', C2: 'needed', C3: 'needed', C4: 'suggested', C5: 'suggested', C6: 'suggested',
  K1: 'needed', K2: 'needed', K3: 'needed', K4: 'needed', K5: 'needed', K6: 'needed', K7: 'suggested',
};

/** A step that says what one of its scope's to-dos says: shown (and counted) once, as the to-do. */
const TWIN: Record<string, string> = { K7: 'schedule:archive_date' };

/** The horizon's length in days: the engine's, else 7. */
export const horizonDays = (h: Horizon | undefined) => h?.days ?? 7;

/**
 * When a problem bites. The engine's `bites`; else from its `when` and the console's clock:
 * none, or one already passed, is `now`; one within the horizon's days (7 by default) is
 * `soon`; a later one `later`.
 */
export function bitesOf(p: Problem, horizon: Horizon | undefined, now: number): Bites {
  if (p.bites) return p.bites;
  if (!p.when) return 'now';
  const at = Date.parse(p.when);
  if (at <= now) return 'now';
  return at <= now + horizonDays(horizon) * 864e5 ? 'soon' : 'later';
}

/** The problems that stand now or soon (red and counted); `later` ones wait in Coming up. */
export function standing(status: Status | undefined, now: number, scope?: 'course'): Problem[] {
  if (!status) return [];
  return (status.problems ?? []).filter((p) => (!scope || p.scope === scope) && bitesOf(p, status.horizon, now) !== 'later');
}

/** The one problem count (decision 0034): problems now or soon. Every count shown (nav, Home, Semesters rows, tabs) is this. */
export function problemCount(status: Status | undefined, now: number): number {
  return standing(status, now).length;
}

/** The problems beyond the horizon: "Coming up: n not ready yet". */
export function comingUp(status: Status | undefined, now: number, scope?: 'course'): Problem[] {
  if (!status) return [];
  return (status.problems ?? []).filter((p) => (!scope || p.scope === scope) && bitesOf(p, status.horizon, now) === 'later');
}

/** A check's or to-do's need: the engine's `need`; else `blocks` (a check), else `optional` (a to-do). */
export function needOf(x: { need?: Need; blocks?: boolean; optional?: boolean }): Need {
  if (x.need) return x.need;
  if (x.blocks !== undefined) return x.blocks ? 'needed' : 'suggested';
  return x.optional === true ? 'suggested' : 'needed';
}

type Scope = Pick<CourseStatus, 'stages'> & Partial<Pick<CourseStatus, 'stage_need' | 'stage_optional' | 'stage_set_aside' | 'stage_why'>>;

/** A setup step's need: `stage_need`, else `stage_optional`, else the contract's default. */
export function stepNeed(c: Scope, id: string): Need {
  const n = c.stage_need?.[id];
  if (n) return n;
  const opt = c.stage_optional?.[id];
  if (opt !== undefined) return opt ? 'suggested' : 'needed';
  return STEP_NEED[id] ?? 'needed';
}

export type ItemState = 'done' | 'open' | 'problem' | 'waiting';

/** A suggestion still open (or waiting): listed under Suggestions, counted as one. A problem is never a suggestion. */
export const isSuggestion = (need: Need, state: ItemState) => need === 'suggested' && (state === 'open' || state === 'waiting');

/** A step's row state. A stage flagged `problem` whose every problem is `later` reads as open (contract B). */
export function stepState(stage: StageState | undefined, problems: Bites[]): ItemState {
  if (stage === 'done') return 'done';
  if (stage === 'blocked') return 'waiting';
  if (stage === 'problem') return problems.length && problems.every((b) => b === 'later') ? 'open' : 'problem';
  return 'open';
}

/** Whether a setup step is set aside: suggested, not done, and listed (the engine's flag until the list is read). */
export function stepAside(c: Scope, id: string, list: string[] | null): boolean {
  if (stepNeed(c, id) !== 'suggested' || c.stages[id] === 'done') return false;
  return list ? list.includes(id) : !!c.stage_set_aside?.[id];
}

/** Whether a to-do is set aside: suggested and listed. A done one is not a to-do. */
export function todoAside(t: Pick<Todo, 'id' | 'need' | 'optional' | 'set_aside'> & { ids?: string[] }, list: string[] | null): boolean {
  if (needOf(t) !== 'suggested') return false;
  return list ? (t.ids ?? [t.id]).every((id) => list.includes(id)) : !!t.set_aside;
}

/** A sentence as the tail of "Not ready: ...": first letter lower-case, no full stop. */
export function missingClause(why: string): string {
  const s = why.trim().replace(/\.$/, '');
  return s ? s[0].toLowerCase() + s.slice(1) : 'not done yet';
}

/** The steps a scope shows: its own, less a step whose to-do twin is listed. */
export function shownSteps(ids: string[], todo: { id: string }[]): string[] {
  return ids.filter((id) => !(TWIN[id] && todo.some((t) => t.id === TWIN[id])));
}

/** What a verdict needs of a Setup & To do row (`ui/SetupPanel`'s `SetupItem`). */
export interface VerdictItem {
  kind: 'step' | 'todo';
  need: Need;
  state: ItemState;
  why?: string;
  aside?: boolean;
}

/** A course's or semester's verdict, as the console words it (decision 0034, amended). */
export interface Verdict {
  scope: 'course' | 'semester';
  /** The horizon's length, for the semester's words ("in the next 7 days"). */
  days: number;
  state: VerdictState['state'];
  /** Problems now or soon. */
  problems: number;
  /** The first open needed setup step's sentence. */
  missing: string | null;
  suggestions: number;
  coming_up: number;
}

/**
 * The verdict from the rows the panel shows and the problems' times. Fixing: a problem now or
 * soon. Not ready (course) / not set up (semester): a needed setup step open, named. Else ready
 * (course) / on track (semester). Coming up: on the course, the needed to-dos open and the
 * `later` problems; on the semester, what its Coming up fold lists (`coming`).
 * The verdict is computed here from the engine's per-item fields (`need`, `bites`, `stages`),
 * so it reads the same on a status the previous engine wrote.
 */
export function verdictOf(items: VerdictItem[], problems: Bites[], scope: 'course' | 'semester', coming?: number, days = 7): Verdict {
  const fixing = problems.filter((b) => b !== 'later').length;
  const step = items.find((i) => i.kind === 'step' && i.need === 'needed' && i.state !== 'done');
  const todos = items.filter((i) => i.kind === 'todo' && i.need === 'needed' && i.state !== 'done' && !i.aside).length;
  const state = fixing ? 'fixing' : step ? 'not_ready' : 'ready';
  return {
    scope,
    days,
    state,
    problems: fixing,
    missing: state === 'not_ready' ? step!.why ?? 'Not done yet.' : null,
    suggestions: items.filter((i) => isSuggestion(i.need, i.state) && !i.aside).length,
    coming_up: coming ?? todos + problems.filter((b) => b === 'later').length,
  };
}

/** Whether every needed setup step is done: the semester lede's "Setup complete.", the Setup fold's "Complete". */
export function setupComplete(items: VerdictItem[]): boolean {
  return items.every((i) => i.kind !== 'step' || isSuggestion(i.need, i.state) || i.aside || i.state === 'done');
}

// --------------------------------------------------------------------------- the syllabus row

/** The syllabus and its weekly plan are two engine checks (`syllabus`, `sessions`) and one row on screen. */
export const SYLLABUS_HINT = 'One file, SYLLABUS.md, two parts: your own text, and a weekly plan the console writes into it from the schedule (Write, on the materials settings page). Students get the file when a release names it.';
export const SYLLABUS_LABEL = 'Syllabus';
const PLAN_CLAUSE = 'the weekly plan (written from the schedule) is not in it yet';

/** The syllabus row's why: the unmet part, or both joined. */
export function syllabusWhy(syllabus: string | null | undefined, plan: string | null | undefined): string | undefined {
  if (syllabus && plan) return `${syllabus.trim().replace(/\.$/, '')}, and ${PLAN_CLAUSE}.`;
  return syllabus || plan || undefined;
}

const mergedNeed = (a: Need, b: Need): Need => (a === 'needed' || b === 'needed' ? 'needed' : 'suggested');

/** A materials repo's checks with `syllabus` and `sessions` as one row (id `syllabus`). */
export function displayChecks(checks: MaterialsCheck[]): MaterialsCheck[] {
  const syl = checks.find((c) => c.id === 'syllabus'), plan = checks.find((c) => c.id === 'sessions');
  if (!syl || !plan) return checks;
  const one: MaterialsCheck = {
    id: 'syllabus', label: SYLLABUS_LABEL, done: syl.done && plan.done, blocks: syl.blocks || plan.blocks,
    // Needed only while a needed part is open (a done syllabus and an open plan is a suggestion).
    need: syl.done === plan.done ? mergedNeed(needOf(syl), needOf(plan)) : needOf(syl.done ? plan : syl),
    why: syllabusWhy(syl.done ? null : syl.why ?? syl.label, plan.done ? null : plan.why ?? plan.label) ?? null,
  };
  return checks.flatMap((c) => (c === syl ? [one] : c === plan ? [] : [c]));
}

/** A to-do as the panel lists it: the syllabus and weekly-plan to-dos of one repo are one, carrying both ids. */
export type ShownTodo = Todo & { ids: string[] };

export function displayTodos(todo: Todo[]): ShownTodo[] {
  const part = (t: Todo) => (t.kind === 'materials' ? /:(syllabus|sessions)$/.exec(t.id)?.[1] : undefined);
  const out: ShownTodo[] = [];
  for (const t of todo) {
    const k = part(t);
    if (!k) { out.push({ ...t, ids: [t.id] }); continue; }
    if (out.some((x) => x.repo === t.repo && x.ids.some((i) => part({ ...t, id: i })))) continue;
    const syl = todo.find((x) => x.repo === t.repo && part(x) === 'syllabus'), plan = todo.find((x) => x.repo === t.repo && part(x) === 'sessions');
    const both = [syl, plan].filter((x): x is Todo => !!x);
    out.push({
      ...t,
      id: (syl ?? t).id,
      ids: both.map((x) => x.id),
      text: syllabusWhy(syl?.text, plan?.text) ?? t.text,
      need: both.map(needOf).reduce(mergedNeed),
      set_aside: both.every((x) => x.set_aside),
    });
  }
  return out;
}

/** A materials repo's checklist read for its chip: the first needed check open, the suggestions open. */
export interface RepoReadiness {
  state: 'problem' | 'not_ready' | 'ready';
  missing: string | null;
  suggestions: number;
}

export function materialsReadiness(m: { state: string; checks?: MaterialsCheck[] }): RepoReadiness {
  const checks = displayChecks(m.checks ?? []);
  const first = checks.find((c) => needOf(c) === 'needed' && !c.done);
  const suggestions = checks.filter((c) => needOf(c) === 'suggested' && !c.done).length;
  const state = m.state === 'problem' ? 'problem' : m.state === 'ready' ? 'ready' : 'not_ready';
  return { state, missing: state === 'not_ready' ? (first ? first.why ?? first.label : 'Not ready yet.') : null, suggestions };
}

/** A template's chip: a standing problem; else its first needed to-do open (or a later problem's sentence); else ready. */
export function templateReadiness(t: { repo: string; state: string }, todo: Todo[], problems: { text: string; b: Bites }[]): RepoReadiness {
  const open = todo.find((x) => x.kind === 'template' && x.repo === t.repo && needOf(x) === 'needed');
  const later = problems.length > 0 && problems.every((x) => x.b === 'later');
  if (t.state === 'problem' && !later) return { state: 'problem', missing: null, suggestions: 0 };
  if (open) return { state: 'not_ready', missing: open.text, suggestions: 0 };
  if (t.state === 'problem') return { state: 'not_ready', missing: problems[0].text, suggestions: 0 };
  if (t.state !== 'ready') return { state: 'not_ready', missing: 'The brief (README.md) is not written yet.', suggestions: 0 };
  return { state: 'ready', missing: null, suggestions: 0 };
}

/** A template's state as read from a status that carries the course block (the course's, or a semester's copy). */
export function templateReadinessIn(status: Status | undefined, repo: string, now: number): RepoReadiness | null {
  const t = status?.course?.templates?.find((x) => x.repo === repo);
  if (!t) return null;
  const problems = (status!.problems ?? []).filter((x) => x.scope === 'course' && x.fix?.entry === repo).map((x) => ({ text: x.text, b: bitesOf(x, status!.horizon, now) }));
  return templateReadiness(t, status!.course!.todo ?? [], problems);
}

/** A repo's state in words, the same everywhere (decision 0034): "Ready", "Not ready: <why>", "Has a problem". */
export function repoWords(r: RepoReadiness): string {
  return r.state === 'problem' ? 'Has a problem' : r.state === 'ready' ? 'Ready' : `Not ready: ${missingClause(r.missing ?? '')}`;
}

// --------------------------------------------------------------------------- a release row's mark

/** A schedule label as the engine slugs it into a problem id (`status_json._slugify`). */
const slugOf = (s: string) => s.replace(/[^A-Za-z0-9._-]+/g, '-').replace(/^-+|-+$/g, '') || 'file';

/**
 * The problem a release stands on: its `schedule:<entry>:...` problem (a folder not found, a
 * placeholder left out, `LATE`), else the `number:<kind>:<entry>` one holding it for a number.
 * Its kind and duplicate-number problems do not stop the release, so they are not it.
 */
export function problemForRelease(status: Status | undefined, releaseId: string): Problem | undefined {
  const slug = slugOf(releaseId);
  const own = (status?.problems ?? []).filter((p) => p.scope === 'semester');
  return own.find((p) => p.id.startsWith(`schedule:${slug}:`))
    ?? own.find((p) => p.id.startsWith('number:') && p.id.endsWith(`:${slug}`) && p.fix?.entry === releaseId);
}

/** A held release's chip by when its problem bites (decision 0034's time words). */
export const SKIP_WORD: Record<Bites, string> = { now: 'was skipped', soon: 'will be skipped', later: 'not ready yet' };

/** What a release row marks: its chip word, when it bites, and the problem (absent on a status that names none). */
export interface ReleaseMark {
  word: string;
  bites: Bites;
  /** Late with nothing else to say why: the row links its Details, not a Fix. */
  late: boolean;
  problem?: Problem;
}

/**
 * A release row's mark (decision 0034 §6), or null for one with nothing to fix. A late release
 * with no other problem (`schedule:<entry>:LATE`) is "late"; a held one takes its problem's
 * time word: was skipped (now), will be skipped (soon), not ready yet (later).
 */
export function releaseMark(status: Status, rel: { id: string; state: string; when: string | null }, now: number): ReleaseMark | null {
  if (rel.state !== 'will_be_skipped' && rel.state !== 'late') return null;
  const problem = problemForRelease(status, rel.id);
  if (rel.state === 'late' && (!problem || problem.id.endsWith(':LATE'))) return { word: 'late', bites: 'now', late: true, problem };
  const bites = bitesOf(problem ?? { id: '', scope: 'semester', stage: '', text: '', stops: '', when: rel.when ?? undefined }, status.horizon, now);
  return { word: SKIP_WORD[bites], bites, late: false, problem };
}
