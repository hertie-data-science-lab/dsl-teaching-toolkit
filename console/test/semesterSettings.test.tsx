// @vitest-environment happy-dom
// Decision 0043 rule 5, clicked through against a fake GitHub: the Dashboard's Semester settings
// (one line per setting, one modal of three folds, one commit per file), the session date a
// released TBC entry offers on its release page, and the to-do that points there.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { parse } from 'yaml';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient, decodeBase64 } from '../src/github/client';
import type { Course } from '../src/model/discovery';
import { StatusStore, type Loaded } from '../src/model/status';
import { tier } from '../src/model/readiness';
import type { Release, Status } from '../src/model/types';
import { DispatchAdapter } from '../src/ops/adapter';
import { OpsSession } from '../src/ops/session';
import { CohortScreen, semesterItems } from '../src/screens/Cohort';
import { ReleaseScreen } from '../src/screens/Schedule';
import { settingsLines } from '../src/screens/SemesterSettings';
import { withSessionDate } from '../src/screens/SessionDate';
import { StaticFiles } from './staticFiles';
import example from './fixtures/status.example.json';
import { FakeGitHub, type Seen } from './fake';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const ORG = 'hertie-dsl-demo-f2026';
const USER = { login: 'a-example', id: 1, name: 'A. Example', email: null, avatar_url: '' };
const cohort = { org: ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };
const STATUS = example as unknown as Status;
const LAB8: Release = { id: 'lab-8', when: null, kind: 'lab', number: 8, title: 'Transformers', state: 'released', source: { repo: 'course-materials-f2026', path: 'labs/08' }, dest: { repo: 'materials', path: 'labs/08' }, show_on_site: true, tbc: false };
const status: Status = {
  ...STATUS,
  releases: [...(STATUS.releases ?? []), LAB8],
  semester: {
    ...STATUS.semester!,
    todo: [...(STATUS.semester!.todo ?? []), { id: 'schedule:released_tbc:lab-8', kind: 'schedule', check: 'released_tbc', repo: 'semester-config', text: 'Lab 8 is released; its session date is still TBC.', screen: 'release', entry: 'lab-8', need: 'suggested', set_aside: false }],
  },
};
const loaded: Loaded = { kind: 'ready', status, sha: 's', stale: [] };
const SCHEDULE = [
  '# INSTRUCTOR-OWNED',
  'timezone: Europe/Berlin',
  'semester:',
  '  start: {type: start, event_datetime: 2026-09-07}',
  '  end: {type: end, event_datetime: 2026-12-18}',
  'labs:',
  '  lab-8:',
  '    session: 8',
  '    event_datetime: tbc',
  '    files:',
  '      - from_course_repo: course-materials-f2026',
  '        from_path: labs/08',
  '',
].join('\n');
const SEMESTER = '# INSTRUCTOR-OWNED\ndefaults:\n  late_window_days: 5\n  late_penalty_per_day: 5%\n';
const files = (semester = SEMESTER) => new StaticFiles({ [`${ORG}/semester-config/schedule.yml`]: SCHEDULE, [`${ORG}/semester-config/semester.yml`]: semester });

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(view: (f: StaticFiles) => preact.VNode, semester = SEMESTER) {
  const gh = new FakeGitHub().on('PUT', /\/contents\//, { content: { sha: 'b1' }, commit: { sha: 'c1' } }).on('GET', /\/check-runs/, { check_runs: [] });
  const client = new GitHubClient({ token: () => 't', fetch: gh.fetch });
  const f = files(semester);
  const env: Env = { client, user: USER, ops: new OpsSession(new DispatchAdapter(client, () => USER.login)), statuses: new StatusStore(client), files: f, pollMs: 0 };
  root = document.body.appendChild(document.createElement('div'));
  await act(() => render(<EnvCtx.Provider value={env}>{view(f)}</EnvCtx.Provider>, root!));
  return gh;
}
const props = (f: StaticFiles, over = {}) => ({ course, cohort, loaded, files: f, now: Date.parse('2026-11-03T10:00:00+01:00'), ...over });
const q = <T extends Element>(sel: string) => root!.querySelector<T>(sel)!;
const button = (label: string, within: Element = root!) => [...within.querySelectorAll('button')].find((b) => b.textContent?.trim() === label)!;
async function type(sel: string, value: string, ev = 'input') {
  await act(() => {
    const el = q<HTMLInputElement>(sel);
    el.value = value;
    el.dispatchEvent(new Event(ev, { bubbles: true }));
  });
}
async function settle(until: () => boolean) {
  for (let i = 0; i < 200 && !until(); i++) await act(() => new Promise((r) => setTimeout(r, 0)));
}
const puts = (gh: FakeGitHub, path: string) => gh.seen.filter((s) => s.method === 'PUT' && s.url.includes(`/contents/${path}`));
const body = (s: Seen) => decodeBase64((s.body as { content: string }).content);

describe('Semester settings', () => {
  it('words one line per setting, its value and where it comes from', () => {
    expect(settingsLines(status)).toEqual({
      assignments: expect.stringMatching(/^Defaults: /),
      releases: 'Send fixes to released files automatically: off (institution default).',
      dates: 'Semester dates: 7 Sep to 18 Dec.',
    });
  });

  it('sits at the bottom of the Dashboard; Edit opens the modal with every fold closed', async () => {
    await mount((f) => <CohortScreen {...props(f)} />);
    const panels = [...root!.querySelectorAll('.stack > section.panel')];
    const panel = panels[panels.length - 1];
    expect(panel.querySelector('h2')!.textContent).toBe('Semester settings');
    expect(panel.textContent).toContain('Send fixes to released files automatically: off (institution default).');
    await act(() => button('Edit', panel).click());
    const folds = [...q('[role="dialog"]').querySelectorAll<HTMLDetailsElement>('details.settings-fold')];
    expect(folds.map((d) => d.open)).toEqual([false, false, false]);
    expect(folds[1].querySelector('summary')!.textContent).toContain('off (institution default)');
  });

  it('opens on arrival from the #settings route (the Site page’s and New semester’s link)', async () => {
    await mount((f) => <CohortScreen {...props(f)} settings />);
    expect(q('[role="dialog"]').textContent).toContain('Semester settings');
  });

  it('asks for the timezone once: the defaults one, not schedule.yml’s', async () => {
    await mount((f) => <CohortScreen {...props(f)} settings />);
    const dialog = q('[role="dialog"]');
    expect(dialog.querySelector('#sd-timezone')).not.toBeNull();
    expect(dialog.querySelector('#e-tz')).toBeNull();
  });

  it('checks only what was edited: a fault in a fold left alone never blocks the save', async () => {
    // The file's window has no penalty (a fault), but only the dates are edited.
    const gh = await mount((f) => <CohortScreen {...props(f)} settings />, '# INSTRUCTOR-OWNED\ndefaults:\n  late_window_days: 5\n');
    await type('#e-te', '2026-12-20');
    await act(() => button('Save', q('[role="dialog"]')).click());
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    expect(puts(gh, 'schedule.yml')).toHaveLength(1);
    expect(puts(gh, 'semester.yml')).toHaveLength(0);
  });

  it('saves Assignments and Releases in one commit to semester.yml, and the dates in one to schedule.yml', async () => {
    const gh = await mount((f) => <CohortScreen {...props(f)} settings />);
    await type('#sd-late_window_days', '7');
    await type('#sr-autosync_released_edits', 'true', 'change');
    await type('#e-te', '2026-12-20');
    await act(() => button('Save', q('[role="dialog"]')).click());
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    const sem = puts(gh, 'semester.yml');
    expect(sem).toHaveLength(1);
    expect(parse(body(sem[0])).defaults).toEqual({ late_window_days: 7, late_penalty_per_day: '5%', autosync_released_edits: true });
    const sched = puts(gh, 'schedule.yml');
    expect(sched).toHaveLength(1);
    expect(parse(body(sched[0])).semester.end).toEqual({ type: 'end', event_datetime: '2026-12-20' });
  });

});

describe('the session date after a TBC release', () => {
  it('is offered on the released entry’s page and written into its schedule entry', async () => {
    const gh = await mount((f) => <ReleaseScreen {...props(f, { entry: 'lab-8' })} />);
    expect(root!.textContent).toContain('Session date: TBC');
    await type('#rd-sd-date', '2026-11-12');
    await type('#rd-sd-time', '14:00');
    await act(() => button('Set date').click());
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    const lab = parse(body(puts(gh, 'schedule.yml')[0])).labs['lab-8'];
    expect(String(lab.event_datetime)).toMatch(/^2026-11-12T14:00/);
    expect(lab.files[0].from_path).toBe('labs/08');
  });

  it('Leave as TBC puts it away', async () => {
    await mount((f) => <ReleaseScreen {...props(f, { entry: 'lab-8' })} />);
    await act(() => button('Leave as TBC').click());
    expect(root!.textContent).not.toContain('Session date: TBC');
  });

  it('dates the first slot only and clears TBC', () => {
    const slot = { date: '', time: '', location: 'B1', details: '' };
    const d = withSessionDate({ kind: 'entry', type: 'lab', id: 'lab-8', session: 8, number: '', title: '', details: '', slots: [slot], files: [], guide: '', show: true, provisional: false, tbc: true }, '2026-11-12', '');
    expect(d.tbc).toBe(false);
    expect(d.slots).toEqual([{ ...slot, date: '2026-11-12' }]);
  });

  it('is a suggested to-do on the Dashboard that opens that release', () => {
    const item = semesterItems(status, tier(status)).find((i) => i.id === 'schedule:released_tbc:lab-8')!;
    expect(item.label).toBe('Lab 8 is released; its session date is still TBC.');
    expect(item.need).toBe('suggested');
    expect(item.link).toEqual({ href: '#release-lab-8', label: 'Open' });
  });
});
