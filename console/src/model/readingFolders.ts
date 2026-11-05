// Decision 0046 rules 5-7: a reading list belongs to its readings folder. A folder directly
// under `readings/` that holds files and no `READINGS.md` (any case) is flagged on the course
// materials tree, and the engine suggests one (`reading_list:<folder>`, `materials_checks`).
// The engine's `status_json.reading_folders` is the same rule in Python; change both together.

import { READINGS_FILE } from './scheduleEdit';

export const READINGS_DIR = 'readings';

/** A folder directly under `readings/`: its files directly inside (dot files and folders not counted), and whether one is a `READINGS.md`. */
export interface ReadingFolder {
  path: string;
  files: number;
  hasList: boolean;
}

/** Every `readings/<name>/` folder among a repo tree's `paths`, sorted. */
export function readingFolderState(paths: readonly { path: string; dir: boolean }[]): ReadingFolder[] {
  const found = new Map<string, string[]>();
  for (const { path, dir } of paths) {
    if (dir) continue;
    const [top, name, ...below] = path.split('/');
    if (top !== READINGS_DIR || !name || !below.length) continue;
    const folder = `${READINGS_DIR}/${name}`;
    const names = found.get(folder) ?? [];
    found.set(folder, names);
    if (below.length === 1 && !below[0]!.startsWith('.')) names.push(below[0]!);
  }
  return [...found].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0)).map(([path, names]) => ({
    path, files: names.length, hasList: names.some((n) => n.toLowerCase() === READINGS_FILE.toLowerCase()),
  }));
}

/** The readings folders the tree flags: files in them and no list. */
export const foldersWithoutList = (paths: readonly { path: string; dir: boolean }[]) => new Set(readingFolderState(paths).filter((f) => f.files && !f.hasList).map((f) => f.path));

/** Where a folder's reading list is written: the course file view on `<folder>/READINGS.md`, which opens the editor on a list not there yet. */
export const readingListHref = (org: string, repo: string, folder: string) => `?course=${org}&file=${encodeURIComponent(`${repo}/${folder}/${READINGS_FILE}`)}#materials`;

/** The engine's check id prefix for a readings folder with no list (`reading_list:<folder>`). */
export const READING_LIST_CHECK = 'reading_list:';

/** The folder a materials check or to-do id names, or null when it is no readings folder's (`…reading_list:<folder>`). */
export function readingListFolder(id: string): string | null {
  const i = id.indexOf(READING_LIST_CHECK);
  return i < 0 || (i > 0 && id[i - 1] !== ':') ? null : id.slice(i + READING_LIST_CHECK.length) || null;
}
