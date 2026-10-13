// @vitest-environment happy-dom
// The Site tab's settings, clicked through against a fake GitHub: each value is saved into
// the file the sync reads it from (dsl-course.yml, schedule.yml, or the site's own
// _config.yml, byte for byte except the key changed), and Add an announcement creates a file.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { SiteScreen } from '../src/screens/Site';
import { homeText } from '../src/model/student';
import { StaticFiles } from './staticFiles';
import example from './fixtures/status.example.json';
import { FakeGitHub, json, type Seen } from './fake';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const ORG = 'hertie-dsl-demo-f2026';
const SITE = `${ORG}.github.io`;
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const cohort = { org: ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };
const loaded: Loaded = { kind: 'ready', status: example as unknown as Status, sha: 's', stale: [] };
const COURSE_YML = '# INSTRUCTOR-OWNED\ncourse_name: Machine Learning\ncourse_code: E1234\npeople:\n  course_admins:\n    - github_handle: a-example\n      email: a@staff.example.org\n';
const SCHEDULE = '# INSTRUCTOR-OWNED\ntimezone: Europe/Berlin\nsemester_start: 2026-09-07\nsemester_end: 2026-12-18\n';
const CONFIG = '# INSTRUCTOR-OWNED\ncourse_name: "Machine Learning"\n\n# --- Institution ---\nschoolname: "Hertie School"\nschoolurl: "https://hertie-school.org/en/datasciencelab"\ndsl_org_url: "https://github.com/hertie-data-science-lab"\naddress: |\n    Data Science Lab\n    Hertie School\n\nplugins:\n  - jekyll-remote-theme\n';

const HOME = 'Welcome to {{ site.course_name }}.\n\n{% if site.content_repo %}Materials live in {{ site.content_repo }}.{% endif %}\n';

const files = new StaticFiles({
  [`${COURSE_ORG}/.github/dsl-course.yml`]: COURSE_YML,
  [`${ORG}/semester-config/schedule.yml`]: SCHEDULE,
  [`${ORG}/${SITE}/_config.yml`]: CONFIG,
  [`${ORG}/${SITE}/index.md`]: `---\nlayout: home\n---\n${HOME}`,
});

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount() {
  const gh = new FakeGitHub().on('PUT', /\/contents\//, json({ content: { sha: 'b1' }, commit: { sha: 'c1' } })).on('GET', /\/check-runs/, { check_runs: [] });
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  const env: Env = { client, user: USER, ops: new OpsSession(new DispatchAdapter(client, () => USER.login)), statuses: new StatusStore(client), files, pollMs: 0 };
  root = document.body.appendChild(document.createElement('div'));
  await act(() => render(<EnvCtx.Provider value={env}><SiteScreen course={course} cohort={cohort} loaded={loaded} files={files} now={Date.parse('2026-09-23T10:00:00+02:00')} /></EnvCtx.Provider>, root!));
  return gh;
}
const q = <T extends Element>(sel: string) => root!.querySelector<T>(sel)!;
async function type(sel: string, value: string) {
  await act(() => {
    const el = q<HTMLInputElement>(sel);
    el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
  });
}
const section = (h: string) => [...root!.querySelectorAll('.form-section')].find((s) => s.querySelector('h3')?.textContent?.trim() === h)!;
const saveIn = (el: Element) => [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Save')!;
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}
const puts = (gh: FakeGitHub, path: string) => gh.seen.filter((s) => s.method === 'PUT' && s.url.includes(`/contents/${path}`));
const body = (s: Seen) => decodeBase64((s.body as { content: string }).content);

describe('Site settings', () => {
  it('saves an institution key into the site’s _config.yml, leaving every other byte', async () => {
    const gh = await mount();
    await type('#ss-inst-schoolname', 'Hertie School of Governance');
    await act(() => saveIn(section('Institution')).click());
    await settle(() => puts(gh, '_config.yml').length > 0);
    const [w] = puts(gh, '_config.yml');
    expect(w.url).toContain(`/repos/${ORG}/${SITE}/contents/_config.yml`);
    expect(body(w)).toBe(CONFIG.replace('schoolname: "Hertie School"', 'schoolname: Hertie School of Governance'));
  });

  it('saves the course name into dsl-course.yml and says the site follows at the next sync', async () => {
    const gh = await mount();
    const course = section('Course');
    expect(course.textContent).toContain('Saved to Course details; the site updates at the next sync.');
    expect(course.querySelector('a.textlink')!.getAttribute('href')).toBe(`?course=${COURSE_ORG}#details`);
    expect(course.querySelector('a.edit-file')!.getAttribute('title')).toBe(`${COURSE_ORG}/.github/dsl-course.yml`);
    await type('#ss-course-course_name', 'Machine Learning II');
    await act(() => saveIn(course).click());
    await settle(() => puts(gh, 'dsl-course.yml').length > 0);
    const [w] = puts(gh, 'dsl-course.yml');
    expect(w.url).toContain(`/repos/${COURSE_ORG}/.github/contents/dsl-course.yml`);
    expect(body(w)).toBe(COURSE_YML.replace('course_name: Machine Learning', 'course_name: Machine Learning II'));
    expect(puts(gh, '_config.yml')).toEqual([]);
  });

  it('saves the semester dates into schedule.yml, pointing at the Schedule', async () => {
    const gh = await mount();
    const sem = section('Semester dates');
    expect(sem.textContent).toContain('Saved to Schedule; the site updates at the next sync.');
    expect(sem.querySelector('a.edit-file')!.getAttribute('title')).toBe(`${ORG}/semester-config/schedule.yml`);
    await type('#e-te', '2026-12-20');
    await act(() => saveIn(sem).click());
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    expect(parse(body(puts(gh, 'schedule.yml')[0])).semester_end).toBe('2026-12-20');
  });

  it('shows the semester label and org as what they are, not as fields', async () => {
    await mount();
    expect(section('Semester and org').textContent).toContain('from the semester org’s name');
  });
});

describe('Home text', () => {
  it('previews what students see, and names the site keys _config.yml does not set', async () => {
    await mount();
    const prev = q('.md-prev');
    expect(prev.textContent).toBe(`What students see${homeText(HOME, { course_name: 'Machine Learning' })}`);
    expect(prev.textContent).not.toContain('{%');
    expect(prev.textContent).not.toContain('content_repo');
    expect(q('#site-home').closest('.section')!.querySelector('p.footnote')!.textContent).toBe('Not set in _config.yml: content_repo - this text shows nothing for it.');
  });
});

describe('Institution', () => {
  it('sits in a collapsed Advanced fold at the bottom of the settings', async () => {
    await mount();
    const fold = section('Institution').closest('details')!;
    expect(fold.open).toBe(false);
    expect(fold.querySelector('summary')!.textContent).toBe('Advanced');
    expect(fold.parentElement!.lastElementChild).toBe(fold);
  });
});

describe('Add an announcement', () => {
  it('opens a title, date and text form and creates a file under _announcements/', async () => {
    const gh = await mount();
    expect(root!.querySelector('#ann-text')).toBeNull();
    await act(() => [...root!.querySelectorAll('button')].find((b) => b.textContent === 'Add an announcement')!.click());
    await type('#ann-title', 'Room change');
    await type('#ann-date', '2026-10-05');
    await act(() => {
      const t = q<HTMLTextAreaElement>('#ann-text');
      t.value = 'We meet in B2.01.';
      t.dispatchEvent(new Event('input', { bubbles: true }));
    });
    await act(() => saveIn(q('#ann-text').closest('.form-section')!).click());
    await settle(() => puts(gh, '_announcements/').length > 0);
    const [w] = puts(gh, '_announcements/');
    expect(w.url).toContain(`/repos/${ORG}/${SITE}/contents/_announcements/2026-10-05-room-change.md`);
    expect(parse(body(w).split('---')[1])).toEqual({ title: 'Room change', date: '2026-10-05', details: 'We meet in B2.01.' });
  });
});
