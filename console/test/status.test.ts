import { describe, expect, it } from 'vitest';
import { GitHubClient, type Tree } from '../src/github/client';
import { STATUS_PATH, StatusStore, loadStatus, staleInputs, validateStatus } from '../src/model/status';
import example from './fixtures/status.example.json';
import { FakeGitHub, fileBody } from './fake';

const treeOf = (entries: Record<string, string>): Tree => ({
  sha: 'root', truncated: false,
  tree: Object.entries(entries).map(([path, sha]) => ({ path, sha, mode: '100644', type: ['grading_sheets', '.system'].includes(path) ? 'tree' : 'blob' })),
});
// The recursive tree the engine recorded from: every present input (nested ones included,
// under their directory's entry); `teams.csv` is recorded `null`, so it is absent.
const matching = () => {
  const own = Object.entries(example.inputs).filter(([k, v]) => !k.startsWith('course/') && v !== null) as [string, string][];
  return treeOf({ '.system': 'sys-tree', ...Object.fromEntries(own) });
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

  it('matches a nested input by its full path and a recorded null by an absent file', () => {
    expect(example.inputs['.system/assignments.lock.yml']).toBe('sha-6');
    expect(example.inputs['teams.csv']).toBeNull();
    expect(staleInputs(example.inputs, matching())).toEqual([]);
  });

  it('names the inputs that changed, went missing or appeared', () => {
    const t = matching();
    t.tree = t.tree
      .filter((e) => e.path !== 'students.csv')
      .map((e) => (e.path === 'schedule.yml' || e.path === '.system/assignments.lock.yml' ? { ...e, sha: 'edited' } : e));
    t.tree.push({ path: 'teams.csv', sha: 'new', mode: '100644', type: 'blob' });
    expect(staleInputs(example.inputs, t).sort()).toEqual(['.system/assignments.lock.yml', 'schedule.yml', 'students.csv', 'teams.csv']);
  });

  it('does not count a path a truncated tree lacks', () => {
    const t = matching();
    t.truncated = true;
    t.tree = t.tree.filter((e) => e.path !== '.system/assignments.lock.yml');
    expect(staleInputs(example.inputs, t)).toEqual([]);
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

  it('loads, validates and checks staleness with one recursive tree read', async () => {
    const t = matching();
    const gh = new FakeGitHub()
      .on('GET', `/repos/o/semester-config/contents/${STATUS_PATH}`, fileBody(STATUS_PATH, JSON.stringify(example), 'st'))
      .on('GET', '/repos/o/semester-config/git/trees/HEAD?recursive=1', t);
    const l = await loadStatus(new GitHubClient({ token: () => 't', fetch: gh.fetch }), 'o', 'semester-config');
    expect(l.kind).toBe('ready');
    if (l.kind === 'ready') {
      expect(l.stale).toEqual([]);
      expect(l.status.semester?.label).toBe('Fall 2026');
    }
    expect(gh.seen.filter((s) => s.url.includes('/git/trees/')).map((s) => s.url)).toEqual(['https://api.github.com/repos/o/semester-config/git/trees/HEAD?recursive=1']);
  });

  it('reads the tree only once a screen that shows staleness asks', async () => {
    const gh = new FakeGitHub()
      .on('GET', `/repos/o/semester-config/contents/${STATUS_PATH}`, fileBody(STATUS_PATH, JSON.stringify(example), 'st'))
      .on('GET', '/repos/o/semester-config/git/trees/HEAD?recursive=1', matching());
    const store = new StatusStore(new GitHubClient({ token: () => 't', fetch: gh.fetch }));
    const trees = () => gh.seen.filter((x) => x.url.includes('/git/trees/')).length;
    const settle = () => new Promise((r) => setTimeout(r, 0));
    store.cohort('o', false);
    await settle();
    expect([store.cohort('o', false).value.kind, trees()]).toEqual(['ready', 0]);
    store.cohort('o');
    await settle();
    expect(trees()).toBe(1);
    await store.reload('o', 'semester-config');
    expect(trees()).toBe(2); // reloads keep it
  });

  it('reports a file that is not JSON, or not the shape, as invalid', async () => {
    const gh = new FakeGitHub().on('GET', /status\.json$/, fileBody(STATUS_PATH, '{nope'));
    expect((await loadStatus(new GitHubClient({ token: () => 't', fetch: gh.fetch }), 'o', 'r')).kind).toBe('invalid');
    const gh2 = new FakeGitHub().on('GET', /status\.json$/, fileBody(STATUS_PATH, '{"schema":"dsl.status/1"}'));
    const l = await loadStatus(new GitHubClient({ token: () => 't', fetch: gh2.fetch }), 'o', 'r');
    expect(l.kind === 'invalid' && l.errors.join()).toMatch(/inputs/);
  });
});
