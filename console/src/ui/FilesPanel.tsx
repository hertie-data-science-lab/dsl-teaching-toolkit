// The Files panel (plan 2026-11-04, decisions 1-8): one course repo's tree on the left, a right
// column beside it, and a `Browse | Withhold | Release` control. Browse: a name opens the file in
// the right column. Withhold: ticked = reaches students, untick to withhold (`.releaseignore`),
// the pattern box right and the Save bar below, as the Withheld panel had them. Release: ticked =
// copy now, the manifest right, one Release button. The course repo settings page shows all three
// (mode `course`, `Release into [semester]`); the semester's Released materials page only Release
// (mode `semester`, `From [course repo]`, "Release by hand"). The tree is one instance across the
// views, so the folders open stay open. A viewer who cannot change the course sees the course
// page's panel as Browse alone (no views to pick, no ticks); the semester's is not drawn.
//
// Chips per view (decision 3): Browse shows one muted withheld chip on every row that does not
// reach students (no strike-through, no note why); Withhold shows withheld, never released and
// never copied; Release each file's state against the run's destination (three-way, `fileStates`; an up to date
// file greyed with a muted `released`, left out of its folder's roll-up) and withheld.
// Release ticks follow the saved `.releaseignore`, as the engine does. One `rowState` per row,
// from its standing (`.releaseignore` compiled with the institution's defaults) and, in Release,
// its state, draws the chip, the tick and the folder roll-up in every view; the Withhold summary
// counts the same standings.
//
// On the course page, Release: when the chosen semester edited released files of this repo (ahead,
// diverged or removed in semester, and the files it added under this repo's released folders), a
// notice over the tree says how many, with PR into source and "See which", which narrows the tree
// to those files and lists the added ones (no course row) under the notice ("Show all" undoes it).
//
// Decision B (2026-11-04): the institution's names in two groups. Never released (`.env`, `.git`,
// grading files, `.system`): no tick in any view, "never released". Content (`solutions`,
// `tests`): "withheld" like a line's row, its note "withheld by default", unticked in Withhold
// until a tick (or a typed line) writes the `!` line that releases it (its note "released by
// `!line`"); a release that names one outright still ships it.

import type { ComponentChildren } from 'preact';
import { useMemo, useRef, useState } from 'preact/hooks';
import { badgeFiles, bucket, buildTree, tally, treeModel, type RowModel } from '../edit/badges';
import { compileAll, type Rule } from '../edit/glob';
import type { SaveState } from '../edit/save';
import type { FileState as SavedFile, Files, TreePath, TreeState } from '../model/files';
import { plural } from '../model/format';
import { DENYLIST_CONTENT, DENYLIST_HARD } from '../model/selection';
import { withMark } from '../model/materialsRepo';
import {
  assignmentStems, COMMA_TITLE, countsWords, editedState, effectiveDestRepo, fileStates, foldersCopied, groupPicks, destPathOf, outOfStep, releaseBlocked, requestOf, semesterAdded,
  type FileState as ReleaseState, type Group, type ReleasePick, type ReleaseRow,
} from '../model/releaseState';
import type { Loaded } from '../model/status';
import { releasePicked } from '../ops/defs';
import { CheckLine, Loading, ghUrl } from './bits';
import { useYourSetup } from './FileRow';
import { OpenSplit } from './OpenSplit';
import { profileHref } from '../model/open';
import { SaveBar } from './edit';
import { Hint } from './Hint';
import { PatternTree } from './PatternTree';
import { destinationRows } from './ReleaseChips';
import { ReleaseFoot, ReleaseManifest, RepoSelect, groupKey, type Dests } from './ReleaseManifest';
import { RepoPickerModal } from './RepoPickerModal';
import { SplitPanes } from './Splitter';
import { ChipMeanings, NEVER_RELEASED_TITLE, StateChip, type ChipKind } from './StateChip';
import { RepoFile } from './Viewer';

export type FilesView = 'browse' | 'withhold' | 'release';

/** A semester a release may write into, and what it knows about its own repos. */
export interface SemesterRef {
  org: string;
  termLabel: string;
  loaded?: Loaded;
  ended?: boolean;
  /** The `release_destinations` allow-list, beside the repos this semester's releases wrote to. */
  destinations?: string[];
}

export interface FilesPanelProps {
  files: Files;
  courseOrg: string;
  /** Course mode: the page's repo. Semester mode: the source the From select starts on. */
  repo: string;
  mode: 'course' | 'semester';
  /** Can the viewer change this course? Without it the course page's panel is Browse alone and the semester's is not drawn. */
  write: boolean;
  /** Semester mode: the semester released into. */
  semester?: SemesterRef;
  /** Course mode: the Release into choices, newest first. */
  semesters?: SemesterRef[];
  /** Semester mode: the From select's repos; `onAdd` behind "Add a course repo…", over `others` (the caller sets the topic). */
  sources?: { materials: string[]; others: string[]; onAdd?: (repo: string) => void };
  /** The Schedule editor's new entry with the picks prefilled. */
  scheduleHref?: (semOrg: string, rows: ReleaseRow[]) => string;
  /** The `?` after Schedule on the calendar; default `SCHEDULE_LINK_LEAD` (bold) and `SCHEDULE_LINK_REST`. */
  scheduleHint?: ComponentChildren;
  /** An ahead chip's Keep for future semesters: the caller opens `keepFuture`. */
  onKeep?: (semester: SemesterRef) => void;
  /** A diverged file's open pull request; default: the destination repo's pull requests. */
  prUrl?: (semOrg: string, repo: string, path: string) => string | undefined;
  /** Withhold: saves `.releaseignore` with `text`; true when saved (the draft is dropped). */
  onSaveIgnore?: (text: string) => Promise<boolean>;
  ignoreSave?: SaveState;
  /** What Save writes from the draft; default: a trailing newline and the reviewed mark (`withMark`). */
  ignoreOut?: (draft: string) => string;
  /** Withhold: drawn under the summary (the "Withheld patterns reviewed" suggestion). */
  withholdExtra?: ComponentChildren;
}

const ADD = '__add';
const IDLE: SaveState = { kind: 'idle' };
const NOTHING = 'Nothing to release: the semester has this version.';
const NEVER_FOLDER = 'A folder release never copies these files; tick the file itself.';
export const REMOVED_TICK = 'Removed in the semester: a plain release changes nothing; Overwrite (on the semester’s Released materials) restores the course version.';
const linesOf = (text: string) => text.split('\n');
const textOf = (lines: string[]) => lines.join('\n');
const defaultOut = (d: string) => withMark(d.endsWith('\n') || !d ? d : `${d}\n`);

/** The `?` after Schedule on the calendar: its first sentence in bold, then the rest. */
export const SCHEDULE_LINK_LEAD = 'You do not need finished materials to automate their release in advance.';
export const SCHEDULE_LINK_REST = 'A schedule entry simply names a path and a date; the copy happens at that snapshot date, from whatever is in the path at that time. You can specify not-yet-existent file paths, or half-finished files, then keep editing in the course repo until the release time. Only what is there at that moment reaches students. Pre-scheduled entries also appear on the students’ calendar in advance, so the class can see what is coming.';
const scheduleLinkHint = () => <><b>{SCHEDULE_LINK_LEAD}</b> {SCHEDULE_LINK_REST}</>;

const FILES_HINT = 'The repo’s files. Browse: a name opens the file here. Withhold: a tick means the file reaches students; untick a file or folder to withhold it (.releaseignore), then Save. Release: tick files or folders and Release to copy them to the chosen semester’s materials repo now, at the same paths; copies are additive and nothing is added to the schedule. Each row says whether that semester already has the file.';
const RBH_HINT = 'Copies what you tick from the course repo to this semester’s materials repo now, at the same paths. Copies are additive and idempotent; files withheld by the repo’s .releaseignore never ship. Each row compares the course file with the semester’s repo the run writes to, at the same path; a group sent to another repo or path (Edit destination) is compared there in the manifest.';

/** A list of patterns, each in `<code>`, comma-separated. */
const codes = (xs: string[]) => xs.map((x, i) => <>{i ? ', ' : ''}<code>{x}</code></>);

/** The Withheld patterns `?` (decision B): what the list does, and the institution's two default groups. */
export function WithheldHint() {
  return (
    <Hint label="About withheld patterns" items={[
      <>One pattern per line, as in .gitignore, e.g. <code>solutions/</code>, <code>**/draft-*</code>, <code>exam-*.pdf</code>.</>,
      'A matching file is skipped by every copy out of this repo: scheduled and manual releases, assignment hand-outs and the public website, even when a release names its folder. A release that names the file itself is refused.',
      <>The institution withholds by default: {codes(DENYLIST_HARD)}, which can never be released, and the content names {codes(DENYLIST_CONTENT)}, which a <code>!</code> line here overrides for this repo. A release that names one of those outright still ships it.</>,
      'Patterns stop future copies only: a file already released stays in the semester until you remove it there.',
    ]}>
      Guards for this repo: files that must never leave it by mistake.
    </Hint>
  );
}

/**
 * Above the pattern box, folded (decision B): the institution's never-released names, muted, and
 * its content names as chips; a click on one writes its `!<name>/` line (`onAdd`), which releases
 * every folder of that name in this repo.
 */
export function InstitutionDefaults({ onAdd }: { onAdd?: (line: string) => void }) {
  const [open, setOpen] = useState(false);
  return (
    <details class="fold withhold-defaults" open={open} onToggle={(e) => setOpen((e.currentTarget as HTMLDetailsElement).open)}>
      <summary>Withheld by default{open ? ' (set at institutional-level)' : ''}</summary>
      <ul class="defaults-hard">{DENYLIST_HARD.map((x) => <li><code>{x}</code></li>)}</ul>
      <ul class="defaults-content">
        {DENYLIST_CONTENT.map((x) => (
          <li><button type="button" class="chip" title={`Release every ${x}/ folder in this repo: adds !${x}/`} disabled={!onAdd} onClick={() => onAdd?.(`!${x}/`)}>{x}</button></li>
        ))}
      </ul>
    </details>
  );
}

/** The line a tree click added or changed (the first index where the lists differ); null when nothing changed or a line went. */
export function changedLine(before: string[], after: string[]): number | null {
  if (after.length < before.length) return null;
  if (before.length === after.length && before.every((l, i) => l === after[i])) return null;
  let i = 0;
  while (i < before.length && before[i] === after[i]) i++;
  return i;
}

interface Flash {
  line: number;
  n: number;
}

/** The pattern textarea over a mirror of its lines, where the line a tree click changed flashes (decision 0026 rule 5). */
export function PatternBox({ id, text, onText, flash }: { id: string; text: string; onText: (t: string) => void; flash: Flash | null }) {
  const mirror = useRef<HTMLPreElement>(null);
  return (
    <div class="pattern-box">
      <pre class="pattern-mirror" aria-hidden="true" ref={mirror}>
        {linesOf(text).map((l, i) => (flash?.line === i ? <span key={`f${flash.n}`} class="pm-line flash">{l || ' '}</span> : <span key={`l${i}`} class="pm-line">{l || ' '}</span>))}
      </pre>
      <textarea class="code" id={id} value={text} onInput={(e) => onText((e.target as HTMLTextAreaElement).value)}
        onScroll={(e) => { if (mirror.current) mirror.current.scrollTop = (e.target as HTMLTextAreaElement).scrollTop; }} />
    </div>
  );
}

/** Beside the pattern box: the rules that match no file, or that every rule matches one. */
function Unmatched({ files, lines, rules, partial }: { files: string[]; lines: string[]; rules: Rule[]; partial: boolean }) {
  const b = badgeFiles(files, lines, rules);
  if (!b.rules) return <p class="footnote">No rules yet.</p>;
  if (partial) return <p class="footnote">Cannot tell which rules match nothing: the file list is incomplete.</p>;
  return b.unmatched.length ? <ul class="unmatched">{b.unmatched.map((r) => <li><code>{r}</code> matches no file</li>)}</ul> : <p class="footnote">Every rule matches at least one file.</p>;
}

/** A tree's files, read once per tree state: the blobs with their shas, and their paths. */
const READ = new WeakMap<TreeState, { blobs: TreePath[]; paths: string[] }>();
const NO_FILES = { blobs: [] as TreePath[], paths: [] as string[] };
function filesOf(tree: TreeState) {
  if (tree.kind !== 'ready') return NO_FILES;
  let r = READ.get(tree);
  if (!r) {
    const blobs = tree.paths.filter((x) => !x.dir);
    READ.set(tree, (r = { blobs, paths: blobs.map((b) => b.path) }));
  }
  return r;
}

/** A saved `.releaseignore` compiled with the institution's defaults, once per file state. */
const SAVED = new WeakMap<SavedFile, Rule[]>();
function savedRules(ign: SavedFile): Rule[] {
  let r = SAVED.get(ign);
  if (!r) SAVED.set(ign, (r = compileAll(linesOf(ign.kind === 'ready' ? ign.text : ''), { defaults: true })));
  return r;
}

/** A repo's files with their shas, `.releaseignore` as saved, and how the tree reads. */
function repoOf(files: Files, org: string, repo: string) {
  const tree = files.tree(org, repo);
  const ign = files.file(org, repo, '.releaseignore');
  return { tree, ...filesOf(tree), ign, saved: ign.kind === 'ready' ? ign.text : '' };
}

const NO_STATES: Record<string, ReleaseState> = {};
const NO_PATHS: string[] = [];
const NO_FOLDERS = new Set<string>();
const pickKey = (repo: string, path: string, dir: boolean) => `${repo}\0${dir ? 'd' : 'f'}\0${path}`;
/** The folders above a path, nearest last. */
const above = (path: string) => path.split('/').slice(0, -1).map((_, i, a) => a.slice(0, i + 1).join('/'));

/** One row as every view draws it: its chip, the folder's counts, and the Release tick (none: no tick; gap: a blank in its place; else why it is disabled, null when it is not, and an enabled tick's title). */
interface RowState {
  chip: ChipKind | null;
  row: RowModel<ReleaseState>;
  tick: 'none' | 'gap' | { off: string | null; title?: string };
}

export function FilesPanel(p: FilesPanelProps) {
  if (!p.write && p.mode !== 'course') return null;
  return <Panel {...p} />;
}

function Panel(p: FilesPanelProps) {
  const course = p.mode === 'course';
  const [picked, setView] = useState<FilesView>(course ? 'browse' : 'release');
  const [file, setFile] = useState<string | null>(null);
  const [draft, setDraft] = useState<string | null>(null);
  const [flash, setFlash] = useState<Flash | null>(null);
  const [semOrg, setSemOrg] = useState<string | null>(null);
  const [src, setSrc] = useState(p.repo);
  const [adding, setAdding] = useState(false);
  const [picks, setPicks] = useState<ReleasePick[]>([]);
  const [runPick, setRunPick] = useState<{ org: string; repo: string } | null>(null);
  const [overrides, setOverrides] = useState<Record<string, { destRepo?: string; destPath?: string }>>({});
  const [chosen, setChosen] = useState<string[]>([]);
  // Course Release view: the tree narrowed to the files the semester edited ("See which").
  const [onlyEdited, setOnlyEdited] = useState(false);
  const setup = useYourSetup();
  // Read-only: Browse alone, whatever view was last picked.
  const view = p.write ? picked : 'browse';
  const release = view === 'release';

  const repo = course ? p.repo : src;
  const sem = course ? (p.semesters ?? []).find((s) => s.org === semOrg) ?? p.semesters?.[0] : p.semester;
  const here = repoOf(p.files, p.courseOrg, repo);
  const text = release ? here.saved : (draft ?? here.saved);
  const lines = useMemo(() => linesOf(text), [text]);
  const rules = useMemo(() => compileAll(lines, { defaults: true }), [lines]);
  const tree = useMemo(() => buildTree(here.paths), [here.paths]);

  // ---- Release only: where this run writes, the semester's trees, the picks (decision 6)
  const into = release ? sem : undefined;
  const defaultDest = into ? effectiveDestRepo(into.loaded) : '';
  const runRepo = into && runPick?.org === into.org ? runPick.repo : defaultDest;
  const upstream = into && runRepo ? p.files.tree(into.org, runRepo, 'upstream') : null;
  const live = into && runRepo ? p.files.tree(into.org, runRepo) : null;
  // Each file's state at the run's destination.
  const states = useMemo(() => (live ? fileStates(here.blobs, upstream, live) : NO_STATES), [here.blobs, upstream, live]);
  const model = useMemo(() => treeModel<ReleaseState>(tree, rules, lines, { state: live ? (path) => states[path] : undefined }), [tree, rules, lines, live, states]);
  const copied = useMemo(() => (release ? foldersCopied(here.paths, rules) : NO_FOLDERS), [release, here.paths, rules]);

  const pickSet = useMemo(() => new Set(picks.map((x) => pickKey(x.repo, x.path, x.dir))), [picks]);
  const isPicked = (path: string, dir: boolean) => pickSet.has(pickKey(repo, path, dir));
  const inPicked = (path: string) => above(path).some((f) => pickSet.has(pickKey(repo, f, true)));
  const togglePick = (path: string, dir: boolean) =>
    setPicks(isPicked(path, dir)
      ? picks.filter((x) => !(x.repo === repo && x.path === path && x.dir === dir))
      // A folder picked takes its contents with it: their own picks go.
      : [...picks.filter((x) => !(dir && x.repo === repo && x.path.startsWith(`${path}/`))), { repo, path, dir }]);

  const pickedRepos = into ? [...new Set(picks.map((x) => x.repo))] : [];
  const reads = pickedRepos.map((r) => (r === repo ? here : repoOf(p.files, p.courseOrg, r)));
  const groups = useMemo(() => {
    if (!into) return [];
    const trees = Object.fromEntries(pickedRepos.map((r, i) => [r, { files: reads[i].paths, rules: savedRules(reads[i].ign) }]));
    return groupPicks(picks, trees).map((g) => ({ ...g, ...overrides[groupKey(g)] }));
  }, [!!into, picks, overrides, ...reads.flatMap((o) => [o.tree, o.ign])]);

  const rowState = (path: string, dir: boolean): RowState => {
    const row = model.get(path)!;
    const s = row.s;
    const never = s.kind === 'never_released' || s.kind === 'never_copied' ? s.kind : null;
    const out = bucket(s) === 'withheld';
    const state = !dir && !never && !out ? states[path] : undefined;
    // Browse: whatever does not reach students reads withheld, nothing more.
    const chip: ChipKind | null = view === 'browse' ? (never || out ? 'withheld' : null) : never ?? (out ? 'withheld' : state === 'up_to_date' ? 'released' : state && state !== 'unknown' ? state : null);
    if (!release) return { chip, row, tick: 'none' };
    // Withheld by a line, or never material: no tick. A content name withheld by default ships when ticked (named outright).
    if (s.kind === 'own' || s.kind === 'broader' || s.kind === 'never_copied') return { chip, row, tick: 'gap' };
    const off = never ? NEVER_RELEASED_TITLE : inPicked(path) ? 'Picked with its folder' : path.includes(',') ? COMMA_TITLE
      : dir ? (copied.has(path) ? null : NEVER_FOLDER) : state === 'up_to_date' ? NOTHING : null;
    return { chip, row, tick: { off, title: off === null && state === 'removed' ? REMOVED_TICK : undefined } };
  };

  // Each group's states at its own destination, worked out once per change of its inputs: the
  // group, the run's repo and the three trees it compares (each tree state a stable object until it is read again).
  const groupTrees = groups.map((g) => {
    const to = g.destRepo || runRepo;
    return into ? [p.files.tree(p.courseOrg, g.repo), p.files.tree(into.org, to, 'upstream'), p.files.tree(into.org, to)] as const : null;
  });
  const groupStates = useMemo(() => new Map(groups.map((g, i) => {
    const t = groupTrees[i];
    if (!t) return [groupKey(g), NO_STATES];
    const [courseTree, up, at] = t;
    const ship = new Set(g.ship);
    const blobs = filesOf(courseTree).blobs.filter((b) => ship.has(b.path));
    return [groupKey(g), fileStates(blobs, up, at, { destPath: (x) => destPathOf(g, x) })];
  })), [groups, runRepo, ...groupTrees.flat()]);
  const stateOf = (g: Group, path: string) => groupStates.get(groupKey(g))?.[path];
  // A plain release of a file the semester removed copies nothing: the run's panel says so.
  const anyRemoved = groups.some((g) => g.ship.some((x) => stateOf(g, x) === 'removed'));

  const dests = into ? destsOf(p, into, defaultDest, chosen) : null;
  const prOf = (destRepo: string, path: string) => (into ? (p.prUrl?.(into.org, destRepo, path) ?? `${ghUrl(into.org, destRepo)}/pulls`) : undefined);
  const keep = into && p.onKeep ? () => p.onKeep!(into) : undefined;
  const chip = (path: string, dir: boolean): ComponentChildren => {
    const r = rowState(path, dir);
    const words = release && dir && r.chip === null ? countsWords(r.row.states) : '';
    return (
      <>
        {r.chip ? <StateChip kind={r.chip} onTick={r.chip === 'behind' && !isPicked(path, false) && !inPicked(path) ? () => togglePick(path, false) : undefined} onKeep={keep} prUrl={r.chip === 'diverged' ? prOf(runRepo, path) : undefined} /> : null}
        {/* A folder's roll-up, about its contents: shown only while it is closed (`.ft-rollup`); its own chip above shows either way. */}
        {dir && r.chip === null && (r.row.withheld || words) ? (
          <span class="ft-rollup">
            {r.row.withheld ? <span class="chip muted">{r.row.withheld} withheld</span> : null}
            {words ? <span class="chip">{words}</span> : null}
          </span>
        ) : null}
      </>
    );
  };
  // Course Release view: the files this repo released that the semester edited (ahead, diverged or removed), as their chips say,
  // and the files the semester added under this repo's released folders (no course row: listed under the notice).
  const edited = course && release && live ? here.paths.filter((f) => editedState(rowState(f, false).chip as ReleaseState)) : NO_PATHS;
  const status = into?.loaded?.kind === 'ready' ? into.loaded.status : undefined;
  const added = course && release && status && into ? semesterAdded(outOfStep(status, p.files, p.courseOrg, into.org, defaultDest), repo, runRepo) : NO_PATHS;
  const changed = edited.length + added.length;
  const narrowed = onlyEdited && changed > 0;
  const editedKey = edited.join('\n');
  const shownTree = useMemo(() => (narrowed ? buildTree(edited) : tree), [narrowed, editedKey, tree]);
  const editedNote = changed && into ? (
    <>
      <p class="edited-note" role="status">
        <span class="step-bang" aria-hidden="true">!</span>
        <span>{into.termLabel} has edited {plural(changed, 'released file')} from this repo.</span>
        {keep ? <button class="btn small quiet" type="button" onClick={keep}>PR into source</button> : null}
        <button class="textlink" type="button" aria-pressed={narrowed} onClick={() => setOnlyEdited(!narrowed)}>{narrowed ? 'Show all' : 'See which'}</button>
      </p>
      {narrowed && added.length ? (
        <ul class="edited-added" aria-label="Added in the semester">
          {added.map((path) => (
            <li><code>{path}</code> <StateChip kind="ahead" onKeep={keep} /> <OpenSplit org={into.org} repo={runRepo} path={path} setup={setup} profile={profileHref(into.org)} /></li>
          ))}
        </ul>
      ) : null}
    </>
  ) : null;
  const pickControl = (path: string, dir: boolean) => {
    const t = rowState(path, dir).tick;
    if (typeof t !== 'object') return <span class="ft-gap" />;
    const by = inPicked(path);
    return <input type="checkbox" class="ft-tick" checked={by || isPicked(path, dir)} disabled={t.off !== null} title={t.off ?? t.title} aria-label={`Pick ${path}${dir ? '/' : ''}`} onClick={() => togglePick(path, dir)} />;
  };
  const pickRow = (path: string) => {
    const t = rowState(path, false).tick;
    if (typeof t === 'object' && t.off === null) togglePick(path, false);
  };

  // ---- Withhold: the draft, written by the tree or the box (decision 0026 rule 5)
  const fromTree = (next: string[]) => {
    const line = changedLine(lines, next);
    if (line !== null) setFlash({ line, n: (flash?.n ?? 0) + 1 });
    setDraft(textOf(next));
  };
  // A content-name chip: its `!name/` line last, once.
  const addLine = (line: string) => {
    if (lines.some((l) => l.trim() === line)) return;
    fromTree(lines.length && !lines[lines.length - 1].trim() ? [...lines.slice(0, -1), line] : [...lines, line]);
  };
  const out = (p.ignoreOut ?? defaultOut)(draft ?? here.saved);
  const saveIgn = async () => {
    if (p.onSaveIgnore && (await p.onSaveIgnore(out))) {
      setDraft(null);
      setFlash(null);
    }
  };

  // ---- the tree, one instance for every view
  const name = (path: string) => (
    <button type="button" class="ft-name" aria-current={file === path ? 'true' : undefined} onClick={() => setFile(path)}>{path.split('/').pop()}</button>
  );
  const partial = here.tree.kind === 'ready' && here.tree.truncated;
  const treeView = here.tree.kind === 'loading' ? <Loading what="Reading the repo" />
    : here.tree.kind !== 'ready' ? <p class="footnote">Could not read the repo’s files.</p>
    : narrowed && !edited.length ? <p class="footnote">No course file changed: the semester’s own files are listed above.</p>
    : (
      <PatternTree
        files={narrowed ? edited : here.paths}
        patterns={lines}
        rules={rules}
        model={{ tree: shownTree, rows: model }}
        onChange={view === 'withhold' ? fromTree : () => undefined}
        mode={view === 'browse' ? 'exclude' : 'select'}
        releasedWord=""
        control={view === 'browse' ? () => null : release ? pickControl : undefined}
        chip={chip}
        rowClick={view === 'withhold' ? true : release ? pickRow : undefined}
        nameLink={view === 'browse' ? name : undefined}
        plain={view === 'browse'}
      />
    );

  const right = view === 'browse'
    ? (file ? <RepoFile org={p.courseOrg} at={{ repo, path: file }} /> : <p class="empty-note">Choose a file to view it here.</p>)
    : view === 'withhold' ? (
      <>
        <InstitutionDefaults onAdd={addLine} />
        <div class="field">
          <label for="ign-pat">Withheld patterns</label> <WithheldHint />
          <PatternBox id="ign-pat" text={draft ?? here.saved} onText={(t) => { setFlash(null); setDraft(t); }} flash={flash} />
          <p class="hint">One pattern per line, as in .gitignore. Click the tree, or write them here.</p>
        </div>
        <div class="field"><span class="label">Rules</span><Unmatched files={here.paths} lines={lines} rules={rules} partial={partial} /></div>
      </>
    )
    : into && dests ? <ReleaseManifest groups={groups} runRepo={runRepo} dests={dests} stateOf={stateOf} onKeep={keep}
      prUrl={(g, path) => prOf(g.destRepo || runRepo, destPathOf(g, path))} onGroupDest={(k, patch) => {
      if (patch.destRepo && !chosen.includes(patch.destRepo)) setChosen([...chosen, patch.destRepo]);
      setOverrides({ ...overrides, [k]: { ...overrides[k], ...patch } });
    }} />
    : <p class="empty-note">No semester to release into yet.</p>;

  const fromRow = course ? (
    <>
      <label for="rel-into" class="split-lbl">Release into</label>
      <select id="rel-into" onChange={(e) => { setSemOrg((e.target as HTMLSelectElement).value); setOverrides({}); }}>
        {(p.semesters ?? []).map((s) => <option value={s.org} selected={s.org === sem?.org}>{s.termLabel}{s.ended ? ' (ended)' : ''}</option>)}
      </select>
    </>
  ) : (
    <>
      <label for="rbh-from" class="split-lbl">From</label>
      <select id="rbh-from" onChange={(e) => {
        const s = e.target as HTMLSelectElement;
        if (s.value !== ADD) return void (setSrc(s.value), setFile(null), setDraft(null));
        s.value = src;
        setAdding(true);
      }}>
        {p.sources?.materials.length ? <optgroup label="Course materials repos">{p.sources.materials.map((r) => <option value={r} selected={r === src}>{r}</option>)}</optgroup> : null}
        <optgroup label="Other repos">
          {(p.sources?.others ?? []).map((r) => <option value={r} selected={r === src}>{r}</option>)}
          {[src].filter((r) => r && !p.sources?.materials.includes(r) && !p.sources?.others.includes(r)).map((r) => <option value={r} selected>{r}</option>)}
          {p.sources?.onAdd ? <option value={ADD}>Add a course repo…</option> : null}
        </optgroup>
      </select>
    </>
  );
  // The run's To row over the manifest: the semester org, then its repo.
  const toRow = into && dests ? (
    <>
      <span class="split-lbl">To</span>
      <span class="mf-into">{into.org} /</span>
      <RepoSelect cls="mf-repo" label="To repo" value={runRepo} dests={dests} onPick={(r) => {
        if (!chosen.includes(r)) setChosen([...chosen, r]);
        setRunPick({ org: into.org, repo: r });
      }} />
    </>
  ) : null;

  const rows = into ? requestOf(groups, { defaultDestRepo: runRepo }) : [];
  const body = (
    <>
      {editedNote}
      <SplitPanes label={repo} left={treeView} right={right} heads={release ? [fromRow, toRow] : undefined} />
      {partial && !release ? <CheckLine cls="bad">GitHub returned only part of this repo’s file list; badges may be incomplete.</CheckLine> : null}
      {view === 'withhold' ? (
        <div class="stack files-withhold">
          {here.tree.kind === 'ready' ? <WithholdCounts n={tally(here.paths, (f) => model.get(f)!.s)} /> : null}
          {p.withholdExtra}
          <p class="footnote">This reads the repo’s top-level .releaseignore; one in a subfolder still applies there.</p>
          <SaveBar state={p.ignoreSave ?? IDLE} onSave={() => void saveIgn()} small file={ignRef(p, repo, here.ign.kind !== 'absent')}
            disabled={!p.onSaveIgnore || here.ign.kind === 'loading' || (here.ign.kind === 'ready' && out === here.saved)} />
        </div>
      ) : null}
      {into ? (
        <ReleaseFoot paths={rows.length} blocked={releaseBlocked(rows)} files={groups.reduce((n, g) => n + g.ship.length, 0)} def={releasePicked({ courseOrg: p.courseOrg, cohortOrg: into.org, where: into.termLabel }, groups, { defaultDestRepo: runRepo, removed: anyRemoved })} />
      ) : null}
      {adding && p.sources?.onAdd ? (
        <RepoPickerModal kind="source" rows={p.sources.others.map((name) => ({ name, note: 'not course materials yet' }))} onClose={() => setAdding(false)} onChoose={(r) => { p.sources!.onAdd!(r); setSrc(r); }} />
      ) : null}
    </>
  );
  // The panel's lede in Release (the semester page's always): the picks, or none, as a new schedule entry.
  const sched = into && p.scheduleHref ? (
    <p class="lede rh-sched">
      Want to automate these releases? <a class="textlink" href={p.scheduleHref(into.org, rows)}>Schedule on the calendar →</a>{' '}
      <Hint small label="About scheduling these releases">{p.scheduleHint ?? scheduleLinkHint()}</Hint>
    </p>
  ) : null;
  const hint = <><span class="para">{course ? FILES_HINT : RBH_HINT}</span><ChipMeanings /></>;

  if (!course) {
    return (
      <details class="panel section panel-fold files-panel" open>
        {/* The ? inside the summary must not fold the panel. */}
        <summary><h2 id="rbh-h">Release by hand <span onClick={(e) => e.preventDefault()}><Hint label="About Release by hand">{hint}</Hint></span></h2></summary>
        {sched}
        {body}
      </details>
    );
  }
  const tab = (v: FilesView, label: string) => (
    <button type="button" role="tab" id={`files-tab-${v}`} aria-selected={view === v} onClick={() => { setView(v); setFlash(null); }}>{label}</button>
  );
  return (
    <section class="panel section files-panel" aria-labelledby="files-h">
      <div class="section-head">
        <h2 id="files-h">Files <Hint label="About the files">{hint}</Hint></h2>
        {p.write ? <div class="seg" role="tablist" aria-label="View">{tab('browse', 'Browse')}{tab('withhold', 'Withhold')}{tab('release', 'Release')}</div> : null}
      </div>
      {sched}
      {body}
    </section>
  );
}

/** Where Save writes `.releaseignore`, for the Save bar's file link. */
function ignRef(p: FilesPanelProps, repo: string, exists: boolean) {
  const repos = p.files.repos(p.courseOrg);
  return { org: p.courseOrg, repo, path: '.releaseignore', exists, branch: repos.kind === 'ready' ? repos.repos.find((r) => r.name === repo)?.default_branch : undefined };
}

/**
 * The destination choices every repo select shares (Release view only): the run's default, the
 * `release_destinations` list, the repos this semester's releases wrote to and any chosen this
 * session, then the semester's other repos a release may write into (`destinationRows`), each
 * with how many releases wrote there.
 */
function destsOf(p: FilesPanelProps, into: SemesterRef, defaultDest: string, chosen: string[]): Dests {
  const st = into.loaded?.kind === 'ready' ? into.loaded.status : undefined;
  const wrote = (st?.releases ?? []).map((r) => r.dest?.repo).filter((r): r is string => !!r);
  const options = [...new Set([defaultDest, ...(into.destinations ?? []), ...wrote, ...chosen].filter(Boolean))];
  const repos = p.files.repos(into.org);
  const list = repos.kind === 'ready' ? repos.repos : [];
  // Any `<assignment stem>-...` name is a student's repo, made or not (the engine's rule).
  const reserved = { org: into.org, stems: assignmentStems(st) };
  const note = (name: string) => {
    const n = wrote.filter((r) => r === name).length;
    return name === defaultDest ? 'the default' : n ? plural(n, 'release') : 'no releases yet';
  };
  return {
    options,
    reserved,
    rows: [...options.map((name) => ({ name, note: note(name) })), ...destinationRows(list.filter((r) => r.name), options, reserved).map((r) => ({ ...r, note: note(r.name) }))],
  };
}

/** The Withhold view's last line: the repo's files never released, withheld (by the list or by default), and released. */
function WithholdCounts({ n }: { n: { never: number; withheld: number; released: number } }) {
  return <p class="footnote">{n.never} never released · {n.withheld} withheld · {n.released} released</p>;
}
