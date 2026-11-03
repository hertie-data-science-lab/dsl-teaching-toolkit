// "Include marks per question" in Return marks: prefilled from the assignment's effective
// `marks_per_question`, explained by its `?`, and - when the instructor changes it - saved as
// the assignment's run setting BEFORE the run is dispatched, so automatic returns follow it.

import { render } from 'preact-render-to-string';
import { parse } from 'yaml';
import { describe, expect, it } from 'vitest';
import { SchemaForm, fieldErrors } from '../src/forms/Form';
import { RUN_KEYS, fileValue, readSetting, valueWord, writeBlock } from '../src/model/settings';
import { withRunSetting } from '../src/screens/RunSettings';
import { YamlText } from '../src/edit/yamlText';
import { RUN_LABEL, runTier } from '../src/tiers/runSettings';
import type { Adapter, Handle, Progress, Result } from '../src/ops/adapter';
import * as defs from '../src/ops/defs';
import { opSpec } from '../src/ops/registry';
import { OpsSession } from '../src/ops/session';
import { PER_QUESTION_HINT } from '../src/tiers/ops';
import { perQuestionOf } from '../src/screens/Marking';
import type { Assignment } from '../src/model/types';

const scope = { courseOrg: 'hertie-dsl-demo-course-e1234', cohortOrg: 'hertie-dsl-demo-f2026', where: 'Fall 2026' };
const ref = { slug: 'assignment-1', title: 'Assignment 1', template: 'assignment-1', units: 3, group: false, when: 'Marking' };
const tick = () => new Promise<void>((r) => setTimeout(r, 0));

/** Records every submit, in order with the saves the test logs into the same list. */
class Recorder implements Adapter {
  constructor(private log: string[]) {}
  async submit(i: { op: string; courseOrg: string; preview: boolean; args: Record<string, unknown> }): Promise<Handle> {
    this.log.push(`${i.preview ? 'preview' : 'run'} per_question=${String(i.args.per_question)}`);
    return { op: i.op, runId: 7, htmlUrl: '', preview: i.preview, courseOrg: i.courseOrg };
  }
  async watch(): Promise<Progress> {
    return { state: 'completed', conclusion: 'success', steps: [], htmlUrl: '' };
  }
  async outcome(): Promise<Result> {
    return { outcome: { run_id: 7, op: 'grades.return', conclusion: 'done', summary: 'Returned.', finished: '2026-10-14T10:00:00Z' } as Result['outcome'] };
  }
  async cancel(): Promise<void> {}
}

function setup(value: boolean, save: (on: boolean) => Promise<void>) {
  const log: string[] = [];
  const s = new OpsSession(new Recorder(log), { pollMs: 0, sleep: tick, outcomeTries: 1 });
  const def = defs.returnMarks(scope, ref, 3, 'assignment-1', null, [], null, { value, source: 'semester', save: async (on) => { log.push(`save ${on}`); await save(on); } });
  s.open(def);
  return { s, def, log };
}

describe('Include marks per question', () => {
  it('is prefilled from the effective setting, says where it comes from, and carries its ?', () => {
    const { def } = setup(false, async () => {});
    expect(def.args.per_question).toBe(false);
    const t = def.options!.per_question;
    expect(t).toMatchObject({ label: 'Include marks per question', widget: 'checkbox', default: false, defaultLabel: 'off, this semester’s default' });
    expect(t.hint?.text).toBe(PER_QUESTION_HINT);
    expect(PER_QUESTION_HINT).toBe('On: the student’s page lists the mark per question and the per-question feedback. Off: the score, late line, adjustment and final only. Saved for this assignment, so automatic returns do the same.');
    const out = render(<SchemaForm id="o" schema={null} tiers={def.options!} values={def.args} onChange={() => {}} />);
    expect(out).toContain('Include marks per question');
    expect(out).toContain('About Include marks per question');
    // The registry accepts it: an op arg the engine knows.
    expect(fieldErrors(opSpec('grades.return').args_schema, def.options!, def.args)).toEqual({});
  });

  it('a changed box saves the run setting before the run is dispatched', async () => {
    const { s, log } = setup(true, async () => {});
    s.setValues({ ...s.current.value!.values, per_question: false });
    await s.start('preview');
    expect(log).toEqual(['preview per_question=false']); // a preview saves nothing
    await s.start('run');
    expect(log).toEqual(['preview per_question=false', 'save false', 'run per_question=false']);
  });

  it('a preview carries the box as it stands, and saves nothing', async () => {
    const { s, log } = setup(false, async () => {});
    s.setValues({ ...s.current.value!.values, per_question: true });
    await s.start('preview');
    expect(log).toEqual(['preview per_question=true']);
  });

  it('an unchanged box saves nothing', async () => {
    const { s, log } = setup(true, async () => {});
    await s.start('run');
    expect(log).toEqual(['run per_question=true']);
  });

  it('a save that fails stops the run and says why', async () => {
    const { s, log } = setup(false, async () => { throw new Error('Include marks per question was not saved, so nothing was returned: conflict'); });
    s.setValues({ ...s.current.value!.values, per_question: true });
    await s.start('run');
    expect(log).toEqual(['save true']);
    expect(s.current.value).toMatchObject({ phase: 'ready', error: 'Include marks per question was not saved, so nothing was returned: conflict' });
  });
});

describe('the run setting it saves', () => {
  const file = (text: string) => ({ text, sha: 'abc', doc: parse(text) as Record<string, unknown>, error: null });

  it('writes it inline on the assignment’s schedule entry, keeping everything else (decision 0038 rule 9)', () => {
    const text = '# mine\nassignments:\n  assignment-1:\n    number: 1\n    template: assignment-linear-regression\n    due_datetime: 2026-10-13\n    late_penalty_per_day: 5%\n    late_window_days: 2\n';
    const out = withRunSetting(file(text), 'assignment-1', 'marks_per_question', false)!;
    expect(out).toContain('# mine');
    expect(out).toContain('late_penalty_per_day: 5%');
    expect(out).toMatch(/assignment-1:\n(?:.*\n)*\s+marks_per_question: false/);
    // Already so: nothing to write.
    expect(withRunSetting(file(`${text}    marks_per_question: false\n`), 'assignment-1', 'marks_per_question', false)).toBeNull();
  });

  it('refuses an assignment the schedule does not name', () => {
    expect(() => withRunSetting(file('assignments: {}\n'), 'assignment-2', 'marks_per_question', true)).toThrow(/not in the schedule/);
  });

  it('refuses a file that does not parse', () => {
    expect(() => withRunSetting({ text: 'a: [', sha: 'x', doc: {}, error: 'bad' }, 'a1', 'marks_per_question', true)).toThrow(/does not parse/);
  });

  it('reads on from the institution in a status written before the setting', () => {
    const a = { slug: 'assignment-1' } as Assignment;
    const p = { files: {} as never, cohort: { org: 'o' } as never };
    expect(perQuestionOf(p, a, null)).toMatchObject({ value: true, source: 'institution' });
    const set = { ...a, settings: { marks_per_question: { value: false, source: 'course' as const } } };
    expect(perQuestionOf(p, set, null)).toMatchObject({ value: false, source: 'course' });
  });
});

describe('marks per question in How this semester runs it', () => {
  it('is a run setting like the others: effective value, source, a select that writes a boolean', () => {
    expect(RUN_KEYS).toContain('marks_per_question');
    expect(RUN_LABEL.marks_per_question).toBe('Marks per question on the student’s page');
    const t = runTier('marks_per_question', { value: true, source: 'institution' }, { override: true });
    expect(t.widget).toBe('select');
    expect(t.defaultLabel).toBe('institution default: on');
    expect(t.options!.map((o) => o.value)).toEqual(['', 'true', 'false']);
    expect([readSetting('marks_per_question', 'false'), readSetting('marks_per_question', true), valueWord('marks_per_question', false)]).toEqual([false, true, 'off']);
    expect(fileValue('marks_per_question', 'false')).toBe(false);
    const y = new YamlText('assignments:\n  a1:\n    late_window_days: 2\n');
    expect(writeBlock(y, ['assignments', 'a1'], { late_window_days: 2 }, { late_window_days: 2, marks_per_question: 'false' }, RUN_KEYS)).toBe(true);
    expect(y.text).toContain('marks_per_question: false');
    // Choosing what the file already holds writes nothing.
    expect(writeBlock(y, ['assignments', 'a1'], { marks_per_question: false }, { marks_per_question: 'false' }, RUN_KEYS)).toBe(false);
  });
});
