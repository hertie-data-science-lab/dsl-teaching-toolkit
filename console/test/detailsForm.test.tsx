// @vitest-environment happy-dom
// Course details (WP-S6): the public website switch writes opencourse.yml at once, each
// select lists its inherited value once as "X (default)", and "Edit the file directly".

import { render } from 'preact';
import { render as html } from 'preact-render-to-string';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { EnvCtx, type Env } from '../src/env';
import { CONFLICT } from '../src/edit/save';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StaticFiles } from '../src/model/files';
import { POLICY } from '../src/model/policy';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { DetailsScreen } from '../src/screens/CourseEdit';
import { courseDefaultTiers, COURSE_FACTS } from '../src/tiers/course';
import { runTier } from '../src/tiers/runSettings';
import { EditFile } from '../src/ui/bits';
import example from './fixtures/status.example.json';
import { FakeGitHub, json, type Seen } from './fake';

const ORG = 'hertie-dsl-demo-course-e1234';
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const course: Course = { org: ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [], meta: {} };
const loaded: Loaded = { kind: 'ready', status: example as unknown as Status, sha: 's', stale: [] };
const META = 'course_name: Machine Learning\ncourse_code: E1234\n';

function envOf(gh: FakeGitHub, f: StaticFiles): Env {
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  return { client, user: USER, ops: new OpsSession(new DispatchAdapter(client, () => USER.login)), statuses: new StatusStore(client), files: f, pollMs: 0 };
}
const github = () => new FakeGitHub().on('PUT', /\/contents\//, () => json({ content: { sha: 'b1' }, commit: { sha: 'c1' } })).on('GET', /\/check-runs/, { check_runs: [] });

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});
async function mount(env: Env, f: StaticFiles) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}><DetailsScreen course={course} loaded={loaded} cohortStates={{}} files={f} now={0} /></EnvCtx.Provider>, root!));
}
async function flip() {
  await act(() => root!.querySelector<HTMLInputElement>('#cd-web')!.click());
}
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}
const puts = (gh: FakeGitHub) => gh.seen.filter((s) => s.method === 'PUT' && s.url.endsWith('/contents/opencourse.yml'));
const body = (s: Seen) => decodeBase64((s.body as { content: string }).content);
const word = () => root!.querySelector('.web-switch .footnote')!.textContent;

describe('the public website switch in Course details', () => {
  it('writes enabled at once, keeping the rest of the file', async () => {
    const gh = github();
    const f = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META, [`${ORG}/.github/opencourse.yml`]: '# mine\nenabled: true\nsource_repo: course-materials-f2026\n' });
    await mount(envOf(gh, f), f);
    expect(word()).toBe('On: updates daily');
    await flip();
    await settle(() => puts(gh).length > 0 && word() !== 'Saving…');
    expect(body(puts(gh)[0])).toBe('# mine\nenabled: false\nsource_repo: course-materials-f2026\n');
    expect(word()).toBe('Off');
    expect(root!.querySelector<HTMLInputElement>('#cd-web')!.checked).toBe(false);
  });

  it('seeds opencourse.yml when there is none, with the newest materials repo as the source', async () => {
    const gh = github();
    const f = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META });
    await mount(envOf(gh, f), f);
    expect(word()).toBe('Off');
    await flip();
    expect(word()).toBe('Saving…');
    expect(root!.querySelector<HTMLInputElement>('#cd-web')!.disabled).toBe(true);
    await settle(() => puts(gh).length > 0 && word() !== 'Saving…');
    const text = body(puts(gh)[0]);
    expect(text.startsWith('# INSTRUCTOR-OWNED')).toBe(true);
    expect(parse(text)).toMatchObject({ enabled: true, source_repo: 'course-materials-f2026' });
    expect((puts(gh)[0].body as { sha?: string }).sha).toBeUndefined();
    expect(word()).toBe('On: updates daily');
  });

  it('is not part of the form’s Save', async () => {
    const gh = github();
    const f = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META });
    await mount(envOf(gh, f), f);
    await flip();
    await settle(() => puts(gh).length > 0 && word() !== 'Saving…');
    expect(gh.seen.filter((s) => s.method === 'PUT').map((s) => s.url.split('/contents/')[1])).toEqual(['opencourse.yml']);
    expect([...root!.querySelectorAll('button')].find((b) => b.textContent === 'Save')!.disabled).toBe(true);
  });
});

describe('selects list the inherited value once', () => {
  const labels = (options: { label: string }[] | undefined) => (options ?? []).map((o) => o.label);
  it('as "X (default)" first, with value "", and not again', () => {
    const lic = COURSE_FACTS.licence.options!;
    expect(lic[0]).toEqual({ value: '', label: `${POLICY.licences[0].name} (default)` });
    expect(lic.filter((o) => o.label.startsWith(POLICY.licences[0].name))).toHaveLength(1);
    const t = courseDefaultTiers();
    expect(labels(t.formats.options)).toEqual(['Jupyter notebook (default)', 'Python files', 'R Markdown', 'Quarto', 'LaTeX', 'No starter file']);
    expect(labels(t.submit_via.options)).toEqual(['Their own repo (default)', 'A shared drop box', 'Elsewhere']);
    expect(labels(t.visibility.options)).toEqual(['Private (default)', 'Public', 'Student\'s choice']);
    const course = runTier('team_formation', { value: 'assigned', source: 'course' });
    expect(labels(course.options)).toEqual(['You assign them (default)', 'Students form their own']);
    expect(course.options![0].value).toBe('');
    for (const tier of [COURSE_FACTS.licence, t.formats, t.submit_via, t.visibility, t.team_formation])
      expect(labels(tier.options).some((l) => l.startsWith('Default ('))).toBe(false);
  });
});

describe('copy', () => {
  it('says "Edit the file directly" on every link and in the conflict message', () => {
    expect(html(<EditFile org="o" repo="r" path="p" />)).toContain('Edit the file directly');
    expect(CONFLICT).toContain('or use Edit the file directly.');
  });
  it('explains the linked file types with a ?, and that the public website is not affected', () => {
    const f = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META });
    const out = html(<DetailsScreen course={course} loaded={loaded} cohortStates={{}} files={f} now={0} />);
    expect(out).toContain('File types linked on each semester’s student site <span class="hint"><button class="hint-btn" type="button" aria-label="About linked file types"');
    expect(out).toContain('Other files are still released: students reach them through the repo.');
    expect(out).toContain('The public website is not affected.');
    expect(out).toContain('Released files of these types get a direct link on every semester’s student site.');
  });
});
