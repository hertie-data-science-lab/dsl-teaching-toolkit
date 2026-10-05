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

  it('ends the watch on sign-out and forgets the runs and the previews', async () => {
    const a = new Flaky(0);
    let finished = 0;
    const s = new OpsSession(a, { pollMs: 0, sleep: tick, outcomeTries: 1, onFinished: () => finished++ });
    const def = defs.checkNow(scope);
    s.open(def);
    await s.start('preview');
    await (s.open(def), s.start('run'));
    expect(s.runs.value).toHaveLength(2);
    expect(s.isPreviewed(def)).toBe(true);
    a.held = true;
    s.open(def);
    const p = s.start('run');
    await tick();
    s.reset();
    a.held = false;
    await p;
    expect(s.current.value).toBeNull();
    expect(s.runs.value).toEqual([]);
    expect(s.isPreviewed(def)).toBe(false);
    expect(finished).toBe(1);
  });
});
