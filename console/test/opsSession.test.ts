// The ops session's watch: a run it cannot read is given up on after a bounded number of
// polls, with a visible line, and sign-out ends every watch and forgets the runs and the gate.

import { describe, expect, it } from 'vitest';
import type { Adapter, Handle, Progress, Result } from '../src/ops/adapter';
import * as defs from '../src/ops/defs';
import { LOST_RUN, MISSED_POLL, OpsSession } from '../src/ops/session';

const scope = { courseOrg: 'hertie-dsl-demo-course-e1234', cohortOrg: 'hertie-dsl-demo-f2026', where: 'Fall 2026' };
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

/** `fails` polls fail first; then the run reads completed. `held` keeps every poll running until released. */
class Flaky implements Adapter {
  polls = 0;
  held = false;
  constructor(private fails: number) {}
  async submit(i: { op: string; courseOrg: string; preview: boolean }): Promise<Handle> {
    return { op: i.op, runId: 5, htmlUrl: 'https://github.com/run/5', preview: i.preview, courseOrg: i.courseOrg };
  }
  async watch(): Promise<Progress> {
    this.polls++;
    if (this.polls <= this.fails) throw new Error('Not Found');
    return { state: this.held ? 'running' : 'completed', conclusion: 'success', steps: [], htmlUrl: '' };
  }
  async outcome(): Promise<Result> {
    return { outcome: { run_id: 5, op: 'check.now', conclusion: 'done', summary: 'Checked.', finished: '2026-10-05T10:00:00Z' } as Result['outcome'] };
  }
  async cancel(): Promise<void> {}
}

describe('following a run', () => {
  it('stops after maxMisses unreadable polls and says so', async () => {
    const a = new Flaky(Infinity);
    const s = new OpsSession(a, { pollMs: 0, sleep: tick, maxMisses: 4 });
    s.open(defs.checkNow(scope));
    await s.start('run');
    expect(a.polls).toBe(4);
    expect(s.current.value).toMatchObject({ phase: 'ready', running: null, error: LOST_RUN });
    expect(s.runs.value).toEqual([]);
  });

  it('rides out a few missed polls, showing it is retrying, and clears the line once read', async () => {
    const a = new Flaky(2);
    const s = new OpsSession(a, { pollMs: 0, sleep: tick, maxMisses: 4, outcomeTries: 1 });
    s.open(defs.checkNow(scope));
    const p = s.start('run');
    await tick();
    expect(s.current.value?.error).toBe(MISSED_POLL);
    await p;
    expect(s.current.value).toMatchObject({ phase: 'done', error: null });
    expect(s.runs.value.map((r) => r.run_id)).toEqual([5]);
  });

  it('ends the watch on sign-out and forgets the runs', async () => {
    const a = new Flaky(0);
    let finished = 0;
    const s = new OpsSession(a, { pollMs: 0, sleep: tick, outcomeTries: 1, onFinished: () => finished++ });
    const def = defs.checkNow(scope);
    s.open(def);
    await s.start('preview');
    await (s.open(def), s.start('run'));
    expect(s.runs.value).toHaveLength(2);
    a.held = true;
    s.open(def);
    const p = s.start('run');
    await tick();
    s.reset();
    a.held = false;
    await p;
    expect(s.current.value).toBeNull();
    expect(s.runs.value).toEqual([]);
    expect(finished).toBe(1);
  });
});

describe('a confirm-in-panel verb', () => {
  it('only opens the panel, however often the page verb is pressed; the panel verb runs it', async () => {
    const a = new Flaky(0);
    const s = new OpsSession(a, { pollMs: 0, sleep: tick, outcomeTries: 1 });
    const def = defs.archive(scope, '2026-12-20', true);
    s.open(def, 'run');
    s.open(def, 'run');
    await tick();
    expect(a.polls).toBe(0);
    expect(s.current.value).toMatchObject({ phase: 'ready', running: null });
    await s.start('run');
    expect(s.runs.value.map((r) => r.run_id)).toEqual([5]);
  });
});

describe('the same operation opened again', () => {
  it('takes the new figures and keeps the options and the preview', async () => {
    const a = new Flaky(0);
    const s = new OpsSession(a, { pollMs: 0, sleep: tick, outcomeTries: 1 });
    const def = defs.checkNow(scope);
    s.open({ ...def, facts: ['2 students get marks'] });
    await s.start('preview');
    s.setValues({ kept: true });
    s.minimise();
    s.open({ ...def, facts: ['3 students get marks'] });
    expect(s.current.value?.def.facts).toEqual(['3 students get marks']);
    expect(s.current.value).toMatchObject({ min: false, values: { kept: true }, phase: 'ready' });
    expect(s.current.value?.dry).not.toBeNull();
  });
});

describe('a background run (a save’s follow-up Refresh)', () => {
  it('runs minimised and says once GitHub has the run', async () => {
    const a = new Flaky(0);
    a.held = true;
    const s = new OpsSession(a, { pollMs: 0, sleep: tick, outcomeTries: 1 });
    expect(await s.background(defs.checkNow(scope))).toBe(true);
    expect(s.current.value).toMatchObject({ phase: 'running', min: true });
    // One already running or pending: no second run.
    expect(await s.background(defs.checkNow(scope))).toBe(false);
    s.reset();
  });

  it('leaves another operation’s open panel alone', async () => {
    const s = new OpsSession(new Flaky(0), { pollMs: 0, sleep: tick });
    s.open(defs.previewNext(scope));
    expect(await s.background(defs.checkNow(scope))).toBe(false);
    expect(s.current.value?.def.op).toBe('semester.preview_automation');
  });

  it('closes its panel again when the request is refused', async () => {
    class Refused extends Flaky {
      override async submit(): Promise<Handle> {
        throw new Error('Forbidden');
      }
    }
    const s = new OpsSession(new Refused(0), { pollMs: 0, sleep: tick });
    expect(await s.background(defs.checkNow(scope))).toBe(false);
    expect(s.current.value).toBeNull();
  });
});
