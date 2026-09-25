// A grading sheet (`grading_sheets/<slug>.yml` in the config repo) as the marks grid reads it,
// and the arithmetic `grades.py` does on output: a per-question total (a blank question
// skipped, a non-number making the whole total unknown), then
// `total x (1 - rate x days_late) + adjustment`, floored at 0. Never stored in the sheet.

import { readTable } from '../edit/csv';
import { obj, type Path } from '../edit/yamlText';

export const NOTES_KEY = 'notes_not_shared_with_students';

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
  people: Person[];
}

export interface Sheet {
  group: boolean;
  frozen: boolean;
  units: Unit[];
}

export function readSheet(text: string, doc: unknown): Sheet {
  const d = obj(doc);
  const group = 'teams' in d;
  const container = group ? 'teams' : 'submissions';
  const frozen = /^# Status: FROZEN/m.test(text.split(/\n(?!#)/)[0] ?? '');
  const units: Unit[] = Object.entries(obj(d[container])).map(([key, raw]) => {
    const u = obj(raw);
    const info = u.info && typeof u.info === 'object' ? obj(u.info) : null;
    if (group)
      return {
        key, info, score: u.score_group ?? null, scorePath: [container, key, 'score_group'], feedback: u.feedback_group ?? null, feedbackPath: [container, key, 'feedback_group'],
        people: Object.entries(obj(u.members)).map(([handle, m]) => {
          const mm = obj(m);
          return { handle, adjustment: mm.adjustment_individual ?? null, feedback: mm.feedback_individual ?? null, notes: mm[NOTES_KEY] ?? null, base: [container, key, 'members', handle] };
        }),
      };
    return {
      key, info, score: u.score_individual ?? null, scorePath: [container, key, 'score_individual'], feedback: null, feedbackPath: null,
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

const blank = (v: unknown) => v === null || v === undefined || String(v).trim() === '';

const isMap = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

/** One `questions:` entry's maximum: `Q1: 15`, or `Q1: {points: 15, file: report.tex}`. */
export function questionPoints(entry: unknown): unknown {
  return isMap(entry) ? (entry.points ?? null) : entry;
}

/** The submission file a question is marked from, when it names one. */
export function questionFile(entry: unknown): string | null {
  return isMap(entry) && typeof entry.file === 'string' && entry.file.trim() ? entry.file : null;
}

/** The `questions:` value the template form writes from its rows: each name with its points,
 *  and a question that was a mapping (`{points, file}`) stays one, so `file:` survives a save. */
export function questionsFromRows(rows: [string, string][], was: unknown): Record<string, unknown> | undefined {
  const old = isMap(was) ? was : {};
  const kept = rows.filter(([name]) => name.trim());
  if (!kept.length) return undefined;
  return Object.fromEntries(
    kept.map(([name, n]) => {
      const points = n === '' ? null : Number.isFinite(Number(n)) ? Number(n) : n;
      const before = old[name.trim()];
      return [name.trim(), isMap(before) ? { ...before, points } : points];
    }),
  );
}

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

/** `late_penalty_per_day` as a fraction (`10%` and `0.1` both 0.1); null for a value the engine refuses. */
export function penaltyRate(text: unknown): number | null {
  if (blank(text)) return null;
  const raw = String(text).trim();
  const pct = raw.endsWith('%');
  let rate = num(pct ? raw.slice(0, -1) : raw);
  if (rate === null) return null;
  if (pct) rate /= 100;
  else if (rate >= 1) return null;
  return rate < 0 || rate > 1 ? null : rate;
}

export function finalGrade(total: number | null, rate: number | null, daysLate: unknown, adjustment: unknown): number | null {
  if (total === null) return null;
  let earned = total;
  const days = num(daysLate);
  if (rate !== null && days !== null && days > 0) earned *= 1 - rate * days;
  return Math.max(0, earned + (num(adjustment) ?? 0));
}

/** The penalty as the grid shows it: "−20%". */
export function penaltyText(rate: number | null, daysLate: unknown): string {
  const days = num(daysLate);
  if (rate === null || days === null || days <= 0) return '';
  return `−${Math.round(rate * days * 1000) / 10}%`;
}

export const round = (n: number | null) => (n === null ? '' : String(Math.round(n * 100) / 100));

/** A typed cell as it goes into the sheet: a number when it is one, blank as null, else the text. */
export function cellValue(raw: string): string | number | null {
  const t = raw.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) && /^-?\d+(\.\d+)?$/.test(t) ? n : raw;
}

/** Every student handle on a sheet: the students, or each team's members. */
export function sheetHandles(sheet: Sheet): string[] {
  return sheet.units.flatMap((u) => u.people.map((x) => x.handle));
}

/**
 * `gradebook/distributed.csv` (machine-written) as handle -> when that student's gradebook
 * was last written: the `gradebook` rows for the whole book (`assignment` blank), as the
 * engine's `status_json._returned_at` reads them.
 */
export function gradebookWrites(text: string): Map<string, string> {
  const out = new Map<string, string>();
  for (const r of readTable(text).rows) {
    const target = (r.target ?? '').trim(), when = (r.distributed_at ?? '').trim();
    if (target && when && (r.channel ?? '').trim() === 'gradebook' && !(r.assignment ?? '').trim()) out.set(target.toLowerCase(), when);
  }
  return out;
}

/** When a returned sheet's marks reached the last of its students: the latest of their gradebook writes. */
export function returnedOn(sheet: Sheet, writes: Map<string, string>): string | null {
  let last: string | null = null;
  for (const h of sheetHandles(sheet)) {
    const at = writes.get(h.toLowerCase());
    if (at && (!last || Date.parse(at) > Date.parse(last))) last = at;
  }
  return last;
}
