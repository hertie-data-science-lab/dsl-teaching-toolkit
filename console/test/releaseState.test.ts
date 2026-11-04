// Release by hand's model (plan 2026-11-04 decisions 3-7): each file's three-way state, the
// folder roll-ups, the manifest's groups, the paired request, the disabled ticks, the refused
// destination names, the op def, and the trees read again after a release.

import { describe, expect, it, vi } from 'vitest';
import { refreshAfterRun } from '../src/app';
import { buildTree, treeModel } from '../src/edit/badges';
import { compileAll } from '../src/edit/glob';
import { LiveFiles, type TreeState } from '../src/model/files';
import { DEFAULT_DEST_REPO } from '../src/model/policy';
import {
  assignmentStems, COMMA_TITLE, countsWords, destPathOf, destRepos, effectiveDestRepo, eligibleDestination, entryDestRepos, fileStates, foldersCopied, groupPicks, LIST_MAX,
  releaseBlocked, requestOf, toArgs, TOO_MANY, type FileState, type Group, folderStep, semesterOnly,
} from '../src/model/releaseState';

describe('a released folder’s step', () => {
  it('is ahead, behind (a course addition counts) or diverged, in step otherwise, and unknown while a state is', () => {
    expect(folderStep(['up_to_date', 'ahead'])).toBe('ahead');
    expect(folderStep(['behind', 'up_to_date'])).toBe('behind');
    expect(folderStep(['not_released'])).toBe('behind');
    expect(folderStep(['ahead', 'not_released'])).toBe('diverged');
    expect(folderStep(['diverged'])).toBe('diverged');
    expect(folderStep(['up_to_date'])).toBeNull();
    expect(folderStep(['ahead', 'unknown'])).toBeNull();
  });

  it('finds the files only the semester copy has under a folder, clutter aside', () => {
    const t: TreeState = { kind: 'ready', truncated: false, paths: ['a/x.md', 'a/new.md', 'a/.DS_Store', 'ab/y.md', 'a/sub'].map((path) => ({ path, dir: path === 'a/sub', sha: 's' })) };
    expect(semesterOnly(t, 'a', new Set(['a/x.md']))).toEqual(['a/new.md']);
    expect(semesterOnly({ kind: 'loading' }, 'a', new Set())).toEqual([]);
  });
});
import { fnmatch } from '../src/model/selection';
import { validateArgs } from '../src/ops/adapter';
import { opSpec } from '../src/ops/registry';
import type { Loaded } from '../src/model/status';
import { releaseAdhoc, releasePicked } from '../src/ops/defs';
import { StaticFiles } from './staticFiles';

const ORG = 'hertie-dsl-demo-f2026';
const tree = (shas: Record<string, string>): TreeState => ({ kind: 'ready', truncated: false, paths: Object.entries(shas).map(([path, sha]) => ({ path, sha, dir: false })) });

describe('each file’s state', () => {
  const course = [
    { path: 'same', sha: 'a' },
    { path: 'behind', sha: 'new' },
    { path: 'ahead', sha: 'a' },
    { path: 'diverged', sha: 'new' },
    { path: 'missing', sha: 'a' },
  ];
  const upstream = tree({ same: 'a', behind: 'a', ahead: 'a', diverged: 'a' });
  const students = tree({ same: 'a', behind: 'a', ahead: 'edited', diverged: 'edited' });

  it('is three-way against the upstream branch and the branch students read', () => {
    expect(fileStates(course, upstream, students)).toEqual({ same: 'up_to_date', behind: 'behind', ahead: 'ahead', diverged: 'diverged', missing: 'not_released' });
  });

  it('is two-way on a semester with no upstream branch: equal or behind', () => {
    const two = { same: 'up_to_date', behind: 'behind', ahead: 'behind', diverged: 'behind', missing: 'not_released' };
    expect(fileStates(course, null, students)).toEqual(two);
    expect(fileStates(course, { kind: 'absent' }, students)).toEqual(two);
  });

  it('judges a path the upstream branch lacks two-way', () => {
    expect(fileStates([{ path: 'x', sha: 'a' }, { path: 'y', sha: 'b' }], tree({}), tree({ x: 'a', y: 'c' }))).toEqual({ x: 'up_to_date', y: 'behind' });
  });

  it('is unknown while the semester copy loads or cannot be read, and not released when the repo does not exist', () => {
    expect(fileStates(course, upstream, { kind: 'loading' }).same).toBe('unknown');
    expect(fileStates(course, upstream, { kind: 'error', message: 'HTTP 502' }).same).toBe('unknown');
    expect(fileStates(course, { kind: 'loading' }, students).same).toBe('unknown');
    expect(fileStates(course, upstream, { kind: 'absent' }).same).toBe('not_released');
  });

  it('compares at the destination path it is given', () => {
    const s = fileStates([{ path: 'labs/l1.ipynb', sha: 'a' }], null, tree({ 'materials/labs/l1.ipynb': 'a' }), { destPath: (p) => `materials/${p}` });
    expect(s).toEqual({ 'labs/l1.ipynb': 'up_to_date' });
  });

  it('reads shas from the trees StaticFiles serves', () => {
    const f = new StaticFiles({}, {}, { 'c/r': ['a.md', 'b.md'], [`${ORG}/materials`]: ['a.md', 'b.md'] }, {}, {}, { [`${ORG}/materials/b.md`]: 'edited' });
    const c = f.tree('c', 'r');
    const paths = c.kind === 'ready' ? c.paths.filter((p) => !p.dir) : [];
    expect(fileStates(paths, null, f.tree(ORG, 'materials'))).toEqual({ 'a.md': 'up_to_date', 'b.md': 'behind' });
  });
});

describe('a folder’s roll-up', () => {
  const files = ['lectures/a.md', 'lectures/b.md', 'lectures/c.md', 'lectures/.DS_Store', 'lectures/d.md', 'lectures/e.md'];
  const states: Record<string, FileState> = { 'lectures/a.md': 'behind', 'lectures/b.md': 'behind', 'lectures/c.md': 'ahead', 'lectures/d.md': 'not_released', 'lectures/e.md': 'not_released', 'lectures/.DS_Store': 'not_released' };

  it('counts the files left in, in the short words, zero parts omitted', () => {
    const lines = ['/lectures/e.md'];
    const rows = treeModel(buildTree(files), compileAll(lines, { defaults: true }), lines, { state: (p) => states[p] });
    const n = rows.get('lectures')!;
    expect(n.states).toEqual({ behind: 2, ahead: 1, not_released: 1 });
    // The withheld file and the never-material one.
    expect(n.withheld).toBe(2);
    expect(countsWords(n.states)).toBe('2 behind · 1 ahead · 1 not released');
    expect(countsWords({ up_to_date: 3, diverged: 1, unknown: 2 })).toBe('1 diverged');
    expect(countsWords({})).toBe('');
    // A folder all up to date: no roll-up.
    expect(countsWords({ up_to_date: 4 })).toBe('');
  });
});

describe('the manifest’s groups', () => {
  const FILES = ['labs/lab-1.ipynb', 'labs/lab-2.ipynb', 'labs/solutions/s1.ipynb', 'labs/draft.md', 'labs/.DS_Store',
    'lectures/01_intro/notes.md', 'lectures/01_intro/slides.pdf', 'lectures/01_intro/extra.md', 'lectures/02_trees/notes.md',
    'README.md', 'SYLLABUS.md', 'tests/check.py'];
  const D = { defaults: true };
  const trees = { 'course-materials-f2026': { files: FILES, rules: compileAll(['/labs/draft.md'], D) }, other: { files: ['x/a.md', 'b.md'], rules: compileAll([], D) } };
  const pick = (path: string, dir = false, repo = 'course-materials-f2026') => ({ repo, path, dir });

  it('groups by immediate parent in tree order: a picked folder whole, sibling files n of m, top level last', () => {
    const g = groupPicks([pick('SYLLABUS.md'), pick('lectures/01_intro/slides.pdf'), pick('labs', true), pick('lectures/01_intro/notes.md')], trees);
    expect(g.map((x) => [x.head, x.kind, x.files, x.of])).toEqual([
      ['labs/', 'all', 2, undefined],
      ['lectures/01_intro/', 'partial', 2, 3],
      ['(top level)', 'partial', 1, 2],
    ]);
    expect(g[1].ship).toEqual(['lectures/01_intro/notes.md', 'lectures/01_intro/slides.pdf']);
  });

  it('lists what a picked folder leaves out, with the rule (null: withheld by default), never-material names absent', () => {
    const [labs] = groupPicks([pick('labs', true)], trees);
    expect(labs.ship).toEqual(['labs/lab-1.ipynb', 'labs/lab-2.ipynb']);
    expect(labs.withheld).toEqual([{ path: 'labs/solutions/s1.ipynb', rule: null }, { path: 'labs/draft.md', rule: '/labs/draft.md' }]);
    expect(labs.sources).toEqual(['labs']);
  });

  it('implies a pick under a picked folder, and ships a denylisted file named outright but not a withheld one', () => {
    const g = groupPicks([pick('labs', true), pick('labs/lab-1.ipynb'), pick('tests/check.py'), pick('labs/draft.md')], trees);
    expect(g.map((x) => x.head)).toEqual(['labs/', 'tests/']);
    expect(g[1].ship).toEqual(['tests/check.py']);
    const draft = groupPicks([pick('labs/draft.md')], trees)[0];
    expect(draft.ship).toEqual([]);
    expect(draft.withheld).toEqual([{ path: 'labs/draft.md', rule: '/labs/draft.md' }]);
  });

  it('ships a content name a ! line re-includes, and leaves a never-released name, named or swept, as never released', () => {
    const t = { r: { files: ['labs/a.md', 'labs/solutions/s1.ipynb', 'labs/.env', 'grading.yml'], rules: compileAll(['!solutions/'], D) } };
    const [labs, top] = groupPicks([pick('labs', true, 'r'), pick('grading.yml', false, 'r')], t);
    expect(labs.ship).toEqual(['labs/a.md', 'labs/solutions/s1.ipynb']);
    expect(labs.withheld).toEqual([{ path: 'labs/.env', rule: null, never: true }]);
    expect(top.ship).toEqual([]);
    expect(top.sources).toEqual([]);
    expect(top.withheld).toEqual([{ path: 'grading.yml', rule: null, never: true }]);
    // Without the ! line the default withholds it again.
    expect(groupPicks([pick('labs', true, 'r')], { r: { ...t.r, rules: compileAll([], D) } })[0].withheld).toContainEqual({ path: 'labs/solutions/s1.ipynb', rule: null });
  });

  it('keeps each repo’s picks together, in the order the repos were first picked', () => {
    const g = groupPicks([pick('b.md', false, 'other'), pick('README.md'), pick('x', true, 'other')], trees);
    expect(g.map((x) => `${x.repo}:${x.head}`)).toEqual(['other:x/', 'other:(top level)', 'course-materials-f2026:(top level)']);
  });

  it('pairs every path with its destination, a group’s own repo and path overriding the run default', () => {
    const g = groupPicks([pick('labs', true), pick('lectures/01_intro/notes.md'), pick('lectures/01_intro/slides.pdf'), pick('b.md', false, 'other')], trees);
    g[1] = { ...g[1], destRepo: 'slides', destPath: 'week1/' };
    expect(destPathOf(g[1], 'lectures/01_intro/notes.md')).toBe('week1/notes.md');
    const rows = requestOf(g, { defaultDestRepo: 'materials' });
    expect(rows[1]).toEqual({ repo: 'course-materials-f2026', path: 'lectures/01_intro/notes.md', toRepo: 'slides', toPath: 'week1/notes.md' });
    expect(toArgs(rows)).toEqual({
      course_source_repo: 'course-materials-f2026,course-materials-f2026,course-materials-f2026,other',
      course_source_path: 'labs,lectures/01_intro/notes.md,lectures/01_intro/slides.pdf,b.md',
      semester_dest_repo: 'materials,slides,slides,materials',
      semester_dest_path: 'labs,week1/notes.md,week1/slides.pdf,b.md',
    });
  });

  it('builds the Release by hand op: one run, the groups as facts, no Options', () => {
    const g: Group[] = groupPicks([pick('labs', true), pick('lectures/01_intro/notes.md'), pick('b.md', false, 'other')], trees);
    const def = releasePicked({ courseOrg: 'c', cohortOrg: ORG, where: 'Fall 2026' }, g, { defaultDestRepo: 'materials' });
    expect(def).toMatchObject({ op: 'release.adhoc', name: 'Release by hand', title: '3 paths from 2 repos', verb: 'Release 3 paths', cancel: 'Stop before copying' });
    expect(def.options).toBeUndefined();
    expect(def.facts).toEqual(['labs/ · all 2 files → materials/labs/', 'lectures/01_intro/ · 1 of 3 files → materials/lectures/01_intro/', '(top level) · 1 of 1 file → materials/']);
    expect(def.args.semester_dest_repo).toBe('materials,materials,materials');
    expect(def.target).toBe(`https://github.com/${ORG}/materials/tree/main/labs`);
    const one = releasePicked({ courseOrg: 'c', cohortOrg: ORG, where: 'Fall 2026' }, g.slice(0, 1), { defaultDestRepo: 'materials' });
    expect(one.title).toBe('1 path from course-materials-f2026');
  });
});

describe('a disabled folder tick', () => {
  const files = ['labs/a.md', 'only/solutions/s.md', 'only/.DS_Store', 'tests/t.py', 'keys/.env', 'keys/.DS_Store'];
  const copied = (lines: string[] = []) => foldersCopied(files, compileAll(lines, { defaults: true }));
  it('is a folder whose copy would take nothing', () => {
    expect(copied().has('only')).toBe(false);
    expect(copied().has('keys')).toBe(false);
    expect(copied().has('labs')).toBe(true);
    // The denylist judges the names below the folder a copy names (`selection.deny`): `tests/` and `only/solutions/` named ship.
    expect(copied().has('tests')).toBe(true);
    expect(copied().has('only/solutions')).toBe(true);
  });

  it('follows a ! line that re-includes a content name, never a never-released one', () => {
    expect(copied(['!solutions/']).has('only')).toBe(true);
    expect(copied(['!.env']).has('keys')).toBe(false);
  });
});

describe('a destination name', () => {
  const known = { org: ORG, stems: ['assignment-1'] };
  it('refuses the semester’s reserved names and student repos', () => {
    expect(eligibleDestination('semester-config', known)).toBe('That name is reserved for the semester’s config.');
    expect(eligibleDestination('.github', known)).toMatch(/^That name is reserved for /);
    expect(eligibleDestination(`${ORG}.github.io`, known)).toBe('That name is reserved for the semester’s website.');
    expect(eligibleDestination('join', known)).toBe('That name is reserved for the enrolment page.');
    expect(eligibleDestination('grades-octocat', known)).toBe('That name is reserved for students’ marks.');
    expect(eligibleDestination('Assignment-1-Octocat', known)).toBe('That name is reserved for a student’s repo.');
    expect(eligibleDestination('has space', known)).toMatch(/letters, digits/);
    // The engine's reserved names and globs (`selection.json` `reserved`).
    expect(eligibleDestination('other-org.github.io', known)).toBe('That name is reserved for the semester.');
    expect(eligibleDestination('Join', known)).toBe('That name is reserved for the enrolment page.');
    expect(eligibleDestination('slides', known)).toBeNull();
  });

  it('refuses any `<assignment stem>-` name, made or not, as the engine does', () => {
    // A repo not made yet is still a student's, when they accept (`discovery.reserved_destination`).
    expect(eligibleDestination('assignment-1-nobody-yet', known)).toBe('That name is reserved for a student’s repo.');
    expect(eligibleDestination('assignment-1', known)).toBeNull();
    expect(eligibleDestination('assignment-10-x', known)).toBeNull();
    // The stems: each assignment's key, and its semester-side name where that differs.
    const status = { assignments: [{ slug: 'hw1', semester_name: 'hw1-f2026' }, { slug: 'Project' }] } as never;
    expect(assignmentStems(status)).toEqual(['hw1', 'hw1-f2026', 'project']);
    expect(eligibleDestination('project-team-a', { org: ORG, stems: assignmentStems(status) })).toBe('That name is reserved for a student’s repo.');
    expect(assignmentStems(undefined)).toEqual([]);
  });

  it('matches the reserved globs with the one fnmatch builder', () => {
    expect(fnmatch('*.github.io').test('x.github.io')).toBe(true);
    expect(fnmatch('*.github.io').test('xgithubio')).toBe(false);
    expect(fnmatch('grades-*').test('grades-octocat')).toBe(true);
    expect(fnmatch('grades-*').test('my-grades-x')).toBe(false);
    expect(eligibleDestination('Grades-Octocat', known)).toBe('That name is reserved for students’ marks.');
  });
});

describe('one release by hand', () => {
  const row = (path: string, toPath = path) => ({ repo: 'course-materials-f2026', path, toRepo: 'materials', toPath });
  it('caps each list at the engine’s LIST_MAX, and refuses a path with a comma', () => {
    // The schema's cap and the console's are one number.
    const props = opSpec('release.adhoc').args_schema.properties as Record<string, { pattern: string }>;
    for (const k of ['course_source_repo', 'course_source_path', 'semester_dest_repo', 'semester_dest_path']) expect(props[k].pattern).toContain(`{1,${LIST_MAX}}`);
    const files = Array.from({ length: 40 }, (_, i) => row(`lectures/01_intro/notebooks/part_${i}.ipynb`));
    expect(toArgs(files).course_source_path.length).toBeGreaterThan(1024);
    expect(releaseBlocked(files)).toBeNull();
    expect(validateArgs('release.adhoc', toArgs(files))).toEqual([]);
    const many = Array.from({ length: 300 }, (_, i) => row(`lectures/01_intro/notebooks/part_${i}.ipynb`));
    expect(releaseBlocked(many)).toBe(TOO_MANY);
    expect(validateArgs('release.adhoc', toArgs(many))).not.toEqual([]);
    expect(releaseBlocked([row('labs/a,b.md')])).toBe(COMMA_TITLE);
    expect(releaseBlocked([row('labs/a.md', 'x,y')])).toBe(COMMA_TITLE);
    expect(COMMA_TITLE).toBe('A path with a comma cannot be released from here; rename it.');
    expect(TOO_MANY).toBe('Too many paths for one release: tick folders instead, or release in two goes.');
  });

  it('reads a cleared To path as no override: the group lands at its own path', () => {
    const g: Group = { repo: 'r', head: 'labs/', folder: 'labs', kind: 'all', files: 1, sources: ['labs'], ship: ['labs/a.md'], withheld: [] };
    for (const blank of ['', '  ']) {
      const rows = requestOf([{ ...g, destPath: blank }, { ...g, folder: 'lectures', sources: ['lectures'], destPath: 'slides' }], { defaultDestRepo: 'materials' });
      expect(toArgs(rows).semester_dest_path).toBe('labs,slides');
    }
    expect(destPathOf({ ...g, destPath: '' }, 'labs/a.md')).toBe('labs/a.md');
  });
});

describe('after a release', () => {
  const ready = (materials_repo: unknown): Loaded => ({ kind: 'ready', sha: 's', stale: [], status: { semester: { defaults: { materials_repo: { value: materials_repo, source: 'course' } } } } as never });

  it('names the semester’s effective default destination, else the institution’s', () => {
    expect(effectiveDestRepo(ready('course-files'))).toBe('course-files');
    expect(effectiveDestRepo(ready(null))).toBe(DEFAULT_DEST_REPO);
    expect(effectiveDestRepo({ kind: 'loading' })).toBe(DEFAULT_DEST_REPO);
    expect(destRepos({ semester_dest_repo: 'a,b,a' }, 'x')).toEqual(['a', 'b']);
    expect(destRepos({}, 'x')).toEqual(['x']);
  });

  it('reads again the trees of every repo it wrote to, and none after another op', () => {
    const files = { refresh: vi.fn(), refreshUnder: vi.fn(), refreshTree: vi.fn() };
    const statuses = { reload: vi.fn(async () => {}) };
    const def = releaseAdhoc({ courseOrg: 'c', cohortOrg: ORG, where: 'Fall 2026' }, ['course-materials-f2026']);
    refreshAfterRun(def, statuses, files, { semester_dest_repo: 'materials,slides' }, 'materials');
    expect(files.refreshTree.mock.calls).toEqual([[ORG, 'materials'], [ORG, 'slides']]);
    files.refreshTree.mockClear();
    refreshAfterRun(def, statuses, files, { course_source_path: 'labs' }, 'course-files');
    expect(files.refreshTree.mock.calls).toEqual([[ORG, 'course-files']]);
    files.refreshTree.mockClear();
    refreshAfterRun({ ...def, op: 'semester.check' }, statuses, files, {}, 'materials');
    expect(files.refreshTree).not.toHaveBeenCalled();
  });

  it('after a schedule entry, reads again the repo that entry wrote to, else every one known', () => {
    const files = { refresh: vi.fn(), refreshUnder: vi.fn(), refreshTree: vi.fn() };
    const statuses = { reload: vi.fn(async () => {}) };
    const def = { ...releaseAdhoc({ courseOrg: 'c', cohortOrg: ORG, where: 'Fall 2026' }, ['course-materials-f2026']), op: 'release.entry' };
    const rel = (id: string, repo: string) => ({ id, dest: { repo, path: '' } }) as never;
    const releases = [rel('s1', 'materials'), rel('code-1', 'lecture-code'), rel('s2', 'materials')];
    refreshAfterRun(def, statuses, files, { entry: 'code-1' }, 'materials', releases);
    expect(files.refreshTree.mock.calls).toEqual([[ORG, 'lecture-code']]);
    files.refreshTree.mockClear();
    refreshAfterRun(def, statuses, files, { entry: 'gone' }, 'materials', releases);
    expect(files.refreshTree.mock.calls).toEqual([[ORG, 'materials'], [ORG, 'lecture-code']]);
    expect(entryDestRepos('x', [], 'materials')).toEqual(['materials']);
  });

  it('LiveFiles reads a held tree again at every ref, into the same state, with its shas', async () => {
    let n = 0;
    const listTree = vi.fn(async () => ({ sha: 't', truncated: false, tree: [{ path: 'a.md', mode: '100644', type: 'blob' as const, sha: `v${n}` }] }));
    const live = new LiveFiles({ listTree } as never);
    const flush = async () => { for (let i = 0; i < 4; i++) await Promise.resolve(); };
    live.tree(ORG, 'materials');
    live.tree(ORG, 'materials', 'upstream');
    live.tree(ORG, 'other');
    await flush();
    expect(live.tree(ORG, 'materials')).toEqual({ kind: 'ready', truncated: false, paths: [{ path: 'a.md', dir: false, sha: 'v0' }] });
    n = 1;
    live.refreshTree(ORG, 'materials');
    // The old tree stands until the new one answers.
    expect(live.tree(ORG, 'materials').kind).toBe('ready');
    await flush();
    expect(listTree).toHaveBeenCalledTimes(5);
    expect(live.tree(ORG, 'materials', 'upstream')).toMatchObject({ paths: [{ sha: 'v1' }] });
    expect(live.tree(ORG, 'other')).toMatchObject({ paths: [{ sha: 'v0' }] });
  });
});
