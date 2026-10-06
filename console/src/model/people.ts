// students.csv and instructors.yml, read for the Students and Instructors screens (private surfaces:
// the user's token gates them). The enrol code column is never kept.

import { parse } from 'yaml';

/** Whether two GitHub handles name the same account: GitHub ignores case. */
export const sameHandle = (a: string, b: string) => a.trim().toLowerCase() === b.trim().toLowerCase();

/** One CSV record and the file line it ends on (header = 1), as Python's `csv` reader's `line_num` counts. */
export interface CsvRecord {
  cells: string[];
  line: number;
}

/**
 * RFC 4180 records with their line numbers; a leading BOM is dropped, as the engine's
 * strip_bom does, and blank records are skipped. The line counts every physical line read,
 * blank ones and quoted newlines included, so it is the line the engine's faults name.
 */
export function csvRecords(text: string): CsvRecord[] {
  const src = text.replace(/^﻿/, '');
  const out: CsvRecord[] = [];
  let row: string[] = [], field = '', q = false, line = 1;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    const nl = c === '\n' || c === '\r';
    if (nl && c === '\r' && src[i + 1] === '\n') i++;
    if (q) {
      if (c === '"' && src[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') q = false;
      else { field += nl && c === '\r' && src[i] === '\n' ? '\r\n' : c; if (nl) line++; }
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (nl) {
      row.push(field); out.push({ cells: row, line }); row = []; field = ''; line++;
    } else field += c;
  }
  if (field !== '' || row.length) { row.push(field); out.push({ cells: row, line }); }
  return out.filter((r) => r.cells.some((f) => f.trim() !== ''));
}

/** RFC 4180 rows; a leading BOM is dropped, as the engine's strip_bom does. */
export function parseCsv(text: string): string[][] {
  return csvRecords(text).map((r) => r.cells);
}

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
  const all = csvRecords(text);
  if (!all.length) return { rows: [], error: null };
  const head = all[0].cells.map((h) => h.trim());
  const missing = ROSTER_HEADER.filter((h) => !head.includes(h));
  if (missing.length) return { rows: [], error: `students.csv is missing the ${missing.join(', ')} column${missing.length > 1 ? 's' : ''}.` };
  const col = (r: string[], k: string) => (head.indexOf(k) >= 0 ? (r[head.indexOf(k)] ?? '').trim() : '');
  return {
    rows: all.slice(1).map(({ cells: r, line }) => ({
      line,
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
