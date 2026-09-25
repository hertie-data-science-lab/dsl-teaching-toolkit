// Decisions 0010 and 0012 in the console: one module spells every repo and path name, no
// retired spelling is left in src/, an org still on old names gets the one NOT_MIGRATED
// screen, the renamed hashes redirect, and requests carry the new keys.

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import { GitHubClient } from '../src/github/client';
import { courseLeftovers, notMigratedText, semesterLeftovers } from '../src/model/migration';
import { NAMES } from '../src/model/names';
import { buildRequest } from '../src/ops/adapter';
import { hashOf, movedHash, parseHash, wizardOf } from '../src/router';
import { NotMigratedScreen } from '../src/screens/NotMigrated';
import { formatsAfter } from '../src/screens/CourseEdit';
import { FakeGitHub, fileBody, json } from './fake';

const SRC = new URL('../src/', import.meta.url).pathname;

/** The one module allowed to spell a retired name: it looks for them to say so. */
const RETIRED_HOME = new Set(['model/migration.ts']);

function sources(dir = SRC): string[] {
  return readdirSync(dir).flatMap((f) => {
    const p = join(dir, f);
    return statSync(p).isDirectory() ? sources(p) : /\.tsx?$/.test(f) ? [p] : [];
  });
}
const ours = () => sources().map((p) => ({ rel: relative(SRC, p), text: readFileSync(p, 'utf8') }));

/** Lines that must keep a retired spelling: the receipts label chain recognises the old label (0012 exception a). */
const ALLOWED: { file: string; line: RegExp }[] = [{ file: 'model/mine.ts', line: /^export const RECEIPTS_LABELS = \['dsl-receipts', 'dsl-feedback'\];$/ }];
const allowed = (file: string, line: string) => ALLOWED.some((a) => a.file === file && a.line.test(line.trim()));

const RETIRED: [string, RegExp][] = [
  ['the old config repo', /classroom-config/],
  ['the old join repo', /['"`]welcome['"`]/],
  ['the old system folder', /\.dsl\b/],
  ['the old instructors file', /(?<!_data\/)people\.yml/],
  ['the old registry', /cohort-courses-pages/],
  ['the old semester topic', /dsl-cohort/],
  ['request field cohort_org', /\bcohort_org\b/],
  ['cohort_dest_*', /\bcohort_dest_/],
  ['cohort_defaults', /\bcohort_defaults\b/],
  ['status cohort.term_label', /\bterm_label\b/],
  ['status late_until', /\blate_until\b/],
  ['include_solution', /\binclude_solution\b/],
  ['dry_run', /\bdry_run\b/],
  ['the old receipts label', /dsl-feedback/],
  ['receipts thread', /receipts thread/i],
  ['tag (the semester key)', /\btag\s*:|['"]tag['"]|\.tag\b/],
  ['semester_tag', /\bsemester_tag\b/],
  ['term_tag', /\bterm_tag\b/],
  ['format as a key', /\bformat\s*:|['"]format['"]|\.format\b(?!\()/],
  ['silent', /\bsilent\b/],
];

describe('names', () => {
  it('has exactly the keys the engine exports', () => {
    expect(Object.keys(NAMES).sort()).toEqual(['assignments_file', 'config_repo', 'instructors_file', 'join_repo', 'records', 'registry_file', 'system_dir']);
    expect(NAMES.config_repo).toBe('semester-config');
    expect(NAMES.records.status.startsWith(`${NAMES.system_dir}/`)).toBe(true);
  });

  it('agrees with schemas/names.json, which the engine exports, and holds every record the console reads', () => {
    const file = JSON.parse(readFileSync(new URL('../schemas/names.json', import.meta.url), 'utf8'));
    expect(NAMES).toEqual(file);
    const decided = { config_repo: 'semester-config', join_repo: 'join', system_dir: '.system', instructors_file: 'instructors.yml', assignments_file: 'assignments.yml', registry_file: 'semesters.yml' };
    for (const [k, v] of Object.entries(decided)) expect(file[k], k).toBe(v);
    for (const k of ['status', 'outcomes', 'distributed', 'pointer']) expect(file.records[k], `records.${k}`).toMatch(/^\.system\//);
  });

  it('leaves no retired spelling in src/, outside the migration check', () => {
    const hits = ours()
      .filter((f) => !RETIRED_HOME.has(f.rel))
      .flatMap((f) => f.text.split('\n').flatMap((line, i) => (allowed(f.rel, line) ? [] : RETIRED.filter(([, re]) => re.test(line)).map(([what]) => `${f.rel}:${i + 1}: ${what}`))));
    expect(hits).toEqual([]);
  });

  it('spells repo names only in model/names.ts', () => {
    const hits = ours()
      .filter((f) => f.rel !== 'model/names.ts' && !RETIRED_HOME.has(f.rel))
      .filter((f) => /['"](semester-config|\.github|\.system(\/[^'"]*)?|instructors\.yml|semesters\.yml)['"]/.test(f.text))
      .map((f) => f.rel);
    expect(hits).toEqual([]);
  });
});

describe('an org on old names', () => {
  const client = (gh: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: gh.fetch });
  const ORG = 'hertie-dsl-demo-f2026';

  it('a migrated semester has no leftovers', async () => {
    const gh = new FakeGitHub()
      .on('GET', `/repos/${ORG}/semester-config`, { name: 'semester-config' })
      .on('GET', `/repos/${ORG}/join`, { name: 'join' })
      .on('GET', `/repos/${ORG}/.github`, { name: '.github', topics: ['dsl-semester'] })
      .on('GET', `/repos/${ORG}/semester-config/git/trees/HEAD`, { tree: [{ path: 'schedule.yml', type: 'blob', sha: 'a' }, { path: 'instructors.yml', type: 'blob', sha: 'b' }, { path: '.system', type: 'tree', sha: 'c' }] });
    expect(await semesterLeftovers(client(gh), ORG)).toEqual([]);
  });

  it('names every retired repo, file, folder and topic a semester still carries', async () => {
    const gh = new FakeGitHub()
      .on('GET', `/repos/${ORG}/classroom-config`, { name: 'classroom-config' })
      .on('GET', `/repos/${ORG}/welcome`, { name: 'welcome' })
      .on('GET', `/repos/${ORG}/.github`, { name: '.github', topics: ['dsl-cohort'] })
      .on('GET', `/repos/${ORG}/classroom-config/git/trees/HEAD`, { tree: [{ path: 'people.yml', type: 'blob', sha: 'a' }, { path: '.dsl', type: 'tree', sha: 'b' }] });
    const left = await semesterLeftovers(client(gh), ORG);
    expect(left.map((l) => l.old)).toEqual(['dsl-cohort', 'classroom-config', 'welcome', 'people.yml', '.dsl/']);
    expect(notMigratedText(left[1])).toBe('NOT_MIGRATED: `classroom-config` is the old name of `semester-config` - run the migration');
  });

  it('names a course whose registry and folder still carry old names', async () => {
    const gh = new FakeGitHub().on('GET', '/repos/c/.github/git/trees/HEAD', { tree: [{ path: 'cohort-courses-pages.yml', type: 'blob', sha: 'a' }, { path: 'dsl-course.yml', type: 'blob', sha: 'b' }] });
    expect((await courseLeftovers(client(gh), 'c')).map((l) => l.new)).toEqual(['semesters.yml']);
    const done = new FakeGitHub().on('GET', '/repos/c/.github/git/trees/HEAD', () => json({ tree: [{ path: 'semesters.yml', type: 'blob', sha: 'a' }] }));
    expect(await courseLeftovers(client(done), 'c')).toEqual([]);
  });

  it('reads the course files too: a cohorts: registry key or a retired dsl-course.yml key is not migrated', async () => {
    const gh = new FakeGitHub()
      .on('GET', '/repos/c/.github/git/trees/HEAD', { tree: [{ path: 'semesters.yml', type: 'blob', sha: 'a' }, { path: 'dsl-course.yml', type: 'blob', sha: 'b' }] })
      .on('GET', '/repos/c/.github/contents/semesters.yml', fileBody('semesters.yml', 'cohorts:\n  - s\n'))
      .on(
        'GET',
        '/repos/c/.github/contents/dsl-course.yml',
        fileBody('dsl-course.yml', 'org: c\norg_name: X\ncourse_name: X\ncohort_defaults:\n  timezone: UTC\nsemester_defaults:\n  timezone: UTC\n'),
      );
    const left = await courseLeftovers(client(gh), 'c');
    expect(left.map((l) => l.old)).toEqual(['cohorts:', 'org:', 'org_name:', 'cohort_defaults:', 'semester_defaults:']);
    expect(notMigratedText(left[1])).toBe('NOT_MIGRATED: `org:` is no longer read - run the migration');
  });

  it('a check GitHub does not answer fails, rather than passing as migrated', async () => {
    const down = new FakeGitHub().on('GET', /.*/, () => json({ message: 'boom' }, 500));
    await expect(courseLeftovers(client(down), 'c')).rejects.toThrow();
    await expect(semesterLeftovers(client(down), ORG)).rejects.toThrow();
    const noTree = new FakeGitHub().on('GET', `/repos/${ORG}/semester-config`, { name: 'semester-config' }).on('GET', `/repos/${ORG}/.github`, { name: '.github', topics: ['dsl-semester'] });
    await expect(semesterLeftovers(client(noTree), ORG)).rejects.toThrow();
  });

  it('shows one screen with the NOT_MIGRATED sentence and nothing else', () => {
    const out = render(<NotMigratedScreen what="semester" org={ORG} leftovers={[{ old: 'classroom-config', new: 'semester-config' }]} />);
    expect(out).toContain('This semester has not been migrated yet');
    expect(out).toContain('NOT_MIGRATED: `classroom-config` is the old name of `semester-config` - run the migration');
    expect(out).not.toContain('Check now');
  });
});

describe('the course default format', () => {
  it('replaces only the first entry and keeps the others', () => {
    expect(formatsAfter({ assignment_defaults: { formats: ['ipynb', 'py'] } }, 'rmd')).toEqual(['rmd', 'py']);
    expect(formatsAfter({ assignment_defaults: { formats: 'ipynb,py' } }, 'py')).toEqual(['py']);
    expect(formatsAfter({}, 'qmd')).toEqual(['qmd']);
    expect(formatsAfter({ assignment_defaults: { formats: ['ipynb', 'py'] } }, '')).toBeUndefined();
  });
});

describe('renamed routes', () => {
  it('redirects each old hash to its new name', () => {
    expect(movedHash('#cohort')).toBe('#semester');
    expect(movedHash('#staff')).toBe('#instructors');
    expect(movedHash('#new-cohort-2')).toBe('#new-semester-2');
    expect(movedHash('#new-cohort')).toBe('#new-semester');
    expect(movedHash('#schedule-term')).toBe('#schedule-semester');
    expect(movedHash('#semester')).toBeNull();
    expect(hashOf(parseHash('#schedule-s5'))).toBe('#schedule-s5');
    expect(wizardOf(parseHash('#new-cohort-3').screen)).toEqual({ name: 'new-semester', step: 3 });
  });
});

describe('the request', () => {
  it('names the semester org semester_org, and the args follow the registry', () => {
    const r = buildRequest('a', { op: 'site.update', courseOrg: 'c', cohortOrg: 's', args: {}, preview: false });
    expect(r.semester_org).toBe('s');
    expect(JSON.stringify(r)).not.toContain('cohort_org');
    expect(() => buildRequest('a', { op: 'materials.create', courseOrg: 'c', args: { tag: 'f2026' }, preview: false })).toThrow();
    expect(buildRequest('a', { op: 'assignment.handout_now', courseOrg: 'c', cohortOrg: 's', args: { course_source_repo: 'x', solution_datetime: 'now' }, preview: true }).args).toEqual({ course_source_repo: 'x', solution_datetime: 'now' });
  });
});
