// Readiness (decision 0034): when a problem bites, what an item needs, the verdicts, and the
// tolerance for a status the previous engine wrote (contract C).

import { describe, expect, it } from 'vitest';
import { verdictWords } from '../src/model/format';
import {
  bitesOf, displayChecks, displayTodos, materialsReadiness, needOf, problemCount, problemForRelease, releaseMark, repoWords, stepNeed, stepState, templateReadiness, verdictOf, type VerdictItem,
} from '../src/model/readiness';
import type { MaterialsCheck, Problem, Status, Todo } from '../src/model/types';
import example from './fixtures/status.example.json';

const STATUS = example as unknown as Status;
const NOW = Date.parse('2026-10-08T12:00:00+02:00');
const DAY = 864e5;
const at = (ms: number): Problem => ({ id: 'p', scope: 'semester', stage: 'K4', text: 't', stops: 's', when: new Date(ms).toISOString() });

describe('bitesOf', () => {
  it('reads the engine’s bites first', () => {
    expect(bitesOf({ ...at(NOW + 60 * DAY), bites: 'soon' }, undefined, NOW)).toBe('soon');
  });
  it('without bites: none or past is now, up to the horizon’s days soon, beyond later', () => {
    expect(bitesOf({ ...at(NOW), when: undefined }, undefined, NOW)).toBe('now');
    expect(bitesOf(at(NOW), undefined, NOW)).toBe('now');
    expect(bitesOf(at(NOW + 1), undefined, NOW)).toBe('soon');
    // The boundary: exactly 7 days ahead is still inside; a minute later is not.
    expect(bitesOf(at(NOW + 7 * DAY), undefined, NOW)).toBe('soon');
    expect(bitesOf(at(NOW + 7 * DAY + 60000), undefined, NOW)).toBe('later');
    // The engine's horizon length, when it gives one.
    expect(bitesOf(at(NOW + 10 * DAY), { days: 14 }, NOW)).toBe('soon');
    expect(bitesOf(at(NOW + 15 * DAY), { days: 14 }, NOW)).toBe('later');
  });
  it('counts only problems now or soon, the one count every screen shows', () => {
    const s: Status = { ...STATUS, problems: [at(NOW - DAY), at(NOW + DAY), at(NOW + 30 * DAY)] };
    expect(problemCount(s, NOW)).toBe(2);
    expect(problemCount(undefined, NOW)).toBe(0);
  });
});

describe('need', () => {
  it('reads need, else blocks (a check), else optional (a to-do)', () => {
    expect(needOf({ need: 'suggested', blocks: true })).toBe('suggested');
    expect(needOf({ blocks: true })).toBe('needed');
    expect(needOf({ blocks: false })).toBe('suggested');
    expect(needOf({ optional: true })).toBe('suggested');
    expect(needOf({})).toBe('needed');
  });
  it('reads a step’s need from stage_need, else stage_optional, else the contract’s default', () => {
    const c = STATUS.course!;
    expect(stepNeed({ ...c, stage_need: { C6: 'needed' } }, 'C6')).toBe('needed');
    expect(stepNeed(c, 'C6')).toBe('suggested'); // stage_optional
    expect(stepNeed(STATUS.semester!, 'K7')).toBe('suggested');
    expect(stepNeed(STATUS.semester!, 'K3')).toBe('needed');
  });
  it('reads a stage flagged problem by later problems only as open', () => {
    expect(stepState('problem', ['later', 'later'])).toBe('open');
    expect(stepState('problem', ['later', 'soon'])).toBe('problem');
    expect(stepState('problem', [])).toBe('problem');
    expect(stepState('blocked', [])).toBe('waiting');
  });
});

describe('verdictOf', () => {
  const step = (need: 'needed' | 'suggested', state: VerdictItem['state'], why?: string): VerdictItem => ({ kind: 'step', need, state, why });
  const items = [step('needed', 'done'), step('suggested', 'open'), { kind: 'todo' as const, need: 'suggested' as const, state: 'open' as const }];
  it('says Needs fixing while a problem stands now or soon, never for a later one', () => {
    const v = verdictOf(items, ['now', 'soon', 'later'], 'course');
    expect(v.state).toBe('fixing');
    expect(verdictWords(v)).toBe('Needs fixing: 2 problems · 2 suggestions');
    expect(verdictWords(verdictOf(items, ['soon', 'later'], 'semester', 3, 7))).toBe('Needs fixing: 1 problem in the next 7 days · 3 coming up');
  });
  it('says Not ready / Not set up, naming the first needed step open', () => {
    const open = [step('needed', 'open', 'No instructor is declared in instructors.yml yet.'), ...items];
    expect(verdictWords(verdictOf(open, ['later'], 'course'))).toBe('Not ready: no instructor is declared in instructors.yml yet · 2 suggestions');
    expect(verdictWords(verdictOf(open, [], 'semester', 0, 7))).toBe('Not set up: no instructor is declared in instructors.yml yet');
  });
  it('says Ready / On track otherwise; a needed to-do is not a setup step', () => {
    const todo = { kind: 'todo' as const, need: 'needed' as const, state: 'open' as const };
    expect(verdictWords(verdictOf([...items, todo], [], 'course'))).toBe('Ready for a new semester · 2 suggestions');
    expect(verdictWords(verdictOf(items, ['later'], 'semester', 1, 14))).toBe('On track: nothing to fix in the next 14 days · 1 coming up');
    // A set-aside suggestion is not counted.
    expect(verdictOf([step('suggested', 'open'), { ...step('suggested', 'open'), aside: true }], [], 'course').suggestions).toBe(1);
  });
});

describe('the syllabus row and repo words', () => {
  const checks = STATUS.course!.materials[0].checks!;
  it('merges the syllabus and its weekly plan into one row, needed only while a needed part is open', () => {
    const shown = displayChecks(checks);
    expect(shown.map((c) => c.id)).toEqual(['kind_folder', 'syllabus', 'withheld']);
    // A done syllabus and an open plan (a suggestion): the row is a suggestion, its why the plan's.
    expect(shown[1]).toMatchObject({ label: 'Syllabus', done: false, need: 'suggested', why: 'The weekly plan is not in SYLLABUS.md yet.' });
    const both = displayChecks(checks.map((c): MaterialsCheck => (c.id === 'syllabus' ? { ...c, done: false, why: 'SYLLABUS.md is still the placeholder.', need: 'suggested' } : c)));
    expect(both[1].why).toBe('SYLLABUS.md is still the placeholder, and the weekly plan (written from the schedule) is not in it yet.');
    expect(materialsReadiness({ state: 'ready', checks }).suggestions).toBe(1);
  });
  it('merges the two to-dos of one repo into one carrying both ids', () => {
    const t = (part: string, text: string): Todo => ({ id: `materials:r:${part}`, kind: 'materials', repo: 'r', text, need: 'suggested' });
    const out = displayTodos([t('syllabus', 'SYLLABUS.md is still the placeholder.'), t('sessions', 'The weekly plan is not in SYLLABUS.md yet.'), t('withheld', 'w')]);
    expect(out.map((x) => x.ids)).toEqual([['materials:r:syllabus', 'materials:r:sessions'], ['materials:r:withheld']]);
  });
  it('words a template the same everywhere: Ready, Not ready: <why>, Has a problem', () => {
    const brief: Todo = { id: 'template:a:brief', kind: 'template', repo: 'a', text: 'The brief (README.md) is not written yet.', need: 'needed' };
    expect(repoWords(templateReadiness({ repo: 'a', state: 'todo' }, [brief], []))).toBe('Not ready: the brief (README.md) is not written yet');
    expect(repoWords(templateReadiness({ repo: 'a', state: 'problem' }, [], [{ text: 'x', b: 'now' }]))).toBe('Has a problem');
    // A template problem beyond the horizon is not red yet.
    expect(repoWords(templateReadiness({ repo: 'a', state: 'problem' }, [], [{ text: 'Its settings do not parse.', b: 'later' }]))).toBe('Not ready: its settings do not parse');
    expect(repoWords(templateReadiness({ repo: 'a', state: 'ready' }, [], []))).toBe('Ready');
  });
});

describe('the status schema (contract B)', () => {
  it('takes a status the new engine writes, and still one the previous engine wrote', async () => {
    const { validateStatus } = await import('../src/model/status');
    expect(validateStatus(STATUS)).toEqual([]);
    const next: Status = {
      ...STATUS,
      horizon: { days: 7 },
      problems: STATUS.problems!.map((p) => ({ ...p, bites: 'later' as const })),
      course: {
        ...STATUS.course!,
        stage_need: { C1: 'needed', C6: 'suggested' },
        verdict: { state: 'ready', problems: 0, missing: null, suggestions: 1 },
        todo: [{ id: 'template:a:brief', kind: 'template', repo: 'a', text: 'The brief (README.md) is not written yet.', need: 'needed', needed_by: '2026-11-04T10:00:00+01:00', problem_from: '2026-10-28T10:00:00+01:00' }],
      },
      semester: { ...STATUS.semester!, stage_need: { K7: 'suggested' }, todo: [{ id: 'site:home', kind: 'site', repo: 'site', text: 'The home page is still the placeholder.', screen: 'site', need: 'suggested' }], verdict: { state: 'fixing', problems: 1, missing: null, suggestions: 1, coming_up: 2 } },
    };
    expect(validateStatus(next)).toEqual([]);
  });
});

describe('problemForRelease and releaseMark (decision 0034 §6)', () => {
  const p = (id: string, entry: string, over: Partial<Problem> = {}): Problem => ({ ...at(NOW + 2 * DAY), id, fix: { repo: 'o/r', path: 'schedule.yml', line: 1, screen: 'schedule', entry }, ...over });
  const status = (problems: Problem[]): Status => ({ ...STATUS, problems });

  it('joins a release to the problem that holds it: its schedule id (whatever its fix names), else its own number problem', () => {
    const unwritten = p('schedule:lecture_03:SOURCE_UNWRITTEN', 'course-materials');
    expect(problemForRelease(status([p('kinds:lecture_03', 'lecture_03'), unwritten]), 'lecture_03')).toBe(unwritten);
    const number = p('number:lecture:guest', 'guest');
    expect(problemForRelease(status([p('number:lecture:4', 'guest'), number]), 'guest')).toBe(number);
    expect(problemForRelease(status([p('kinds:guest', 'guest')]), 'guest')).toBeUndefined();
  });

  it('words a held release by when its problem bites, and a late one with no fault as late', () => {
    const rel = (state: string) => ({ id: 's1', state, when: new Date(NOW + 2 * DAY).toISOString() });
    expect(releaseMark(status([]), rel('planned'), NOW)).toBeNull();
    expect(releaseMark(status([p('schedule:s1:SOURCE_MISSING', 's1', { when: new Date(NOW - DAY).toISOString() })]), rel('late'), NOW)).toMatchObject({ word: 'was skipped', bites: 'now', late: false });
    expect(releaseMark(status([p('schedule:s1:SOURCE_MISSING', 's1')]), rel('will_be_skipped'), NOW)).toMatchObject({ word: 'will be skipped', bites: 'soon' });
    expect(releaseMark(status([p('schedule:s1:SOURCE_MISSING', 's1', { bites: 'later' })]), rel('will_be_skipped'), NOW)).toMatchObject({ word: 'not ready yet', bites: 'later' });
    expect(releaseMark(status([p('schedule:s1:LATE', 's1')]), rel('late'), NOW)).toMatchObject({ word: 'late', bites: 'now', late: true });
    // A status that names no problem: late is late; a held one is worded by its own date.
    expect(releaseMark(status([]), rel('late'), NOW)).toMatchObject({ word: 'late', late: true });
    expect(releaseMark(status([]), { ...rel('will_be_skipped'), when: new Date(NOW + 30 * DAY).toISOString() }, NOW)).toMatchObject({ word: 'not ready yet' });
  });
});
