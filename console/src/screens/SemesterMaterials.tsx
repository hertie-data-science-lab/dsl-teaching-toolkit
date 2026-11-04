// A semester's Released materials (`?cohort=<org>#materials`): what its students have received,
// the mirror of the course materials index (`CourseIndex.tsx`) for the semester's copies. One row
// per materials repo of the semester (`materials`, then any other repo a release wrote to), each
// with its tree from the index (`RepoFiles`, `MaterialsTree`: the file row both consoles share),
// every file opening in the viewer both consoles use (`Split`, `RepoFile`). A released copy edited
// in the semester says so in the Dashboard's words, with a link to its Release page. Below, for
// those who can change the course, Release by hand: the Files panel's Release view
// (`FilesPanel`, plan 2026-11-04 decision 2). Withheld files are the course's concern, so nothing
// here edits them.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import type { CohortRef } from '../model/discovery';
import { ago, fmtDay, plural } from '../model/format';
import { effectiveDestRepo } from '../model/releaseState';
import { effectiveOf, semesterDefaults } from '../model/settings';
import type { Loaded } from '../model/status';
import type { Problem, Release, Status } from '../model/types';
import { scheduleNewHref } from '../router';
import { FilesPanel, type SemesterRef } from '../ui/FilesPanel';
import { CheckLine } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { OpenButton } from '../ui/OpenButton';
import { RepoFile, Split } from '../ui/Viewer';
import { RepoFiles, addMaterialsTopic, fileIn, focusIn, repoOf } from './CourseIndex';
import { newestRepo } from './CourseEdit';
import { sourceRepos } from './Schedule';
import { WithStatus, cohortScope } from './common';
import { keepFuture } from '../ops/defs';
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

/** The released copies of `repo` edited in the semester (`DIVERGED_RELEASED`), each with its release. */
export function editedCopies(status: Status, repo: string, def: string): { p: Problem; r: Release }[] {
  const byId = new Map(releasedOf(status).filter((r) => destOf(r, def).repo === repo).map((r) => [r.id, r]));
  return (status.problems ?? []).flatMap((p) => {
    const r = p.kind === 'DIVERGED_RELEASED' && p.release ? byId.get(p.release) : undefined;
    return r ? [{ p, r }] : [];
  });
}

const releaseHref = (r: Release) => `#release-${r.id}`;

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
                {editedCopies(p.status, repo, def).map(({ p: x, r }) => (
                  <span class="r-sub"><b>{x.label ?? 'Released copy edited'}</b>: {x.text} <a class="textlink" href={releaseHref(r)}>Release page</a></span>
                ))}
                <span class="r-side"><OpenButton org={org} repo={repo} small quiet /></span>
                <RepoFiles files={p.files} org={org} repo={repo} focus={focusIn(p.folder, repo)} at="cohort" />
              </li>
            );
          })}
        </ul>
      </details>
      <ReleaseByHand {...p} />
    </>
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
