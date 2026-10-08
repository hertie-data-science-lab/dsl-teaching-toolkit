// Readiness (decision 0034): when a problem bites, the one normalisation at load for a status the
// previous engine wrote (contract C), the verdict as the engine computed it, the repo words, and
// release rows joined on the problem's `release`.

import { describe, expect, it } from 'vitest';
import { heldSentence, markWord, repoWords, verdictWords } from '../src/model/format';
import {
  bitesAt, materialsReadiness, normalise, problemCount, problemFromDay, releaseMarks, releaseProblems, standing, stepState, suggestionsCount, templateReadiness, tier, verdictOf,
} from '../src/model/readiness';
import type { Problem, Status, Todo } from '../src/model/types';
import example from './fixtures/status.example.json';

const STATUS = example as unknown as Status;
const NOW = Date.parse('2026-10-08T12:00:00+02:00');
const DAY = 864e5;
const iso = (ms: number) => new Date(ms).toISOString();
const at = (ms: number, over: Partial<Problem> = {}): Problem => ({ id: 'p', scope: 'semester', stage: 'K4', text: 't', stops: 's', when: iso(ms), ...over });

describe('bitesAt', () => {
  it('none or past is now, up to the horizon’s days soon, beyond later', () => {
    expect(bitesAt(undefined, undefined, NOW)).toBe('now');
    expect(bitesAt(iso(NOW), undefined, NOW)).toBe('now');
    expect(bitesAt(iso(NOW + 1), undefined, NOW)).toBe('soon');
    // The boundary: exactly 7 days ahead is still inside; a minute later is not.
    expect(bitesAt(iso(NOW + 7 * DAY), undefined, NOW)).toBe('soon');
    expect(bitesAt(iso(NOW + 7 * DAY + 60000), undefined, NOW)).toBe('later');
    // The engine's horizon length, when it gives one.
    expect(bitesAt(iso(NOW + 10 * DAY), { days: 14 }, NOW)).toBe('soon');
    expect(bitesAt(iso(NOW + 15 * DAY), { days: 14 }, NOW)).toBe('later');
  });
  it('says the day a moment’s problem starts to count', () => {
    expect(problemFromDay('2026-11-04T10:00:00+01:00', undefined, 'Europe/Berlin')).toBe('2026-10-28');
    expect(problemFromDay('2026-11-04T10:00:00+01:00', { days: 14 }, 'Europe/Berlin')).toBe('2026-10-21');
  });
});

describe('normalise (contract C)', () => {
  // A status the previous engine wrote: no bites, no need, no stage_need.
  const old: Status = {
    ...STATUS,
    horizon: undefined,
    problems: [at(NOW + 2 * DAY), at(NOW + 30 * DAY, { id: 'q' }), { ...at(0), id: 'r', when: undefined }],
    course: {
      ...STATUS.course!,
      stage_need: undefined,
      materials: STATUS.course!.materials.map((m) => ({ ...m, checks: m.checks!.map(({ need: _n, ...c }) => c) })),
      todo: [{ id: 't', kind: 'materials', repo: 'r', text: 'x', optional: true }, { id: 'u', kind: 'template', repo: 'a', text: 'y' }],
    },
    semester: { ...STATUS.semester!, stage_need: undefined, todo: undefined },
  };
  const n = normalise(old, NOW);

  it('fills bites off when and the horizon, and keeps the engine’s', () => {
    expect(n.problems!.map((p) => p.bites)).toEqual(['soon', 'later', 'now']);
    expect(normalise({ ...old, problems: [at(NOW + 30 * DAY, { bites: 'soon' })] }, NOW).problems![0].bites).toBe('soon');
  });
  it('fills need from blocks (a check) or optional (a to-do), and stage_need from stage_optional', () => {
    expect(n.course!.materials[0].checks!.map((c) => c.need)).toEqual(['needed', 'suggested', 'suggested']);
    expect(n.course!.todo!.map((t) => t.need)).toEqual(['suggested', 'needed']);
    expect(n.course!.stage_need).toMatchObject({ C3: 'needed', C6: 'suggested' });
    expect(n.semester!.stage_need).toMatchObject({ K1: 'needed', K6: 'needed' });
  });
  it('takes a course block with no materials or templates list (nor todo, problems or semester todo)', () => {
    const { materials: _m, templates: _p, todo: _t, ...course } = STATUS.course!;
    const bare = normalise({ ...STATUS, problems: undefined, course: course as typeof STATUS.course, semester: { ...STATUS.semester!, todo: undefined } }, NOW);
    expect(bare.course!.materials).toEqual([]);
    expect(bare.course!.templates).toEqual([]);
    expect(bare.course!.todo).toBeUndefined();
    expect(bare.problems).toBeUndefined();
  });
  it('passes a status today’s engine wrote through unchanged', () => {
    const now = normalise(n, NOW);
    expect(now).toEqual(n);
  });
});

describe('tier and the counts', () => {
  it('tiers each problem once, and counts only those now or soon', () => {
    const s: Status = { ...STATUS, problems: [at(NOW - DAY), at(NOW + DAY), at(NOW + 30 * DAY)] };
    const t = tier(s, NOW);
    expect(t.map((x) => x.b)).toEqual(['now', 'soon', 'later']);
    expect(standing(t)).toHaveLength(2);
    expect(problemCount(s, NOW)).toBe(2);
    expect(problemCount(undefined, NOW)).toBe(0);
    // The engine's bites wins over the console's clock.
    expect(tier({ ...s, problems: [at(NOW + 30 * DAY, { bites: 'now' })] }, NOW)[0].b).toBe('now');
  });
  it('reads a step’s state off its stage alone', () => {
    expect(stepState('problem')).toBe('problem');
    expect(stepState('blocked')).toBe('waiting');
    expect(stepState('todo')).toBe('open');
    expect(stepState('done')).toBe('done');
  });
});

describe('the verdict (the engine’s)', () => {
  it('words the engine’s verdict, with the suggestions counted from the rows', () => {
    const v = verdictOf({ state: 'fixing', problems: 2, missing: null, suggestions: 9 }, 'course', 2);
    expect(verdictWords(v!)).toBe('Needs fixing: 2 problems · 2 suggestions');
    const sem = verdictOf({ state: 'fixing', problems: 1, missing: null, suggestions: 0, coming_up: 3 }, 'semester', 0, 7);
    expect(verdictWords(sem!)).toBe('Needs fixing: 1 problem in the next 7 days · 3 coming up');
    const open = verdictOf({ state: 'not_ready', problems: 0, missing: 'No instructor is declared in instructors.yml yet.', suggestions: 0 }, 'semester', 0, 7);
    expect(verdictWords(open!)).toBe('Not set up: no instructor is declared in instructors.yml yet');
    expect(verdictWords(verdictOf({ state: 'ready', problems: 0, missing: null, suggestions: 0, coming_up: 1 }, 'semester', 0, 14)!)).toBe('On track: nothing to fix in the next 14 days · 1 coming up');
    expect(verdictWords(verdictOf({ state: 'ready', problems: 0, missing: null, suggestions: 0 }, 'course', 1)!)).toBe('Ready for a new semester · 1 suggestion');
  });
  it('is absent when the status carries none: no verdict line', () => {
    expect(verdictOf(undefined, 'course', 3)).toBeNull();
  });
  it('counts a set-aside suggestion out', () => {
    expect(suggestionsCount([{ need: 'suggested', state: 'open' }, { need: 'suggested', state: 'open', aside: true }, { need: 'needed', state: 'open' }])).toBe(1);
  });
});

describe('repo words', () => {
  it('reads the one syllabus check as the engine writes it', () => {
    const m = STATUS.course!.materials[0];
    expect(m.checks!.map((c) => c.id)).toEqual(['kind_folder', 'syllabus', 'withheld']);
    expect(materialsReadiness(m)).toEqual({ state: 'ready', missing: null, suggestions: 1 });
  });
  it('words a template the same everywhere: Ready, Not ready: <why>, Has a problem', () => {
    const brief: Todo = { id: 'template:a:brief', kind: 'template', check: 'brief', repo: 'a', text: 'The brief (README.md) is not written yet.', need: 'needed' };
    const p = (b: 'now' | 'later', text = 'x') => ({ p: at(0, { scope: 'course', text }), b });
    expect(repoWords(templateReadiness({ repo: 'a', state: 'todo' }, [brief], []))).toBe('Not ready: the brief (README.md) is not written yet');
    expect(repoWords(templateReadiness({ repo: 'a', state: 'problem' }, [], [p('now')]))).toBe('Has a problem');
    // A template problem beyond the horizon is not red yet.
    expect(repoWords(templateReadiness({ repo: 'a', state: 'problem' }, [], [p('later', 'Its settings do not parse.')]))).toBe('Not ready: its settings do not parse');
    // A brief a hand-out cites later: its to-do names what is missing.
    expect(repoWords(templateReadiness({ repo: 'a', state: 'todo' }, [brief], [p('later', 'The brief (README.md) is not written yet.')]))).toBe('Not ready: the brief (README.md) is not written yet');
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
        todo: [{ id: 'template:a:brief', kind: 'template', check: 'brief', repo: 'a', text: 'The brief (README.md) is not written yet.', need: 'needed' }],
      },
    };
    expect(validateStatus(next)).toEqual([]);
    const { kind: _k, release: _r, ...old } = STATUS.problems![0];
    expect(validateStatus({ ...STATUS, problems: [old] })).toEqual([]);
  });
});

describe('release marks (decision 0034 §6)', () => {
  const p = (id: string, release: string | undefined, over: Partial<Problem> = {}): Problem => ({ ...at(NOW + 2 * DAY), id, kind: id.split(':').pop(), release, fix: { repo: 'o/r', path: 'schedule.yml', line: 1, screen: 'schedule', entry: release ?? 'x' }, ...over });
  const rel = (state: string, when = iso(NOW + 2 * DAY)) => ({ ...STATUS.releases![0], id: 's1', state: state as 'late', when });
  const marks = (problems: Problem[], r = rel('will_be_skipped')) => {
    const s: Status = { ...STATUS, releases: [r], problems };
    return releaseMarks(s, tier(s, NOW), NOW).get('s1');
  };

  it('joins a release to the problem that holds it back on `release`, whatever its fix names', () => {
    const unwritten = p('schedule:s1:SOURCE_UNWRITTEN', 's1', { fix: { repo: 'o/m', path: 'SYLLABUS.md', screen: 'materials', entry: 'course-materials' } });
    expect(marks([p('kinds:s1', undefined), unwritten])!.problem).toBe(unwritten);
    expect(releaseProblems({ ...STATUS, problems: [p('kinds:s1', undefined), unwritten] }, { id: 's1' })).toEqual([unwritten]);
  });

  it('words a held release by when its problem bites, and a late one with no fault as late', () => {
    const s: Status = { ...STATUS, releases: [rel('planned')], problems: [] };
    expect(releaseMarks(s, [], NOW).size).toBe(0);
    expect(markWord(marks([p('schedule:s1:SOURCE_MISSING', 's1', { when: iso(NOW - DAY) })], rel('late'))!)).toBe('was skipped');
    expect(markWord(marks([p('schedule:s1:SOURCE_MISSING', 's1')])!)).toBe('will be skipped');
    expect(markWord(marks([p('schedule:s1:SOURCE_MISSING', 's1', { bites: 'later' })])!)).toBe('not ready yet');
    expect(marks([p('schedule:s1:LATE', 's1')], rel('late'))).toMatchObject({ bites: 'now', late: true });
    // A status that names no problem: late is late; a held one is worded by its own date.
    expect(marks([], rel('late'))).toMatchObject({ late: true });
    expect(markWord(marks([], rel('will_be_skipped', iso(NOW + 30 * DAY)))!)).toBe('not ready yet');
  });

  it('counts held releases in the Schedule lede’s words', () => {
    expect(heldSentence('now', 1)).toBe('One release was skipped.');
    expect(heldSentence('now', 2)).toBe('2 releases were skipped.');
    expect(heldSentence('later', 3)).toBe('3 releases are not ready yet.');
  });
});
