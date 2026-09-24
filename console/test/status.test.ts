import { describe, expect, it } from 'vitest';
import { GitHubClient, type Tree } from '../src/github/client';
import { STATUS_PATH, loadStatus, staleInputs, validateStatus } from '../src/model/status';
import example from './fixtures/status.example.json';
import { FakeGitHub, fileBody } from './fake';

const treeOf = (entries: Record<string, string>): Tree => ({
  sha: 'root', truncated: false,
  tree: Object.entries(entries).map(([path, sha]) => ({ path, sha, mode: '100644', type: path === 'grading_sheets' ? 'tree' : 'blob' })),
});
const matching = () => {
  const { 'course/dsl-course.yml': _course, ...own } = example.inputs;
  return treeOf(own);
};

describe('status', () => {
  it('accepts the contracts example', () => {
    expect(validateStatus(example)).toEqual([]);
  });

  it('rejects a wrong schema tag and a bad state', () => {
    expect(validateStatus({ ...example, schema: 'dsl.status/2' }).length).toBeGreaterThan(0);
    const bad = structuredClone(example);
    bad.releases[0].state = 'at_risk';
    expect(validateStatus(bad).join(' ')).toMatch(/releases\/0\/state/);
  });

  it('is fresh when every input sha matches the tree', () => {
    expect(staleInputs(example.inputs, matching())).toEqual([]);
  });

  it('names the inputs that changed or went missing', () => {
    const t = matching();
    t.tree = t.tree.filter((e) => e.path !== 'teams.csv').map((e) => (e.path === 'schedule.yml' ? { ...e, sha: 'edited' } : e));
    expect(staleInputs(example.inputs, t).sort()).toEqual(['schedule.yml', 'teams.csv']);
  });

  it('compares course/ inputs only against the course tree', () => {
    const inputs = { 'course/dsl-course.yml': 'c1', 'schedule.yml': 's1' };
    expect(staleInputs(inputs, treeOf({ 'schedule.yml': 's1' }))).toEqual([]);
    expect(staleInputs(inputs, treeOf({ 'schedule.yml': 's1' }), treeOf({ 'dsl-course.yml': 'c2' }))).toEqual(['course/dsl-course.yml']);
  });

  it('reports an absent file as not computed yet', async () => {
    const c = new GitHubClient({ token: () => 't', fetch: new FakeGitHub().fetch });
    expect(await loadStatus(c, 'o', 'semester-config')).toEqual({ kind: 'absent' });
  });

  it('loads, validates and checks staleness with one tree read', async () => {
    const t = matching();
    const gh = new FakeGitHub()
      .on('GET', `/repos/o/semester-config/contents/${STATUS_PATH}`, fileBody(STATUS_PATH, JSON.stringify(example), 'st'))
      .on('GET', '/repos/o/semester-config/git/trees/HEAD', t);
    const l = await loadStatus(new GitHubClient({ token: () => 't', fetch: gh.fetch }), 'o', 'semester-config');
    expect(l.kind).toBe('ready');
    if (l.kind === 'ready') {
      expect(l.stale).toEqual([]);
      expect(l.status.semester?.label).toBe('Fall 2026');
    }
    expect(gh.seen.filter((s) => s.url.includes('/git/trees/'))).toHaveLength(1);
  });

  it('reports a file that is not JSON, or not the shape, as invalid', async () => {
    const gh = new FakeGitHub().on('GET', /status\.json$/, fileBody(STATUS_PATH, '{nope'));
    expect((await loadStatus(new GitHubClient({ token: () => 't', fetch: gh.fetch }), 'o', 'r')).kind).toBe('invalid');
    const gh2 = new FakeGitHub().on('GET', /status\.json$/, fileBody(STATUS_PATH, '{"schema":"dsl.status/1"}'));
    const l = await loadStatus(new GitHubClient({ token: () => 't', fetch: gh2.fetch }), 'o', 'r');
    expect(l.kind === 'invalid' && l.errors.join()).toMatch(/inputs/);
  });
});
