// A semester's Released materials (`?cohort=<org>#materials`): what its students have received,
// the mirror of the course materials index (`CourseIndex.tsx`) for the semester's copies. One row
// per materials repo of the semester (`materials`, then any other repo a release wrote to), each
// with its tree from the index (`RepoFiles`, `MaterialsTree`: the file row both consoles share),
// every file opening in the viewer both consoles use (`Split`, `RepoFile`). The released folders
// whose files differ from the course copy sit in their own folded panel ("Out of step with the
// course", worked out here three-way, `outOfStep`), each with its two ways to settle it (Release
// again, Keep for future semesters) and its differing files; the engine's `DIVERGED_RELEASED`
// lines stay on the Dashboard.
// Below, for those who can change the course, Release by hand: the Files panel's Release view
// (`FilesPanel`, plan 2026-11-04 decision 2). Withheld files are the course's concern, so nothing
// here edits them.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { CohortRef } from '../model/discovery';
import { ago, fmtDay, plural } from '../model/format';
import { destPathOf, effectiveDestRepo, fileStates, folderStep, groupPicks, stepWords, type FileState as ReleaseFileState, type FolderStep } from '../model/releaseState';
import { effectiveOf, semesterDefaults } from '../model/settings';
import type { Loaded } from '../model/status';
import type { Release, Status } from '../model/types';
import type { Files } from '../model/files';
import { compileAll } from '../edit/glob';
import { profileHref } from '../model/open';
import { scheduleNewHref } from '../router';
import { FilesPanel, type SemesterRef } from '../ui/FilesPanel';
import { CheckLine, ghUrl } from '../ui/bits';
import { FileRow, useYourSetup } from '../ui/FileRow';
import { fileViewHref } from '../ui/MaterialsTree';
import { StateChip, type ChipKind } from '../ui/StateChip';
import { Hint } from '../ui/Hint';
import { OpenButton } from '../ui/OpenButton';
import { RepoFile, Split } from '../ui/Viewer';
import { RepoFiles, addMaterialsTopic, fileIn, focusIn, repoOf } from './CourseIndex';
import { releaseRef } from './Cohort';
import { newestRepo } from './CourseEdit';
import { sourceRepos } from './Schedule';
import { WithStatus, cohortScope, tzOf, yearOf } from './common';
import { keepFuture, releaseAgain } from '../ops/defs';
import { OpOpen } from '../ops/Panel';
import type { CohortProps, ReadyProps } from './types';

/** Where a release writes in the semester, as the Schedule reads it: its repo (the semester's default destination `def` when unnamed) and folder (its source's when unnamed). */
export const destOf = (r: Release, def: string) => ({ repo: r.dest?.repo || def, path: (r.dest?.path || r.source?.path || '').replace(/\/+$/, '') });

const releasedOf = (status: Status) => (status.releases ?? []).filter((r) => r.state === 'released');

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
 * A released folder out of step with the course: where it landed, the release that wrote it
 * last, its step and its differing files (semester paths, with their states).
 */
export interface OutOfStep {
  r: Release;
  repo: string;
  path: string;
  step: FolderStep;
  files: { path: string; state: ReleaseFileState }[];
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
    const states = fileStates(blobs.filter((b) => ship.has(b.path)), files.tree(semOrg, d.repo, 'upstream'), files.tree(semOrg, d.repo), { destPath: to });
    const step = folderStep(Object.values(states));
    if (!step) continue;
    out.push({ r, repo: d.repo, path: d.path, step, files: Object.entries(states).filter(([, x]) => x !== 'up_to_date').map(([path, state]) => ({ path: to(path), state })) });
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

const STEP_HINT =
  'For each released folder the toolkit compares the course copy with the semester’s. A folder is listed when the two differ: edited in the semester (ahead), changed in the course since the release (behind), or both (diverged). Release again copies the course version over, merged onto the semester’s edit; a conflict opens a pull request for the instructors. Keep for future semesters opens a pull request carrying the semester’s edit back into the course. Nothing changes until you choose.';

const STEP_TABS: [FolderStep | 'all', string][] = [['all', 'All'], ['ahead', 'Edited in the semester'], ['behind', 'Changed in the course'], ['diverged', 'Both']];

/** The released folders out of step with the course (`outOfStep`), folded, with a filter by step; not drawn when there are none. */
function OutOfStepPanel(p: ReadyProps & { def: string }) {
  const env = useEnv();
  const setup = useYourSetup();
  const [show, setShow] = useState<FolderStep | 'all'>('all');
  const org = p.cohort.org;
  const rows = outOfStep(p.status, p.files, p.course.org, org, p.def);
  if (!rows.length) return null;
  const scope = cohortScope(p);
  const tz = tzOf(p.status), year = yearOf(p.now, tz);
  const repos = p.files.repos(org);
  const branchOf = (repo: string) => (repos.kind === 'ready' ? repos.repos.find((x) => x.name === repo)?.default_branch : undefined) ?? 'main';
  const keep = () => env?.ops.open(keepFuture(scope));
  const count = (k: FolderStep | 'all') => (k === 'all' ? rows.length : rows.filter((x) => x.step === k).length);
  return (
    <details class="panel section panel-fold out-of-step">
      {/* The ? inside the summary must not fold the panel. */}
      <summary>
        <h2>Out of step with the course <span class="step-bang" aria-hidden="true">!</span> <span class="chip amber">{plural(rows.length, 'released folder out of step', 'released folders out of step')}</span>{' '}
          <span onClick={(e) => e.preventDefault()}><Hint label="About folders out of step">{STEP_HINT}</Hint></span></h2>
      </summary>
      <div class="seg" role="tablist" aria-label="Show">
        {STEP_TABS.map(([k, label]) => (
          <button type="button" role="tab" aria-selected={show === k} onClick={() => setShow(k)}>{label} <span class="cnt">{count(k)}</span></button>
        ))}
      </div>
      <ul class="rows">
        {rows.filter((x) => show === 'all' || x.step === show).map((x) => {
          const ref = releaseRef(x.r, tz, year);
          const pulls = `${ghUrl(org, x.repo)}/pulls`;
          const compare = `${ghUrl(org, x.repo)}/compare/upstream...${branchOf(x.repo)}`;
          return (
            <li>
              <span class="r-title"><code>{x.repo === p.def ? '' : `${x.repo}: `}{x.path}/</code> <StateChip kind={x.step} onKeep={keep} prUrl={x.step === 'diverged' ? pulls : undefined} /></span>
              <span class="r-sub footnote">{stepWords(x.files.map((f) => f.state))}</span>
              <span class="r-side">
                {ref ? <OpOpen def={releaseAgain(scope, ref)} cls="btn small quiet" label="Release again" /> : null}
                <OpOpen def={keepFuture(scope)} cls="btn small quiet" label="Keep for future semesters" />
              </span>
              <details class="fold step-files">
                <summary aria-label={`The files of ${x.path}/ that differ`}>{plural(x.files.length, 'file')}</summary>
                <ul>
                  {x.files.map((f) => {
                    const name = <code>{f.path.slice(x.path.length + 1) || f.path}</code>;
                    return (
                      <li>
                        {f.state === 'not_released' ? name
                          : <FileRow org={org} link={{ name: f.path.split('/').pop() ?? f.path, repo: x.repo, path: f.path, url: ghUrl(org, x.repo, f.path, 'HEAD') }} here={fileViewHref(org, x.repo, f.path, 'cohort')} setup={setup} profile={profileHref(org)} cls="ft-name" label={name} />}
                        {' '}<StateChip kind={f.state as ChipKind} onKeep={keep} prUrl={f.state === 'diverged' ? pulls : undefined} />
                        {f.state === 'ahead' || f.state === 'diverged' ? <>{' '}<a class="textlink" href={compare} target="_blank" rel="noopener">Compare on GitHub</a></> : null}
                      </li>
                    );
                  })}
                </ul>
              </details>
            </li>
          );
        })}
      </ul>
    </details>
  );
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
