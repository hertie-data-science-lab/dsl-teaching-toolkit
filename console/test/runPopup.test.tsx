// @vitest-environment happy-dom
// The run popup (decision 0031 rules 5-7): Stop cancels the workflow run in every popup and
// says so, each real step shows once, the drawer resizes, "See on GitHub" opens the op's
// target, the previews nobody acts on are gone, and "Marked from" carries its `?`.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import { StaticFiles } from '../src/model/files';
import { StatusStore } from '../src/model/status';
import { DispatchAdapter, stepsOf, type Adapter, type Handle, type Progress, type Result } from '../src/ops/adapter';
import * as defs from '../src/ops/defs';
import { OpButtons, OpPanel, targetOf } from '../src/ops/Panel';
import { GATED, PREVIEW_NOT_OFFERED, modeOf, opSpec } from '../src/ops/registry';
import { OpsSession, type OpDef } from '../src/ops/session';
import { MARKED_FROM_HINT, Questions } from '../src/screens/Course';
import { FakeGitHub, json } from './fake';

const COURSE = 'hertie-dsl-demo-course-e1234';
const COHORT = 'hertie-dsl-demo-f2026';
const scope = { courseOrg: COURSE, cohortOrg: COHORT, where: 'Fall 2026' };
const course = { courseOrg: COURSE, where: 'Deep Learning' };
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const asg = { slug: 'assignment-2', title: 'Assignment 2', template: 'assignment-2-f2026', units: 12, group: false, when: 'Due Fri 9 Oct' };
const rel = { id: 's5', ident: 'Session 5', title: 'Trees', when: 'Thu 8 Oct 10:00', source: { repo: 'course-materials-f2026', path: 'lectures/05' }, dest: { repo: 'materials', path: 'lectures/05' } };

/** Every def the console builds, one per op (the weekly plan's is U5's and left as it is). */
const ALL: OpDef[] = [
  defs.checkNow(scope), defs.previewNext(scope), defs.keepFuture(scope), defs.releaseEarly(scope, rel), defs.releaseAgain(scope, rel),
  defs.releaseNow(scope, rel), defs.releaseAdhoc(scope, ['course-materials-f2026']), defs.handout(scope, asg), defs.updateCopies(scope, asg, ['README.md']),
  defs.collect(scope, asg), defs.returnMarks(scope, asg, 10, 'assignment-2'), defs.sendCodes(scope, 3), defs.updateSite(scope), defs.checkAccess(scope),
  defs.archive(scope, null, true), defs.publishWebsite(course, true), defs.teamsWindow(scope, { ...asg, group: true }, 'Fri 9 Oct'),
  defs.derive(course, 'assignment-2-f2026', 'assignment-2-f2026', 'Assignment 2'), defs.generateSyllabus(scope, 'course-materials-f2026'),
  defs.bootstrapCohort({ ...scope, cohortOrg: COHORT }, 'Deep Learning'), defs.createAssignment(course, 'assignment-9-f2026', 'Assignment 9', { title: 'Assignment 9' }),
  defs.createMaterials(course, 'course-materials-s2027', {}),
];

/** An adapter whose run stays in progress until `finish` is called, with `conclusion`. */
class Scripted implements Adapter {
  cancels: number[] = [];
  submitted = 0;
  conclusion = 'success';
  outcome_: Result['outcome'] = null;
  cancelError: Error | null = null;
  private done = false;
  private release: (h: Handle) => void = () => {};
  private readonly named: Promise<Handle> | null;
  constructor(hold = false) {
    this.named = hold ? new Promise((r) => (this.release = r)) : null;
  }
  async submit(i: { op: string; courseOrg: string; preview: boolean }): Promise<Handle> {
    this.submitted++;
    const h = { op: i.op, runId: 77, htmlUrl: 'https://github.com/run/77', preview: i.preview, courseOrg: i.courseOrg };
    if (this.named) {
      await this.named;
    }
    return h;
  }
  name(): void {
    this.release({} as Handle);
  }
  finish(conclusion = this.conclusion): void {
    this.conclusion = conclusion;
    this.done = true;
  }
  async watch(): Promise<Progress> {
    return this.done
      ? { state: 'completed', conclusion: this.conclusion, steps: [], htmlUrl: 'https://github.com/run/77' }
      : { state: 'running', conclusion: null, steps: [{ name: 'Checking you may do this', state: 'done' }, { name: 'Running', state: 'running' }], htmlUrl: 'https://github.com/run/77' };
  }
  async outcome(): Promise<Result> {
    return { outcome: this.outcome_, people: [], leaked: [] };
  }
  async cancel(h: Handle): Promise<void> {
    if (this.cancelError) throw this.cancelError;
    this.cancels.push(h.runId);
    this.finish('cancelled');
  }
}

const tick = () => new Promise<void>((r) => setTimeout(r, 0));
const session = (a: Adapter) => new OpsSession(a, { pollMs: 0, sleep: tick, outcomeTries: 1 });

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

function envOf(ops: OpsSession): Env {
  const client = new GitHubClient({ token: () => 't', fetch: new FakeGitHub().fetch });
  return { client, user: USER, ops, statuses: new StatusStore(client), files: new StaticFiles({}), pollMs: 0 };
}
async function mount(env: Env, ui: preact.VNode) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{ui}</EnvCtx.Provider>, root!));
  return root;
}
const buttons = () => [...root!.querySelectorAll('button')];
const button = (text: string) => buttons().find((b) => b.textContent?.trim() === text);
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(tick);
}

describe('the steps', () => {
  // The Console job as GitHub reported it on the demo (run 36892777622), post steps and all.
  const job = { id: 1, name: 'console', status: 'completed', conclusion: 'success', steps: [
    'Set up job', 'Verify the user may run actions for THIS repo', 'Run actions/checkout@11d5960a', 'Run actions/setup-python@a26af69b',
    'Run pip install -r requirements.txt', 'Run the request', 'Report a failed Console run as an issue', 'Email the maintainer the failed step’s log',
    'Close the failure issue once a run succeeds', 'Post Run actions/setup-python@a26af69b', 'Post Run actions/checkout@11d5960a', 'Complete job',
  ].map((name, i) => ({ name, number: i + 1, status: 'queued', conclusion: null })) };

  it('shows each real step once: the post steps GitHub adds are not a second Preparing and Getting the engine', () => {
    expect(stepsOf(job as never, 'Deriving the student version').map((s) => s.name)).toEqual(['Checking you may do this', 'Getting the engine', 'Preparing', 'Deriving the student version']);
  });
});

describe('Stop', () => {
  it('cancels the workflow run through the Actions API, says Stopping, then Stopped', async () => {
    const gh = new FakeGitHub()
      .on('POST', `/repos/${COURSE}/.github/actions/workflows/console.yml/dispatches`, { workflow_run_id: 77, html_url: 'h' })
      .on('GET', `/repos/${COURSE}/.github/actions/runs/77`, () => json({ id: 77, status: cancelled ? 'completed' : 'in_progress', conclusion: cancelled ? 'cancelled' : null, html_url: 'h' }))
      .on('GET', `/repos/${COURSE}/.github/actions/runs/77/jobs`, { jobs: [] })
      .on('POST', `/repos/${COURSE}/.github/actions/runs/77/cancel`, () => ((cancelled = true), json({}, 202)))
      .on('GET', /contents/, () => json({ message: 'Not Found' }, 404));
    let cancelled = false;
    const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
    const ops = session(new DispatchAdapter(client, () => 'a-example'));
    await mount(envOf(ops), <OpPanel />);
    await act(() => ops.open(defs.derive(course, 'assignment-2-f2026', 'assignment-2-f2026', 'Assignment 2'), 'run'));
    await settle(() => !!ops.current.value?.handle);
    await act(() => button('Stop')!.click());
    expect(gh.seen.some((s) => s.method === 'POST' && s.url.endsWith('/actions/runs/77/cancel'))).toBe(true);
    await settle(() => ops.current.value?.phase === 'done');
    expect(ops.current.value?.stopped).toBe(true);
    expect(root!.textContent).toContain('Stopped.');
    expect(ops.runs.value[0]).toMatchObject({ run_id: 77, conclusion: 'skipped', summary: 'Stopped before it finished.' });
  });

  it('shows Stopping and holds the button while GitHub ends the run', async () => {
    const a = new Scripted();
    a.cancel = async (h: Handle) => void a.cancels.push(h.runId); // GitHub has not ended it yet
    const ops = session(a);
    await mount(envOf(ops), <OpPanel />);
    await act(() => ops.open(defs.updateSite(scope), 'run'));
    await settle(() => !!ops.current.value?.handle);
    await act(() => button('Stop; the site keeps its current version')!.click());
    await act(tick);
    const b = button('Stopping…')!;
    expect(b.disabled).toBe(true);
    expect(root!.querySelector('.op-mode')!.textContent).toBe('Stopping…');
    a.finish('cancelled');
    await settle(() => ops.current.value?.phase === 'done');
    expect(a.cancels).toEqual([77]);
  });

  it('sends the cancel as soon as GitHub names a run Stop was pressed before', async () => {
    const a = new Scripted(true);
    const ops = session(a);
    ops.open(defs.checkNow(scope));
    const p = ops.start('run');
    await tick();
    await ops.cancel();
    expect(a.cancels).toEqual([]);
    expect(ops.current.value?.stopping).toBe(true);
    a.name();
    await p;
    expect(a.cancels).toEqual([77]);
    expect(ops.current.value).toMatchObject({ phase: 'done', stopped: true, stopping: false });
  });

  it('says so when the run had already finished, keeping what it reports', async () => {
    const a = new Scripted();
    a.outcome_ = { schema: 'dsl.outcome/1', op: 'assignment.derive_starter', run_id: 77, actor: 'a', preview: false, conclusion: 'done', summary: 'Derived 1 file onto main.' };
    const ops = session(a);
    await mount(envOf(ops), <OpPanel />);
    await act(() => ops.open(defs.derive(course, 'assignment-2-f2026', 'assignment-2-f2026', 'Assignment 2'), 'run'));
    await settle(() => !!ops.current.value?.handle);
    await act(() => button('Stop')!.click());
    await settle(() => ops.current.value?.phase === 'done');
    expect(root!.textContent).toContain('Stopped, but too late: it had already finished.');
    expect(root!.textContent).toContain('Derived 1 file onto main.');
  });

  it('shows why when GitHub refuses the cancel, and lets Stop be pressed again', async () => {
    const a = new Scripted();
    a.cancelError = new Error('Resource not accessible by integration');
    const ops = session(a);
    await mount(envOf(ops), <OpPanel />);
    await act(() => ops.open(defs.checkNow(scope), 'run'));
    await settle(() => !!ops.current.value?.handle);
    await act(() => button('Stop')!.click());
    await settle(() => root!.textContent!.includes('Could not stop it'));
    expect(root!.textContent).toContain('Could not stop it: Resource not accessible by integration');
    expect(button('Stop')!.disabled).toBe(false);
    a.finish();
    await settle(() => ops.current.value?.phase === 'done');
  });

  it('treats GitHub’s 409 for a run that has already ended as nothing left to stop', async () => {
    const gh = new FakeGitHub().on('POST', `/repos/${COURSE}/.github/actions/runs/77/cancel`, () => json({ message: 'Cannot cancel a workflow run that is completed.' }, 409));
    const adapter = new DispatchAdapter(new GitHubClient({ token: () => 't', fetch: gh.fetch }), () => 'a');
    await expect(adapter.cancel({ op: 'site.update', runId: 77, htmlUrl: '', preview: false, courseOrg: COURSE })).resolves.toBeUndefined();
  });

  it('stops every op the console runs: each popup’s Stop cancels its own run', async () => {
    for (const def of ALL) {
      const a = new Scripted();
      const ops = session(a);
      ops.open(def);
      if (def.needsCheck) ops.setChecked(true);
      const mode = modeOf(def.op);
      const p = ops.start(mode === 'gated' || mode === 'previewOnly' ? 'preview' : 'run');
      await tick();
      expect(def.cancel, def.op).toBeTruthy();
      await ops.cancel();
      await p;
      expect(a.cancels, def.op).toEqual([77]);
      expect(ops.current.value?.stopped, def.op).toBe(true);
    }
  });
});

describe('See on GitHub', () => {
  it('opens the place the run changed once it ends: derive goes to the starter on main', async () => {
    const a = new Scripted();
    a.outcome_ = { schema: 'dsl.outcome/1', op: 'assignment.derive_starter', run_id: 77, actor: 'a', preview: false, conclusion: 'done', summary: 'Derived 1 file onto main.' };
    const ops = session(a);
    await mount(envOf(ops), <OpPanel />);
    await act(() => ops.open(defs.derive(course, 'assignment-2-f2026', 'assignment-2-f2026', 'Assignment 2'), 'run'));
    expect([...root!.querySelectorAll('a')].some((x) => x.textContent === 'See on GitHub')).toBe(false);
    a.finish();
    await settle(() => ops.current.value?.phase === 'done');
    const link = [...root!.querySelectorAll('a')].find((x) => x.textContent === 'See on GitHub')!;
    expect(link.getAttribute('href')).toBe(`https://github.com/${COURSE}/assignment-2-f2026/tree/main`);
    expect(link.getAttribute('target')).toBe('_blank');
  });

  it('names a place for every op that changes something on GitHub, and none for a look or an email', () => {
    const none = new Set(['semester.check', 'semester.preview_automation', 'roster.send_codes', 'teams.open_window', 'assignment.generate_syllabus']);
    for (const def of ALL) {
      const t = targetOf(def, def.args);
      if (none.has(def.op)) expect(t, def.op).toBeNull();
      else expect(t, def.op).toMatch(/^https:\/\/github\.com\//);
    }
    expect(targetOf(defs.releaseEarly(scope, rel), {})).toBe(`https://github.com/${COHORT}/materials/tree/main/lectures/05`);
    expect(targetOf(defs.releaseAdhoc(scope, []), { semester_dest_repo: 'extras', semester_dest_path: '/week4/' })).toBe(`https://github.com/${COHORT}/extras/tree/main/week4`);
  });
});

describe('Preview buttons', () => {
  it('offers no Preview for derive: one button that runs it', async () => {
    const ops = session(new Scripted());
    await mount(envOf(ops), <OpButtons def={defs.derive(course, 'assignment-2-f2026', 'assignment-2-f2026', 'Assignment 2')} small />);
    expect(buttons().map((b) => b.textContent)).toEqual(['Derive student version']);
    await act(() => button('Derive student version')!.click());
    expect(ops.current.value?.mode).toBe('direct');
  });

  it('keeps the gate’s Preview and drops the ones nobody acts on', () => {
    // Publishing has no engine preview at all: it asks for a tick instead.
    for (const op of GATED) expect(modeOf(op), op).toBe(opSpec(op).preview ? 'gated' : 'direct');
    for (const op of PREVIEW_NOT_OFFERED) expect(modeOf(op), op).toBe('direct');
    expect(modeOf('release.adhoc')).toBe('preview');
    expect(modeOf('semester.preview_automation')).toBe('previewOnly');
  });
});

describe('the drawer', () => {
  it('resizes by dragging its bottom-left grip, within the viewport', async () => {
    const ops = session(new Scripted());
    await mount(envOf(ops), <OpPanel />);
    await act(() => ops.open(defs.updateSite(scope)));
    const drawer = root!.querySelector('aside.drawer') as HTMLElement;
    drawer.getBoundingClientRect = () => ({ width: 440, height: 300, top: 64, left: 0, right: 440, bottom: 364, x: 0, y: 64, toJSON: () => ({}) });
    const grip = root!.querySelector('.drawer-grip')!;
    await act(() => void grip.dispatchEvent(new PointerEvent('pointerdown', { clientX: 500, clientY: 360, bubbles: true })));
    await act(() => void window.dispatchEvent(new PointerEvent('pointermove', { clientX: 400, clientY: 460 })));
    await act(() => void window.dispatchEvent(new PointerEvent('pointerup', {})));
    expect(drawer.classList.contains('sized')).toBe(true);
    expect(drawer.style.width).toBe('540px');
    expect(drawer.style.height).toBe('400px');
    await act(() => void grip.dispatchEvent(new PointerEvent('pointerdown', { clientX: 400, clientY: 460, bubbles: true })));
    await act(() => void window.dispatchEvent(new PointerEvent('pointermove', { clientX: 2000, clientY: -500 })));
    expect(drawer.style.width).toBe('320px');
    expect(drawer.style.height).toBe('240px');
  });
});

describe('Marked from', () => {
  it('carries a ? that says what it is, how to set it, and that blank is fine', async () => {
    const ops = session(new Scripted());
    await mount(envOf(ops), <Questions rows={[{ name: 'Q1', points: '5', file: '' }]} set={() => {}} files={[]} />);
    const th = [...root!.querySelectorAll('th')].find((x) => x.textContent?.startsWith('Marked from'))!;
    expect(th.querySelector('.hint-wrap')).not.toBeNull();
    expect(MARKED_FROM_HINT).toContain('report.tex');
    expect(MARKED_FROM_HINT).toContain('Leave it blank');
  });
});
