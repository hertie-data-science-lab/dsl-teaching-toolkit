// @vitest-environment happy-dom
// Decision 0043 rule 5, clicked through against a fake GitHub: the Semester defaults
// screen and its modal (three folds, each heading carrying its line; one commit per file), the session date a
// released TBC entry offers on its release page, and the to-do that points there.

import { render } from 'preact';
import { render as html } from 'preact-render-to-string';
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
import { movedHash, parseHash } from '../src/router';
import { CohortScreen, semesterItems } from '../src/screens/Cohort';
import { AssignmentsScreen } from '../src/screens/Assignments';
import { ReleaseScreen, ScheduleScreen, SemesterForm } from '../src/screens/Schedule';
import { SemesterDefaultsScreen, settingsLines } from '../src/screens/SemesterSettings';
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

describe('Semester defaults', () => {
  it('words one line per setting, its value and where it comes from', () => {
    expect(settingsLines(status)).toEqual({
      assignments: expect.stringMatching(/^Defaults: /),
      releases: 'Send fixes to released files automatically: off (institution default). Lands in materials (institution default).',
      dates: 'Semester dates: 7 Sep to 18 Dec.',
    });
  });

  it('is a full screen: a page heading with its hint, the three sections open, each heading carrying its line', async () => {
    await mount((f) => <SemesterDefaultsScreen {...props(f)} />);
    expect(root!.querySelector('[role="dialog"]')).toBeNull();
    const h = q('.page-head h2');
    expect(h.textContent).toMatch(/^Semester defaults/);
    expect(h.textContent).toContain('the semester’s wins');
    const folds = [...root!.querySelectorAll<HTMLDetailsElement>('details.settings-fold')];
    expect(folds.map((d) => [d.querySelector('summary span')!.textContent, d.open])).toEqual([['Assignments', true], ['Releases', true], ['Semester dates', true]]);
    expect(folds[1].querySelector('summary')!.textContent).toContain('off (institution default)');
  });

  it('puts a ? on the late window and the late penalty: defaults, waived or overridden when marking', async () => {
    await mount((f) => <SemesterDefaultsScreen {...props(f)} />);
    const hints = [...root!.querySelectorAll('[aria-label^="About "]')].map((b) => b.getAttribute('aria-label'));
    expect(hints).toEqual(expect.arrayContaining(['About Late window, days', 'About Late penalty per day']));
    expect(root!.textContent!.split('each late submission’s penalty can be switched off or replaced with your own percentage')).toHaveLength(3);
  });

  it('the Dashboard no longer carries the panel', async () => {
    await mount((f) => <CohortScreen {...props(f)} />);
    expect(root!.textContent).not.toContain('Semester defaults');
    expect(root!.textContent).not.toContain('Semester settings');
  });

  it('the old #settings route lands on the screen with Assignments focused', async () => {
    expect(parseHash('#settings')).toEqual({ screen: 'defaults', entry: 'assignments' });
    expect(movedHash('#settings')).toBe('#defaults-assignments');
    await mount((f) => <SemesterDefaultsScreen {...props(f, { entry: 'assignments' })} />);
    expect(document.activeElement).toBe(q('details.settings-fold summary'));
  });

  it('asks for the default destination in Releases, after the automatic fixes, not in Assignments (plan 2026-11-04, decision 7)', async () => {
    await mount((f) => <SemesterDefaultsScreen {...props(f)} />);
    const [asg, rel] = [...root!.querySelectorAll<HTMLDetailsElement>('details.settings-fold')];
    expect(asg.querySelector('[id$="materials_repo"]')).toBeNull();
    const ids = [...rel.querySelectorAll('select, [role="group"]')].map((e) => e.id);
    expect(ids).toEqual(['sr-autosync_released_edits', 'sr-into', 'sr-materials_repo']);
    expect(rel.textContent).toContain('Default destination');
  });

  it('asks for the timezone once: the defaults one (schedule.yml names none, decision 0039)', async () => {
    await mount((f) => <SemesterDefaultsScreen {...props(f)} />);
    expect(root!.querySelector('#sd-timezone')).not.toBeNull();
    expect(root!.querySelector('#e-tz')).toBeNull();
  });

  it('the Schedule screen’s dates sheet asks for no timezone', () => {
    const out = html(<SemesterForm d={{ kind: 'semester', start: '2026-09-07', end: '2026-12-18' }} set={() => {}} errors={{}} />);
    expect(out).not.toContain('e-tz');
    expect(out).not.toContain('Timezone');
  });

  it('checks only what was edited: a fault in a fold left alone never blocks the save', async () => {
    // The file's window has no penalty (a fault), but only the dates are edited.
    const gh = await mount((f) => <SemesterDefaultsScreen {...props(f)} />, '# INSTRUCTOR-OWNED\ndefaults:\n  late_window_days: 5\n');
    await type('#e-te', '2026-12-20');
    await act(() => button('Save').click());
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    expect(puts(gh, 'schedule.yml')).toHaveLength(1);
    expect(puts(gh, 'semester.yml')).toHaveLength(0);
  });

  it('saves Assignments and Releases in one commit to semester.yml, and the dates in one to schedule.yml', async () => {
    const gh = await mount((f) => <SemesterDefaultsScreen {...props(f)} />);
    await type('#sd-late_window_days', '7');
    await type('#sr-autosync_released_edits', 'true', 'change');
    await type('#e-te', '2026-12-20');
    await act(() => button('Save').click());
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    const sem = puts(gh, 'semester.yml');
    expect(sem).toHaveLength(1);
    expect(parse(body(sem[0])).defaults).toEqual({ late_window_days: 7, late_penalty_per_day: '5%', autosync_released_edits: true });
    const sched = puts(gh, 'schedule.yml');
    expect(sched).toHaveLength(1);
    expect(parse(body(sched[0])).semester.end).toEqual({ type: 'end', event_datetime: '2026-12-20' });
  });
});

describe('the Semester defaults modal: the page’s form, opened in context', () => {
  const sections = (within: Element) => [...within.querySelectorAll<HTMLDetailsElement>('details.settings-fold')].map((d) => [d.querySelector('summary span')!.textContent, d.open]);
  const fields = (within: Element) => [...within.querySelectorAll('input, select, textarea')].map((e) => e.id).filter(Boolean);
  const dialog = () => q('[role="dialog"]');

  it('renders the same sections and fields as the full page, only the one it opened at open', async () => {
    await mount((f) => <SemesterDefaultsScreen {...props(f)} />);
    const page = fields(root!);
    render(null, root!);
    await mount((f) => <ScheduleScreen {...props(f)} />);
    await act(() => button('Semester defaults').click());
    expect(q('#modal-title').textContent).toBe('Semester defaults');
    expect(fields(dialog())).toEqual(page);
    expect(sections(dialog())).toEqual([['Assignments', false], ['Releases', true], ['Semester dates', false]]);
    expect(document.activeElement).toBe(dialog().querySelectorAll('details.settings-fold summary')[1]);
    expect(dialog().querySelector('a[href="#defaults-releases"]')!.textContent).toBe('Open the full page');
  });

  it('opens at Assignments from the Assignments page, the other two folded but openable', async () => {
    await mount((f) => <AssignmentsScreen {...props(f)} />);
    await act(() => button('Semester defaults').click());
    expect(sections(dialog())).toEqual([['Assignments', true], ['Releases', false], ['Semester dates', false]]);
    const dates = dialog().querySelectorAll<HTMLDetailsElement>('details.settings-fold')[2];
    await act(() => { dates.open = true; });
    expect(dates.open).toBe(true);
    expect(dates.querySelector('#e-te')).not.toBeNull();
    expect(dialog().querySelector('a[href="#defaults-assignments"]')).not.toBeNull();
  });

  it('keeps an unsaved edit across closing and reopening, and saves one commit per file', async () => {
    const gh = await mount((f) => <ScheduleScreen {...props(f)} />);
    await act(() => button('Semester defaults').click());
    await type('#sd-late_window_days', '7');
    await act(() => dialog().querySelector<HTMLButtonElement>('button[aria-label="Close"]')!.click());
    expect(root!.querySelector('[role="dialog"]')).toBeNull();
    await act(() => button('Semester defaults').click());
    expect(q<HTMLInputElement>('#sd-late_window_days').value).toBe('7');
    await type('#e-te', '2026-12-20');
    await act(() => button('Save', dialog()).click());
    await settle(() => puts(gh, 'schedule.yml').length > 0);
    expect(puts(gh, 'semester.yml')).toHaveLength(1);
    expect(parse(body(puts(gh, 'semester.yml')[0])).defaults.late_window_days).toBe(7);
    expect(puts(gh, 'schedule.yml')).toHaveLength(1);
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
