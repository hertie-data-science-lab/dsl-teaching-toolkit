// A semester's Released materials (`?cohort=<org>#materials`): what its students have received,
// the mirror of the course materials index (`CourseIndex.tsx`) for the semester's copies. One row
// per materials repo of the semester (`materials`, then any other repo a release wrote to), each
// with its tree from the index (`RepoFiles`, `MaterialsTree`: the file row both consoles share),
// every file opening in the viewer both consoles use (`Split`, `RepoFile`). The released folders
// whose files differ from the course copy sit in their own folded panel ("Released materials
// that differ from their source files", three-way, `outOfStep` in the model), each
// folder a fold of its own, its differing files listed beneath, each with its two ways to settle
// it (Re-release, with Overwrite in its Options; PR into source), Open and an inline Diff, and
// one PR into source (all) for the panel. A re-release that conflicted waits in an open pull
// request from the semester repo's `upstream` branch: its files then say so, with Open PR and
// Overwrite in place of Re-release. The engine's `DIVERGED_RELEASED` lines stay on the Dashboard.
// Below, for those who can change the course, Release by hand: the Files panel's Release view
// (`FilesPanel`, plan 2026-11-04 decision 2). Withheld files are the course's concern, so nothing
// here edits them.

import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { CohortRef } from '../model/discovery';
import { ago, fmtDay, plural } from '../model/format';
import { destOf, effectiveDestRepo, outOfStep, releasedOf, TOP_LEVEL, type FileState as ReleaseFileState, type Group, type FolderStep, type OutOfStep } from '../model/releaseState';
import { effectiveOf, semesterDefaults } from '../model/settings';
import type { Loaded } from '../model/status';
import type { Release, Status } from '../model/types';
import type { Files } from '../model/files';
import { editableFile, profileHref, type Setup } from '../model/open';
import { extOf, viewKind } from '../model/viewer';
import { scheduleNewHref } from '../router';
import { FilesPanel, type SemesterRef } from '../ui/FilesPanel';
import { CheckLine, ghUrl } from '../ui/bits';
import { DiffView, diffAnchor, lineCounts, useDiff, type Side } from '../ui/FileDiff';
import { useYourSetup } from '../ui/FileRow';
import { OpenSplit } from '../ui/OpenSplit';
import { StateChip, type ChipKind } from '../ui/StateChip';
import { Hint } from '../ui/Hint';
import { OpenButton } from '../ui/OpenButton';
import { RepoFile, Split } from '../ui/Viewer';
import { RepoFiles, addMaterialsTopic, fileIn, focusIn, repoOf } from './CourseIndex';
import { newestRepo } from './CourseEdit';
import { sourceRepos } from './Schedule';
import { WithStatus, cohortScope } from './common';
import { keepFuture, keepFuturePaths, releasePicked, type Scope } from '../ops/defs';
import { REPLACE } from '../tiers/ops';
import { useLoad } from '../ui/load';
import type { GhPull } from '../github/client';
import type { TreeState } from '../model/files';
import type { OpDef } from '../ops/session';
import { OpOpen } from '../ops/Panel';
import type { CohortProps, ReadyProps } from './types';

/** The semester's materials repos: its default destination `def`, then each other repo a release has written to, in the schedule's order. */
export function semesterMaterialsRepos(status: Status, def: string): string[] {
  const out = [def];
  for (const r of releasedOf(status)) if (!out.includes(destOf(r, def).repo)) out.push(destOf(r, def).repo);
  return out;
}

/** `repo`'s released folders, each with the releases that wrote it. */
export function releasedFolders(status: Status, repo: string, def: string): Map<string, Release[]> {
  const out = new Map<string, Release[]>();
  for (const r of releasedOf(status)) {
    const d = destOf(r, def);
    if (d.repo === repo && d.path) out.set(d.path, [...(out.get(d.path) ?? []), r]);
  }
  return out;
}

/**
 * A semester as the Files panel releases into it: its `release_destinations` (status.json, the
 * effective list). Its default destination and the student repos it refuses are the panel's to
 * work out, in the Release view only.
 */
export function semesterRefOf(ref: CohortRef, loaded: Loaded | undefined): SemesterRef {
  const st = loaded?.kind === 'ready' ? loaded.status : undefined;
  const listed = effectiveOf(st ? semesterDefaults(st) : undefined, 'release_destinations').value;
  return {
    org: ref.org, termLabel: ref.termLabel, loaded, ended: st?.semester?.ended,
    destinations: Array.isArray(listed) ? listed.filter((x): x is string => typeof x === 'string' && !!x.trim()) : [],
  };
}

const HINT =
  'What students have received: the semester’s copies, written by scheduled releases and by Release early or Release again on the Schedule. Edit the course copy, not these: a fix reaches students at the next release, or automatically when the semester’s “Send fixes to released files automatically” default is on.';

function Head({ org, back }: { org: string; back: boolean }) {
  return (
    <div class="page-head">
      <div>
        <h2 class="h1">Released materials <Hint label="About released materials">{HINT}</Hint></h2>
        <p class="lede">{back ? <a class="textlink" href={`?cohort=${org}#materials`}>All released materials</a> : 'The semester’s materials repos, as students have them.'}</p>
      </div>
    </div>
  );
}

function Released(p: ReadyProps) {
  const org = p.cohort.org;
  const def = effectiveDestRepo(p.loaded);
  const repos = semesterMaterialsRepos(p.status, def);
  const open = fileIn(p.file, repos);
  if (open) {
    return (
      <>
        <Head org={org} back />
        <Split label={open.repo} side={<section class="panel section" aria-label={open.repo}><h2>{open.repo}</h2><RepoFiles files={p.files} org={org} repo={open.repo} current={open.path} at="cohort" /></section>}>
          <RepoFile org={org} at={open} />
        </Split>
      </>
    );
  }
  const anything = releasedOf(p.status).length > 0;
  return (
    <>
      <Head org={org} back={false} />
      <details class="panel section panel-fold" open>
        <summary><h2>Released materials this semester</h2></summary>
        {anything ? null : <p class="footnote">Nothing released yet: each folder arrives at its time on the Schedule.</p>}
        <ul class="rows">
          {repos.map((repo) => {
            const gh = repoOf(p.files, org, repo);
            const folders = releasedFolders(p.status, repo, def);
            const n = [...folders.values()].reduce((s, rs) => s + rs.length, 0);
            return (
              <li>
                <span class="r-title">{repo} {n ? <span class="chip ok">{plural(n, 'release')}</span> : <span class="chip">nothing released</span>}</span>
                {gh?.pushed_at ? <span class="r-sub">Last change {fmtDay(gh.pushed_at)} ({ago(gh.pushed_at, p.now)}).</span> : null}
                <span class="r-side"><OpenButton org={org} repo={repo} small quiet /></span>
                <RepoFiles files={p.files} org={org} repo={repo} focus={focusIn(p.folder, repo)} at="cohort" />
              </li>
            );
          })}
        </ul>
      </details>
      <OutOfStepPanel {...p} def={def} />
      <ReleaseByHand {...p} />
    </>
  );
}

/** The panel's `?`: what is compared, the three states, the two ways to settle one. */
const STEP_HINT = 'For each released folder the toolkit compares the course copy with the semester’s and lists every file that differs. Nothing changes until you choose.';
const STEP_HINT_ITEMS = [
  <><b>Edited in semester</b> (ahead): the semester’s copy was changed after the release; <b>removed in semester</b>: deleted there, which only Overwrite restores.</>,
  <><b>Edited in course</b> (behind): the course copy changed since the release, additions included.</>,
  <><b>Both</b> (diverged): changed on both sides.</>,
  <><b>Re-release</b> copies one file’s course version over, merged onto the semester’s edit; a conflict opens a pull request for the instructors. Its Options can overwrite instead.</>,
  <><b>PR into source</b> opens a pull request carrying the semester’s edit back into the course repo, so next semester starts from it.</>,
  <><b>Release pending in a pull request</b>: a re-release that conflicted waits there; Open PR to resolve it, or Overwrite to take the course version.</>,
];

const STEP_TABS: [FolderStep | 'all', string][] = [['all', 'All'], ['ahead', 'Edited in semester'], ['behind', 'Edited in course'], ['diverged', 'Both']];

/** A differing file's step, the filter's key: a file the course added counts as edited in the course, one the semester removed as edited in the semester. */
const fileStep = (s: ReleaseFileState): FolderStep => (s === 'removed' ? 'ahead' : s === 'ahead' || s === 'diverged' ? s : 'behind');

/** What a file's diff compares, by its step. */
const DIFF_WHAT: Record<FolderStep, string> = {
  ahead: 'as released → the semester’s copy',
  behind: 'as released → the course’s copy now',
  diverged: 'as released → course and semester edits',
};

export const NO_LINE_DIFF = 'A binary file: no line diff. Open it to compare.';
export const NOTHING_TO_RERELEASE = 'The semester already has the course version; re-releasing changes nothing.';
export const ONLY_IN_SEMESTER = 'Only in the semester: nothing in the course to release.';
const PR_HINT = 'Opens one pull request per course repo carrying every edit listed under Edited in semester and Both, so next semester starts from them.';
export const NOTHING_TO_PROPOSE = 'Nothing edited in the semester for this file.';
export const REMOVED_RERELEASE = 'The semester removed it; Overwrite restores the course version.';
export const NOTHING_TO_CARRY = 'Nothing to carry back: the file is gone on the semester side.';
export const OVERWRITE_CONFIRM = 'Replaces the semester’s copy of this file with the course version; its edit is lost.';

/** Re-release: `release.adhoc` of exactly one course file (`from` in `srcRepo`) onto its released copy (`to` in `destRepo`), with Overwrite (`replace`) in its Options. */
export function reRelease(scope: Scope, srcRepo: string, from: string, destRepo: string, to: string): OpDef {
  const parent = (x: string) => (x.includes('/') ? x.slice(0, x.lastIndexOf('/')) : '');
  const folder = parent(from);
  const g: Group = { repo: srcRepo, head: folder ? `${folder}/` : TOP_LEVEL, folder, kind: 'partial', files: 1, of: 1, sources: [from], ship: [from], withheld: [], destRepo, destPath: parent(to) };
  return { ...releasePicked(scope, [g], { defaultDestRepo: destRepo }), options: REPLACE };
}

/** Overwrite: the Re-release of a file whose release waits in a pull request, `replace` set. */
export const overwrite = (again: OpDef): OpDef => ({ ...again, args: { ...again.args, replace: true }, confirm: OVERWRITE_CONFIRM });

/** A path's blob on two branches differs: there, and not the same object (false while either is being read). */
function differs(a: TreeState, b: TreeState, path: string): boolean {
  if (a.kind !== 'ready' || b.kind !== 'ready') return false;
  const x = a.paths.find((e) => e.path === path && !e.dir), y = b.paths.find((e) => e.path === path && !e.dir);
  return !!x && x.sha !== y?.sha;
}

/** A file no line diff is drawn for, by its name: documents and archives (`editableFile`) and images but svg. */
const binaryName = (path: string) => !editableFile(path) || (viewKind(path) === 'image' && extOf(path) !== 'svg');

/**
 * Released materials that differ from their source files (`outOfStep`), folded, with a filter by
 * step, and one PR into source for every semester edit; not drawn when there are none. Inside,
 * each folder a fold, open at first, headed by its path alone, its differing files beneath, each
 * with Re-release (that one file), its Open split button and a Diff toggle drawing the file's diff
 * under the row.
 */
function OutOfStepPanel(p: ReadyProps & { def: string }) {
  const env = useEnv();
  const setup = useYourSetup();
  const [show, setShow] = useState<FolderStep | 'all'>('all');
  const org = p.cohort.org;
  const rows = outOfStep(p.status, p.files, p.course.org, org, p.def);
  // A re-release that conflicted waits in an open pull request from `upstream`: read once per repo listed.
  const prRepos = [...new Set(rows.map((x) => x.repo))];
  const prs = useLoad(env && prRepos.length ? () => Promise.all(prRepos.map((r) => env.client.listPulls(org, r, { head: 'upstream', state: 'open' }))) : null, [org, prRepos.join('\n')]);
  const prOf = (repo: string): GhPull | undefined => (prs.kind === 'ready' ? prs.value[prRepos.indexOf(repo)]?.[0] : undefined);
  if (!rows.length) return null;
  const scope = cohortScope(p);
  const repos = p.files.repos(org);
  const branchOf = (repo: string) => (repos.kind === 'ready' ? repos.repos.find((x) => x.name === repo)?.default_branch : undefined) ?? 'main';
  const keep = () => env?.ops.open(keepFuture(scope));
  const all = rows.flatMap((x) => x.files);
  const edited = all.some((f) => f.state === 'ahead' || f.state === 'diverged');
  // Every differing file of the listed folders, the semester-only ones included.
  const count = (k: FolderStep | 'all') => (k === 'all' ? all.length : all.filter((f) => fileStep(f.state) === k).length);
  const shown = (f: OutOfStep['files'][number]) => show === 'all' || fileStep(f.state) === show;
  return (
    <details class="panel section panel-fold out-of-step">
      {/* The ? inside the summary must not fold the panel. */}
      <summary>
        <h2>Released materials that differ from their source files <span class="step-bang" aria-hidden="true">!</span> <span class="chip amber">{plural(all.length, 'released file differs', 'released files differ')}</span>{' '}
          <span onClick={(e) => e.preventDefault()}><Hint label="About released materials that differ" items={STEP_HINT_ITEMS}>{STEP_HINT}</Hint></span></h2>
      </summary>
      <div class="dif-top">
        <div class="seg" role="tablist" aria-label="Show">
          {STEP_TABS.map(([k, label]) => (
            <button type="button" role="tab" aria-selected={show === k} onClick={() => setShow(k)}>{label} <span class="cnt">{count(k)}</span></button>
          ))}
        </div>
        <span class="dif-pr">
          <button class="btn small quiet" type="button" disabled={!edited} title={edited ? undefined : 'Nothing edited in the semester.'} onClick={keep}>PR into source (all)</button>
          <Hint label="About PR into source (all)">{PR_HINT}</Hint>
        </span>
      </div>
      <ul class="dif-list">
        {rows.filter((x) => x.files.some(shown)).map((x) => {
          const pulls = `${ghUrl(org, x.repo)}/pulls`;
          const compare = `${ghUrl(org, x.repo)}/compare/upstream...${branchOf(x.repo)}`;
          const src = x.r.source!.repo;
          const pr = prOf(x.repo);
          const up = p.files.tree(org, x.repo, 'upstream'), live = p.files.tree(org, x.repo);
          // Its file the pending release changes: edited in the semester, and `upstream` (the course version) differing from what students read.
          const pendingOf = (f: OutOfStep['files'][number]) => (pr && (f.state === 'ahead' || f.state === 'diverged') && differs(up, live, f.path) ? pr.html_url : undefined);
          return (
            <li class="dif-folder"><details class="fold" open>
              <summary class="dif-fhead"><code class="dif-path">{x.repo === p.def ? '' : `${x.repo}: `}{x.path}/</code></summary>
              <ul class="dif-files">
                {x.files.filter(shown).map((f) => (
                  <DiffFile key={f.path} files={p.files} org={org} repo={x.repo} folder={x.path} f={f} setup={setup} onKeep={keep} pulls={pulls} compare={compare}
                    course={f.from ? [p.course.org, src, f.from] : null} again={f.from ? reRelease(scope, src, f.from, x.repo, f.path) : null}
                    keepFile={keepFuturePaths(scope, [x.repo === p.def ? f.path : `${x.repo}/${f.path}`])} pending={pendingOf(f)} />
                ))}
              </ul>
            </details></li>
          );
        })}
      </ul>
    </details>
  );
}

/** One differing file: its name, chip and line counts (once its diff is read), Open, and Diff, which draws the diff under the row. */
function DiffFile(p: {
  files: Files; org: string; repo: string; folder: string; f: OutOfStep['files'][number]; setup: Setup | null;
  onKeep: () => void; pulls: string; compare: string; course: Side | null;
  /** Re-release: that one file, or null for a file only the semester has. */
  again: OpDef | null;
  /** PR into source: that one file. */
  keepFile: OpDef;
  /** The open pull request its re-release waits in, if any: Open PR and Overwrite in place of Re-release. */
  pending?: string;
}) {
  const { f } = p;
  const [open, setOpen] = useState(false);
  // Read once first opened, so the row keeps its counts after Diff folds it again.
  const [seen, setSeen] = useState(false);
  const binary = binaryName(f.path);
  const removed = f.state === 'removed';
  const inSemester = f.state !== 'not_released' && !removed;
  const d = useDiff(p.files, p.course, inSemester ? [p.org, p.repo, f.path] : null, seen && !binary);
  const sum = binary ? 'binary' : d.kind === 'ready' ? lineCounts(d) : d.kind === 'not_diffable' ? 'binary or too large' : '';
  const step = fileStep(f.state);
  return (
    <li class="dif-file">
      <div class="dif-row">
        <code class="dif-name">{f.path.slice(p.folder.length + 1) || f.path}</code>
        <StateChip kind={f.state as ChipKind} onKeep={p.onKeep} prUrl={f.state === 'diverged' ? p.pulls : undefined} />
        <span class="dif-sum">{sum}</span>
        <span class="dif-right">
          {p.pending ? null : p.again && (f.state === 'behind' || f.state === 'diverged' || f.state === 'not_released')
            ? <OpOpen def={p.again} cls="btn small quiet" label="Re-release" />
            : <button class="btn small quiet" type="button" disabled title={removed ? REMOVED_RERELEASE : p.again ? NOTHING_TO_RERELEASE : ONLY_IN_SEMESTER}>Re-release</button>}
          {removed && p.again && !p.pending ? <OpOpen def={overwrite(p.again)} cls="btn small quiet" label="Overwrite" /> : null}
          {removed
            ? <button class="btn small quiet" type="button" disabled title={NOTHING_TO_CARRY}>PR into source</button>
            : step === 'behind'
            ? <button class="btn small quiet" type="button" disabled title={NOTHING_TO_PROPOSE}>PR into source</button>
            : <OpOpen def={p.keepFile} cls="btn small quiet" label="PR into source" />}
          {inSemester ? <OpenSplit org={p.org} repo={p.repo} path={f.path} setup={p.setup} profile={profileHref(p.org)} /> : null}
          {binary
            ? <button class="btn small quiet" type="button" disabled title={NO_LINE_DIFF}>Diff</button>
            : <button class="btn small quiet" type="button" aria-expanded={open} aria-pressed={open} aria-label={`Diff ${f.path}`} onClick={() => { setOpen(!open); setSeen(true); }}>Diff</button>}
        </span>
      </div>
      {p.pending ? (
        <div class="dif-pending" role="status">
          <span>Release pending in a pull request</span>
          <a class="btn small quiet" href={p.pending} target="_blank" rel="noopener">Open PR</a>
          {p.again ? <OpOpen def={overwrite(p.again)} cls="btn small quiet" label="Overwrite" /> : null}
        </div>
      ) : null}
      {open && !binary ? (
        <div class="dif-diff">
          <div class="dif-diff-head">
            <code>{f.path}</code>
            <span class="footnote">{DIFF_WHAT[step]}</span>
            {step !== 'behind' ? <CompareLink base={p.compare} path={f.path} /> : null}
          </div>
          <DiffView d={d} />
        </div>
      ) : null}
    </li>
  );
}

/** Compare on GitHub, at the file's own diff on the compare page once its anchor is worked out. */
function CompareLink({ base, path }: { base: string; path: string }) {
  const [anchor, setAnchor] = useState('');
  useEffect(() => {
    let live = true;
    void diffAnchor(path).then((a) => live && setAnchor(a));
    return () => void (live = false);
  }, [path]);
  return <a class="textlink" href={anchor ? `${base}#${anchor}` : base} target="_blank" rel="noopener">Compare on GitHub</a>;
}

/** Release by hand (decision 2): the Files panel's Release view from the course's repos into this semester; drawn only for those who can change the course. */
function ReleaseByHand(p: ReadyProps) {
  const env = useEnv();
  const [addBad, setAddBad] = useState<string | null>(null);
  const materials = (p.status.course?.materials ?? []).map((m) => m.repo);
  const sources = sourceRepos(p, materials);
  const semester = semesterRefOf(p.cohort, p.loaded);
  // "Add a course repo…" (decision 7) sets the topic as Treat as course materials does; the course lists it after the next Refresh.
  const onAdd = env ? (repo: string) => {
    setAddBad(null);
    addMaterialsTopic(env.client, p.course.org, repo).catch((e) => setAddBad(`Could not add the topic: ${e instanceof Error ? e.message : String(e)}`));
  } : undefined;
  return (
    <>
      <FilesPanel files={p.files} courseOrg={p.course.org} repo={newestRepo(materials) ?? sources.others[0] ?? ''} mode="semester" write={p.course.write}
        semester={semester} sources={{ materials: sources.materials, others: sources.others, onAdd }}
        scheduleHref={scheduleNewHref} onKeep={() => env?.ops.open(keepFuture(cohortScope(p)))} />
      {addBad ? <CheckLine cls="bad">{addBad}</CheckLine> : null}
    </>
  );
}

export function SemesterMaterialsScreen(p: CohortProps) {
  return <WithStatus props={p} title="Released materials">{(r) => <Released {...r} />}</WithStatus>;
}
