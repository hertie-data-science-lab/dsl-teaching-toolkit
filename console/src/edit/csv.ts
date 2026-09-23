// students.csv and teams.csv as tables the console edits and writes back. The header's
// column order is kept, columns the console does not show (the enrol code) ride along
// untouched, and the engine's `require_csv_header` + `strip_bom` reading rules hold: a
// leading BOM is dropped and the named columns must be present.

import { parseCsv } from '../model/people';

export interface Table {
  header: string[];
  rows: Record<string, string>[];
}

export function readTable(text: string): Table {
  const all = parseCsv(text);
  if (!all.length) return { header: [], rows: [] };
  const header = all[0].map((h) => h.trim());
  const rows = all.slice(1).map((r) => Object.fromEntries(header.map((h, i) => [h, r[i] ?? ''])));
  return { header, rows };
}

/** The columns `required` lists that `header` lacks. */
export function missingColumns(header: string[], required: string[]): string[] {
  return required.filter((h) => !header.includes(h));
}

function cell(v: string): string {
  return /[",\r\n]/.test(v) || /^\s|\s$/.test(v) ? `"${v.replace(/"/g, '""')}"` : v;
}

/** The table as CSV text: `header` first (then any column the rows carry that it lacks). */
export function writeTable(t: Table, columns: string[] = t.header): string {
  const cols = [...columns];
  for (const r of t.rows) for (const k of Object.keys(r)) if (!cols.includes(k)) cols.push(k);
  return [cols, ...t.rows.map((r) => cols.map((c) => r[c] ?? ''))].map((r) => r.map(cell).join(',')).join('\n') + '\n';
}

export const EMAIL_RE = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

export interface RosterDiff {
  added: Record<string, string>[];
  changed: { before: Record<string, string>; after: Record<string, string> }[];
  removed: Record<string, string>[];
  unchanged: number;
}

const YOURS = ['name', 'role'];

/**
 * What replacing the roster with `upload` would do, matched by email (case-insensitive).
 * A matched row keeps its system columns; only name and role come from the upload.
 */
export function diffRoster(current: Record<string, string>[], upload: Record<string, string>[]): { diff: RosterDiff; rows: Record<string, string>[] } {
  const key = (r: Record<string, string>) => (r.hertie_email ?? '').trim().toLowerCase();
  const byEmail = new Map(current.map((r) => [key(r), r]));
  const seen = new Set<string>();
  const diff: RosterDiff = { added: [], changed: [], removed: [], unchanged: 0 };
  const rows: Record<string, string>[] = [];
  for (const u of upload) {
    const k = key(u);
    if (!k || seen.has(k)) continue;
    seen.add(k);
    const before = byEmail.get(k);
    if (!before) {
      const row = { hertie_email: u.hertie_email.trim(), name: (u.name ?? '').trim(), role: (u.role ?? '').trim() };
      diff.added.push(row);
      rows.push(row);
      continue;
    }
    const after = { ...before };
    for (const c of YOURS) if (u[c] !== undefined) after[c] = u[c].trim();
    if (YOURS.some((c) => (after[c] ?? '') !== (before[c] ?? ''))) diff.changed.push({ before, after });
    else diff.unchanged++;
    rows.push(after);
  }
  for (const r of current) if (!seen.has(key(r))) diff.removed.push(r);
  return { diff, rows };
}
