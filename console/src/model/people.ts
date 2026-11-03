// The roster and the semester's people, read for the Students, Teams and Instructors screens
// (private surfaces: the user's token gates them). The roster is two files joined on email
// (decision 0039 rule 4): `students.csv` (the instructor's: email, name, role, one `team:<n>`
// column per team assignment) and `.system/roster.csv` (the engine's: handle, id, enrol code,
// when it was sent). The enrol code is never kept.

import { parse } from 'yaml';
import { missingColumns, readTable } from '../edit/csv';
import studentsSchema from '../../schemas/students.schema.json';
import rosterSchema from '../../schemas/roster.schema.json';

/** Whether two GitHub handles name the same account: GitHub ignores case. */
export const sameHandle = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/** One student: the instructor's columns and, once read, the engine's. */
export interface RosterRow {
  line: number; // students.csv's line number, header = 1
  email: string;
  name: string;
  role: string;
  /** `team:<n>` column -> team name, as written ('' = no team). */
  teams: Record<string, string>;
  handle: string;
  id: string;
  sent: string;
}

/** students.csv's own columns, in the seed's order (`roster.FIELDS`). */
export const ROSTER_HEADER: string[] = studentsSchema.columns;
/** `.system/roster.csv`'s columns, all the engine's (`roster.SYSTEM_FIELDS`). */
export const SYSTEM_HEADER: string[] = rosterSchema.columns;
/** A team assignment's column prefix (`roster.TEAM_PREFIX`). */
export const TEAM_PREFIX = Object.keys(studentsSchema.items.patternProperties)[0].replace(/^\^/, '');
/** The students.csv column of one team assignment (`roster.team_column`): its number, else its key. */
export const teamColumn = (a: { number?: number | null; slug: string }) => `${TEAM_PREFIX}${a.number ?? a.slug}`;

/** The roster's role value for an auditor (`roster.ROLE_AUDITOR`); any other value reads as enrolled. */
export const AUDITOR = 'auditor';

/** How many roster rows are students (enrolled) and how many auditors, as `roster.normalise_role` reads the role. */
export function roleSplit(rows: { role?: string }[]): { students: number; auditors: number } {
  const auditors = rows.filter((r) => (r.role ?? '').trim().toLowerCase() === AUDITOR).length;
  return { students: rows.length - auditors, auditors };
}

const emailKey = (e: string) => e.trim().toLowerCase();

/** `.system/roster.csv` by email (casefolded): the first row of an email wins, as `roster.parse_system` reads it. */
export function systemByEmail(text: string | null | undefined): Map<string, Record<string, string>> {
  const out = new Map<string, Record<string, string>>();
  for (const r of text ? readTable(text).rows : []) {
    const k = emailKey(r.email ?? '');
    if (k && !out.has(k)) out.set(k, r);
  }
  return out;
}

/** students.csv's rows joined with the engine's `.system/roster.csv` (`system`, when it was read) on email. */
export function parseRoster(text: string, system?: string | null): { rows: RosterRow[]; error: string | null } {
  const t = readTable(text);
  if (!t.header.length) return { rows: [], error: null };
  const missing = missingColumns(t.header, ROSTER_HEADER);
  if (missing.length) return { rows: [], error: `students.csv is missing the ${missing.join(', ')} column${missing.length > 1 ? 's' : ''}.` };
  const sys = systemByEmail(system);
  const col = (r: Record<string, string>, k: string) => (r[k] ?? '').trim();
  return {
    rows: t.rows.map((r, i) => {
      const e = sys.get(emailKey(col(r, 'email'))) ?? {};
      return {
        line: t.lines?.[i] ?? i + 2,
        email: col(r, 'email'),
        name: col(r, 'name'),
        role: col(r, 'role') || 'enrolled',
        teams: Object.fromEntries(t.header.filter((h) => h.startsWith(TEAM_PREFIX)).map((h) => [h, col(r, h)])),
        handle: col(e, 'github_handle'),
        id: col(e, 'github_id'),
        sent: col(e, 'code_sent_at'),
      };
    }),
    error: null,
  };
}

/** A pending roster commit in words: "2 added, 1 removed, 3 changed", leaving out what is none. */
export function pendingSummary(n: { added: number; removed: number; changed: number }): string {
  return ([['added', n.added], ['removed', n.removed], ['changed', n.changed]] as const).filter(([, k]) => k).map(([w, k]) => `${k} ${w}`).join(', ');
}

/** The Save bar's sentence after the summary: what a save does to added and removed students. */
export const ROSTER_SAVE_NOTE = 'Added students are mailed a code on save. A removed student who had joined loses access at the next sync; their repos are kept.';

export interface Person {
  handle: string;
  email: string;
  name: string;
  title: string;
  photo: string;
  url: string;
  start: string;
  end: string;
  /** instructor | teaching_assistant; anything else is kept as written, and the engine skips the entry. */
  role: string;
}

export const ROLE_WORD: Record<string, string> = { instructor: 'Instructor', teaching_assistant: 'Teaching assistant' };

/** semester.yml's `people:` list, in file order. */
export function parseInstructors(text: string): Person[] {
  let d: unknown;
  try {
    d = parse(text);
  } catch {
    return [];
  }
  const list = (d as { people?: unknown } | null)?.people;
  const s = (v: unknown) => (v == null ? '' : String(v));
  return (Array.isArray(list) ? list : []).map((e: Record<string, unknown>) => ({
    handle: s(e?.github_handle), email: s(e?.email), name: s(e?.name), title: s(e?.title), photo: s(e?.photo), url: s(e?.url),
    start: s(e?.start), end: s(e?.end), role: s(e?.role),
  }));
}
