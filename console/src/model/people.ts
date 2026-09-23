// students.csv and people.yml, read for the Students and Staff screens (private surfaces:
// the user's token gates them). The enrol code column is never kept.

import { parse } from 'yaml';

/** RFC 4180 rows; a leading BOM is dropped, as the engine's strip_bom does. */
export function parseCsv(text: string): string[][] {
  const src = text.replace(/^﻿/, '');
  const rows: string[][] = [];
  let row: string[] = [], field = '', q = false;
  for (let i = 0; i < src.length; i++) {
    const c = src[i];
    if (q) {
      if (c === '"' && src[i + 1] === '"') { field += '"'; i++; }
      else if (c === '"') q = false;
      else field += c;
    } else if (c === '"') q = true;
    else if (c === ',') { row.push(field); field = ''; }
    else if (c === '\n' || c === '\r') {
      if (c === '\r' && src[i + 1] === '\n') i++;
      row.push(field); rows.push(row); row = []; field = '';
    } else field += c;
  }
  if (field !== '' || row.length) { row.push(field); rows.push(row); }
  return rows.filter((r) => r.some((f) => f.trim() !== ''));
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
  const all = parseCsv(text);
  if (!all.length) return { rows: [], error: null };
  const head = all[0].map((h) => h.trim());
  const missing = ROSTER_HEADER.filter((h) => !head.includes(h));
  if (missing.length) return { rows: [], error: `students.csv is missing the ${missing.join(', ')} column${missing.length > 1 ? 's' : ''}.` };
  const col = (r: string[], k: string) => (head.indexOf(k) >= 0 ? (r[head.indexOf(k)] ?? '').trim() : '');
  return {
    rows: all.slice(1).map((r, i) => ({
      line: i + 2,
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
  role: 'instructor' | 'ta';
}

export function parsePeople(text: string): Person[] {
  let d: unknown;
  try {
    d = parse(text);
  } catch {
    return [];
  }
  const people = ((d as { people?: unknown })?.people ?? {}) as Record<string, unknown>;
  const s = (v: unknown) => (v == null ? '' : String(v));
  const read = (list: unknown, role: Person['role']): Person[] =>
    (Array.isArray(list) ? list : []).map((e: Record<string, unknown>) => ({
      handle: s(e.github_handle), email: s(e.email), name: s(e.name), title: s(e.title), photo: s(e.photo), url: s(e.url),
      start: s(e.start), end: s(e.end), role,
    }));
  return [...read(people.instructors, 'instructor'), ...read(people.teaching_assistants, 'ta')];
}
