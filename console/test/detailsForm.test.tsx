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
import { StaticFiles, type FileState } from '../src/model/files';
import { POLICY } from '../src/model/policy';
import { StatusStore, type Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { DetailsScreen } from '../src/screens/CourseEdit';
import { courseDefaultTiers, COURSE_FACTS } from '../src/tiers/course';
import { runTier } from '../src/tiers/runSettings';
import { EditFile } from '../src/ui/bits';
import { Field } from '../src/forms/Form';
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
async function mount(env: Env, f: StaticFiles, l: Loaded = loaded, migrated?: boolean) {
  root ??= document.body.appendChild(document.createElement('div'));
  await act(() => render(<EnvCtx.Provider value={env}><DetailsScreen course={course} loaded={l} cohortStates={{}} files={f} now={0} migrated={migrated} /></EnvCtx.Provider>, root!));
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

const idle = () => !root!.querySelector<HTMLInputElement>('#cd-web')!.disabled;
const input = () => root!.querySelector<HTMLInputElement>('#cd-web')!;
const noMaterials: Loaded = { kind: 'ready', status: { ...(example as unknown as Status), course: { ...(example as unknown as Status).course!, materials: [] } }, sha: 's', stale: [] };

/** A file store whose opencourse.yml could not be read. */
class Unreadable extends StaticFiles {
  file(owner: string, repo: string, path: string): FileState {
    return path === 'opencourse.yml' ? { kind: 'error', message: 'boom' } : super.file(owner, repo, path);
  }
}

describe('the public website switch in Course details', () => {
  it('writes enabled at once, keeping the rest of the file', async () => {
    const gh = github();
    const f = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META, [`${ORG}/.github/opencourse.yml`]: '# mine\nenabled: true\nsource_repo: course-materials-f2026\n' });
    await mount(envOf(gh, f), f);
    expect(word()).toBe('On: updates daily');
    expect(input().getAttribute('aria-describedby')).toBe('cd-web-state');
    await flip();
    await settle(() => puts(gh).length > 0 && idle());
    expect(body(puts(gh)[0])).toBe('# mine\nenabled: false\nsource_repo: course-materials-f2026\n');
    expect(word()).toBe('Off');
    expect(input().checked).toBe(false);
  });

  it('seeds opencourse.yml when there is none, holding the new position while it saves', async () => {
    const gh = github();
    const f = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META });
    await mount(envOf(gh, f), f);
    expect(word()).toBe('Off');
    await flip();
    expect(word()).toBe('Saving…');
    expect(input().disabled).toBe(true);
    expect(input().checked).toBe(true);
    await settle(() => word() === 'Checking…');
    expect(input().checked).toBe(true);
    await settle(() => puts(gh).length > 0 && idle());
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
    await settle(() => puts(gh).length > 0 && idle());
    expect(gh.seen.filter((s) => s.method === 'PUT').map((s) => s.url.split('/contents/')[1])).toEqual(['opencourse.yml']);
    expect([...root!.querySelectorAll('button')].find((b) => b.textContent === 'Save')!.disabled).toBe(true);
  });

  it('goes back and says why when the save fails', async () => {
    const gh = new FakeGitHub().on('PUT', /\/contents\//, () => json({ message: 'Server error' }, 500));
    const f = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META });
    await mount(envOf(gh, f), f);
    await flip();
    await settle(() => idle());
    expect(input().checked).toBe(false);
    expect(word()).toBe('Off');
    expect(root!.querySelector('.web-switch .check-line')!.textContent).toMatch(/^Not saved: /);
  });

  it('refuses to turn on without a materials repo, and writes nothing', async () => {
    const gh = github();
    const f = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META });
    await mount(envOf(gh, f), f, noMaterials);
    await flip();
    expect(input().checked).toBe(false);
    expect(root!.querySelector('.web-switch .check-line')!.textContent).toBe('Choose the source materials before turning the website on.');
    expect(gh.seen.filter((s) => s.method === 'PUT')).toEqual([]);
  });

  it('is disabled, never seeding, when the file could not be read or the names are not confirmed', async () => {
    const gh = github();
    const f = new Unreadable({ [`${ORG}/.github/dsl-course.yml`]: META });
    await mount(envOf(gh, f), f);
    expect(input().disabled).toBe(true);
    expect(word()).toBe('Could not read the website settings.');
    await flip();
    expect(gh.seen.filter((s) => s.method === 'PUT')).toEqual([]);
    render(null, root!);
    const g = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META });
    await mount(envOf(gh, g), g, loaded, false);
    expect(input().disabled).toBe(true);
    expect(word()).toBe('Not yet: the console has not confirmed this course uses the current names.');
  });
});

describe('selects list the inherited value once', () => {
  const labels = (options: { label: string }[] | undefined) => (options ?? []).map((o) => o.label);
  const LIC = POLICY.licences[0].name;
  it('as "X (default)" first, with value "", and not again', () => {
    const t = courseDefaultTiers();
    expect(labels(t.formats.options)).toEqual(['Jupyter notebook (default)', 'Python files', 'R Markdown', 'Quarto', 'LaTeX', 'No starter file']);
    expect(labels(t.submit_via.options)).toEqual(['Their own repo (default)', 'A shared drop box', 'Elsewhere']);
    expect(labels(t.visibility.options)).toEqual(['Private (default)', 'Public', 'Student\'s choice']);
    const semester = runTier('team_formation', { value: 'assigned', source: 'course' });
    expect(labels(semester.options)).toEqual(['You assign them (default)', 'Students form their own']);
    expect(semester.options![0].value).toBe('');
    for (const tier of [COURSE_FACTS.licence, t.formats, t.submit_via, t.visibility, t.team_formation])
      expect(labels(tier.options).some((l) => l.startsWith('Default ('))).toBe(false);
  });
  it('keeps a pin equal to the default as "X (set here)", selected', () => {
    const t = courseDefaultTiers({ visibility: 'private', formats: 'ipynb' });
    expect(labels(t.visibility.options)).toEqual(['Private (default)', 'Private (set here)', 'Public', 'Student\'s choice']);
    expect(t.visibility.options![1].value).toBe('private');
    expect(labels(t.formats.options).slice(0, 2)).toEqual(['Jupyter notebook (default)', 'Jupyter notebook (set here)']);
    const out = html(<Field id="v" k="visibility" t={t.visibility} value="private" values={{ visibility: 'private' }} set={() => {}} />);
    expect(out).toContain('<option value="private" selected>Private (set here)</option>');
  });
  it('lets a course fix the institution’s licence', () => {
    expect(labels(COURSE_FACTS.licence.options).slice(0, 2)).toEqual([`${LIC} (default)`, `${LIC}, fixed for this course`]);
    expect(COURSE_FACTS.licence.options!.map((o) => o.value).slice(0, 2)).toEqual(['', LIC]);
  });
  it('an assignment’s own row offers every value, with no inherit entry', () => {
    const own = runTier('visibility', { value: 'private', source: 'semester' }, { override: true });
    expect(labels(own.options)).toEqual(['Choose…', 'Private', 'Public', 'Student\'s choice']);
    expect(own.options![0].off).toBeTruthy();
    expect(labels(own.options).some((l) => l.includes('(default)'))).toBe(false);
  });
});

describe('copy', () => {
  it('says "Edit the file directly" on every link and in the conflict message', () => {
    expect(html(<EditFile org="o" repo="r" path="p" />)).toContain('Edit the file directly');
    expect(CONFLICT).toContain('or use Edit the file directly.');
  });
  it('explains the linked file types with a ?', () => {
    const f = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META });
    const host = document.body.appendChild(document.createElement('div'));
    render(<DetailsScreen course={course} loaded={loaded} cohortStates={{}} files={f} now={0} />, host);
    const q = host.querySelector('button[aria-label="About linked file types"]');
    expect(q?.textContent).toBe('?');
    expect(q?.closest('label')?.getAttribute('for')).toBe('cdk');
    render(null, host);
    host.remove();
  });
  it('says what the linked file types do, and that the public website is not affected', () => {
    const f = new StaticFiles({ [`${ORG}/.github/dsl-course.yml`]: META });
    const out = html(<DetailsScreen course={course} loaded={loaded} cohortStates={{}} files={f} now={0} />);
    expect(out).toContain('File types linked on each semester’s student site');
    expect(out).toContain('Other files are still released: students reach them through the repo.');
    expect(out).toContain('The public website is not affected.');
    expect(out).toContain('Released files of these types get a direct link on every semester’s student site.');
  });
});
