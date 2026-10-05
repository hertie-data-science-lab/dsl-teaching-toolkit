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
  const list = (d as { instructors?: unknown })?.instructors;
  const s = (v: unknown) => (v == null ? '' : String(v));
  return (Array.isArray(list) ? list : []).map((e: Record<string, unknown>) => ({
    handle: s(e?.github_handle), email: s(e?.email), name: s(e?.name), title: s(e?.title), photo: s(e?.photo), url: s(e?.url),
    start: s(e?.start), end: s(e?.end), role: s(e?.role),
  }));
}
