// Readiness (decision 0034): every item has a need (needed or suggested), a state (done, open,
// problem, waiting), and a problem has a time (now, soon, later). The engine writes `need`,
// `bites`, `horizon` and `verdict` into status.json; `normalise` fills `bites` and `need` once,
// at load, for a file the previous engine wrote (contract C). Every helper below and every
// screen trusts the fields.

import { DAY } from './week';
import type { Bites, CourseStatus, Horizon, MaterialsCheck, Need, Problem, Release, SemesterStatus, Status, Todo, VerdictState } from './types';

/** Each step's need when the status does not say (contract B): C1-C3 and K1-K6 needed. */
const STEP_NEED: Record<string, Need> = { C4: 'suggested', C5: 'suggested', C6: 'suggested' };

/** The horizon's length in days: the engine's, else 7. */
export const horizonDays = (h: Horizon | undefined) => h?.days ?? 7;

/** When a moment bites by the console's clock: none, or one already passed, is `now`; one within the horizon's days `soon`; a later one `later`. */
export function bitesAt(when: string | null | undefined, horizon: Horizon | undefined, now: number): Bites {
  if (!when) return 'now';
  const at = Date.parse(when);
  if (at <= now) return 'now';
  return at <= now + horizonDays(horizon) * DAY ? 'soon' : 'later';
}

/** A check's or to-do's need on a status the previous engine wrote: `blocks` (a check), else `optional` (a to-do). */
const needOf = (x: { need?: Need; blocks?: boolean; optional?: boolean }): Need =>
  x.need ?? (x.blocks !== undefined ? (x.blocks ? 'needed' : 'suggested') : x.optional === true ? 'suggested' : 'needed');

/** A scope's `stage_need`, else from `stage_optional`, else the contract's default. */
function stageNeed(c: Pick<CourseStatus, 'stages' | 'stage_need' | 'stage_optional'>): Record<string, Need> {
  if (c.stage_need) return c.stage_need;
  return Object.fromEntries(Object.keys(c.stages).map((id) => {
    const opt = c.stage_optional?.[id];
    return [id, opt === undefined ? STEP_NEED[id] ?? 'needed' : opt ? 'suggested' : 'needed'];
  }));
}

/**
 * The one normalisation, at load (contract C): a status the previous engine wrote gets `bites`
 * (off `when` and the horizon), each check's and to-do's `need`, and each scope's `stage_need`.
 * A status today's engine wrote passes through unchanged.
 */
export function normalise(status: Status, now: number): Status {
  const todo = (list: Todo[] | undefined) => list?.map((t) => (t.need ? t : { ...t, need: needOf(t) }));
  const course = status.course && {
    ...status.course,
    stage_need: stageNeed(status.course),
    // A previous engine's course block may carry no materials or templates list.
    materials: (status.course.materials ?? []).map((m) => ({ ...m, checks: m.checks?.map((c): MaterialsCheck => (c.need ? c : { ...c, need: needOf(c) })) })),
    templates: status.course.templates ?? [],
    todo: todo(status.course.todo),
  };
  const semester: SemesterStatus | undefined = status.semester && { ...status.semester, stage_need: stageNeed(status.semester), todo: todo(status.semester.todo) };
  const problems = status.problems?.map((p) => (p.bites ? p : { ...p, bites: bitesAt(p.when, status.horizon, now) }));
  return { ...status, ...(course ? { course } : {}), ...(semester ? { semester } : {}), ...(problems ? { problems } : {}) };
}

/** An ISO moment's own UTC offset in ms: its `±hh:mm` suffix; 0 for `Z` or none. */
function offsetOf(iso: string): number {
  const m = /T.*([+-])(\d{2}):(\d{2})$/.exec(iso);
  return m ? (m[1] === '-' ? -1 : 1) * (Number(m[2]) * 60 + Number(m[3])) * 60000 : 0;
}

/** The instant an ISO moment names; one with no offset is wall-clock time, read as UTC so its wall day stands. */
const instantOf = (iso: string) => Date.parse(iso.includes('T') && !/(Z|[+-]\d{2}:\d{2})$/.test(iso) ? `${iso}Z` : iso);

/** The day (yyyy-mm-dd) instant `ms` falls on at `iso`'s own offset: the engine writes each moment in the semester's. */
export const dayAt = (ms: number, iso: string) => new Date(ms + offsetOf(iso)).toISOString().slice(0, 10);

/** The day (yyyy-mm-dd, at the moment's own offset) its problem starts to count: the horizon's days before it. */
export function problemFromDay(iso: string, horizon: Horizon | undefined): string {
  return dayAt(instantOf(iso) - horizonDays(horizon) * DAY, iso);
}

/** A problem with when it bites. */
export type Tiered = { p: Problem; b: Bites };

/** Every problem of a status with its time, worked out once per render (`useMemo` on [status, now]). */
export function tier(status: Status | undefined, now: number, scope?: 'course'): Tiered[] {
  return (status?.problems ?? []).filter((p) => !scope || p.scope === scope).map((p) => ({ p, b: p.bites ?? bitesAt(p.when, status!.horizon, now) }));
}

/** The problems that stand now or soon (red and counted); `later` ones wait in Coming up. */
export const standing = (t: Tiered[]): Problem[] => t.filter((x) => x.b !== 'later').map((x) => x.p);
/** The problems beyond the horizon: "Coming up: n not ready yet". */
export const later = (t: Tiered[]): Problem[] => t.filter((x) => x.b === 'later').map((x) => x.p);

/** The one problem count (decision 0034): problems now or soon. Every count shown (nav, Home, Semesters rows, tabs) is this. */
export function problemCount(status: Status | undefined, now: number): number {
  return standing(tier(status, now)).length;
}

export type ItemState = 'done' | 'open' | 'problem' | 'waiting';

/** A suggestion still open (or waiting): listed under Suggestions, counted as one. A problem is never a suggestion. */
export const isSuggestion = (need: Need, state: ItemState) => need === 'suggested' && (state === 'open' || state === 'waiting');

/** A step's row state off its stage's. */
export const stepState = (stage: string | undefined): ItemState => (stage === 'done' ? 'done' : stage === 'blocked' ? 'waiting' : stage === 'problem' ? 'problem' : 'open');

/** Whether a setup step is set aside: suggested, not done, and listed (the engine's flag until the list is read). */
export function stepAside(c: Pick<CourseStatus, 'stages' | 'stage_need' | 'stage_set_aside'>, id: string, list: string[] | null): boolean {
  if (c.stage_need?.[id] !== 'suggested' || c.stages[id] === 'done') return false;
  return list ? list.includes(id) : !!c.stage_set_aside?.[id];
}

/** Whether a to-do is set aside: suggested and listed. A done one is not a to-do. */
export function todoAside(t: Pick<Todo, 'id' | 'need' | 'set_aside'>, list: string[] | null): boolean {
  if (t.need !== 'suggested') return false;
  return list ? list.includes(t.id) : !!t.set_aside;
}

/** What a step row needs of the panel (`ui/SetupPanel`'s `SetupItem`), without its import. */
export interface StepRow {
  id: string;
  label: string;
  kind: 'step';
  need: Need;
  state: ItemState;
  why?: string;
  hint?: string;
  hintLabel?: string;
  link?: { href: string; label: string; ext?: boolean; aria?: string };
  fix?: string;
  aside?: boolean;
}

/**
 * Both dashboards' setup steps (decision 0034): each step's state off its stage, its why the
 * engine's `stage_why` (the problem standing against it, the step it waits for, or what it
 * lacks), a Fix to the first problem now or soon, and the screen where an open one is done.
 */
export function stepItems(
  scope: Pick<CourseStatus, 'stages' | 'stage_why' | 'stage_need' | 'stage_set_aside'>,
  steps: { id: string; name: string }[],
  tiered: Tiered[],
  list: string[] | null,
  opts: { hint: Record<string, string>; link: (id: string) => StepRow['link']; fixHref: (p: Problem) => string | null },
): StepRow[] {
  return steps.map(({ id, name }) => {
    const state = stepState(scope.stages[id]);
    const first = state === 'problem' ? tiered.find((x) => x.p.stage === id && x.b !== 'later')?.p : undefined;
    const why = scope.stage_why?.[id];
    return {
      id, label: name, kind: 'step', need: scope.stage_need?.[id] ?? 'needed', state, hint: opts.hint[id], hintLabel: 'About this step',
      why: state === 'done' ? undefined : first?.text ?? why ?? (state === 'open' ? 'Not done yet.' : undefined),
      link: state === 'open' ? opts.link(id) : undefined,
      fix: first ? opts.fixHref(first) ?? undefined : undefined,
      aside: stepAside(scope, id, list),
    };
  });
}

/** The open suggestions not set aside: the verdict's "· n suggestions" tag, counted from the rows as shown. */
export const suggestionsCount = (items: { need: Need; state: ItemState; aside?: boolean }[]) => items.filter((i) => isSuggestion(i.need, i.state) && !i.aside).length;

/** A course's or semester's verdict as the console words it: the engine's, with the scope, the horizon's days, and the suggestions and (a semester) what is coming up counted here. */
export interface Verdict extends VerdictState {
  scope: 'course' | 'semester';
  days: number;
}

/**
 * The engine's verdict for a scope, or null when its status carries none (one tick at most): no
 * verdict line then. `suggestions` and `comingUp` are the console's own counts, so the line
 * agrees with the tab badges.
 */
export function verdictOf(v: VerdictState | undefined, scope: 'course' | 'semester', suggestions: number, days = 7, comingUp?: number): Verdict | null {
  return v ? { ...v, scope, days, suggestions, ...(comingUp === undefined ? {} : { coming_up: comingUp }) } : null;
}

// --------------------------------------------------------------------------- repos

/** One file, SYLLABUS.md, two parts; the engine checks them as one (`syllabus`). */
export const SYLLABUS_HINT = 'One file, SYLLABUS.md, two parts: your own text, and a weekly plan the console writes into it from the schedule (Write, on the materials settings page). Students get the file when a release names it.';
export const SYLLABUS_LABEL = 'Syllabus';

/** A materials repo's checklist read for its chip: the first needed check open, the suggestions open. */
export interface RepoReadiness {
  state: 'problem' | 'not_ready' | 'ready';
  missing: string | null;
  suggestions: number;
}

export function materialsReadiness(m: { state: string; checks?: MaterialsCheck[] }): RepoReadiness {
  const checks = m.checks ?? [];
  const first = checks.find((c) => c.need === 'needed' && !c.done);
  const suggestions = checks.filter((c) => c.need === 'suggested' && !c.done).length;
  const state = m.state === 'problem' ? 'problem' : m.state === 'ready' ? 'ready' : 'not_ready';
  return { state, missing: state === 'not_ready' ? (first ? first.why ?? first.label : 'Not ready yet.') : null, suggestions };
}

/**
 * A template's chip off its problems (course scope, this template's) and the course's to-dos: a
 * problem now or soon; else the first needed to-do open, or a later problem's sentence; else
 * ready once the engine says so.
 */
export function templateReadiness(t: { repo: string; state: string }, todo: Todo[], problems: Tiered[]): RepoReadiness {
  if (problems.some((x) => x.b !== 'later')) return { state: 'problem', missing: null, suggestions: 0 };
  const open = todo.find((x) => x.kind === 'template' && x.repo === t.repo && x.need === 'needed');
  const missing = open?.text ?? problems[0]?.p.text ?? (t.state === 'ready' ? null : 'The brief (README.md) is not written yet.');
  return { state: missing ? 'not_ready' : 'ready', missing, suggestions: 0 };
}

/** A template's state off a course block and the tiered problems of the status that carries it (the course's, or a semester's copy). */
export function templateReadinessIn(course: CourseStatus | undefined | null, repo: string, tiered: Tiered[]): RepoReadiness | null {
  const t = course?.templates?.find((x) => x.repo === repo);
  if (!t) return null;
  return templateReadiness(t, course!.todo ?? [], tiered.filter((x) => x.p.scope === 'course' && x.p.fix?.entry === repo));
}

// --------------------------------------------------------------------------- a release row's mark

/** What a release row marks: when it bites, whether it is late with nothing else to say why, and the problem (absent on a status that names none). */
export interface ReleaseMark {
  bites: Bites;
  /** Late with nothing else to say why: the row links its Details, not a Fix. */
  late: boolean;
  problem?: Problem;
}

/** Earliest first: the order a release's problems mark it by. */
const BITE_ORDER: Record<Bites, number> = { now: 0, soon: 1, later: 2 };

/**
 * Each held or late release's mark (decision 0034 §6), by release id, joined on the problem's
 * `release`: one map per [status, now], for the Schedule lede, its rows and entry sheet and the
 * Dashboard. A late release with no other problem (`LATE`) is late; a held one takes the time of
 * its earliest-biting problem (now, then soon, then later), else its own date's.
 */
export function releaseMarks(status: Status, tiered: Tiered[], now: number): Map<string, ReleaseMark> {
  const out = new Map<string, ReleaseMark>();
  for (const rel of status.releases ?? []) {
    if (rel.state !== 'will_be_skipped' && rel.state !== 'late') continue;
    const own = tiered.filter((x) => x.p.release === rel.id).sort((a, b) => BITE_ORDER[a.b] - BITE_ORDER[b.b])[0];
    if (rel.state === 'late' && (!own || own.p.kind === 'LATE')) out.set(rel.id, { bites: 'now', late: true, problem: own?.p });
    else out.set(rel.id, { bites: own?.b ?? bitesAt(rel.when, status.horizon, now), late: false, problem: own?.p });
  }
  return out;
}

/** The problems a release's detail page lists: those that hold it back. */
export const releaseProblems = (status: Status, rel: Pick<Release, 'id'>): Problem[] => (status.problems ?? []).filter((p) => p.release === rel.id);
