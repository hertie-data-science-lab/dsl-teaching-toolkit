// Decisions 0010 and 0012 in the console: one module spells every repo and path name, no
// retired spelling is left in src/, and requests carry the new keys.

import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative } from 'node:path';
import { describe, expect, it } from 'vitest';
import { NAMES } from '../src/model/names';
import { buildRequest } from '../src/ops/adapter';
import { formatsAfter } from '../src/screens/CourseEdit';
import { syncedValues } from '../src/screens/Site';

const SRC = new URL('../src/', import.meta.url).pathname;

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
  ['the old archive row', /cohort-archived/],
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
    expect(Object.keys(NAMES).sort()).toEqual(['assignments_file', 'config_repo', 'instructors_file', 'join_markers', 'join_repo', 'legacy', 'opencourse_file', 'records', 'registry_file', 'semester_topic', 'site_config', 'system_dir']);
    expect(NAMES.config_repo).toBe('semester-config');
    expect(NAMES.records.status.startsWith(`${NAMES.system_dir}/`)).toBe(true);
  });

  it('gives every _config.yml key the engine says a sync rewrites a value from the course or the semester', () => {
    const course = { org: 'c', name: 'N', code: 'C', description: 'D', write: true, admins: [], cohorts: [], meta: {} };
    const values = syncedValues(course, { org: 's', term: 'f2026', termLabel: 'Fall 2026' });
    expect(Object.keys(values)).toEqual(NAMES.site_config.synced);
    for (const [k, v] of Object.entries(values)) expect(v, k).not.toBe('');
  });

  it('agrees with schemas/names.json, which the engine exports, and holds every record the console reads', () => {
    const file = JSON.parse(readFileSync(new URL('../schemas/names.json', import.meta.url), 'utf8'));
    expect(NAMES).toEqual(file);
    const decided = { config_repo: 'semester-config', join_repo: 'join', system_dir: '.system', instructors_file: 'instructors.yml', assignments_file: 'assignments.yml', registry_file: 'semesters.yml' };
    for (const [k, v] of Object.entries(decided)) expect(file[k], k).toBe(v);
    for (const k of ['status', 'outcomes', 'distributed', 'pointer']) expect(file.records[k], `records.${k}`).toMatch(/^\.system\//);
  });

  it('leaves no retired spelling in src/', () => {
    const hits = ours()
      .flatMap((f) => f.text.split('\n').flatMap((line, i) => (allowed(f.rel, line) ? [] : RETIRED.filter(([, re]) => re.test(line)).map(([what]) => `${f.rel}:${i + 1}: ${what}`))));
    expect(hits).toEqual([]);
  });

  it('spells repo names only in model/names.ts', () => {
    const hits = ours()
      .filter((f) => f.rel !== 'model/names.ts')
      .filter((f) => /['"](semester-config|\.github|\.system(\/[^'"]*)?|instructors\.yml|semesters\.yml)['"]/.test(f.text))
      .map((f) => f.rel);
    expect(hits).toEqual([]);
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

describe('the request', () => {
  it('names the semester org semester_org, and the args follow the registry', () => {
    const r = buildRequest('a', { op: 'site.update', courseOrg: 'c', cohortOrg: 's', args: {}, preview: false });
    expect(r.semester_org).toBe('s');
    expect(JSON.stringify(r)).not.toContain('cohort_org');
    expect(() => buildRequest('a', { op: 'materials.create', courseOrg: 'c', args: { tag: 'f2026' }, preview: false })).toThrow();
    expect(buildRequest('a', { op: 'assignment.handout_now', courseOrg: 'c', cohortOrg: 's', args: { course_source_repo: 'x', solution_datetime: 'now' }, preview: true }).args).toEqual({ course_source_repo: 'x', solution_datetime: 'now' });
  });
});
