import { describe, expect, it } from 'vitest';
import { ConflictError, GitHubClient, authorOf, decodeBase64 } from '../src/github/client';
import { FakeGitHub, fileBody, json } from './fake';

const client = (gh: FakeGitHub, onRateLimit?: (r: unknown) => void) =>
  new GitHubClient({ token: () => 'tok', fetch: gh.fetch, onRateLimit });

describe('GitHubClient', () => {
  it('sends the bearer token', async () => {
    const gh = new FakeGitHub().on('GET', '/user', { login: 'a' });
    await client(gh).getUser();
    expect(gh.seen[0].headers.Authorization).toBe('Bearer tok');
  });

  it('revalidates with the ETag and reuses the cached body on 304', async () => {
    let n = 0;
    const gh = new FakeGitHub().on('GET', '/repos/o/r/contents/schedule.yml', (req) => {
      n++;
      if (req.headers['If-None-Match'] === '"v1"') return json(null, 304, { etag: '"v1"' });
      return json(fileBody('schedule.yml', 'timezone: Europe/Berlin\n'), 200, { etag: '"v1"' });
    });
    const c = client(gh);
    const first = await c.getContents('o', 'r', 'schedule.yml');
    const second = await c.getContents('o', 'r', 'schedule.yml');
    expect(n).toBe(2);
    expect(gh.seen[0].headers['If-None-Match']).toBeUndefined();
    expect(gh.seen[1].headers['If-None-Match']).toBe('"v1"');
    expect(second).toEqual(first);
    expect(second?.text).toBe('timezone: Europe/Berlin\n');
  });

  it('keeps the browser cache out, and a write drops the read however it was cased', async () => {
    let text = 'v1';
    const gh = new FakeGitHub()
      .on('GET', /\/repos\/Org\/R\/contents\/a\.yml/, () => json(fileBody('a.yml', text), 200, { etag: `"${text}"` }))
      .on('PUT', /\/repos\/org\/r\/contents\/a\.yml/, () => {
        text = 'v2';
        return { body: { content: { sha: 'n' }, commit: { sha: 'c' } } };
      });
    const c = client(gh);
    expect((await c.getContents('Org', 'R', 'a.yml'))?.text).toBe('v1');
    await c.putContents({ owner: 'org', repo: 'r', path: 'a.yml', text: 'v2', sha: 's', message: 'm', author: { name: 'a', email: 'a@x' } });
    expect((await c.getContents('Org', 'R', 'a.yml'))?.text).toBe('v2');
    expect(gh.seen[2].headers['If-None-Match']).toBeUndefined();
    expect(gh.seen.every((r) => r.cache === 'no-store')).toBe(true);
  });

  it('turns a missing file into null', async () => {
    expect(await client(new FakeGitHub()).getContents('o', 'r', 'nope.yml')).toBeNull();
  });

  it('decodes UTF-8 content', () => {
    expect(decodeBase64(btoa(String.fromCharCode(...new TextEncoder().encode('Friedrichstraße'))))).toBe('Friedrichstraße');
  });

  it('writes sha-conditionally, as the signed-in user', async () => {
    const gh = new FakeGitHub().on('PUT', '/repos/o/semester-config/contents/instructors.yml', { content: { sha: 'new' }, commit: { sha: 'c1' } });
    const author = authorOf({ login: 'octo', id: 7, name: null, email: null, avatar_url: '' });
    const r = await client(gh).putContents({ owner: 'o', repo: 'semester-config', path: 'instructors.yml', text: 'instructors: []\n', sha: 'old', message: 'instructors: edit', author });
    const body = gh.seen[0].body as Record<string, unknown>;
    expect(body.sha).toBe('old');
    expect(body.author).toEqual({ name: 'octo', email: '7+octo@users.noreply.github.com' });
    expect(decodeBase64(body.content as string)).toBe('instructors: []\n');
    expect(r).toEqual({ sha: 'new', commit: 'c1' });
  });

  it('omits the sha when creating a file', async () => {
    const gh = new FakeGitHub().on('PUT', /contents\/new\.md$/, { content: { sha: 'n' }, commit: { sha: 'c' } });
    await client(gh).putContents({ owner: 'o', repo: 'r', path: 'new.md', text: 'x', sha: null, message: 'm', author: { name: 'a', email: 'a@x' } });
    expect((gh.seen[0].body as Record<string, unknown>).sha).toBeUndefined();
  });

  it('raises ConflictError when the file moved on', async () => {
    const gh = new FakeGitHub().on('PUT', /contents/, () => json({ message: 'is at abc but expected def' }, 409));
    await expect(
      client(gh).putContents({ owner: 'o', repo: 'r', path: 'a', text: 'x', sha: 'def', message: 'm', author: { name: 'a', email: 'a@x' } }),
    ).rejects.toBeInstanceOf(ConflictError);
  });

  it('drops cached reads of a repo after writing to it', async () => {
    let gets = 0;
    const gh = new FakeGitHub()
      .on('GET', '/repos/o/r/contents/a', () => { gets++; return json(fileBody('a', 'v1'), 200, { etag: '"e"' }); })
      .on('PUT', '/repos/o/r/contents/a', { content: { sha: 's' }, commit: { sha: 'c' } });
    const c = client(gh);
    await c.getContents('o', 'r', 'a');
    await c.putContents({ owner: 'o', repo: 'r', path: 'a', text: 'v2', sha: 'blob-sha', message: 'm', author: { name: 'a', email: 'a@x' } });
    await c.getContents('o', 'r', 'a');
    expect(gets).toBe(2);
    expect(gh.seen[2].headers['If-None-Match']).toBeUndefined();
  });

  it('surfaces the rate limit headers', async () => {
    let seen: unknown = null;
    const gh = new FakeGitHub().on('GET', '/user', () => json({ login: 'a' }, 200, { 'x-ratelimit-limit': '5000', 'x-ratelimit-remaining': '4990', 'x-ratelimit-reset': '1790000000' }));
    const c = client(gh, (r) => (seen = r));
    await c.getUser();
    expect(c.rateLimit).toEqual({ limit: 5000, remaining: 4990, reset: 1790000000 });
    expect(seen).toEqual(c.rateLimit);
  });

  it('dispatches with return_run_details and returns the run', async () => {
    const gh = new FakeGitHub().on('POST', '/repos/o/.github/actions/workflows/console.yml/dispatches', { workflow_run_id: 99, run_url: 'u', html_url: 'h' });
    const r = await client(gh).dispatchWorkflow({ owner: 'o', repo: '.github', workflow: 'console.yml', ref: 'main', inputs: { request: '{}' } });
    expect(gh.seen[0].body).toEqual({ ref: 'main', inputs: { request: '{}' }, return_run_details: true });
    expect(r?.workflow_run_id).toBe(99);
  });

  it('pages through the user orgs', async () => {
    const page1 = Array.from({ length: 100 }, (_, i) => ({ login: `o${i}` }));
    const gh = new FakeGitHub()
      .on('GET', '/user/orgs?per_page=100&page=1', page1)
      .on('GET', '/user/orgs?per_page=100&page=2', [{ login: 'last' }]);
    const orgs = await client(gh).listUserOrgs();
    expect(orgs).toHaveLength(101);
  });

  it('reads runs, jobs, check runs, topics and a tree', async () => {
    const gh = new FakeGitHub()
      .on('GET', '/repos/o/r/actions/runs/5', { id: 5, status: 'completed' })
      .on('GET', '/repos/o/r/actions/runs/5/jobs', { jobs: [{ id: 1, name: 'console' }] })
      .on('GET', '/repos/o/r/commits/main/check-runs', { check_runs: [{ id: 3, name: 'Validate schedule' }] })
      .on('GET', '/repos/o/r/topics', { names: ['dsl-course-hub'] })
      .on('GET', '/repos/o/r/git/trees/HEAD', { sha: 't', tree: [], truncated: false })
      .on('GET', /^\/search\/repositories/, { items: [{ name: 'course-materials-f2026' }] });
    const c = client(gh);
    expect((await c.getRun('o', 'r', 5)).id).toBe(5);
    expect((await c.listJobs('o', 'r', 5))[0].name).toBe('console');
    expect((await c.listCheckRunsForRef('o', 'r', 'main'))[0].name).toBe('Validate schedule');
    expect(await c.getRepoTopics('o', 'r')).toEqual(['dsl-course-hub']);
    expect((await c.listTree('o', 'r', 'HEAD'))?.sha).toBe('t');
    expect((await c.searchRepos('org:o course-materials'))[0].name).toBe('course-materials-f2026');
  });
});
