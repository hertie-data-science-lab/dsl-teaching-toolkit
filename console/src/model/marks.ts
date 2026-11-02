// A grading sheet (`grading_sheets/<slug>.yml` in the config repo) as the marks grid reads it,
// and the arithmetic `grades.py` does on output: a per-question total (a blank question
// skipped, a non-number making the whole total unknown), then
// `total x (1 - penalty) + adjustment`, floored at 0, the penalty `rate x days_late` unless the
// unit's `late_penalty` waives or sets it. Never stored in the sheet.

import { YamlText, blank, deepEqual, isObj, obj, type Path } from '../edit/yamlText';
import { plural } from './format';
import { RETURNED_DIR } from './names';
import { penaltyRate } from './policy';

export const NOTES_KEY = 'notes_not_shared_with_students';
/** Feedback on each question: the student's on a solo sheet, the team's on a group sheet (grades.QUESTION_FEEDBACK_KEY). */
export const QUESTION_FEEDBACK_KEY = 'feedback_per_question';
/** The unit's late penalty, beside `info:`: `apply` (absent means the same), `waive`, or the whole penalty (`25%`) (grades.LATE_PENALTY_KEY). */
export const LATE_PENALTY_KEY = 'late_penalty';
/** Work handed in outside GitHub: the grader's whole days late, beside the marks; blank is 0 (grades.DAYS_LATE_KEY). */
export const DAYS_LATE_KEY = 'days_late';

export interface Person {
  handle: string;
  adjustment: unknown;
  feedback: unknown;
  notes: unknown;
  base: Path; // where this person's three fields live
}

export interface Unit {
  key: string; // the student's handle, or the team's name
  info: Record<string, unknown> | null;
  score: unknown;
  scorePath: Path;
  feedback: unknown; // the team's shared feedback (teams only)
  feedbackPath: Path | null;
  /** Feedback per question, keyed by question; its cells live under `questionFeedbackPath`. */
  questionFeedback: Record<string, unknown>;
  questionFeedbackPath: Path;
  latePenalty: unknown;
  latePenaltyPath: Path;
  /** The grader's days late, where the work is handed in outside GitHub (no `info:`). */
  daysLate: unknown;
  daysLatePath: Path;
  /** The team's own private notes (a group sheet; a person's notes are on the person). */
  notes: unknown;
  notesPath: Path | null;
  people: Person[];
}

export interface Sheet {
  group: boolean;
  frozen: boolean;
  units: Unit[];
}

/** A sheet's units by key: its `teams:` on a group sheet, else its `submissions:`. */
export function sheetUnits(doc: unknown): Record<string, unknown> {
  const d = obj(doc);
  return obj('teams' in d ? d.teams : d.submissions);
}

export function readSheet(text: string, doc: unknown): Sheet {
  const group = 'teams' in obj(doc);
  const container = group ? 'teams' : 'submissions';
  const frozen = /^# Status: FROZEN/m.test(text.split(/\n(?!#)/)[0] ?? '');
  const units: Unit[] = Object.entries(sheetUnits(doc)).map(([key, raw]) => {
    const u = obj(raw);
    const info = u.info && typeof u.info === 'object' ? obj(u.info) : null;
    if (group)
      return {
        key, info, score: u.score_group ?? null, scorePath: [container, key, 'score_group'], feedback: u.feedback_group ?? null, feedbackPath: [container, key, 'feedback_group'],
        questionFeedback: obj(u[QUESTION_FEEDBACK_KEY]), questionFeedbackPath: [container, key, QUESTION_FEEDBACK_KEY],
        latePenalty: u[LATE_PENALTY_KEY] ?? null, latePenaltyPath: [container, key, LATE_PENALTY_KEY],
        daysLate: u[DAYS_LATE_KEY] ?? null, daysLatePath: [container, key, DAYS_LATE_KEY],
        notes: u[NOTES_KEY] ?? null, notesPath: [container, key, NOTES_KEY],
        people: Object.entries(obj(u.members)).map(([handle, m]) => {
          const mm = obj(m);
          return { handle, adjustment: mm.adjustment_individual ?? null, feedback: mm.feedback_individual ?? null, notes: mm[NOTES_KEY] ?? null, base: [container, key, 'members', handle] };
        }),
      };
    return {
      key, info, score: u.score_individual ?? null, scorePath: [container, key, 'score_individual'], feedback: null, feedbackPath: null,
      questionFeedback: obj(u[QUESTION_FEEDBACK_KEY]), questionFeedbackPath: [container, key, QUESTION_FEEDBACK_KEY],
      latePenalty: u[LATE_PENALTY_KEY] ?? null, latePenaltyPath: [container, key, LATE_PENALTY_KEY],
      daysLate: u[DAYS_LATE_KEY] ?? null, daysLatePath: [container, key, DAYS_LATE_KEY],
      notes: null, notesPath: null,
      people: [{ handle: key, adjustment: u.adjustment_individual ?? null, feedback: u.feedback_individual ?? null, notes: u[NOTES_KEY] ?? null, base: [container, key] }],
    };
  });
  return { group, frozen, units };
}

/** A cell as a number, or null when it is blank or not a number (`pass`, `A-` stay text). */
export function num(v: unknown): number | null {
  if (v === null || v === undefined || typeof v === 'boolean') return null;
  const t = String(v).trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

/** A sheet's (or a "last told" record's) units by key; none when the text is absent or does not parse. */
export function unitsOf(text: string | null): Record<string, unknown> {
  if (text === null) return {};
  const y = new YamlText(text);
  return y.errors.length ? {} : sheetUnits(y.toJS());
}

/** How many units (teams or students) differ between two texts of one sheet, added or removed ones included: what someone else changed under a save. */
export function rowsChanged(before: string, after: string): number {
  const a = unitsOf(before), b = unitsOf(after);
  return [...new Set([...Object.keys(a), ...Object.keys(b)])].filter((k) => !deepEqual(a[k], b[k])).length;
}

/** Where the engine records what each unit was last told (`.system/returned/<sheet name>.yml` in the config repo). */
export const toldPath = (name: string) => `${RETURNED_DIR}/${name}.yml`;

/** A "last told" record: each unit's block, and the late rate those marks were worked out under. */
export interface Told {
  units: Record<string, unknown>;
  /** `late_penalty_per_day` as `penaltyRate` reads it (null: none); undefined for a record from before the engine wrote it. */
  rate?: number | null;
}

/**
 * A "last told" record (the sheet's own container and dialect): each unit's block as it stood when
 * its people were last emailed, beside the late rate they went out under (`grades.returned_record`).
 * Absent or unreadable: nobody has been told yet.
 */
export function readTold(text: string | null): Told {
  if (text === null) return { units: {} };
  const y = new YamlText(text);
  if (y.errors.length) return { units: {} };
  const doc = obj(y.toJS());
  return { units: sheetUnits(doc), ...('late_penalty_per_day' in doc ? { rate: penaltyRate(doc.late_penalty_per_day) } : {}) };
}

/**
 * The sheet keys whose change Return marks emails about: what feeds `grades._GRADER_KEYS` (final
 * grade, score, feedback, feedback per question, team feedback) - the scores, feedback, adjustment
 * and late penalty, the days late (the grader's beside the marks, the toolkit's under `info:`), and
 * `members:` read with the same list. Anything else on a block (private notes, the submission
 * facts, a key a grader invented) never reaches a student, so changing it emails nobody. Keep in
 * step with grades._GRADER_KEYS.
 */
export const TOLD_KEYS = [
  'score_group', 'score_individual', 'feedback_group', 'feedback_individual', QUESTION_FEEDBACK_KEY,
  'adjustment_individual', LATE_PENALTY_KEY, DAYS_LATE_KEY, 'info', 'members',
] as const;

/** Which keys of a mapping `toldPart` keeps: a list, every one (inside a kept value), or each handle of a `members:`. */
type Keep = readonly string[] | 'all' | 'members';

/**
 * A unit's block as students are told it: the `TOLD_KEYS` only (of `info:`, its `days_late`; of
 * each member, the same list), blank cells as absent. Every member's handle stays, even one whose
 * fields are all blank: who is in the team is told too, so a member swapped for another counts.
 */
function toldPart(v: unknown, keep: Keep = TOLD_KEYS): unknown {
  if (!isObj(v)) return v;
  const out: Record<string, unknown> = {};
  for (const [k, x] of Object.entries(v)) {
    if (keep === 'members') {
      out[k] = blank(x) ? {} : toldPart(x, TOLD_KEYS);
      continue;
    }
    if ((typeof keep !== 'string' && !keep.includes(k)) || blank(x)) continue;
    const next: Keep = keep !== TOLD_KEYS ? 'all' : k === 'info' ? ['days_late'] : k === 'members' ? 'members' : 'all';
    const part = toldPart(x, next);
    if (!(isObj(part) && !Object.keys(part).length)) out[k] = part;
  }
  return out;
}

/** Whether a saved unit block differs from what its people were last told (`told` undefined: never told). */
export function untold(block: unknown, told: unknown): boolean {
  return told === undefined || !deepEqual(toldPart(block), toldPart(told));
}

/**
 * Whether a unit's final grade moves with the assignment's late rate: late, and its penalty left to
 * the rule (not waived or set by hand). Such a unit's block stays the same when the rate changes,
 * but the engine's digest of its final grade does not, so Return marks emails it again.
 */
export function rateApplies(daysLate: unknown, latePenalty: unknown): boolean {
  const days = num(daysLate);
  return days !== null && days > 0 && lateChoice(latePenalty).mode === 'apply';
}

/** One `questions:` entry's maximum: `Q1: 15`, or `Q1: {points: 15, file: report.tex}`. */
export function questionPoints(entry: unknown): unknown {
  return isObj(entry) ? (entry.points ?? null) : entry;
}

/** The submission file a question is marked from, when it names one. */
export function questionFile(entry: unknown): string | null {
  return isObj(entry) && typeof entry.file === 'string' && entry.file.trim() ? entry.file : null;
}

/** The `questions:` value the template form writes from its rows: each name with its points,
 *  and a question that was a mapping (`{points, file}`) stays one, so `file:` survives a save.
 *  A row's third cell, when given, is the file it is marked from: named, the question becomes
 *  `{points, file}`; emptied, `file:` goes and a bare maximum is written. */
export function questionsFromRows(rows: ([string, string] | [string, string, string])[], was: unknown): Record<string, unknown> | undefined {
  const old = isObj(was) ? was : {};
  const kept = rows.filter(([name]) => name.trim());
  if (!kept.length) return undefined;
  return Object.fromEntries(
    kept.map(([name, n, file]) => {
      const points = n === '' ? null : Number.isFinite(Number(n)) ? Number(n) : n;
      const before = old[name.trim()];
      const rest = isObj(before) ? { ...before } : {};
      if (file !== undefined) {
        delete rest.file;
        if (file.trim()) rest.file = file.trim();
      }
      return [name.trim(), Object.keys(rest).some((k) => k !== 'points') ? { ...rest, points } : points];
    }),
  );
}

/** A unit marked with one score rather than per question: a scalar, not blank (it stays the total until per-question marks replace it). */
export const singleScore = (u: Unit) => !isObj(u.score) && !blank(u.score);

/** What a score cell adds up to; null when there is nothing to add or a value is not a number. */
export function scoreTotal(score: unknown, questions?: Record<string, unknown> | null): number | null {
  if (!score || typeof score !== 'object') return num(score);
  let total = 0, marked = false;
  for (const [k, v] of Object.entries(score as Record<string, unknown>)) {
    if (questions && Object.keys(questions).length && !(k in questions)) continue;
    if (blank(v)) continue;
    const n = num(v);
    if (n === null) return null;
    total += n;
    marked = true;
  }
  return marked ? total : null;
}

export type LateMode = 'apply' | 'waive' | 'set' | 'bad';

/** A unit's `late_penalty` read once (grades.late_choice): `apply` (absent or blank too), `waive`
 *  (a set `0%` too), `set` - the whole penalty is `set` - or `bad`, which nobody can apply and the
 *  engine holds. */
export function lateChoice(latePenalty: unknown): { mode: LateMode; set?: number } {
  const t = latePenalty === null || latePenalty === undefined ? '' : String(latePenalty).trim();
  if (t === '' || t.toLowerCase() === 'apply') return { mode: 'apply' };
  if (t.toLowerCase() === 'waive') return { mode: 'waive' };
  const set = penaltyRate(t);
  if (set === null) return { mode: 'bad' };
  return set === 0 ? { mode: 'waive' } : { mode: 'set', set };
}

/** What a unit loses for lateness (grades.effective_penalty): `fraction` of its total, the `mode`
 *  of the grader's choice behind it, and whether it is `late` at all. */
export interface Penalty {
  fraction: number;
  mode: LateMode;
  late: boolean;
}

/** `rate x days` when absent or `apply`, nothing when waived or not late, the written penalty when
 *  set by hand; a `bad` one keeps the late rule's arithmetic, and the engine holds the unit. With no
 *  late rule (`rate` null) nothing comes off and `late_penalty` is ignored, as the engine ignores it. */
export function effectivePenalty(rate: number | null, daysLate: unknown, latePenalty: unknown = null): Penalty {
  const days = num(daysLate);
  const late = days !== null && days > 0;
  if (rate === null) return { fraction: 0, mode: 'apply', late };
  const choice = lateChoice(latePenalty);
  if (!late || choice.mode === 'waive') return { fraction: 0, mode: choice.mode, late };
  return { fraction: choice.set ?? rate * days, mode: choice.mode, late };
}

/** `total x (1 - penalty) + adjustment`, the penalty capped at 1 and the result floored at 0; null when there is no total. */
export function finalGrade(total: number | null, penalty: Penalty, adjustment: unknown): number | null {
  if (total === null) return null;
  return Math.max(0, total * (1 - Math.min(penalty.fraction, 1)) + (num(adjustment) ?? 0));
}

const percent = (fraction: number) => Math.round(fraction * 1000) / 10;
/** What a penalty takes off, as the grid shows it in front of its switch: "−20%". */
export const percentOff = (fraction: number) => `−${percent(fraction)}%`;

/** The penalty as the grid shows it: "−20%", "waived" or "−25% (set by hand)"; '' when not late,
 *  or when no late rule gives one. */
export function penaltyText(p: Penalty, rate: number | null): string {
  if (!p.late) return '';
  switch (p.mode) {
    case 'waive':
      return 'waived';
    case 'set':
      return `${percentOff(p.fraction)} (set by hand)`;
    default:
      return rate === null ? '' : percentOff(p.fraction);
  }
}

/** What the override box writes: `15` or `15%` as `15%`, `0` as `waive`, cleared as `apply`, anything
 *  else as typed (the engine holds it, and the grid says so). */
export function overrideValue(raw: string): string {
  const t = raw.trim().replace(/%$/, '').trim();
  if (!t) return 'apply';
  const set = penaltyRate(`${t}%`);
  if (set === null) return raw.trim();
  return set === 0 ? 'waive' : `${Number(t)}%`;
}

/** The override box's text for a stored `late_penalty`: empty unless the penalty is set by hand. */
export function overrideText(latePenalty: unknown): string {
  const choice = lateChoice(latePenalty);
  switch (choice.mode) {
    case 'set':
      return `${percent(choice.set ?? 0)}`;
    case 'bad':
      return String(latePenalty).trim();
    default:
      return '';
  }
}

export const round = (n: number | null) => (n === null ? '' : String(Math.round(n * 100) / 100));

/** A typed cell as it goes into the sheet: a number when it is one, blank as null, else the text. */
export function cellValue(raw: string): string | number | null {
  const t = raw.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && /^-?\d+(\.\d+)?$/.test(t) ? n : raw;
}

/**
 * The late rule in one wording, for the Marks tab's lede, its Penalty `?` and the Return marks
 * pop-up alike: "<rate> of the total per day started, up to <window> days"; null with no late penalty.
 * `windowDays` is the late window (`late_window_days`), which caps the days counted.
 */
export function lateRule(rate: number | null, windowDays?: unknown, entered = false): string | null {
  if (rate === null) return null;
  // Handed in outside GitHub: no window, and the marker says how many days late.
  if (entered) return `${percent(rate)}% of the total per day late, on the days late you enter`;
  const days = num(windowDays);
  return `${percent(rate)}% of the total per day started${days ? `, up to ${plural(days, 'day')}` : ''}`;
}

/** Why Return marks holds a mark back (grades.HOLD_REASONS; `returned` is the run's own and not here). */
export type HoldReason = 'penalty' | 'score' | 'adjustment' | 'late_penalty' | 'days_late' | 'question' | 'duplicate';

/** Each reason as the grid's held chip says it (grades._HOLD_PREVIEW, in the grader's words). */
export const HOLD_WORDS: Record<HoldReason, string> = {
  penalty: 'late, and the mark is not a number a late penalty can come off',
  score: 'a question mark that is not a number',
  adjustment: 'an adjustment that is not a number',
  late_penalty: 'a late penalty that is not apply, waive or a percentage',
  days_late: 'days late that are not a whole number',
  question: 'a mark for a question the assignment does not have',
  duplicate: 'in more than one team',
};

/** A cell as the engine's `as_decimal` reads it: a plain decimal number, else null (`0x10`, `−3`, `14/15` are not numbers). */
function decimal(v: unknown): number | null {
  if (v === null || v === undefined || typeof v === 'boolean') return null;
  const t = String(v).trim();
  if (!/^[+-]?(\d+\.?\d*|\.\d+)(e[+-]?\d+)?$/i.test(t)) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : null;
}

const typo = (v: unknown) => !blank(v) && decimal(v) === null;

/** One unit as the hold rules read it: what the sheet says, with the grid's edits over it. */
export interface HoldUnit {
  score: unknown;
  daysLate: unknown;
  /** Whether `daysLate` is the grader's own (work handed in outside GitHub), and so checked. */
  byMarker?: boolean;
  latePenalty: unknown;
  people: { handle: string; adjustment: unknown }[];
}

/** What is wrong with a unit's score cell (grades._score_fault): a scalar is free text by design. */
function scoreFault(score: unknown, questions: Record<string, unknown> | null): HoldReason | null {
  if (!isObj(score)) return null;
  if (questions && Object.keys(questions).length && Object.keys(score).some((k) => !(k in questions))) return 'question';
  return Object.values(score).some(typo) ? 'score' : null;
}

/** A grader's days late that is not blank and not a whole number, 0 or more (grades._days_late_fault). */
export function daysLateFault(v: unknown): boolean {
  if (blank(v)) return false;
  const d = decimal(v);
  return d === null || d < 0 || !Number.isInteger(d);
}

/** One unit's days late: the toolkit's `info.days_late`, or for external work the grader's, blank counting as 0 (grades.unit_days_late). */
export function unitDaysLate(info: Record<string, unknown> | null, external: boolean, typed: unknown): unknown {
  if (!external) return info?.days_late;
  return blank(typed) ? 0 : typed;
}

/** A word for a score under a penalty that takes something off (grades.needs_hand_decision). */
export function needsHandDecision(u: HoldUnit, questions: Record<string, unknown> | null, rate: number | null): boolean {
  if (scoreTotal(u.score, questions) !== null || isObj(u.score) || blank(u.score)) return false;
  return effectivePenalty(rate, u.daysLate, u.latePenalty).fraction > 0;
}

/**
 * Every handle whose mark Return marks holds back, and why: grades.sheet_hold_reasons (what a
 * grader typed) over grades.undecided_marks (what the arithmetic cannot do), one reason each.
 */
export function holdReasons(units: HoldUnit[], questions: Record<string, unknown> | null, rate: number | null): Record<string, HoldReason> {
  const held: Record<string, HoldReason> = {};
  for (const u of units) if (needsHandDecision(u, questions, rate)) for (const pr of u.people) held[pr.handle] = 'penalty';
  const seen = new Set<string>();
  for (const u of units) {
    const days = decimal(u.daysLate);
    const fault = scoreFault(u.score, questions) ?? (u.byMarker && daysLateFault(u.daysLate) ? 'days_late' : null) ?? (rate !== null && days !== null && days > 0 && lateChoice(u.latePenalty).mode === 'bad' ? 'late_penalty' : null);
    for (const pr of u.people) {
      const folded = pr.handle.toLowerCase();
      if (seen.has(folded)) {
        held[pr.handle] = 'duplicate';
        continue;
      }
      seen.add(folded);
      const reason = fault ?? (typo(pr.adjustment) ? 'adjustment' : null);
      if (reason) held[pr.handle] = reason;
    }
  }
  return held;
}

/** The final grades in one line, for the Return marks pop-up: "Final: median 38, range 22–47, 2 held". */
export function spread(finals: number[], held: number): string {
  const parts: string[] = [];
  if (finals.length) {
    const s = [...finals].sort((x, y) => x - y);
    const mid = Math.floor(s.length / 2);
    const median = s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
    parts.push(`median ${round(median)}`, `range ${round(s[0])}–${round(s[s.length - 1])}`);
  } else parts.push('nothing marked yet');
  if (held) parts.push(`${held} held`);
  return `Final: ${parts.join(', ')}`;
}

/** What one student's marks page shows of one assignment: the whole breakdown, never the private notes. */
export interface StudentMarks {
  final: string;
  max: number | null;
  /** The marks per question (a flat score as `{ '': score }`). */
  score: Record<string, unknown>;
  /** The score before the late penalty and the adjustment; '' when there is none. */
  total: string;
  /** The student's own adjustment as typed ("+3", "-2"); '' when none is set. */
  adjustment: string;
  daysLate: number | null;
  penalty: string;
  feedback: string;
  team: string | null;
  teamFeedback: string;
  questionFeedback: [string, string][];
}

/**
 * One student's view of a unit: the marks per question, the score, the late penalty, their own
 * adjustment where one is set, and the final grade (score total, the late penalty, then the
 * adjustment), with the team's feedback beside their own on a team assignment.
 */
export function studentMarks(o: {
  group: boolean;
  team: string;
  score: unknown;
  questions: Record<string, unknown> | null;
  max: number | null;
  rate: number | null;
  daysLate: unknown;
  latePenalty: unknown;
  adjustment: unknown;
  feedback: unknown;
  teamFeedback: unknown;
  questionFeedback: Record<string, unknown>;
}): StudentMarks {
  const pen = effectivePenalty(o.rate, o.daysLate, o.latePenalty);
  const final = finalGrade(scoreTotal(o.score, o.questions), pen, o.adjustment);
  const verbatim = isObj(o.score) || blank(o.score) ? '' : String(o.score).trim();
  const marked = isObj(o.score) ? Object.fromEntries(Object.entries(o.score).filter(([, v]) => !blank(v))) : blank(o.score) ? {} : { '': o.score };
  const text = (v: unknown) => (blank(v) ? '' : String(v).trim());
  const adj = num(o.adjustment);
  return {
    final: final !== null ? round(final) : verbatim,
    max: o.max,
    score: marked,
    total: round(scoreTotal(o.score, o.questions)),
    adjustment: adj === null || adj === 0 ? '' : `${adj > 0 ? '+' : '−'}${round(Math.abs(adj))}`,
    daysLate: num(o.daysLate),
    penalty: penaltyText(pen, o.rate),
    feedback: text(o.feedback),
    team: o.group ? o.team : null,
    teamFeedback: o.group ? text(o.teamFeedback) : '',
    questionFeedback: Object.entries(o.questionFeedback).filter(([, v]) => !blank(v)).map(([q, v]) => [q, text(v)]),
  };
}
