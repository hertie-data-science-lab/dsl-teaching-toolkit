// @vitest-environment happy-dom
// The student console as the instructor's equivalent (decision 0029): the banner's week line
// and dates, the Schedule's semester weeks, This week's Updated line, a ? on every page title,
// Guide only for instructors, the student shell's footer, supporting files demoted, the fork
// check that runs again by itself, the live lines through CheckLine, and the Hint wrapper's
// class kept apart from the `p.hint` note.

import { readFileSync } from 'node:fs';
import { render } from 'preact';
import { render as html } from 'preact-render-to-string';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { App, createState } from '../src/app';
import { ConsoleAuth } from '../src/auth/console';
import { PatAuth } from '../src/auth/pat';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, type TreeEntry } from '../src/github/client';
import type { Semester } from '../src/model/discovery';
import type { Mine } from '../src/model/mine';
import { STUDENT_STATUS_PATH } from '../src/model/names';
import { StatusFileSource, factsFromStatus, type SemesterFacts } from '../src/model/student';
import { closesWords, formingAt, nextLine, semesterLine, weekItems, weekPhrase, weekWords } from '../src/model/week';
import { STUDENT_HINTS, ScheduleView, StudentBanner, StudentScreen } from '../src/screens/Student';
import { AskedList, TeamForm } from '../src/screens/StudentJoin';
import { MaterialsTree } from '../src/screens/StudentMaterials';
import { RECHECK_FOR_MS, RECHECK_MS, SetupView } from '../src/screens/StudentSetup';
import { FakeGitHub, fileBody } from './fake';
import FILE from './fixtures/student-status.json?raw';

const ORG = 'hertie-dsl-demo-f2026';
const LOGIN = 'octo-student';
const DOC = JSON.parse(FILE);
// Read from the console's folder (vitest's working directory): this file runs in happy-dom.
const css = (name: string) => readFileSync(`${process.cwd()}/src/styles/${name}`, 'utf8');
const NOW = Date.parse('2026-09-23T12:00:00Z');
const semester: Semester = { org: ORG, term: 'f2026', termLabel: 'Fall 2026', courseOrg: 'hertie-dsl-demo-course-e1234', courseName: 'Deep Learning', archived: false, role: 'student' };
const user = { login: LOGIN, id: 1, name: 'O', email: null, avatar_url: '' };
const client = (f: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: f.fetch });
const withFile = (doc: object = DOC) => new FakeGitHub().on('GET', `/repos/${ORG}/.github/contents/.system/student-status.json`, fileBody(STUDENT_STATUS_PATH, JSON.stringify(doc)));

let root: HTMLElement | null = null;
beforeEach(() => localStorage.clear());
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  vi.restoreAllMocks();
  history.replaceState(null, '', '/');
});

async function mount(v: preact.VNode, f: FakeGitHub) {
  const env = { user, client: client(f) } as unknown as Env;
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{v}</EnvCtx.Provider>, root!));
  await settle();
  return root;
}
const settle = async () => {
  for (let i = 0; i < 4; i++) await act(async () => { await (vi.isFakeTimers() ? vi.advanceTimersByTimeAsync(0) : new Promise((r) => setTimeout(r, 0))); });
};

describe('the semester dates and when the file was written', () => {
  it('are read from the file; a file of the first schema still reads, without them', async () => {
    const f = factsFromStatus(DOC);
    expect([f.start, f.end, f.generatedAt]).toEqual(['2026-09-07', '2026-12-18', '2026-09-23T09:00:00+00:00']);
    const { semester_start: _s, semester_end: _e, generated_at: _g, ...rest } = DOC;
    const old = await new StatusFileSource(client(withFile({ ...rest, schema: 'dsl.student-status/1' }))).facts(ORG);
    expect(old?.assignments.length).toBe(DOC.assignments.length);
    expect([old?.start, old?.end, old?.generatedAt]).toEqual([undefined, undefined, undefined]);
  });

  it('write a team formation’s close as every other date, and close it at that instant', () => {
    const f = factsFromStatus(DOC);
    const a = f.assignments.find((x) => x.teamFormation)!;
    const tz = 'Europe/Berlin';
    const teams = (now: number) => weekItems(f, null, now).filter((i) => i.kind === 'teams').map((i) => i.text);
    expect(teams(NOW)).toEqual([`Team formation is open for ${a.title} until Mon 9 Nov 23:59`]);
    const after = Date.parse('2026-11-10T00:00:00+01:00');
    expect(teams(after)).toEqual([]);
    expect(formingAt(a.teamFormation, Date.parse('2026-11-09T23:58:00+01:00'), tz)).toBe(true);
    expect(formingAt(a.teamFormation, Date.parse('2026-11-09T23:59:00+01:00'), tz)).toBe(false);
    expect(closesWords('2026-11-09 23:59', tz)).toBe('Mon 9 Nov 23:59');
    expect(closesWords('26th Oct', tz)).toBe('26th Oct');
    expect(html(<TeamForm org={ORG} assignments={[a]} mine={null} tz={tz} now={NOW} />)).toContain('Teams can form until Mon 9 Nov 23:59.');
    expect(html(<TeamForm org={ORG} assignments={[a]} mine={null} tz={tz} now={after} />)).toContain('No assignment is forming teams now.');
  });

  it('give the week as the instructor’s banner counts it: none before week 1, the last after the end', () => {
    const f = factsFromStatus(DOC);
    expect(semesterLine(f, NOW)).toEqual({ week: 'Week 3 of 15', dates: expect.stringMatching(/7 Sep.* to .*18 Dec/) });
    expect(semesterLine(f, Date.parse('2026-09-01T12:00:00Z'))).toMatchObject({ week: undefined, starts: 'Starts Mon 7 Sep' });
    expect(semesterLine(f, Date.parse('2027-01-10T12:00:00Z')).week).toBe('Week 15 of 15');
    expect(semesterLine({ ...f, start: undefined }, NOW)).toEqual({});
    // One phrase for both cards: the instructor's from status, the student's from the facts.
    expect(weekWords({ week: 0, weeks: 15, start: '2026-09-07', timezone: 'Europe/Berlin' })).toBe(semesterLine(f, Date.parse('2026-09-01T12:00:00Z')).starts);
    expect(weekWords({ week: 3, weeks: 15, start: '2026-09-07', timezone: 'Europe/Berlin' })).toBe(semesterLine(f, NOW).week);
    expect(weekPhrase(0, 15, null, 'Europe/Berlin')).toBe('Before week 1');
    expect(weekPhrase(null, null, '2026-09-07', 'Europe/Berlin')).toBe('');
  });

  it('give each semester card its next dated row, worded as the instructor’s cards', () => {
    const f = factsFromStatus(DOC);
    const row = (kind: string, title: string, when: string) => ({ ...f.rows[0], id: when, kind, title, when });
    const lecture = row('lecture', 'Lecture 2', '2026-09-30T10:00:00+02:00');
    const handOut = row('assignment', 'Assignment 2', '2026-10-06T10:00:00+02:00');
    expect(nextLine({ ...f, rows: [handOut, lecture] }, NOW)).toBe('Next: Lecture 2, Wed 30 Sep');
    expect(nextLine({ ...f, rows: [handOut] }, NOW)).toBe('Next: Assignment 2 hand out, Tue 6 Oct');
    expect(nextLine({ ...f, rows: [] }, NOW)).toBe('Nothing scheduled');
  });
});

describe('the student banner and screens', () => {
  it('the banner shows the week and the dates once the facts are read', async () => {
    const el = await mount(<StudentBanner root="Your semesters" screen="week" semester={semester} studentView={false} now={NOW} />, withFile());
    expect(el.querySelector('.cb-sem')!.textContent).toContain('Week 3 of 15');
    expect(el.querySelector('.cb-sem .sem-title')!.textContent).toBe('Fall 2026');
    expect(el.querySelector('h1')!.textContent).toBe(semester.courseName);
    expect(el.textContent).not.toContain('Back to instructor view');
  });

  it('every student page title carries a ?', () => {
    for (const screen of Object.keys(STUDENT_HINTS)) {
      const out = html(<StudentScreen semester={semester} screen={screen} studentView={false} now={NOW} />);
      expect(out).toMatch(/<h2 class="h1">[^<]+<span class="hint-wrap"><button class="hint-btn"/);
      expect(out).toContain(STUDENT_HINTS[screen].replace(/’/g, '&rsquo;').slice(0, 30));
    }
    for (const t of Object.values(STUDENT_HINTS)) expect(t.split(/[.!?](\s|$)/).filter((x) => x.trim()).length).toBeLessThanOrEqual(2);
  });

  it('This week says how old the facts are, and nothing for a file that does not say', async () => {
    const el = await mount(<StudentScreen semester={semester} screen="week" studentView={false} now={Date.parse('2026-09-23T12:00:00Z')} />, withFile());
    expect(el.querySelector('.updated')!.textContent).toBe('Updated 3 h ago');
    render(null, root!);
    const { generated_at: _g, ...rest } = DOC;
    const old = await mount(<StudentScreen semester={semester} screen="week" studentView={false} now={NOW} />, withFile(rest));
    expect(old.querySelector('.updated')).toBeNull();
  });

  it('the Schedule numbers weeks from the semester start, and only by position for an older file', () => {
    const f = factsFromStatus(DOC);
    const heads = (facts: SemesterFacts) => [...html(<ScheduleView facts={facts} mine={null} now={NOW} org={ORG} />).matchAll(/<h2 class="week-h">(.*?)<\/h2>/g)].map((m) => m[1].replace(/<[^>]+>/g, '').trim());
    const weeks = (facts: SemesterFacts) => heads(facts).map((h) => h.replace(/ from .*/, ''));
    // Weeks 5 and 6 have no row: the numbers skip them, as the Dashboard counts.
    expect(weeks(f)).toEqual(['Week 1', 'Week 2', 'Week 3', 'Week 4', 'Week 7', 'Week 8', 'Week 15']);
    expect(weeks({ ...f, start: undefined, end: undefined })).toEqual(['Week 1', 'Week 2', 'Week 3', 'Week 4', 'Week 5', 'Week 6', 'Week 7']);
    // A Wednesday start: weeks run Wednesday to Tuesday, as the Dashboard counts them, with one
    // group for what falls before the semester and one for after.
    const row = (when: string) => ({ ...f.rows[1], id: when, when, kind: 'lecture', title: when });
    const wed = { ...f, start: '2026-09-09', end: '2026-12-18', rows: ['2026-09-01T10:00:00+02:00', '2026-09-08T10:00:00+02:00', '2026-09-09T10:00:00+02:00', '2026-09-14T10:00:00+02:00', '2026-09-16T10:00:00+02:00', '2027-01-05T10:00:00+01:00'].map(row) };
    expect(heads(wed)).toEqual(['Before the semester', 'Week 1 from Wed 9 Sep', 'Week 2 from Wed 16 Sep', 'After the semester']);
  });
});

describe('the student shell', () => {
  it('lands a student with one live semester on its This week, with its footer and no Guide', async () => {
    const f = withFile();
    const s = createState({ auth: new ConsoleAuth(new PatAuth({ store: null }), null), client: client(f) });
    s.user.value = user;
    s.estate.value = { courses: [], semesters: [semester], roles: new Map([[ORG, 'student' as const]]), kind: 'classic' };
    root = document.createElement('div');
    document.body.appendChild(root);
    await act(async () => render(<App state={s} />, root!));
    await settle();
    expect(root.querySelector('#view h2.h1')?.textContent).toContain('This week');
    expect(root.querySelector('.site-footer h2')!.textContent).toBe('Deep Learning');
    expect(root.querySelector('.site-footer p')!.textContent).toBe('Fall 2026');
    expect([...root.querySelectorAll('.topbar a')].some((a) => a.textContent === 'Guide')).toBe(false);
  });
});

describe('Materials', () => {
  it('shows no empty list above Supporting files when a repo holds nothing else', () => {
    const tree: TreeEntry[] = ['data/x.csv'].map((path) => ({ path, mode: '100644', type: 'blob', sha: path, size: 1 }));
    const out = html(<MaterialsTree org={ORG} trees={[['materials', tree]]} />);
    expect(out.slice(0, out.indexOf('Supporting files'))).not.toContain('file-tree');
    expect(out).not.toContain('Nothing released yet');
  });

  it('folds only the folders named as supporting files, never one the instructor may have set a kind on', () => {
    // `code/` is supporting files by default (decision 0031), but the student's status
    // carries no repo's kinds: a course that set it to a lecture must still see it.
    const tree: TreeEntry[] = ['code/run.py', 'static/s.css'].map((path) => ({ path, mode: '100644', type: 'blob', sha: path, size: 1 }));
    const out = html(<MaterialsTree org={ORG} trees={[['materials', tree]]} />);
    const main = out.slice(0, out.indexOf('Supporting files'));
    expect(main).toContain('code/');
    expect(main).not.toContain('static/');
  });

  it('puts supporting folders last, folded, under Supporting files', () => {
    const tree: TreeEntry[] = ['data/x.csv', 'lectures/01/slides.pdf', 'img/a.png', 'SYLLABUS.md'].map((path) => ({ path, mode: '100644', type: 'blob', sha: path, size: 1 }));
    const out = html(<MaterialsTree org={ORG} trees={[['materials', tree]]} />);
    const main = out.slice(0, out.indexOf('Supporting files'));
    expect(main).toContain('lectures/');
    expect(main).toContain('SYLLABUS.md');
    expect(main).not.toContain('data/');
    expect(out).toMatch(/<details class="fold supporting"><summary>Supporting files<\/summary>/);
    const fold = out.slice(out.indexOf('Supporting files'));
    expect(fold).toContain('data/');
    expect(fold).toContain('img/');
    expect(fold).not.toMatch(/<details open/);
    // The same row heads as the instructors' tree: folder and file icons.
    expect(main).toContain('class="ft-closed"');
    expect(main).toContain('<svg class="ft-icon"');
  });
});

describe('live checks', () => {
  const facts = { materialsRepos: ['materials'] } as unknown as SemesterFacts;
  const mine = { units: {} } as unknown as Mine;
  const forked = { name: 'materials', fork: true, parent: { full_name: `${ORG}/materials` }, html_url: `https://github.com/${LOGIN}/materials` };
  const checks = (f: FakeGitHub) => f.seen.filter((x) => x.url.includes(`/repos/${LOGIN}/materials`)).length;

  const notFound = () => new Response('{"message":"Not Found"}', { status: 404 });
  const ok = (body: object) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
  const fakeIntervals = () => vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'Date'] });
  const tickBy = async (ms: number) => {
    await act(async () => void (await vi.advanceTimersByTimeAsync(ms)));
    await settle();
  };
  afterEach(() => vi.useRealTimers());

  it('runs the fork check again every 10 s and on focus while the repo is not forked, and stops once it is', async () => {
    fakeIntervals();
    let fork = false;
    const f = new FakeGitHub().on('GET', `/repos/${LOGIN}/materials`, () => (fork ? ok(forked) : notFound()));
    const el = await mount(<SetupView org={ORG} facts={facts} mine={mine} studentView={false} />, f);
    expect(el.textContent).toContain('Fork materials');
    expect(el.textContent).toContain('Check again');
    const first = checks(f);
    await tickBy(RECHECK_MS);
    expect(checks(f)).toBe(first + 1);
    fork = true;
    await act(() => void window.dispatchEvent(new Event('focus')));
    await settle();
    expect(checks(f)).toBe(first + 2);
    expect(el.querySelector('.check-line.ok')!.textContent).toContain(`You have forked it: ${LOGIN}/materials`);
    expect(el.querySelector('.check-line.ok svg')).not.toBeNull();
    // All forked: nothing runs again by itself.
    await tickBy(RECHECK_MS * 3);
    await act(() => void window.dispatchEvent(new Event('focus')));
    await settle();
    expect(checks(f)).toBe(first + 2);
  });

  it('slows down after a minute, stops re-checking after 5 minutes and on leaving the page; Check again starts it again', async () => {
    fakeIntervals();
    const f = new FakeGitHub().on('GET', `/repos/${LOGIN}/materials`, notFound);
    const el = await mount(<SetupView org={ORG} facts={facts} mine={mine} studentView={false} />, f);
    const first = checks(f);
    for (let t = 0; t < RECHECK_FOR_MS / RECHECK_MS + 3; t++) await tickBy(RECHECK_MS);
    const capped = checks(f);
    // Every 10 s for the first minute (6), then every 30 s until 5 minutes (at 90 s to 270 s: 7).
    expect(capped - first).toBe(13);
    await tickBy(RECHECK_MS * 3);
    expect(checks(f)).toBe(capped);
    await act(() => void [...el.querySelectorAll('button')].find((b) => b.textContent === 'Check again')!.click());
    await settle();
    await tickBy(RECHECK_MS);
    expect(checks(f)).toBe(capped + 2);
    // Leaving the page stops it: neither a focus nor the clock reads anything.
    render(null, root!);
    window.dispatchEvent(new Event('focus'));
    await tickBy(RECHECK_MS * 2);
    expect(checks(f)).toBe(capped + 2);
  });

  it('does not re-check a repo of the name that is not the fork, or after a read that failed', async () => {
    fakeIntervals();
    const other = new FakeGitHub().on('GET', `/repos/${LOGIN}/materials`, () => ok({ ...forked, fork: false, parent: undefined }));
    await mount(<SetupView org={ORG} facts={facts} mine={mine} studentView={false} />, other);
    expect(root!.querySelector('.check-line.warn')!.textContent).toContain('that is not a fork');
    await tickBy(RECHECK_MS * 2);
    expect(checks(other)).toBe(1);
    render(null, root!);
    const failing = new FakeGitHub().on('GET', `/repos/${LOGIN}/materials`, () => new Response('{"message":"Server Error"}', { status: 500 }));
    await mount(<SetupView org={ORG} facts={facts} mine={mine} studentView={false} />, failing);
    const after = checks(failing);
    await tickBy(RECHECK_MS * 2);
    expect(checks(failing)).toBe(after);
  });

  it('a Join request still waiting for the automation shows the spinner', () => {
    const issue = { number: 1, title: 'Join team', state: 'open', html_url: '', created_at: '2026-09-23T10:00:00Z', comments: 0, labels: [], body: '' };
    const out = html(<AskedList asked={[{ issue, reply: null } as never]} />);
    expect(out).toMatch(/<div class="check-line busy"><span class="spin"><\/span><span><b>Join team<\/b>/);
  });
});

describe('the ? wrapper', () => {
  it('is .hint-wrap in every stylesheet, so a p.hint note takes none of its layout', () => {
    const left = [css('console.css'), css('open.css')].flatMap((t) => t.match(/[^\n{}]*\.hint(?![-\w])[^{]*\{/g) ?? []).map((x) => x.trim());
    // Both are form notes (`p.hint`), not the ? wrapper.
    expect(left.sort()).toEqual(['.course-folders li p.hint {', '.field .hint {']);
  });
});
