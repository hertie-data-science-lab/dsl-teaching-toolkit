// The Files panel (plan 2026-11-04, decisions 1-8): one course repo's tree on the left, a right
// column beside it, and a `Browse | Withhold | Release` control. Browse: a name opens the file in
// the right column. Withhold: ticked = reaches students, untick to withhold (`.releaseignore`),
// the pattern box right and the Save bar below, as the Withheld panel had them. Release: ticked =
// copy now, the manifest right, one Release button. The course repo settings page shows all three
// (mode `course`, `Release into [semester]`); the semester's Released materials page only Release
// (mode `semester`, `From [course repo]`, "Release by hand"). The tree is one instance across the
// views, so the folders open stay open.
//
// Chips per view (decision 3): Browse shows withheld and never copied; Withhold withheld, never
// copied and only if named; Release each file's state against the run's destination (three-way,
// `fileStates`) and withheld. Release ticks follow the saved `.releaseignore`, as the engine does.

import type { ComponentChildren } from 'preact';
import { useRef, useState } from 'preact/hooks';
import { badgeFiles, buildTree, denylisted, neverMaterial, withholdCounts, type TreeNode } from '../edit/badges';
import { compileAll, withheldBy } from '../edit/glob';
import type { SaveState } from '../edit/save';
import type { Files, TreePath } from '../model/files';
import { plural } from '../model/format';
import { withMark } from '../model/materialsRepo';
import {
  countsWords, disabledTick, effectiveDestRepo, eligibleDestination, fileStates, folderCounts, groupPicks, destPathOf,
  type FileState as ReleaseState, type Group, type ReleasePick, type RepoFiles,
} from '../model/releaseState';
import type { Loaded } from '../model/status';
import { releasePicked } from '../ops/defs';
import { CheckLine, Loading, ghUrl } from './bits';
import { SaveBar } from './edit';
import { Hint } from './Hint';
import { PatternTree } from './PatternTree';
import { ReleaseFoot, ReleaseManifest, ToRow, groupKey, type Dests } from './ReleaseManifest';
import { RepoPickerModal, type RepoRow } from './RepoPickerModal';
import { SplitPanes } from './Splitter';
import { ChipMeanings, StateChip } from './StateChip';
import { RepoFile } from './Viewer';

export type FilesView = 'browse' | 'withhold' | 'release';

/** A semester a release may write into, and what it knows about its own repos. */
export interface SemesterRef {
  org: string;
  termLabel: string;
  loaded?: Loaded;
  ended?: boolean;
  /** Where a release lands when nothing names a repo; default: the effective `materials_repo` from status.json. */
  defaultDest?: string;
  /** The `release_destinations` allow-list, beside the repos this semester's releases wrote to. */
  destinations?: string[];
  /** Student and team repo names, refused as destinations. */
  students?: string[];
}

export interface FilesPanelProps {
  files: Files;
  courseOrg: string;
  /** Course mode: the page's repo. Semester mode: the source the From select starts on. */
  repo: string;
  mode: 'course' | 'semester';
  /** Can the viewer change this course? Without it the panel is not drawn. */
  write: boolean;
  /** Semester mode: the semester released into. */
  semester?: SemesterRef;
  /** Course mode: the Release into choices, newest first. */
  semesters?: SemesterRef[];
  /** Semester mode: the From select's repos; `addable` + `onAdd` behind "Add a course repo…" (the caller sets the topic). */
  sources?: { materials: string[]; others: string[]; addable?: RepoRow[]; onAdd?: (repo: string) => void };
  /** The Schedule editor's new entry with the picks prefilled. */
  scheduleHref?: (run: { semOrg: string; runRepo: string; groups: Group[] }) => string;
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
const linesOf = (text: string) => text.split('\n');
const textOf = (lines: string[]) => lines.join('\n');
const defaultOut = (d: string) => withMark(d.endsWith('\n') || !d ? d : `${d}\n`);

const FILES_HINT = 'The repo’s files. Browse: a name opens the file here. Withhold: a tick means the file reaches students; untick a file or folder to withhold it (.releaseignore), then Save. Release: tick files or folders and Release to copy them to the chosen semester’s materials repo now, at the same paths; copies are additive and nothing is added to the schedule. Each row says whether that semester already has the file.';
const RBH_HINT = 'Copies what you tick from the course repo to this semester’s materials repo now, at the same paths. Copies are additive and idempotent; files withheld by the repo’s .releaseignore never ship. Each row compares the course file with the semester’s repo the run writes to, at the same path; a group sent to another repo or path (Edit destination) is compared there in the manifest.';

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
function Unmatched({ files, lines, partial }: { files: string[]; lines: string[]; partial: boolean }) {
  const b = badgeFiles(files, lines);
  if (!b.rules) return <p class="footnote">No rules yet.</p>;
  if (partial) return <p class="footnote">Cannot tell which rules match nothing: the file list is incomplete.</p>;
  return b.unmatched.length ? <ul class="unmatched">{b.unmatched.map((r) => <li><code>{r}</code> matches no file</li>)}</ul> : <p class="footnote">Every rule matches at least one file.</p>;
}

/** Every folder of a tree by its path, for the roll-ups. */
function foldersOf(nodes: TreeNode[], out = new Map<string, TreeNode>()): Map<string, TreeNode> {
  for (const n of nodes) if (n.children) {
    out.set(n.path, n);
    foldersOf(n.children, out);
  }
  return out;
}

/** A repo's files with their shas, `.releaseignore` as saved, and how the tree reads. */
function repoOf(files: Files, org: string, repo: string) {
  const tree = files.tree(org, repo);
  const blobs: TreePath[] = tree.kind === 'ready' ? tree.paths.filter((x) => !x.dir) : [];
  const ign = files.file(org, repo, '.releaseignore');
  const saved = ign.kind === 'ready' ? ign.text : '';
  return { tree, blobs, paths: blobs.map((b) => b.path), ign, saved };
}

export function FilesPanel(p: FilesPanelProps) {
  const course = p.mode === 'course';
  const [view, setView] = useState<FilesView>(course ? 'browse' : 'release');
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
  if (!p.write) return null;

  const repo = course ? p.repo : src;
  const sem = course ? (p.semesters ?? []).find((s) => s.org === semOrg) ?? p.semesters?.[0] : p.semester;
  const here = repoOf(p.files, p.courseOrg, repo);
  const release = view === 'release';
  const lines = linesOf(release ? here.saved : (draft ?? here.saved));
  const rules = compileAll(lines);
  const folders = foldersOf(buildTree(here.paths));

  // Where this run writes: the semester's effective default until another repo is chosen.
  const defaultDest = sem ? (sem.defaultDest ?? effectiveDestRepo(sem.loaded)) : '';
  const runRepo = sem && runPick?.org === sem.org ? runPick.repo : defaultDest;
  const releases = sem?.loaded?.kind === 'ready' ? (sem.loaded.status.releases ?? []) : [];
  const wrote = releases.map((r) => r.dest?.repo).filter((r): r is string => !!r);
  const reserved = { org: sem?.org ?? '', students: sem?.students ?? [] };
  const options = [...new Set([defaultDest, ...(sem?.destinations ?? []), ...wrote, ...chosen].filter(Boolean))];
  const orgRepos = sem ? p.files.repos(sem.org) : null;
  const eligible = orgRepos?.kind === 'ready' ? orgRepos.repos.map((r) => r.name).filter((n) => n && eligibleDestination(n, reserved) === null) : [];
  const dests: Dests = {
    options,
    reserved,
    rows: [...new Set([...options, ...eligible])].map((name) => {
      const n = wrote.filter((r) => r === name).length;
      return { name, note: name === defaultDest ? 'the default' : n ? plural(n, 'release') : 'no releases yet' };
    }),
  };
  const choose = (name: string) => !options.includes(name) && setChosen([...chosen, name]);

  // Each file's state at the run's destination, read only in Release (it reads the semester's trees).
  const statesAt = (destRepo: string, blobs: TreePath[], destPath?: (path: string) => string): Record<string, ReleaseState> =>
    sem && destRepo ? fileStates(blobs, p.files.tree(sem.org, destRepo, 'upstream'), p.files.tree(sem.org, destRepo), { destPath }) : {};
  const states = release ? statesAt(runRepo, here.blobs) : {};

  // ---- Release: the picks, across course repos (decision 6)
  const inPicked = (path: string) => picks.some((x) => x.repo === repo && x.dir && path.startsWith(`${x.path}/`));
  const isPicked = (path: string, dir: boolean) => picks.some((x) => x.repo === repo && x.path === path && x.dir === dir);
  const togglePick = (path: string, dir: boolean) =>
    setPicks(isPicked(path, dir)
      ? picks.filter((x) => !(x.repo === repo && x.path === path && x.dir === dir))
      // A folder picked takes its contents with it: their own picks go.
      : [...picks.filter((x) => !(dir && x.repo === repo && x.path.startsWith(`${path}/`))), { repo, path, dir }]);
  const outOf = (path: string, dir = false) => !!withheldBy(rules, path, dir);
  const shipsNot = (path: string) => outOf(path) || neverMaterial(path) || denylisted(path);
  const trees: Record<string, RepoFiles> = {};
  for (const r of new Set(picks.map((x) => x.repo))) {
    const o = r === repo ? here : repoOf(p.files, p.courseOrg, r);
    trees[r] = { files: o.paths, rules: compileAll(linesOf(o.saved)) };
  }
  const groups = groupPicks(picks, trees).map((g) => ({ ...g, ...overrides[groupKey(g)] }));
  const def = releasePicked({ courseOrg: p.courseOrg, cohortOrg: sem?.org, where: sem?.termLabel ?? '' }, groups, { defaultDestRepo: runRepo });
  const nPaths = def.args.course_source_path ? String(def.args.course_source_path).split(',').length : 0;
  const nFiles = groups.reduce((n, g) => n + g.ship.length, 0);
  const prOf = (destRepo: string, path: string) => (sem ? (p.prUrl?.(sem.org, destRepo, path) ?? `${ghUrl(sem.org, destRepo)}/pulls`) : undefined);
  const keep = sem && p.onKeep ? () => p.onKeep!(sem) : undefined;
  const groupStates = new Map<string, Record<string, ReleaseState>>();
  const stateOf = (g: Group, path: string) => {
    const k = groupKey(g);
    if (!groupStates.has(k)) {
      const blobs = (g.repo === repo ? here : repoOf(p.files, p.courseOrg, g.repo)).blobs.filter((b) => g.ship.includes(b.path));
      groupStates.set(k, statesAt(g.destRepo || runRepo, blobs, (x) => destPathOf(g, x)));
    }
    return groupStates.get(k)![path];
  };

  const pickControl = (path: string, dir: boolean) => {
    if (outOf(path, dir) || neverMaterial(path)) return <span class="ft-gap" />;
    const by = inPicked(path);
    const off = by ? 'Picked with its folder' : disabledTick(path, dir, states[path], here.paths) ? (dir ? NEVER_FOLDER : NOTHING) : null;
    return <input type="checkbox" class="ft-tick" checked={by || isPicked(path, dir)} disabled={off !== null} title={off ?? undefined} aria-label={`Pick ${path}${dir ? '/' : ''}`} onClick={() => togglePick(path, dir)} />;
  };
  const releaseSide = (path: string, dir: boolean): ComponentChildren => {
    if (outOf(path, dir)) return null;
    if (!dir) {
      if (neverMaterial(path)) return <StateChip kind="never_copied" />;
      if (denylisted(path)) return <StateChip kind="only_if_named" />;
      const s = states[path];
      if (!s || s === 'unknown') return null;
      return <StateChip kind={s} onTick={s === 'behind' && !isPicked(path, false) && !inPicked(path) ? () => togglePick(path, false) : undefined} onKeep={keep} prUrl={prOf(runRepo, path)} />;
    }
    const node = folders.get(path);
    if (!node) return null;
    const counts = folderCounts(node, states, shipsNot);
    const words = countsWords(counts);
    if (words) return <span class={`chip${Object.keys(counts).every((k) => k === 'up_to_date') ? ' muted' : ''}`}>{words}</span>;
    return disabledTick(path, true, undefined, here.paths) ? <StateChip kind="only_if_named" /> : null;
  };

  // ---- Withhold: the draft, written by the tree or the box (decision 0026 rule 5)
  const fromTree = (next: string[]) => {
    const line = changedLine(lines, next);
    if (line !== null) setFlash({ line, n: (flash?.n ?? 0) + 1 });
    setDraft(textOf(next));
  };
  const out = (p.ignoreOut ?? defaultOut)(draft ?? here.saved);
  const branch = p.files.repos(p.courseOrg);
  const ignRef = {
    org: p.courseOrg, repo, path: '.releaseignore', exists: here.ign.kind !== 'absent',
    branch: branch.kind === 'ready' ? branch.repos.find((r) => r.name === repo)?.default_branch : undefined,
  };
  const saveIgn = async () => {
    if (p.onSaveIgnore && (await p.onSaveIgnore(out))) {
      setDraft(null);
      setFlash(null);
    }
  };

  // ---- the tree, one instance for every view
  const browseFixed = (path: string) => (neverMaterial(path) || denylisted(path) ? 'never copied' : null);
  const withholdFixed = (path: string) => (neverMaterial(path) ? 'never copied' : denylisted(path) ? 'only if named' : null);
  const name = (path: string) => (
    <button type="button" class="ft-name" aria-current={file === path ? 'true' : undefined} onClick={() => setFile(path)}>{path.split('/').pop()}</button>
  );
  const partial = here.tree.kind === 'ready' && here.tree.truncated;
  const tree = here.tree.kind === 'loading' ? <Loading what="Reading the repo" />
    : here.tree.kind !== 'ready' ? <p class="footnote">Could not read the repo’s files.</p>
    : (
      <PatternTree
        files={here.paths}
        patterns={lines}
        onChange={view === 'withhold' ? fromTree : () => undefined}
        mode={view === 'browse' ? 'exclude' : 'select'}
        releasedWord=""
        wordClass="muted"
        fixed={view === 'browse' ? browseFixed : view === 'withhold' ? withholdFixed : () => null}
        control={view === 'browse' ? () => null : release ? pickControl : undefined}
        side={release ? releaseSide : undefined}
        rowClick={view !== 'browse'}
        nameLink={view === 'browse' ? name : undefined}
      />
    );

  const right = view === 'browse'
    ? (file ? <RepoFile org={p.courseOrg} at={{ repo, path: file }} /> : <p class="empty-note">Choose a file to view it here.</p>)
    : view === 'withhold' ? (
      <>
        <div class="field">
          <label for="ign-pat">Withheld patterns</label>
          <PatternBox id="ign-pat" text={draft ?? here.saved} onText={(t) => { setFlash(null); setDraft(t); }} flash={flash} />
          <p class="hint">One pattern per line, as in .gitignore. Clicking the tree writes them here.</p>
        </div>
        <div class="field"><span class="label">Rules</span><Unmatched files={here.paths} lines={lines} partial={partial} /></div>
      </>
    )
    : sem ? (
      <ReleaseManifest groups={groups} runRepo={runRepo} dests={dests} stateOf={stateOf} onKeep={keep} prUrl={(g, path) => prOf(g.destRepo || runRepo, destPathOf(g, path))}
        onGroupDest={(k, patch) => {
          if (patch.destRepo) choose(patch.destRepo);
          setOverrides({ ...overrides, [k]: { ...overrides[k], ...patch } });
        }} />
    ) : <p class="empty-note">No semester to release into yet.</p>;

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
  const toRow = sem ? <ToRow org={sem.org} runRepo={runRepo} dests={dests} onRunRepo={(r) => { choose(r); setRunPick({ org: sem.org, repo: r }); }} /> : null;

  const body = (
    <>
      <SplitPanes label={repo} left={tree} right={right} heads={release ? [fromRow, toRow] : undefined} />
      {partial && view !== 'release' ? <CheckLine cls="bad">GitHub returned only part of this repo’s file list; badges may be incomplete.</CheckLine> : null}
      {view === 'withhold' ? (
        <div class="stack files-withhold">
          {here.tree.kind === 'ready' ? <WithholdCounts files={here.paths} lines={lines} /> : null}
          {p.withholdExtra}
          <p class="footnote">This reads the repo’s top-level .releaseignore; one in a subfolder still applies there.</p>
          <SaveBar state={p.ignoreSave ?? IDLE} onSave={() => void saveIgn()} small file={ignRef}
            disabled={!p.onSaveIgnore || here.ign.kind === 'loading' || (here.ign.kind === 'ready' && out === here.saved)} />
        </div>
      ) : null}
      {release && sem ? <ReleaseFoot paths={nPaths} files={nFiles} def={def} scheduleHref={p.scheduleHref?.({ semOrg: sem.org, runRepo, groups })} /> : null}
      {adding && p.sources?.onAdd ? (
        <RepoPickerModal kind="source" rows={p.sources.addable ?? []} onClose={() => setAdding(false)} onChoose={(r) => { p.sources!.onAdd!(r); setSrc(r); }} />
      ) : null}
    </>
  );
  const hint = <><span class="para">{course ? FILES_HINT : RBH_HINT}</span><ChipMeanings /></>;

  if (!course) {
    return (
      <details class="panel section panel-fold files-panel" open>
        {/* The ? inside the summary must not fold the panel. */}
        <summary><h2 id="rbh-h">Release by hand <span onClick={(e) => e.preventDefault()}><Hint label="About Release by hand">{hint}</Hint></span></h2></summary>
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
        <div class="seg" role="tablist" aria-label="View">{tab('browse', 'Browse')}{tab('withhold', 'Withhold')}{tab('release', 'Release')}</div>
      </div>
      {body}
    </section>
  );
}

/** The Withhold view's last line: the repo's files never copied, withheld by the list, and released. */
function WithholdCounts({ files, lines }: { files: string[]; lines: string[] }) {
  const n = withholdCounts(files, lines);
  return <p class="footnote">{n.never} never copied (by default) · {n.withheld} withheld · {n.released} released</p>;
}
