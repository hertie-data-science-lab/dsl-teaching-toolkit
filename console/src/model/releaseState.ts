// What a release by hand would do, worked out from the trees alone (plan 2026-11-04, decisions
// 3-7): each course file's state against the semester copy, the picks grouped as the manifest
// shows them, and the paired lists `release.adhoc` takes.
//
// State is three-way, GitHub's fork words: the course blob, the semester repo's `upstream`
// branch (where a release lands) and its default branch (what students read, which the release
// merges into). A semester with no `upstream` branch (made before it existed) is two-way. A file
// `upstream` has as the course has it but the default branch lacks was released, then removed in
// the semester: a plain release keeps the deletion (`removed`), only Overwrite restores it.
//
// What a copy takes is the engine's (`deploy.deploy_many`, `selection.deny`): a folder copy skips
// never-material names, the never-released names (`hardDenied`), the content names (`solutions`,
// `tests`) unless a `!` line in `.releaseignore` re-includes them, and whatever `.releaseignore`
// withholds; a file named outright ships even when a content name, never when never-released, and
// not when `.releaseignore` withholds it.

import selectionRules from '../../schemas/selection.json';
import { buildTree, hardDenied, neverMaterial, type TreeNode } from '../edit/badges';
import { compileAll, withheldBy, type Rule } from '../edit/glob';
import type { Files, TreeState } from './files';
import type { Loaded } from './status';
import type { Release, Status } from './types';
import { fnmatch } from './selection';
import { COURSE_REPO, JOIN_REPO, CONFIG_REPO } from './names';
import { REPO_NAME_RE, effectiveOf, semesterDefaults } from './settings';

export type FileState = 'up_to_date' | 'behind' | 'ahead' | 'diverged' | 'removed' | 'not_released' | 'unknown';

/** Each state's word (its chip's, and a folder roll-up's), in the order a roll-up lists them. */
export const STATE_WORDS: Record<Exclude<FileState, 'unknown'>, string> = {
  behind: 'behind',
  ahead: 'ahead',
  diverged: 'diverged',
  removed: 'removed in semester',
  not_released: 'not released',
  up_to_date: 'up to date',
};

const SHAS = new WeakMap<TreeState, Map<string, string>>();
const NONE = new Map<string, string>();

/** A tree's blob shas by path (worked out once per tree read), or null while it cannot be read. */
function blobs(t: TreeState): Map<string, string> | null {
  if (t.kind === 'absent') return NONE;
  if (t.kind !== 'ready') return null;
  let m = SHAS.get(t);
  if (!m) SHAS.set(t, (m = new Map(t.paths.filter((p) => !p.dir).map((p) => [p.path, p.sha]))));
  return m;
}

/**
 * Each course file's state, keyed by its course path. `destPath` maps a course path to where it
 * lands in the semester repo (default: the same path). `upstream` null or absent: two-way.
 * A semester repo that does not exist yet has nothing released; one still loading or that
 * could not be read leaves every file `unknown`, as does an `upstream` still loading.
 * A path the `upstream` branch lacks (released before the branch existed) is judged two-way.
 * A path the default branch lacks that `upstream` has with the course's blob is `removed`.
 */
export function fileStates(
  course: { path: string; sha: string }[],
  upstream: TreeState | null,
  students: TreeState,
  o: { destPath?: (path: string) => string } = {},
): Record<string, FileState> {
  const live = blobs(students);
  const up = upstream === null || upstream.kind === 'absent' ? NONE : blobs(upstream);
  const out: Record<string, FileState> = {};
  for (const { path, sha } of course) {
    const at = o.destPath ? o.destPath(path) : path;
    if (!live || !up) out[path] = 'unknown';
    else if (!live.has(at)) out[path] = students.kind === 'ready' && up.get(at) === sha ? 'removed' : 'not_released';
    else {
      const s = live.get(at)!, u = up.get(at);
      if (u === undefined) out[path] = sha === s ? 'up_to_date' : 'behind';
      else if (sha === u) out[path] = u === s ? 'up_to_date' : 'ahead';
      else out[path] = u === s ? 'behind' : 'diverged';
    }
  }
  return out;
}

/** A released folder against the course: edited in the semester, changed in the course since the release, or both. */
export type FolderStep = 'ahead' | 'behind' | 'diverged';

/**
 * A released folder's step from its files' states (`fileStates`): `ahead` when a file is ahead (or
 * removed in the semester, a semester edit) and none behind, `behind` when a file is behind (or the course added one) and none ahead,
 * `diverged` when a file diverged or both; null when in step, or while any state is unknown.
 */
export function folderStep(states: FileState[]): FolderStep | null {
  if (states.includes('unknown')) return null;
  const ahead = states.some((x) => x === 'ahead' || x === 'removed');
  const behind = states.some((x) => x === 'behind' || x === 'not_released');
  if (states.includes('diverged') || (ahead && behind)) return 'diverged';
  return ahead ? 'ahead' : behind ? 'behind' : null;
}

/**
 * The files under a released `folder` that only the semester copy has (`landed`: where the
 * course's files land), clutter aside: added in the semester, `ahead` as the engine's divergence
 * reads a released-only file. Empty while the semester tree cannot be read.
 */
export function semesterOnly(students: TreeState, folder: string, landed: Set<string>): string[] {
  if (students.kind !== 'ready') return [];
  return students.paths.filter((x) => !x.dir && x.path.startsWith(`${folder}/`) && !landed.has(x.path) && !neverMaterial(x.path)).map((x) => x.path);
}

export type Counts = Partial<Record<FileState, number>>;

/** "2 behind · 1 ahead · 3 not released": zero parts, up to date and `unknown` left out, so a folder all up to date reads ''. */
export function countsWords(counts: Counts): string {
  return (Object.keys(STATE_WORDS) as (keyof typeof STATE_WORDS)[])
    .filter((k) => k !== 'up_to_date' && counts[k])
    .map((k) => `${counts[k]} ${STATE_WORDS[k]}`)
    .join(' · ');
}

/** A course repo's files as the manifest reads them: every file path, and its root `.releaseignore` compiled with the institution's defaults (`compileAll(lines, { defaults: true })`). */
export interface RepoFiles {
  files: string[];
  rules: Rule[];
}

export interface ReleasePick {
  repo: string;
  path: string;
  dir: boolean;
}

/**
 * A file left out of a picked folder, or a picked file that does not ship: `rule` the
 * `.releaseignore` line, null for the institution's content default; `never` for a
 * never-released name (`hardDenied`), which no line or naming ships.
 */
export interface Left {
  path: string;
  rule: string | null;
  never?: true;
}

export interface Group {
  repo: string;
  /** "labs/", "lectures/01_intro/", or "(top level)". */
  head: string;
  /** The folder the group is of: the picked folder, or the parent of the picked files ('' at the top). */
  folder: string;
  kind: 'all' | 'partial';
  /** Files that ship (all) or files picked (partial). */
  files: number;
  /** Partial only: the folder's own files, never-material names aside. */
  of?: number;
  /** The paths the request names: the folder (all) or each picked file that ships (partial). */
  sources: string[];
  /** Every file that would ship, as course paths. */
  ship: string[];
  withheld: Left[];
  /** Overrides; absent means the run's default repo and the same path as `folder`. */
  destRepo?: string;
  destPath?: string;
}

export const TOP_LEVEL = '(top level)';

const parentOf = (path: string) => (path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '');
const under = (path: string, folder: string) => !folder || path.startsWith(`${folder}/`);
const join = (folder: string, name: string) => (folder && name ? `${folder}/${name}` : folder || name);
const trim = (p: string) => p.replace(/^\/+|\/+$/g, '');

/** Where a group's course path lands: its folder's place swapped for the group's `destPath` (a blank one is no override). */
export function destPathOf(g: Group, path: string): string {
  const to = trim(g.destPath?.trim() ? g.destPath.trim() : g.folder);
  const rest = g.folder ? path.slice(g.folder.length).replace(/^\//, '') : path;
  return join(to, rest);
}

/**
 * The picks as the manifest groups them, repo by repo in the order first picked: a picked folder
 * is a group of its own ("all"), picked files go with their siblings under their immediate
 * parent ("n of m"), and a pick under a picked folder is implied. Groups follow the tree
 * (`buildTree`: folders first, by name), each one whole. Never-material names never show.
 */
export function groupPicks(picks: ReleasePick[], trees: Record<string, RepoFiles>): Group[] {
  const repos = [...new Set(picks.map((p) => p.repo))];
  const out: Group[] = [];
  for (const repo of repos) {
    const t = trees[repo] ?? { files: [], rules: [] };
    const mine = picks.filter((p) => p.repo === repo && p.path && !neverMaterial(p.path));
    const folders = new Set(mine.filter((p) => p.dir).map((p) => trim(p.path)));
    // A pick inside a picked folder is implied by it.
    const implied = (path: string) => [...folders].some((f) => f !== path && under(path, f));
    const pickedFiles = new Set(mine.filter((p) => !p.dir && !implied(p.path)).map((p) => p.path));
    const pickedFolders = new Set([...folders].filter((f) => !implied(f)));
    // Named outright: `.releaseignore` still withholds, a content name ships (judged below the path itself: nowhere), a never-released name never does.
    const named = (path: string): Left | null => {
      if (hardDenied(path)) return { path, rule: null, never: true };
      const w = withheldBy(t.rules, path, false, path);
      return w ? { path, rule: w.rule.line } : null;
    };

    const allGroup = (folder: string): Group => {
      const ship: string[] = [], left: Left[] = [];
      for (const f of t.files.filter((x) => under(x, folder))) {
        if (neverMaterial(f)) continue;
        const out = sweptOut(t.rules, folder, f);
        if (out) left.push(out);
        else ship.push(f);
      }
      return { repo, head: `${folder}/`, folder, kind: 'all', files: ship.length, sources: [folder], ship, withheld: left };
    };
    const partialGroup = (folder: string, picked: string[]): Group => {
      const ship = picked.filter((p) => named(p) === null);
      const left = picked.map(named).filter((x): x is Left => x !== null);
      const of = t.files.filter((f) => parentOf(f) === folder && !neverMaterial(f)).length;
      return { repo, head: folder ? `${folder}/` : TOP_LEVEL, folder, kind: 'partial', files: picked.length, of: Math.max(of, picked.length), sources: ship, ship, withheld: left };
    };

    // Picks the tree does not list still show, at the end, in their own groups.
    const listed = new Set(t.files);
    const known = [...t.files, ...[...pickedFiles].filter((p) => !listed.has(p))];
    const walk = (nodes: TreeNode[], folder: string) => {
      let filesDone = false;
      for (const n of nodes) {
        if (n.children) {
          if (pickedFolders.has(n.path)) out.push(allGroup(n.path));
          else walk(n.children, n.path);
        } else if (!filesDone && pickedFiles.has(n.path)) {
          filesDone = true;
          const picked = nodes.filter((x) => !x.children && pickedFiles.has(x.path)).map((x) => x.path);
          out.push(partialGroup(folder, picked));
        }
      }
    };
    walk(buildTree(known), '');
    // A picked folder the tree does not list (an empty or unread tree): still one group.
    const seen = new Set(out.filter((g) => g.repo === repo && g.kind === 'all').map((g) => g.folder));
    for (const f of pickedFolders) if (!seen.has(f) && !known.some((x) => under(x, f))) out.push(allGroup(f));
  }
  return out;
}

/** One path of a release: from a course repo and path, to a semester repo and path. */
export interface ReleaseRow {
  repo: string;
  path: string;
  toRepo: string;
  toPath: string;
}

/**
 * The paths the groups name (a folder, or each file of a partial group), each with its own source
 * repo, destination repo (`run.defaultDestRepo` where the group names none) and destination path.
 */
export function requestOf(groups: Group[], run: { defaultDestRepo: string }): ReleaseRow[] {
  return groups.flatMap((g) => g.sources.map((path) => ({ repo: g.repo, path, toRepo: g.destRepo || run.defaultDestRepo, toPath: destPathOf(g, path) })));
}

/** The longest list `release.adhoc` takes in one field (`registry.PATH_LIST_PATTERN`'s cap). */
export const LIST_MAX = 8192;
export const COMMA_TITLE = 'A path with a comma cannot be released from here; rename it.';
export const TOO_MANY = 'Too many paths for one release: tick folders instead, or release in two goes.';

/** Why these rows cannot go as one `release.adhoc`, or null: a comma in a path would mis-pair the lists, and a list over `LIST_MAX` is refused. */
export function releaseBlocked(rows: ReleaseRow[]): string | null {
  if (rows.some((r) => r.path.includes(',') || r.toPath.includes(','))) return COMMA_TITLE;
  return Object.values(toArgs(rows)).some((v) => v.length > LIST_MAX) ? TOO_MANY : null;
}

/** `release.adhoc`'s four lists, paired by index (`deploy.parse_path_pairs`). */
export function toArgs(rows: ReleaseRow[]) {
  return {
    course_source_repo: rows.map((r) => r.repo).join(','),
    course_source_path: rows.map((r) => r.path).join(','),
    semester_dest_repo: rows.map((r) => r.toRepo).join(','),
    semester_dest_path: rows.map((r) => r.toPath).join(','),
  };
}

/** Where a release lands when it names no repo: the semester's effective `materials_repo` (status.json), else the institution's. */
export function effectiveDestRepo(loaded: Loaded | undefined): string {
  const v = effectiveOf(loaded?.kind === 'ready' ? semesterDefaults(loaded.status) : undefined, 'materials_repo').value;
  return String(v ?? '').trim() || String(effectiveOf(undefined, 'materials_repo').value);
}

/** The destination repos a request writes to, each once; blank means `fallback`. */
export function destRepos(args: Record<string, unknown>, fallback: string): string[] {
  const named = String(args.semester_dest_repo ?? '').split(',').map((x) => x.trim()).filter(Boolean);
  return [...new Set(named.length ? named : [fallback])];
}

/**
 * The destination repos a `release.entry` run of `entry` writes to, as the semester's status
 * knows them (`releases`, each row's first copy): the entry's own, else every destination the
 * status names and `fallback` (the semester's default), each once.
 */
export function entryDestRepos(entry: string, releases: Release[], fallback: string): string[] {
  const own = releases.find((r) => r.id === entry)?.dest?.repo;
  if (own) return [own];
  return [...new Set([fallback, ...releases.map((r) => r.dest?.repo ?? '')].filter(Boolean))];
}

/**
 * The folders a Release-view folder tick can copy something from: a folder copy that would take
 * nothing (every file under it never material, never released, or a content name below the
 * folder no `!` line in `rules`, the saved `.releaseignore` with the defaults, re-includes) is
 * left out. `files` is the repo's file paths. A file that ships from a folder's copy ships from
 * every folder below it too, so each file marks its folders from the shallowest that takes it.
 */
export function foldersCopied(files: string[], rules: Rule[]): Set<string> {
  const out = new Set<string>();
  for (const f of files) {
    if (neverMaterial(f)) continue;
    const parts = f.split('/').slice(0, -1);
    const at = parts.findIndex((_, i) => !sweptOut(rules, parts.slice(0, i + 1).join('/'), f));
    if (at >= 0) for (let i = at; i < parts.length; i++) out.add(parts.slice(0, i + 1).join('/'));
  }
  return out;
}

/** What a folder copy of `folder` leaves of `f` (`selection.deny`): never released, or withheld by a line or the content default, judged below the folder. */
function sweptOut(rules: Rule[], folder: string, f: string): Left | null {
  if (hardDenied(folder ? f.slice(folder.length + 1) : f)) return { path: f, rule: null, never: true };
  const w = withheldBy(rules, f, false, folder);
  return w ? { path: f, rule: w.rule.default ? null : w.rule.line } : null;
}

/** The names a semester already uses for something other than materials. */
export interface Reserved {
  /** The semester org: its site repo is `<org>.github.io`. */
  org: string;
  /** The assignment stems: any `<stem>-...` name is a student's or team's repo (`discovery.reserved_destination`). */
  stems: string[];
}

/** A semester's assignment stems: each assignment's key, and its semester-side name where that differs. */
export function assignmentStems(status: Status | null | undefined): string[] {
  return [...new Set((status?.assignments ?? []).flatMap((a) => [a.slug, a.semester_name ?? a.slug].map((x) => x.toLowerCase())))];
}

/** The engine's reserved names (`schemas/selection.json` `reserved`): exact names, and globs. */
const RESERVED = selectionRules.reserved;
// The name is lowercased before the test, as the engine's `fnmatchcase` on the folded name.
const RESERVED_GLOBS = RESERVED.patterns.map((x) => fnmatch(x));

/** What a reserved name is for, in words ("the semester" where it has none of its own); null when it is not reserved (a student repo aside). `n` is lowercased. */
function reservedFor(n: string, org: string): string | null {
  if (!RESERVED.names.some((x) => x.toLowerCase() === n) && !RESERVED_GLOBS.some((re) => re.test(n))) return null;
  return n === CONFIG_REPO.toLowerCase() ? 'the semester’s config'
    : n === COURSE_REPO ? 'the organisation’s profile'
    : n === `${org}.github.io` ? 'the semester’s website'
    : n === JOIN_REPO.toLowerCase() ? 'the enrolment page'
    : n.startsWith('grades-') ? 'students’ marks'
    : 'the semester';
}

/** Null when `name` may be a release destination, else why not ("That name is reserved for the semester’s config."). */
export function eligibleDestination(name: string, known: Reserved): string | null {
  const n = name.trim().toLowerCase();
  if (n && !REPO_NAME_RE.test(n)) return 'A repo name takes letters, digits, “.”, “-” and “_” only.';
  const what = reservedFor(n, known.org.toLowerCase()) ?? (known.stems.some((s) => n.startsWith(`${s.toLowerCase()}-`)) ? 'a student’s repo' : null);
  return what ? `That name is reserved for ${what}.` : null;
}

// ---- released folders against the course (the semester's differing-files panel, the course's notice)

/** Where a release writes in the semester, as the Schedule reads it: its repo (the semester's default destination `def` when unnamed) and folder (its source's when unnamed). */
export const destOf = (r: Release, def: string) => ({ repo: r.dest?.repo || def, path: (r.dest?.path || r.source?.path || '').replace(/\/+$/, '') });

export const releasedOf = (status: Status) => (status.releases ?? []).filter((r) => r.state === 'released');

/**
 * A released folder out of step with the course: where it landed, the release that wrote it
 * last, its step and its differing files (semester paths, with their states).
 */
export interface OutOfStep {
  r: Release;
  repo: string;
  path: string;
  step: FolderStep;
  /** `from`: the course path (none for a file only the semester copy has, `added`, its state `ahead`). */
  files: { path: string; state: FileState; from?: string; added?: true }[];
}

/**
 * Every released folder whose files differ between the course copy and the semester's, three-way
 * (`fileStates`: the course tree, the semester repo's `upstream` branch and its default branch).
 * What a release copies is the engine's (`groupPicks`: a folder copy under the course repo's
 * `.releaseignore` and the institution's defaults). A folder more than one release wrote is
 * judged once, by the last. Nothing is listed for a folder whose trees are still being read.
 */
export function outOfStep(status: Status, files: Files, courseOrg: string, semOrg: string, def: string): OutOfStep[] {
  const last = new Map<string, Release>();
  for (const r of releasedOf(status)) {
    const d = destOf(r, def);
    if (r.source && d.path) last.set(`${d.repo}\0${d.path}`, r);
  }
  const out: OutOfStep[] = [];
  for (const r of last.values()) {
    const d = destOf(r, def);
    const src = r.source!;
    const from = src.path.replace(/^\/+|\/+$/g, '');
    const course = files.tree(courseOrg, src.repo);
    const ign = files.file(courseOrg, src.repo, '.releaseignore');
    if (!from || course.kind !== 'ready' || ign.kind === 'loading') continue;
    const blobs = course.paths.filter((x) => !x.dir);
    const rules = compileAll((ign.kind === 'ready' ? ign.text : '').split('\n'), { defaults: true });
    const file = blobs.some((b) => b.path === from);
    const g = groupPicks([{ repo: src.repo, path: from, dir: !file }], { [src.repo]: { files: blobs.map((b) => b.path), rules } })[0];
    if (!g) continue;
    const ship = new Set(g.ship);
    const to = (x: string) => (file ? d.path : destPathOf({ ...g, destPath: d.path }, x));
    const live = files.tree(semOrg, d.repo);
    const states = fileStates(blobs.filter((b) => ship.has(b.path)), files.tree(semOrg, d.repo, 'upstream'), live, { destPath: to });
    // A file only the semester copy has was added there: ahead.
    const extra = file ? [] : semesterOnly(live, d.path, new Set(Object.keys(states).map(to)));
    const step = folderStep([...Object.values(states), ...extra.map((): FileState => 'ahead')]);
    if (!step) continue;
    out.push({
      r, repo: d.repo, path: d.path, step,
      files: [
        ...Object.entries(states).filter(([, x]) => x !== 'up_to_date').map(([path, state]) => ({ path: to(path), state, from: path })),
        ...extra.map((path) => ({ path, state: 'ahead' as const, added: true as const })),
      ],
    });
  }
  return out;
}

/** A course file the semester changed: edited there (`ahead`, `diverged`) or removed there. */
export const editedState = (s: FileState | undefined) => s === 'ahead' || s === 'diverged' || s === 'removed';

/**
 * The files only the semester has under `repo`'s released folders in `destRepo` (`outOfStep`'s
 * `added`, semester paths): they have no row in the course tree, so the course side lists them.
 */
export function semesterAdded(rows: OutOfStep[], repo: string, destRepo: string): string[] {
  return [...new Set(rows.filter((x) => x.r.source?.repo === repo && x.repo === destRepo).flatMap((x) => x.files.filter((f) => f.added).map((f) => f.path)))];
}
