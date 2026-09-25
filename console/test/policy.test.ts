// Decision 0009 rule 8: the console keeps no default of its own. Every default comes from
// the engine's export (`schemas/policy.json`, the request schema's name patterns), and the
// cascade resolves the way the engine's `settings.resolve` does.

import { describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import SKELETON from '../../templates/semester-config/assignments.yml?raw';
import policy from '../schemas/policy.json';
import { YamlText } from '../src/edit/yamlText';
import { effectiveWord, institutionLayer, lateWord, layersOf, readSetting, resolve, semesterName, usableBlock, writeBlock, type Layers } from '../src/model/cascade';
import { questionFileError, questionsValue } from '../src/tiers/grading';
import { ARCHIVE_GRACE_DAYS, DEFAULT_DEST_REPO, DEFAULT_TIMEZONE, HANDLE_RE, ORG_NAME_RE, penaltyRate } from '../src/model/policy';
import { assignmentsAfterSchedule } from '../src/screens/RunSettings';

const SOURCES = import.meta.glob('../src/**/*.{ts,tsx}', { query: '?raw', import: 'default', eager: true }) as Record<string, string>;

/** Each literal the engine exports, in the shapes a hard-coded default takes in the console. */
const FORBIDDEN: [string, RegExp][] = [
  ['the timezone', /Europe\/Berlin/],
  ['the late penalty', /10%/],
  ['the starter format', /(\?\?|\|\||default:)\s*\[?\s*['"]ipynb['"]|=\s*\[\s*['"]ipynb['"]\s*\]/],
  // `screen === 'materials'` is a route name, not the release repo.
  ['the release repo', /(\?\?|\|\||default:|placeholder=)\s*['"]materials['"]|(dest|repo)\w*\s*[!=]==\s*['"]materials['"]|default: materials|means materials/],
  ['a numeric default (10, 5, 60)', /(\?\?|\|\|)\s*['"]?(10|5|60)['"]?(?![\w.%])|default:\s*['"]?(10|5|60)['"]?(?![\w.%])|(GRACE|DAYS|SIZE|LATE|TEAM)\w*\s*=\s*(10|5|60)\b/],
  ['the penalty regex', /\\d\+\(\\\.\\d\+\)\?%/],
  ['the handle regex', /\[A-Za-z0-9-\]\{0,38\}/],
];

describe('no default literal in the console', () => {
  it('reads every default from policy.json', () => {
    const hits: string[] = [];
    for (const [file, text] of Object.entries(SOURCES))
      text.split('\n').forEach((line, i) => {
        for (const [what, re] of FORBIDDEN) if (re.test(line)) hits.push(`${file}:${i + 1} ${what}: ${line.trim()}`);
      });
    expect(hits).toEqual([]);
  });

  it('would catch the literals it looks for', () => {
    const old = [
      "export const tzOf = (s: Status) => s.semester?.timezone ?? 'Europe/Berlin';",
      "const rateText = cfg.late_penalty_per_day ?? '10%';",
      "formats: [formatsList(ad.formats)[0] ?? 'ipynb'],",
      "const dest = rel.dest?.repo || 'materials';",
      "const adv = [dp.dest && dp.dest !== 'materials'];",
      "const DEFAULT_FORMATS = ['ipynb'];",
      "const lateDays = String(ad.late_window_days ?? 10);",
      "max_team_size: { tier: 'default', label: 'Max team size', widget: 'number', default: 5, defaultLabel: 'default: 5' },",
      'export const ARCHIVE_GRACE_DAYS = 60;',
      'const PENALTY = /^(\\d+(\\.\\d+)?%|0?\\.\\d+|0|1(\\.0+)?)$/;',
      'const HANDLE_RE = /^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$/;',
    ];
    for (const line of old) expect(FORBIDDEN.some(([, re]) => re.test(line)), line).toBe(true);
  });

  it('takes the values and the name rules from the engine’s export', () => {
    expect(DEFAULT_TIMEZONE).toBe(policy.defaults.timezone);
    expect(ARCHIVE_GRACE_DAYS).toBe(policy.defaults.archive.grace_days);
    expect(DEFAULT_DEST_REPO).toBe(policy.defaults.semester_dest_repo);
    expect(HANDLE_RE.test('anna-adams')).toBe(true);
    expect(HANDLE_RE.test('-anna')).toBe(false);
    expect(ORG_NAME_RE.test('hertie-dsl-demo-f2026')).toBe(true);
  });

  it('reads a penalty as the engine does', () => {
    expect(penaltyRate(policy.defaults.late_penalty_per_day)).toBeCloseTo(0.1);
    expect(penaltyRate('0.05')).toBe(0.05);
    expect(penaltyRate('10')).toBeNull(); // a bare number of 1 or more
    expect(penaltyRate('-5%')).toBeNull();
    expect(penaltyRate('100.9%')).toBeNull();
    expect(penaltyRate('ten')).toBeNull();
  });
});

// ------------------------------------------------------------------ the cascade

const inst = institutionLayer();
const stack = (over: Partial<Layers>): Layers => ({ assignment: {}, semester: {}, course: {}, institution: inst, ...over });

describe('the cascade', () => {
  it('answers from the nearest layer that states a value, and says which', () => {
    expect(resolve('max_team_size', stack({}))).toEqual({ value: policy.defaults.max_team_size, source: 'institution' });
    expect(resolve('max_team_size', stack({ course: { max_team_size: 4 } }))).toEqual({ value: 4, source: 'course' });
    expect(resolve('max_team_size', stack({ course: { max_team_size: 4 }, semester: { max_team_size: 3 } }))).toEqual({ value: 3, source: 'semester' });
    expect(resolve('max_team_size', stack({ semester: { max_team_size: 3 }, assignment: { max_team_size: 2 } }))).toEqual({ value: 2, source: 'assignment' });
    expect(resolve('max_team_size', stack({ assignment: { max_team_size: 2 } }), 'semester').source).toBe('institution');
  });

  it('keeps the late pair one rule: a layer naming one half sets the other to none', () => {
    const l = stack({ course: { late_window_days: 7, late_penalty_per_day: '5%' }, semester: { late_window_days: 3 } });
    expect(resolve('late_window_days', l)).toEqual({ value: 3, source: 'semester' });
    expect(resolve('late_penalty_per_day', l)).toEqual({ value: null, source: 'semester' });
    expect(lateWord(3, null)).toBe('3 days, no penalty');
    expect(lateWord(0, '5%')).toBe('no late work');
  });

  it('lets a value the engine refuses fall through to the next layer', () => {
    const doc = { defaults: { max_team_size: -3, visibility: 'secret', team_formation: 'assigned' } };
    const l = layersOf({}, doc);
    expect(usableBlock(doc.defaults)).toEqual({ team_formation: 'assigned' });
    expect(resolve('max_team_size', l).source).toBe('institution');
    expect(resolve('visibility', l).source).toBe('institution');
    expect(resolve('team_formation', l)).toEqual({ value: 'assigned', source: 'semester' });
  });

  it('reads an assignment’s block by its schedule key, and says the value in words', () => {
    const doc = { defaults: { late_window_days: 5, late_penalty_per_day: '5%' }, assignments: { a2: { max_team_size: 2 } } };
    const l = layersOf({ max_team_size: 4 }, doc, 'a2');
    expect(effectiveWord('max_team_size', resolve('max_team_size', l))).toBe('up to 2, set for this assignment');
    expect(effectiveWord('late_window_days', resolve('late_window_days', l))).toBe('5 days, this semester’s default');
    expect(effectiveWord('visibility', resolve('visibility', l))).toBe('Private, institution default');
  });
});

describe('the readers, as setting_readers reads each value', () => {
  it('reads a negative late window as 0, and refuses a window that is not a whole number', () => {
    expect(readSetting('late_window_days', -3)).toBe(0);
    expect(readSetting('late_window_days', '4')).toBe(4);
    expect(readSetting('late_window_days', 2.5)).toBeUndefined();
    expect(readSetting('late_window_days', 'ten')).toBeUndefined();
    expect(resolve('late_window_days', layersOf({}, { defaults: { late_window_days: -3, late_penalty_per_day: '5%' } }))).toEqual({ value: 0, source: 'semester' });
  });

  it('refuses a submit link that is not a filled-in https:// address', () => {
    expect(readSetting('submit_url', 'https://moodle.example.org/a/1')).toBe('https://moodle.example.org/a/1');
    expect(readSetting('submit_url', 'HTTPS://moodle.example.org/')).toBe('HTTPS://moodle.example.org/');
    expect(readSetting('submit_url', 'https://moodle.example.org/CHANGE-ME')).toBeUndefined();
    expect(readSetting('submit_url', 'https://')).toBeUndefined();
    expect(readSetting('submit_url', 'http://moodle.example.org/')).toBeUndefined();
  });

  it('gives the course layer no submit link', () => {
    const l = layersOf({ submit_url: 'https://moodle.example.org/', max_team_size: 4 }, {});
    expect(l.course).toEqual({ max_team_size: 4 });
  });

  it('lower-cases the enums and checks them against the schema’s lists, not a label map', () => {
    expect(readSetting('team_formation', ' Assigned ')).toBe('assigned');
    expect(readSetting('visibility', 'PUBLIC')).toBe('public');
    expect(readSetting('visibility', 'constructor')).toBeUndefined();
    expect(readSetting('team_formation', 'toString')).toBeUndefined();
  });

  it('accepts a team size written as text, and refuses 0 or a fraction', () => {
    expect(readSetting('max_team_size', '4')).toBe(4);
    expect(readSetting('max_team_size', 0)).toBeUndefined();
    expect(readSetting('max_team_size', 3.5)).toBeUndefined();
    expect(usableBlock({ max_team_size: '4', visibility: 'Private' })).toEqual({ max_team_size: 4, visibility: 'private' });
  });

  it('names an assignment’s semester-side artefacts by its semester_dest_repo, else its key', () => {
    expect(semesterName({ assignments: { 'assignment-1': { semester_dest_repo: 'assignment-1-resit' } } }, 'assignment-1')).toBe('assignment-1-resit');
    expect(semesterName({ assignments: { 'assignment-1': { semester_dest_repo: 'not a repo' } } }, 'assignment-1')).toBe('assignment-1');
    expect(semesterName({}, 'assignment-2')).toBe('assignment-2');
  });
});

describe('questions', () => {
  it('keeps a question’s file when it is renamed, and drops file: when the file is cleared', () => {
    const was = { Q1: { points: 5, file: 'report.tex' } };
    expect(questionsValue([{ name: 'Part A', points: '5', file: 'report.tex' }], was)).toEqual({ 'Part A': { points: 5, file: 'report.tex' } });
    expect(questionsValue([{ name: 'Q1', points: '5', file: '' }], was)).toEqual({ Q1: 5 });
  });

  it('refuses a file outside the submission, as the engine does', () => {
    expect(questionFileError('report.tex')).toBeNull();
    expect(questionFileError('write-up/report.tex')).toBeNull();
    for (const bad of ['../secret.tex', '/etc/passwd', 'a\\b.tex', 'a/../b.tex']) expect(questionFileError(bad)).toContain('is not a file inside the submission');
  });
});

describe('writing a layer', () => {
  it('writes only the keys set, into the seeded skeleton, keeping its comments', () => {
    const y = new YamlText(SKELETON);
    writeBlock(y, ['assignments', 'a2'], {}, { late_window_days: 3, late_penalty_per_day: '5%', max_team_size: undefined }, ['late_window_days', 'late_penalty_per_day', 'max_team_size']);
    expect(parse(y.text)).toEqual({ assignments: { a2: { late_window_days: 3, late_penalty_per_day: '5%' } } });
    expect(y.text.startsWith(SKELETON.trimEnd())).toBe(true);
  });

  it('removes a cleared key, and the block and map it empties', () => {
    const y = new YamlText('# mine\ndefaults:\n  max_team_size: 4\nassignments:\n  a2:\n    visibility: public\n');
    writeBlock(y, ['assignments', 'a2'], { visibility: 'public' }, {}, ['visibility']);
    expect(y.text).toBe('# mine\ndefaults:\n  max_team_size: 4\n');
  });

  it('after a schedule save: the new entry’s block only when a setting was set, and no block for a removed entry', () => {
    const text = 'defaults:\n  max_team_size: 4\nassignments:\n  a1:\n    visibility: public\n';
    const af = { text, sha: 's', doc: parse(text), error: null };
    expect(assignmentsAfterSchedule(af, { key: 'a3', run: {} }, [])).toBeNull();
    expect(parse(assignmentsAfterSchedule(af, { key: 'a3', run: { team_formation: 'assigned' } }, [])!).assignments).toEqual({ a1: { visibility: 'public' }, a3: { team_formation: 'assigned' } });
    expect(parse(assignmentsAfterSchedule(af, null, ['a1', 'a9'])!)).toEqual({ defaults: { max_team_size: 4 } });
  });
});
