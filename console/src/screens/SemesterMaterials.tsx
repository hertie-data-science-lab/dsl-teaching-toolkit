// A semester's Released materials (`?cohort=<org>#materials`): what its students have received,
// the mirror of the course materials index (`CourseIndex.tsx`) for the semester's copies. One row
// per materials repo of the semester (`materials`, then any other repo a release wrote to), each
// with its tree from the index (`RepoFiles`, `MaterialsTree`: the file row both consoles share),
// every file opening in the viewer both consoles use (`Split`, `RepoFile`), and on each released
// folder a link to its Release page. A released copy edited in the semester says so in the
// Dashboard's words. Withheld files are the course's concern, so nothing here edits them.

import { ago, fmtDay, plural } from '../model/format';
import { DEFAULT_DEST_REPO } from '../model/policy';
import type { Problem, Release, Status } from '../model/types';
import { Hint } from '../ui/Hint';
import { OpenButton } from '../ui/OpenButton';
import { RepoFile, Split } from '../ui/Viewer';
import { RepoFiles, fileIn, focusIn, repoOf } from './CourseIndex';
import { WithStatus } from './common';
import type { CohortProps, ReadyProps } from './types';

/** Where a release writes in the semester, as the Schedule reads it: its repo (`materials` when unnamed) and folder (its source's when unnamed). */
export const destOf = (r: Release) => ({ repo: r.dest?.repo || DEFAULT_DEST_REPO, path: (r.dest?.path || r.source?.path || '').replace(/\/+$/, '') });

const releasedOf = (status: Status) => (status.releases ?? []).filter((r) => r.state === 'released');

/** The semester's materials repos: `materials`, then each other repo a release has written to, in the schedule's order. */
export function semesterMaterialsRepos(status: Status): string[] {
  const out = [DEFAULT_DEST_REPO];
  for (const r of releasedOf(status)) if (!out.includes(destOf(r).repo)) out.push(destOf(r).repo);
  return out;
}

/** `repo`'s released folders, each with the releases that wrote it. */
export function releasedFolders(status: Status, repo: string): Map<string, Release[]> {
  const out = new Map<string, Release[]>();
  for (const r of releasedOf(status)) {
    const d = destOf(r);
    if (d.repo === repo && d.path) out.set(d.path, [...(out.get(d.path) ?? []), r]);
  }
  return out;
}

/** The released copies of `repo` edited in the semester (`DIVERGED_RELEASED`), each with its release. */
export function editedCopies(status: Status, repo: string): { p: Problem; r: Release }[] {
  const byId = new Map(releasedOf(status).filter((r) => destOf(r).repo === repo).map((r) => [r.id, r]));
  return (status.problems ?? []).flatMap((p) => {
    const r = p.kind === 'DIVERGED_RELEASED' && p.release ? byId.get(p.release) : undefined;
    return r ? [{ p, r }] : [];
  });
}

const releaseHref = (r: Release) => `#release-${r.id}`;

/** A released folder's Release page link(s), on its row in the tree. */
function folderLinks(folders: Map<string, Release[]>) {
  return (path: string) => {
    const rs = folders.get(path);
    if (!rs) return null;
    return rs.map((r) => <a class="ft-gh" href={releaseHref(r)} aria-label={`Release page of ${path}`}>{rs.length > 1 ? r.id : 'Release page'}</a>);
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
  const repos = semesterMaterialsRepos(p.status);
  const open = fileIn(p.file, repos);
  if (open) {
    return (
      <>
        <Head org={org} back />
        <Split label={open.repo} side={<section class="panel section" aria-label={open.repo}><h2>{open.repo}</h2><RepoFiles files={p.files} org={org} repo={open.repo} current={open.path} at="cohort" folderSide={folderLinks(releasedFolders(p.status, open.repo))} /></section>}>
          <RepoFile org={org} at={open} />
        </Split>
      </>
    );
  }
  const anything = releasedOf(p.status).length > 0;
  return (
    <>
      <Head org={org} back={false} />
      <section class="panel section">
        <h2>Semester materials repos</h2>
        {anything ? null : <p class="footnote">Nothing released yet: each folder arrives at its time on the Schedule.</p>}
        <ul class="rows">
          {repos.map((repo) => {
            const gh = repoOf(p.files, org, repo);
            const folders = releasedFolders(p.status, repo);
            const n = [...folders.values()].reduce((s, rs) => s + rs.length, 0);
            return (
              <li>
                <span class="r-title">{repo} {n ? <span class="chip ok">{plural(n, 'release')}</span> : <span class="chip">nothing released</span>}</span>
                {gh?.pushed_at ? <span class="r-sub">Last change {fmtDay(gh.pushed_at)} ({ago(gh.pushed_at, p.now)}).</span> : null}
                {editedCopies(p.status, repo).map(({ p: x, r }) => (
                  <span class="r-sub"><b>{x.label ?? 'Released copy edited'}</b>: {x.text} <a class="textlink" href={releaseHref(r)}>Release page</a></span>
                ))}
                <span class="r-side"><OpenButton org={org} repo={repo} small quiet /></span>
                <RepoFiles files={p.files} org={org} repo={repo} focus={focusIn(p.folder, repo)} at="cohort" folderSide={folderLinks(folders)} />
              </li>
            );
          })}
        </ul>
      </section>
    </>
  );
}

export function SemesterMaterialsScreen(p: CohortProps) {
  return <WithStatus props={p} title="Released materials">{(r) => <Released {...r} />}</WithStatus>;
}
