import { describe, expect, it } from 'vitest';
import { GitHubClient } from '../src/github/client';
import { DispatchAdapter, RequestInvalid, buildRequest, leakedHandles, parseOutcome, stepsOf } from '../src/ops/adapter';
import * as defs from '../src/ops/defs';
import { OpsSession, mergeOperations } from '../src/ops/session';
import { modeOf } from '../src/ops/registry';
import { FakeGitHub, fileBody, json } from './fake';

const COURSE = 'hertie-dsl-demo-course-e1234';
const COHORT = 'hertie-dsl-demo-f2026';
const RUNS = `/repos/${COURSE}/.github/actions/runs/77`;

const publicOutcome = {
  schema: 'dsl.outcome/1', op: 'roster.send_codes', run_id: 77, actor: 'a-example', preview: true, conclusion: 'previewed',
  summary: 'Preview: 7 students would get a new code; their old codes would stop working.', counts: { would_send: 7 },
  reasons: [{ code: 'WOULD_SEND', text: '7 new codes' }], started: '2026-09-23T09:00:00Z', finished: '2026-09-23T09:01:00Z',
};
const privateOutcome = { ...publicOutcome, people: [{ handle: 'anna-a', text: 'would get a new code' }, { handle: 'ben-b', text: 'would get a new code' }] };

function engine(opts: { annotation?: object; privateFile?: object; polls?: number } = {}) {
  let polls = 0;
  const gh = new FakeGitHub()
    .on('POST', `/repos/${COURSE}/.github/actions/workflows/console.yml/dispatches`, { workflow_run_id: 77, run_url: 'u', html_url: `https://github.com/${COURSE}/.github/actions/runs/77` })
    .on('GET', RUNS, (req) => {
      polls++;
      if (polls > 1 && polls < (opts.polls ?? 3) && req.headers['If-None-Match'] === '"r1"') return json(null, 304, { etag: '"r1"' });
      const done = polls >= (opts.polls ?? 3);
      return json({ id: 77, status: done ? 'completed' : 'in_progress', conclusion: done ? 'success' : null, html_url: 'h' }, 200, { etag: done ? '"r2"' : '"r1"' });
    })
    .on('GET', `${RUNS}/jobs`, { jobs: [{ id: 901, name: 'console', status: 'completed', conclusion: 'success', steps: [
      { name: 'Set up job', status: 'completed', conclusion: 'success', number: 1 },
      { name: 'Verify the user may run actions for THIS repo', status: 'completed', conclusion: 'success', number: 2 },
      { name: 'Run actions/checkout@11d5960', status: 'completed', conclusion: 'success', number: 3 },
      { name: 'Run the request', status: 'in_progress', conclusion: null, number: 5 },
      { name: 'Complete job', status: 'queued', conclusion: null, number: 9 },
    ] }] })
    .on('GET', `/repos/${COURSE}/.github/check-runs/901/annotations`, [
      { path: '.github', start_line: 1, annotation_level: 'notice', title: 'something else', message: 'x' },
      ...(opts.annotation ? [{ path: '.github', start_line: 1, annotation_level: 'notice', title: 'dsl-outcome', message: JSON.stringify(opts.annotation) }] : []),
    ])
    .on('GET', `/repos/${COHORT}/classroom-config/contents/.dsl/outcomes/roster.send_codes.json`, () =>
      opts.privateFile ? json(fileBody('.dsl/outcomes/roster.send_codes.json', JSON.stringify(opts.privateFile))) : json({ message: 'Not Found' }, 404));
  return { gh, polls: () => polls, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }) };
}

const scope = { courseOrg: COURSE, cohortOrg: COHORT, where: 'Fall 2026' };

describe('the request', () => {
  it('follows dsl.request/1 and carries no names', () => {
    const r = buildRequest('a-example', { op: 'release.early', courseOrg: COURSE, cohortOrg: COHORT, args: { entry: 's5', cohort_dest_repo: '' }, preview: true });
    expect(r).toEqual({ schema: 'dsl.request/1', op: 'release.early', actor: 'a-example', course_org: COURSE, cohort_org: COHORT, args: { entry: 's5' }, preview: true, client: 'console/0.1' });
  });
  it('drops the cohort for a course op and refuses bad args, a missing cohort and an impossible preview', () => {
    expect(buildRequest('a', { op: 'assignment.derive_starter', courseOrg: COURSE, cohortOrg: COHORT, args: { course_source_repo: 'assignment-3-f2026' }, preview: true }).cohort_org).toBeUndefined();
    expect(() => buildRequest('a', { op: 'release.early', courseOrg: COURSE, cohortOrg: COHORT, args: {}, preview: false })).toThrow(RequestInvalid);
    expect(() => buildRequest('a', { op: 'release.early', courseOrg: COURSE, cohortOrg: COHORT, args: { entry: '-rf' }, preview: false })).toThrow(/pattern/);
    expect(() => buildRequest('a', { op: 'cohort.check', courseOrg: COURSE, args: {}, preview: false })).toThrow(/needs a cohort/);
    expect(() => buildRequest('a', { op: 'site.update', courseOrg: COURSE, cohortOrg: COHORT, args: {}, preview: true })).toThrow(/no preview/);
  });
});

describe('DispatchAdapter', () => {
  it('dispatches console.yml on main with the request as its one input and gets the run back', async () => {
    const e = engine();
    const h = await new DispatchAdapter(e.client, () => 'a-example').submit({ op: 'roster.send_codes', courseOrg: COURSE, cohortOrg: COHORT, args: {}, preview: true });
    const body = e.gh.seen[0].body as { ref: string; inputs: { request: string }; return_run_details: boolean };
    expect(body.ref).toBe('main');
    expect(body.return_run_details).toBe(true);
    expect(JSON.parse(body.inputs.request)).toMatchObject({ op: 'roster.send_codes', cohort_org: COHORT, preview: true });
    expect(h.runId).toBe(77);
  });

  it('polls the run with its ETag, taking a 304 as no change', async () => {
    const e = engine({ polls: 4 });
    const a = new DispatchAdapter(e.client, () => 'a-example', () => 'Sending new codes');
    const h = { op: 'roster.send_codes', runId: 77, htmlUrl: '', preview: true, courseOrg: COURSE, cohortOrg: COHORT };
    const first = await a.watch(h);
    const second = await a.watch(h);
    expect(first.state).toBe('running');
    expect(second.state).toBe('running');
    const runGets = e.gh.seen.filter((s) => s.url.endsWith('/runs/77'));
    expect(runGets[1].headers['If-None-Match']).toBe('"r1"');
    expect(first.steps.map((s) => [s.name, s.state])).toEqual([['Checking you may do this', 'done'], ['Getting the engine', 'done'], ['Sending new codes', 'running']]);
    await a.watch(h);
    expect((await a.watch(h)).state).toBe('completed');
  });

  it('reads the public annotation, adds the private people, and finds no leak', async () => {
    const e = engine({ annotation: publicOutcome, privateFile: privateOutcome });
    const r = await new DispatchAdapter(e.client, () => 'a').outcome({ op: 'roster.send_codes', runId: 77, htmlUrl: '', preview: true, courseOrg: COURSE, cohortOrg: COHORT });
    expect(r.outcome?.summary).toBe(publicOutcome.summary);
    expect(r.outcome?.people).toBeUndefined();
    expect(r.people.map((p) => p.handle)).toEqual(['anna-a', 'ben-b']);
    expect(r.leaked).toEqual([]);
  });

  it('flags a handle from the private file that reached the public summary', async () => {
    const leaky = { ...publicOutcome, summary: 'Preview: anna-a would get a new code.' };
    const e = engine({ annotation: leaky, privateFile: privateOutcome });
    const r = await new DispatchAdapter(e.client, () => 'a').outcome({ op: 'roster.send_codes', runId: 77, htmlUrl: '', preview: true, courseOrg: COURSE, cohortOrg: COHORT });
    expect(r.leaked).toEqual(['anna-a']);
    expect(leakedHandles(publicOutcome as never, [{ handle: 'anna' }])).toEqual([]);
  });

  it('falls back to the private record, and ignores one from another run', async () => {
    const e = engine({ privateFile: privateOutcome });
    const a = new DispatchAdapter(e.client, () => 'a');
    const r = await a.outcome({ op: 'roster.send_codes', runId: 77, htmlUrl: '', preview: true, courseOrg: COURSE, cohortOrg: COHORT });
    expect(r.outcome?.summary).toBe(publicOutcome.summary);
    const other = engine({ privateFile: { ...privateOutcome, run_id: 12 } });
    expect((await new DispatchAdapter(other.client, () => 'a').outcome({ op: 'roster.send_codes', runId: 77, htmlUrl: '', preview: true, courseOrg: COURSE, cohortOrg: COHORT })).outcome).toBeNull();
  });

  it('parses only a valid outcome', () => {
    expect(parseOutcome('not json')).toBeNull();
    expect(parseOutcome(JSON.stringify({ schema: 'dsl.outcome/1' }))).toBeNull();
    expect(parseOutcome(JSON.stringify(publicOutcome))?.conclusion).toBe('previewed');
    expect(stepsOf(undefined, 'x')).toEqual([]);
  });
});

describe('the gate', () => {
  const run = (e: ReturnType<typeof engine>) => new OpsSession(new DispatchAdapter(e.client, () => 'a-example'), { pollMs: 0, sleep: async () => {} });

  it('keeps a gated verb shut until a preview in this session finishes', async () => {
    const e = engine({ annotation: publicOutcome, privateFile: privateOutcome });
    const s = run(e);
    const def = defs.sendCodes(scope, 7);
    expect(modeOf(def.op)).toBe('gated');
    s.open(def);
    expect(s.canRun(s.current.value!)).toBe(false);
    await s.start('run');
    expect(e.gh.seen.filter((x) => x.method === 'POST')).toHaveLength(0);
    await s.start('preview');
    expect(s.current.value?.dry?.outcome?.summary).toBe(publicOutcome.summary);
    expect(s.isPreviewed(def)).toBe(true);
    expect(s.canRun(s.current.value!)).toBe(true);
    expect(s.isPreviewed(defs.sendCodes({ ...scope, cohortOrg: 'hertie-dsl-demo-s2027' }, 7))).toBe(false);
    expect(s.runs.value[0]).toMatchObject({ run_id: 77, op: 'roster.send_codes', conclusion: 'previewed' });
    // The page's verb after the panel's preview keeps the preview and runs for real.
    s.open(def, 'run');
    await new Promise((r) => setTimeout(r, 0));
    await new Promise((r) => setTimeout(r, 0));
    const posts = e.gh.seen.filter((x) => x.method === 'POST').map((x) => JSON.parse((x.body as { inputs: { request: string } }).inputs.request).preview);
    expect(posts).toEqual([true, false]);
  });

  it('does not unlock on a failed preview', async () => {
    const e = engine({ annotation: { ...publicOutcome, conclusion: 'failed' } });
    const s = run(e);
    const def = defs.sendCodes(scope, 7);
    s.open(def, 'preview');
    await new Promise((r) => setTimeout(r, 0));
    await s.start('preview');
    expect(s.isPreviewed(def)).toBe(false);
  });

  it('runs a direct op without a preview, and asks for the tick where the engine has none', () => {
    const e = engine();
    const s = run(e);
    s.open(defs.updateSite(scope));
    expect(modeOf('site.update')).toBe('direct');
    expect(s.canRun(s.current.value!)).toBe(true);
    s.open(defs.publishWebsite({ courseOrg: COURSE, where: 'Machine Learning' }, ['course-materials-f2026'], {}, false));
    expect(modeOf('course.publish_website')).toBe('direct');
    expect(s.canRun(s.current.value!)).toBe(false);
    s.setChecked(true);
    expect(s.canRun(s.current.value!)).toBe(true);
  });

  it('sends force only when archiving early is ticked', async () => {
    const e = engine({ annotation: { ...publicOutcome, op: 'cohort.archive' } });
    const s = run(e);
    s.open(defs.archive(scope, 'Sun 31 Jan 2027', false));
    s.setChecked(true);
    await s.start('preview');
    const req = JSON.parse((e.gh.seen.find((x) => x.method === 'POST')!.body as { inputs: { request: string } }).inputs.request);
    expect(req.args).toEqual({ force: true });
    expect(req.preview).toBe(true);
  });

  it('keeps one operation at a time', async () => {
    const e = engine({ annotation: publicOutcome });
    let release: () => void = () => {};
    const s = new OpsSession(new DispatchAdapter(e.client, () => 'a'), { pollMs: 0, sleep: () => new Promise((r) => (release = r)) });
    s.open(defs.checkNow(scope));
    const p = s.start('run');
    await new Promise((r) => setTimeout(r, 0));
    s.open(defs.updateSite(scope));
    expect(s.current.value?.def.op).toBe('cohort.check');
    expect(s.notice.value).toContain('One operation at a time');
    release();
    await new Promise((r) => setTimeout(r, 0));
    release();
    await p.catch(() => {});
  });

  it('merges this session’s runs into the status record, newest first, without duplicates', () => {
    const a = { run_id: 1, op: 'release.now', conclusion: 'done' as const, summary: 'a', finished: '2026-09-22T10:00:00Z' };
    const b = { run_id: 2, op: 'site.update', conclusion: 'done' as const, summary: 'b', finished: '2026-09-23T10:00:00Z' };
    expect(mergeOperations([a], [b, a]).map((o) => o.run_id)).toEqual([2, 1]);
  });
});
