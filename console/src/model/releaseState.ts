// What a release by hand would do, worked out from the trees alone (plan 2026-11-04, decisions
// 3-7): each course file's state against the semester copy, the picks grouped as the manifest
// shows them, and the paired lists `release.adhoc` takes.
//
// State is three-way, GitHub's fork words: the course blob, the semester repo's `upstream`
// branch (where a release lands) and its default branch (what students read, which the release
// merges into). A semester with no `upstream` branch (made before it existed) is two-way.
//
// What a copy takes is the engine's (`deploy.deploy_many`): a folder copy skips never-material
// names, the institution denylist and whatever `.releaseignore` withholds; a file named outright
// ships even when denylisted, and not when `.releaseignore` withholds it.

import { buildTree, denylisted, neverMaterial, type TreeNode } from '../edit/badges';
import { withheldBy, type Rule } from '../edit/glob';
import type { TreeState } from './files';
import type { Loaded } from './status';
import { COURSE_REPO, JOIN_REPO, CONFIG_REPO } from './names';
import { DEFAULT_DEST_REPO } from './policy';
import { REPO_NAME_RE } from './settings';

export type FileState = 'up_to_date' | 'behind' | 'ahead' | 'diverged' | 'not_released' | 'unknown';

/** The short words of a folder's roll-up, in the order it lists them. */
export const STATE_WORDS: Record<Exclude<FileState, 'unknown'>, string> = {
  behind: 'behind',
  ahead: 'ahead',
  diverged: 'diverged',
  not_released: 'not released',
  up_to_date: 'up to date',
};

/** A tree's blob shas by path, or null while it cannot be read. */
function blobs(t: TreeState): Map<string, string> | null {
  if (t.kind === 'absent') return new Map();
  if (t.kind !== 'ready') return null;
  return new Map(t.paths.filter((p) => !p.dir).map((p) => [p.path, p.sha]));
}

/**
 * Each course file's state, keyed by its course path. `destPath` maps a course path to where it
 * lands in the semester repo (default: the same path). `upstream` null or absent: two-way.
 * A semester repo that does not exist yet has nothing released; one still loading or that
 * could not be read leaves every file `unknown`, as does an `upstream` still loading.
 * A path the `upstream` branch lacks (released before the branch existed) is judged two-way.
 */
export function fileStates(
  course: { path: string; sha: string }[],
  upstream: TreeState | null,
  students: TreeState,
  o: { destPath?: (path: string) => string } = {},
): Record<string, FileState> {
  const live = blobs(students);
  const up = upstream === null || upstream.kind === 'absent' ? new Map<string, string>() : blobs(upstream);
  const out: Record<string, FileState> = {};
  for (const { path, sha } of course) {
    const at = o.destPath ? o.destPath(path) : path;
    if (!live || !up) out[path] = 'unknown';
    else if (!live.has(at)) out[path] = 'not_released';
    else {
      const s = live.get(at)!, u = up.get(at);
      if (u === undefined) out[path] = sha === s ? 'up_to_date' : 'behind';
      else if (sha === u) out[path] = u === s ? 'up_to_date' : 'ahead';
      else out[path] = u === s ? 'behind' : 'diverged';
    }
  }
  return out;
}

export type Counts = Partial<Record<FileState, number>>;

/** How many files under `node` stand in each state, leaving out those `isOut` (withheld, never material). */
export function folderCounts(node: TreeNode, states: Record<string, FileState>, isOut: (path: string) => boolean): Counts {
  const n: Counts = {};
  const walk = (x: TreeNode) => {
    if (x.children) x.children.forEach(walk);
    else if (!isOut(x.path)) {
      const s = states[x.path];
      if (s) n[s] = (n[s] ?? 0) + 1;
    }
  };
  walk(node);
  return n;
}

/** "2 behind · 1 ahead · 3 not released": zero parts and `unknown` left out. */
export function countsWords(counts: Counts): string {
  return (Object.keys(STATE_WORDS) as (keyof typeof STATE_WORDS)[])
    .filter((k) => counts[k])
    .map((k) => `${counts[k]} ${STATE_WORDS[k]}`)
    .join(' · ');
}

/** A course repo's files as the manifest reads them: every file path, and its root `.releaseignore` compiled. */
export interface RepoFiles {
  files: string[];
  rules: Rule[];
}

export interface ReleasePick {
  repo: string;
  path: string;
  dir: boolean;
}

/** A file left out of a picked folder, or a picked file `.releaseignore` withholds: `rule` the line, null for the institution denylist ("only if named"). */
export interface Left {
  path: string;
  rule: string | null;
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

/** Where a group's course path lands: its folder's place swapped for the group's `destPath`. */
export function destPathOf(g: Group, path: string): string {
  const to = trim(g.destPath ?? g.folder);
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
    const withheld = (path: string) => withheldBy(t.rules, path)?.rule.line ?? null;

    const allGroup = (folder: string): Group => {
      const ship: string[] = [], left: Left[] = [];
      for (const f of t.files.filter((x) => under(x, folder))) {
        if (neverMaterial(f)) continue;
        const rule = withheld(f);
        if (rule !== null) left.push({ path: f, rule });
        else if (denylisted(f)) left.push({ path: f, rule: null });
        else ship.push(f);
      }
      return { repo, head: `${folder}/`, folder, kind: 'all', files: ship.length, sources: [folder], ship, withheld: left };
    };
    const partialGroup = (folder: string, picked: string[]): Group => {
      const ship = picked.filter((p) => withheld(p) === null);
      const left = picked.filter((p) => withheld(p) !== null).map((p) => ({ path: p, rule: withheld(p) }));
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

export interface ReleaseRequest {
  course_source_repo: string;
  course_source_path: string;
  semester_dest_repo: string;
  semester_dest_path: string;
}

/**
 * `release.adhoc`'s four lists, paired by index (`deploy.parse_path_pairs`): one item per path
 * the groups name (a folder, or each file of a partial group), each with its own source repo,
 * destination repo and destination path.
 */
export function requestOf(groups: Group[], run: { defaultDestRepo: string }): ReleaseRequest {
  const rows = groups.flatMap((g) => g.sources.map((src) => ({ repo: g.repo, src, to: g.destRepo || run.defaultDestRepo, at: destPathOf(g, src) })));
  return {
    course_source_repo: rows.map((r) => r.repo).join(','),
    course_source_path: rows.map((r) => r.src).join(','),
    semester_dest_repo: rows.map((r) => r.to).join(','),
    semester_dest_path: rows.map((r) => r.at).join(','),
  };
}

/** Where a release lands when it names no repo: the semester's effective `materials_repo` (status.json), else the institution's. */
export function effectiveDestRepo(loaded: Loaded | undefined): string {
  const v = loaded?.kind === 'ready' ? loaded.status.semester?.defaults?.materials_repo?.value : undefined;
  return typeof v === 'string' && v.trim() ? v.trim() : DEFAULT_DEST_REPO;
}

/** The destination repos a request writes to, each once; blank means `fallback`. */
export function destRepos(args: Record<string, unknown>, fallback: string): string[] {
  const named = String(args.semester_dest_repo ?? '').split(',').map((x) => x.trim()).filter(Boolean);
  return [...new Set(named.length ? named : [fallback])];
}

/**
 * Whether a Release-view tick is disabled: a file already up to date, or a folder whose copy
 * would take nothing (every file under it never copied: never material or denylisted).
 * `files` is the repo's file paths.
 */
export function disabledTick(path: string, dir: boolean, state: FileState | undefined, files: string[]): boolean {
  if (!dir) return state === 'up_to_date';
  return files.filter((f) => under(f, path)).every((f) => neverMaterial(f) || denylisted(f));
}

/** The names a semester already uses for something other than materials. */
export interface Reserved {
  /** The semester org: its site repo is `<org>.github.io`. */
  org: string;
  /** Student and team repo names (`<slug>-<handle>`, `grades-<handle>`, ...). */
  students: string[];
}

/** Null when `name` may be a release destination, else why not ("That name is reserved for the semester’s config."). */
export function eligibleDestination(name: string, known: Reserved): string | null {
  const n = name.trim().toLowerCase();
  if (n && !REPO_NAME_RE.test(n)) return 'A repo name takes letters, digits, “.”, “-” and “_” only.';
  const what =
    n === CONFIG_REPO.toLowerCase() ? 'the semester’s config'
    : n === COURSE_REPO ? 'the organisation’s profile'
    : n === `${known.org.toLowerCase()}.github.io` ? 'the semester’s website'
    : n === JOIN_REPO.toLowerCase() ? 'the enrolment page'
    : n.startsWith('grades-') ? 'students’ marks'
    : known.students.some((s) => s.toLowerCase() === n) ? 'a student’s repo'
    : null;
  return what ? `That name is reserved for ${what}.` : null;
}
