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
import { nextLine, semesterLine } from '../src/model/week';
import { STUDENT_HINTS, ScheduleView, StudentBanner, StudentScreen } from '../src/screens/Student';
import { AskedList } from '../src/screens/StudentJoin';
import { MaterialsTree } from '../src/screens/StudentMaterials';
import { RECHECK_MS, SetupView } from '../src/screens/StudentSetup';
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
  for (let i = 0; i < 4; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
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

  it('give the week as the instructor’s banner counts it: none before week 1, the last after the end', () => {
    const f = factsFromStatus(DOC);
    expect(semesterLine(f, NOW)).toEqual({ week: 'Week 3 of 15', dates: expect.stringMatching(/7 Sep.* to .*18 Dec/) });
    expect(semesterLine(f, Date.parse('2026-09-01T12:00:00Z')).week).toBeUndefined();
    expect(semesterLine(f, Date.parse('2027-01-10T12:00:00Z')).week).toBe('Week 15 of 15');
    expect(semesterLine({ ...f, start: undefined }, NOW)).toEqual({});
  });

  it('give each semester card its next dated row', () => {
    const f = factsFromStatus(DOC);
    expect(nextLine(f, NOW)).toMatch(/^Next: .+, \w{3} \d+ \w{3}$/);
    expect(nextLine({ ...f, rows: [] }, NOW)).toBe('Nothing scheduled');
  });
});

describe('the student banner and screens', () => {
  it('the banner shows the week and the dates once the facts are read', async () => {
    const el = await mount(<StudentBanner semester={semester} studentView={false} now={NOW} />, withFile());
    expect(el.querySelector('.sb-line')!.textContent).toContain('Week 3 of 15');
    expect(el.querySelector('h1')!.textContent).toBe('Fall 2026');
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
    const weeks = (facts: SemesterFacts) => [...html(<ScheduleView facts={facts} mine={null} now={NOW} org={ORG} />).matchAll(/<h2 class="week-h">([^<]+?) <span>/g)].map((m) => m[1]);
    // Weeks 5 and 6 have no row: the numbers skip them, as the Dashboard counts.
    expect(weeks(f)).toEqual(['Week 1', 'Week 2', 'Week 3', 'Week 4', 'Week 7', 'Week 8', 'Week 15']);
    expect(weeks({ ...f, start: undefined, end: undefined })).toEqual(['Week 1', 'Week 2', 'Week 3', 'Week 4', 'Week 5', 'Week 6', 'Week 7']);
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

  it('the fork check runs again on focus and every 10 s while the repo is not forked, and stops once it is', async () => {
    const every = vi.spyOn(window, 'setInterval');
    let fork = false;
    const f = new FakeGitHub().on('GET', `/repos/${LOGIN}/materials`, () => (fork ? new Response(JSON.stringify(forked), { status: 200, headers: { 'content-type': 'application/json' } }) : new Response('{"message":"Not Found"}', { status: 404 })));
    const el = await mount(<SetupView org={ORG} facts={facts} mine={mine} studentView={false} />, f);
    expect(el.textContent).toContain('Fork materials');
    expect(el.textContent).toContain('Check again');
    expect(every).toHaveBeenCalledWith(expect.any(Function), RECHECK_MS);
    expect(RECHECK_MS).toBe(10000);
    const before = checks(f);
    fork = true;
    await act(() => void window.dispatchEvent(new Event('focus')));
    await settle();
    expect(checks(f)).toBe(before + 1);
    expect(el.querySelector('.check-line.ok')!.textContent).toContain(`You have forked it: ${LOGIN}/materials`);
    expect(el.querySelector('.check-line.ok svg')).not.toBeNull();
    // All forked: nothing runs again by itself.
    await act(() => void window.dispatchEvent(new Event('focus')));
    await settle();
    expect(checks(f)).toBe(before + 1);
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
    expect(left).toEqual(['.field .hint {']);
  });
});
