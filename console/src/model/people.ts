// students.csv and instructors.yml, read for the Students and Instructors screens (private surfaces:
// the user's token gates them). The enrol code column is never kept.

import { parse } from 'yaml';
import { missingColumns, readTable } from '../edit/csv';

/** Whether two GitHub handles name the same account: GitHub ignores case. */
export const sameHandle = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

export interface RosterRow {
  line: number; // the file's line number, header = 1
  email: string;
  name: string;
  role: string;
  handle: string;
  id: string;
  sent: string;
}

export const ROSTER_HEADER = ['hertie_email', 'name', 'role'];

/** The roster's role value for an auditor (`roster.ROLE_AUDITOR`); any other value reads as enrolled. */
export const AUDITOR = 'auditor';

/** How many roster rows are students (enrolled) and how many auditors, as `roster.normalise_role` reads the role. */
export function roleSplit(rows: { role?: string }[]): { students: number; auditors: number } {
  const auditors = rows.filter((r) => (r.role ?? '').trim().toLowerCase() === AUDITOR).length;
  return { students: rows.length - auditors, auditors };
}

export function parseRoster(text: string): { rows: RosterRow[]; error: string | null } {
  const t = readTable(text);
  if (!t.header.length) return { rows: [], error: null };
  const missing = missingColumns(t.header, ROSTER_HEADER);
  if (missing.length) return { rows: [], error: `students.csv is missing the ${missing.join(', ')} column${missing.length > 1 ? 's' : ''}.` };
  const col = (r: Record<string, string>, k: string) => (r[k] ?? '').trim();
  return {
    rows: t.rows.map((r, i) => ({
      line: t.lines?.[i] ?? i + 2,
      email: col(r, 'hertie_email'),
      name: col(r, 'name'),
      role: col(r, 'role') || 'enrolled',
      handle: col(r, 'github_handle'),
      id: col(r, 'github_id'),
      sent: col(r, 'code_sent_at'),
    })),
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

/** instructors.yml's one `instructors:` list, in file order. */
export function parseInstructors(text: string): Person[] {
  let d: unknown;
  try {
    d = parse(text);
  } catch {
    return [];
  }
  // semester.yml's `people:` list (decision 0039); a 0.10 instructors.yml's `instructors:`.
  const doc = d as { people?: unknown; instructors?: unknown } | null;
  const list = Array.isArray(doc?.people) ? doc?.people : doc?.instructors;
  const s = (v: unknown) => (v == null ? '' : String(v));
  return (Array.isArray(list) ? list : []).map((e: Record<string, unknown>) => ({
    handle: s(e?.github_handle), email: s(e?.email), name: s(e?.name), title: s(e?.title), photo: s(e?.photo), url: s(e?.url),
    start: s(e?.start), end: s(e?.end), role: s(e?.role),
  }));
}
