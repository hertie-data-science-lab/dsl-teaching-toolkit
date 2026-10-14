// A roster for tests, written once as one table and split as decision 0039 rule 4 splits it:
// `students.csv` (email, name, role, any `team:<n>` column) and the engine's
// `.system/roster.csv` (handle, id, enrol code, when it was sent), keyed by `owner/repo/path`.

import { readTable, writeTable } from '../src/edit/csv';
import { CONFIG_REPO, ROSTER_PATH, STUDENTS_FILE } from '../src/model/names';
import { ROSTER_HEADER, SYSTEM_HEADER } from '../src/model/people';

/** `text`'s columns over both files of `org`'s roster: the engine's columns in its own file, every other in students.csv. */
export function rosterFiles(org: string, text: string): Record<string, string> {
  const t = readTable(text);
  const own = t.header.filter((h) => !SYSTEM_HEADER.includes(h) || h === 'email');
  const students = writeTable({ header: [...ROSTER_HEADER, ...own.filter((h) => !ROSTER_HEADER.includes(h))], rows: t.rows.map((r) => Object.fromEntries(own.map((h) => [h, r[h] ?? '']))) });
  const system = writeTable({ header: SYSTEM_HEADER, rows: t.rows.filter((r) => SYSTEM_HEADER.some((h) => h !== 'email' && r[h])).map((r) => Object.fromEntries(SYSTEM_HEADER.map((h) => [h, r[h] ?? '']))) });
  return { [`${org}/${CONFIG_REPO}/${STUDENTS_FILE}`]: students, [`${org}/${CONFIG_REPO}/${ROSTER_PATH}`]: system };
}
