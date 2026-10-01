// @vitest-environment happy-dom
// All courses shows the institution's catalogue (decision 0021 rule 5): orgs.yml, then each
// other course's public dsl-course.yml and semesters.yml; one failed org is its name only;
// courses the person has no role in are greyed and not links (decision 0030): three sections,
// DSL courses, This semester, Past semesters, each with its own My courses checkbox, on by
// default and remembered per login per section.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import { endedNow, loadCatalogue, parseOrgs, pool, runningNow, termRank } from '../src/model/catalogue';
import type { Loaded } from '../src/model/status';
import type { Course, Semester } from '../src/model/discovery';
import { myCoursesOnly, saveMyCoursesOnly, type PrefStore } from '../src/model/prefs';
import { HomeScreen } from '../src/screens/Home';
import { StaticFiles } from '../src/model/files';
import { CONFIG_REPO, INSTRUCTORS_FILE } from '../src/model/names';
import { FakeGitHub, fileBody, json } from './fake';

const NOW = Date.parse('2026-10-01T12:00:00Z');
const MINE = 'hertie-dsl-demo-course-e1234';
const NLP = 'hertie-nlp-e1282';
const BROKEN = 'hertie-maths-data-science-C23';
const ORGS_URL = '/repos/hertie-data-science-lab/dsl-teaching-toolkit/contents/orgs.yml?ref=main';
const ORGS = `# comment\ncourse_orgs:\n  - ${MINE}\n  - ${NLP}\n  - ${BROKEN}\n`;
const user = { login: 'octo', id: 1, name: 'Octo Cat', email: null, avatar_url: '' };
const course: Course = { org: 'Hertie-DSL-Demo-Course-E1234', name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [], meta: null };

function catalogueFake(orgs: string | null = ORGS) {
  const f = new FakeGitHub();
  if (orgs !== null) f.on('GET', ORGS_URL, fileBody('orgs.yml', orgs));
  return f
    .on('GET', `/repos/${NLP}/.github/contents/dsl-course.yml`, fileBody('dsl-course.yml', 'course_name: Natural Language Processing\ncourse_code: E1282\n'))
    .on('GET', `/repos/${NLP}/.github/contents/semesters.yml`, fileBody('semesters.yml', 'semesters:\n- hertie-nlp-f2025\n- hertie-nlp-f2026\n- hertie-nlp-s2027\n- hertie-nlp-u2026\n- hertie-nlp-w2026\n- hertie-nlp-s2026\n- hertie-nlp-f2024\n'))
    .on('GET', '/repos/hertie-nlp-f2025/.github', { name: '.github', archived: true })
    .on('GET', '/repos/hertie-nlp-f2026/.github', { name: '.github', archived: false })
    .on('GET', '/repos/hertie-nlp-f2026/.github/contents/.system/student-status.json', fileBody('student-status.json', JSON.stringify({ archive_datetime: '2027-01-31T00:00:00Z' })))
    // Its .github refused (403) or failed (500): kept, but not counted as running.
    .on('GET', '/repos/hertie-nlp-u2026/.github', () => json({ message: 'Forbidden' }, 403))
    .on('GET', '/repos/hertie-nlp-w2026/.github', () => json({ message: 'Server Error' }, 500))
    // Running, but its student status is not JSON / carries a non-string date: no end.
    .on('GET', '/repos/hertie-nlp-s2026/.github', { name: '.github', archived: false })
    .on('GET', '/repos/hertie-nlp-s2026/.github/contents/.system/student-status.json', fileBody('student-status.json', '{ not json'))
    .on('GET', '/repos/hertie-nlp-f2024/.github', { name: '.github', archived: false })
    .on('GET', '/repos/hertie-nlp-f2024/.github/contents/.system/student-status.json', fileBody('student-status.json', JSON.stringify({ archive_datetime: 20250131, term_label: 'Fall 2024' })))
    .on('GET', `/repos/${BROKEN}/.github/contents/dsl-course.yml`, () => json({ message: 'Server Error' }, 500));
  // hertie-nlp-s2027 has no .github yet: it is being set up, so it is not in the catalogue.
}
const client = (f: FakeGitHub) => new GitHubClient({ token: () => 't', fetch: f.fetch });

describe('the request pool', () => {
  it('never runs more than its size, even when a newcomer arrives as a slot frees', async () => {
    const run = pool(1);
    let active = 0;
    let most = 0;
    const gates: (() => void)[] = [];
    const started: Promise<void>[] = [];
    const task = () => {
      most = Math.max(most, ++active);
      const p = new Promise<void>((go) => gates.push(() => (active--, go())));
      started.push(p);
      return p;
    };
    const done = [run(task), run(task)]; // the second waits for the slot
    // A newcomer that runs right after the first task ends, before the waiter wakes.
    done.push(started[0].then(() => run(task)));
    gates[0]();
    for (let n = 1; n < 3; n++) {
      while (gates.length <= n) await new Promise((r) => setTimeout(r, 0));
      gates[n]();
    }
    await Promise.all(done);
    expect(most).toBe(1);
  });
});

describe('parsing orgs.yml', () => {
  it('reads course_orgs as spelt, in order', () => {
    expect(parseOrgs(ORGS)).toEqual([MINE, NLP, BROKEN]);
  });
  it('refuses any other shape', () => {
    expect(() => parseOrgs('orgs:\n  - a\n')).toThrow();
    expect(() => parseOrgs('course_orgs:\n  - "not an org"\n')).toThrow();
    expect(() => parseOrgs('course_orgs: [')).toThrow();
  });
});

describe('loading the catalogue', () => {
  it('keeps the person’s courses, reads the others, and shows a failed org by its name', async () => {
    const f = catalogueFake();
    const seen: number[] = [];
    const list = await loadCatalogue(client(f), [course], (l) => seen.push(l.length));
    const byOrg = Object.fromEntries(list.map((c) => [c.org, c]));
    expect(byOrg[course.org]).toMatchObject({ mine: true, name: 'Machine Learning', course });
    expect(byOrg[NLP]).toEqual({
      org: NLP,
      name: 'Natural Language Processing',
      code: 'E1282',
      mine: false,
      semesters: [
        { org: 'hertie-nlp-f2025', termLabel: 'Fall 2025', archived: true },
        { org: 'hertie-nlp-f2026', termLabel: 'Fall 2026', archived: false, end: '2027-01-31T00:00:00Z' },
        { org: 'hertie-nlp-u2026', termLabel: 'Summer 2026' },
        { org: 'hertie-nlp-w2026', termLabel: 'Winter 2026' },
        { org: 'hertie-nlp-s2026', termLabel: 'Spring 2026', archived: false },
        { org: 'hertie-nlp-f2024', termLabel: 'Fall 2024', archived: false },
      ],
    });
    expect(byOrg[BROKEN]).toEqual({ org: BROKEN, name: BROKEN, code: '', semesters: [], mine: false });
    expect(list).toHaveLength(3);
    expect(seen).toEqual([1, 2, 3]);
    // The person's own course is not read again (orgs.yml's case differs from GitHub's).
    expect(f.seen.some((r) => r.url.toLowerCase().includes(`/repos/${MINE}/`))).toBe(false);
  });

  it('keeps a course’s name when its semester list cannot be read', async () => {
    const f = catalogueFake();
    // The fake's first matching route wins, so the failing read goes in front of it.
    const fetch = (u: string, i?: RequestInit) => (u.endsWith(`/repos/${NLP}/.github/contents/semesters.yml`) ? Promise.resolve(json({ message: 'Server Error' }, 500)) : f.fetch(u, i));
    const list = await loadCatalogue(new GitHubClient({ token: () => 't', fetch }), [course]);
    expect(list.find((c) => c.org === NLP)).toEqual({ org: NLP, name: 'Natural Language Processing', code: 'E1282', semesters: [], mine: false });
  });

  it('keeps at most six reads in flight', async () => {
    const f = catalogueFake();
    let active = 0, most = 0;
    const slow = async (u: string, i?: RequestInit) => {
      active++;
      most = Math.max(most, active);
      await new Promise((r) => setTimeout(r, 1));
      try {
        return await f.fetch(u, i);
      } finally {
        active--;
      }
    };
    await loadCatalogue(new GitHubClient({ token: () => 't', fetch: slow }), [course]);
    expect(most).toBeLessThanOrEqual(6);
    expect(most).toBeGreaterThan(1);
  });

  it('reads each org once per page load', async () => {
    const f = catalogueFake();
    const c = client(f);
    await loadCatalogue(c, [course]);
    const n = f.seen.length;
    await loadCatalogue(c, [course]);
    expect(f.seen.length).toBe(n);
  });

  it('shows an org running another toolkit by its name, and keeps the rest', async () => {
    const other = 'hertie-intro-to-data-science-c11';
    const f = new FakeGitHub()
      .on('GET', ORGS_URL, fileBody('orgs.yml', `course_orgs:\n  - ${other}\n  - ${NLP}\n`))
      .on('GET', `/repos/${other}/.github/contents/dsl-course.yml`, fileBody('dsl-course.yml', '- not\n- a mapping\n'))
      .on('GET', `/repos/${other}/.github/contents/semesters.yml`, fileBody('semesters.yml', 'semesters: {'))
      .on('GET', `/repos/${NLP}/.github/contents/dsl-course.yml`, fileBody('dsl-course.yml', 'course_name: Natural Language Processing\n'));
    const list = await loadCatalogue(client(f), [course]);
    expect(list.map((c) => c.name)).toEqual(['Machine Learning', other, 'Natural Language Processing']);
  });

  it('reads orgs.yml at the ref the console was built from, so a branch build sees its own registry', async () => {
    // The demo's console is built from the feature branch, whose orgs.yml main does not have yet.
    vi.stubEnv('VITE_TOOLKIT_REF', 'feature/instructor-console');
    vi.resetModules();
    try {
      const m = await import('../src/model/catalogue');
      expect(m.REGISTRY_REF).toBe('feature/instructor-console');
      const f = new FakeGitHub().on('GET', ORGS_URL.replace('ref=main', 'ref=feature%2Finstructor-console'), fileBody('orgs.yml', `course_orgs:\n  - ${NLP}\n`))
        .on('GET', `/repos/${NLP}/.github/contents/dsl-course.yml`, fileBody('dsl-course.yml', 'course_name: Natural Language Processing\n'));
      const list = await m.loadCatalogue(client(f), [course]);
      expect(list.map((c) => c.name)).toEqual(['Machine Learning', 'Natural Language Processing']);
    } finally {
      vi.unstubAllEnvs();
      vi.resetModules();
    }
  });

  it('fails as a whole only when orgs.yml cannot be read', async () => {
    await expect(loadCatalogue(client(catalogueFake(null)), [course])).rejects.toThrow();
  });

  it('counts a semester as running while it is not archived and its end is not past', () => {
    expect(runningNow({ org: 'a', termLabel: 'Fall 2026', archived: false, end: '2027-01-31T00:00:00Z' }, NOW)).toBe(true);
    expect(runningNow({ org: 'a', termLabel: 'Fall 2026', archived: false }, NOW)).toBe(true);
    expect(runningNow({ org: 'a', termLabel: 'Fall 2025', archived: false, end: '2026-01-31T00:00:00Z' }, NOW)).toBe(false);
    expect(runningNow({ org: 'a', termLabel: 'Fall 2025', archived: true }, NOW)).toBe(false);
    expect(runningNow({ org: 'a', termLabel: 'Fall 2025' }, NOW)).toBe(false);
  });

  it('gives a semester with no end an approximate one from its key, so it does not run for ever', () => {
    // Fall to 1 February, spring to 1 August, summer to 1 October, winter to 1 April.
    expect(runningNow({ org: 'x-f2024', termLabel: 'Fall 2024', archived: false }, NOW)).toBe(false);
    expect(endedNow({ org: 'x-f2024', termLabel: 'Fall 2024', archived: false }, NOW)).toBe(true);
    expect(runningNow({ org: 'x-f2026', termLabel: 'Fall 2026', archived: false }, NOW)).toBe(true);
    expect(endedNow({ org: 'x-s2026', termLabel: 'Spring 2026', archived: false }, NOW)).toBe(true);
    expect(endedNow({ org: 'x-u2026', termLabel: 'Summer 2026', archived: false }, Date.parse('2026-09-30T12:00:00Z'))).toBe(false);
    expect(runningNow({ org: 'x-w2026', termLabel: 'Winter 2026', archived: false }, Date.parse('2027-03-31T12:00:00Z'))).toBe(true);
    // Unknown whether archived (its .github unreadable): the same approximate end.
    expect(runningNow({ org: 'x-w2026', termLabel: 'Winter 2026' }, NOW)).toBe(true);
    expect(endedNow({ org: 'x-u2026', termLabel: 'Summer 2026' }, NOW)).toBe(true);
    // A read end wins over the approximate one.
    expect(runningNow({ org: 'x-f2024', termLabel: 'Fall 2024', archived: false, end: '2027-01-01' }, NOW)).toBe(true);
  });

  it('ranks semesters by their key: spring, summer, fall, winter within a year', () => {
    expect(['x-f2026', 'x-s2026', 'x-w2025', 'x-u2026', 'nokey'].sort((a, b) => termRank(b) - termRank(a))).toEqual(['x-f2026', 'x-u2026', 'x-s2026', 'x-w2025', 'nokey']);
  });
});

describe('the My courses pref', () => {
  const memory = () => {
    const m = new Map<string, string>();
    return { m, store: { getItem: (k: string) => m.get(k) ?? null, setItem: (k: string, v: string) => void m.set(k, v) } as PrefStore };
  };
  it('is on by default, round-trips per login and per section, and survives a refusing store', () => {
    const { store } = memory();
    expect(myCoursesOnly('octo', 'courses', store)).toBe(true);
    saveMyCoursesOnly('octo', 'courses', false, store);
    expect(myCoursesOnly('octo', 'courses', store)).toBe(false);
    expect(myCoursesOnly('octo', 'now', store)).toBe(true);
    expect(myCoursesOnly('other', 'courses', store)).toBe(true);
    saveMyCoursesOnly('octo', 'courses', true, store);
    expect(myCoursesOnly('octo', 'courses', store)).toBe(true);
    const refusing: PrefStore = { getItem: () => { throw new Error('no'); }, setItem: () => { throw new Error('no'); } };
    expect(() => saveMyCoursesOnly('octo', 'past', false, refusing)).not.toThrow();
    expect(myCoursesOnly('octo', 'past', refusing)).toBe(true);
    expect(myCoursesOnly('octo', 'past', null)).toBe(true);
  });
  it('ignores the old page-wide choice and deletes it when first read', () => {
    const { m, store } = memory();
    store.removeItem = (k: string) => void m.delete(k);
    m.set('dsl-console-my-courses:octo', '0');
    for (const s of ['courses', 'now', 'past'] as const) expect(myCoursesOnly('octo', s, store)).toBe(true);
    expect(m.has('dsl-console-my-courses:octo')).toBe(false);
  });
});

// ------------------------------------------------------------------------ the page

let host: HTMLElement | null = null;
beforeEach(() => localStorage.clear());
afterEach(() => {
  if (host) render(null, host);
  host?.remove();
  host = null;
});

const flush = () => act(async () => { for (let i = 0; i < 10; i++) await new Promise((r) => setTimeout(r, 0)); });

function mount(f: FakeGitHub, { courses = [course], semesters = [], cohortStates = {}, files }: { courses?: Course[]; semesters?: Semester[]; cohortStates?: Record<string, Loaded>; files?: StaticFiles } = {}) {
  const env = { client: client(f), user, files } as unknown as Env;
  host = document.createElement('div');
  document.body.appendChild(host);
  act(() => render(<EnvCtx.Provider value={env}><HomeScreen courses={courses} semesters={semesters} cohortStates={cohortStates} now={NOW} user={user} /></EnvCtx.Provider>, host!));
  return host;
}
const greyed = (h: HTMLElement) => [...h.querySelectorAll('.cohort-card.off')];
const section = (h: HTMLElement, id: string) => h.querySelector(`[aria-labelledby="${id}"]`)!;

const names = (el: Element) => [...el.querySelectorAll('.cc-name')].map((n) => n.firstChild?.textContent);
// The first section's checkbox sits in the page head, beside New course.
const boxOf = (h: HTMLElement, id: string) => (id === 'h-courses' ? h.querySelector('.page-head') : section(h, id))!.querySelector<HTMLInputElement>('.my-only input')!;
const head = (h: HTMLElement, id: string) => section(h, id).querySelector('.section-head')!.textContent;

describe('All courses', () => {
  it('says what the page is', async () => {
    const h = mount(catalogueFake());
    await flush();
    expect(h.querySelector('.page-head .lede')!.textContent).toBe('Every course the lab runs, ordered by what needs your attention.');
    // The first section's My courses sits left of New course; the others in their section heads.
    expect([...h.querySelectorAll('.page-head .actions > *')].map((e) => e.textContent)).toEqual(['My courses', 'New course']);
    expect([...h.querySelectorAll('.section-head .my-only')].length).toBe(2);
    expect(section(h, 'h-courses').querySelector('.my-only')).toBeNull();
  });

  it('shows only the person’s rows by default, and says how many others each section hides', async () => {
    const h = mount(catalogueFake());
    await flush();
    expect(greyed(h)).toEqual([]);
    for (const id of ['h-courses', 'h-live', 'h-past']) expect(boxOf(h, id).checked).toBe(true);
    expect(head(h, 'h-courses')).toContain('DSL courses (+2 others)');
    expect(head(h, 'h-live')).toContain('This semester (+2 others)');
    expect(head(h, 'h-past')).toContain('Past semesters (+4 others)');
    expect(section(h, 'h-live').textContent).toContain('None of your semesters is running.');
    expect(h.querySelector('a.cohort-card')?.getAttribute('href')).toBe(`?course=${course.org}#course`);
  });

  it('shows the catalogue greyed and not as links once a section’s My courses is off', async () => {
    const h = mount(catalogueFake());
    await flush();
    await act(async () => boxOf(h, 'h-courses').click());
    expect(h.textContent).not.toContain('Reading the catalogue');
    const courses = section(h, 'h-courses');
    expect(names(courses)).toEqual(['Machine Learning', BROKEN, 'Natural Language Processing']);
    expect(head(h, 'h-courses')).not.toContain('others');
    for (const g of greyed(h)) {
      expect(g.tagName).toBe('DIV');
      expect(g.closest('a')).toBeNull();
      expect(g.getAttribute('aria-disabled')).toBe('true');
      expect(g.querySelector('.cc-name span')!.textContent).toBe('Not one of your courses');
    }
    // The other sections keep their own choice.
    expect(greyed(section(h, 'h-live') as HTMLElement)).toEqual([]);
    await act(async () => boxOf(h, 'h-live').click());
    const now = section(h, 'h-live');
    // By course, newest first; Winter 2026's .github is unreadable, so its key gives its end.
    expect(names(now)).toEqual(['Natural Language Processing, Winter 2026', 'Natural Language Processing, Fall 2026']);
    await act(async () => boxOf(h, 'h-past').click());
    // Fall 2024 and Spring 2026 have no end date: theirs comes from the key, so they are past.
    expect(names(section(h, 'h-past'))).toEqual(['Natural Language Processing, Summer 2026', 'Natural Language Processing, Spring 2026', 'Natural Language Processing, Fall 2025', 'Natural Language Processing, Fall 2024']);
  });

  it('remembers each section’s choice for this login', async () => {
    const h = mount(catalogueFake());
    await flush();
    await act(async () => boxOf(h, 'h-live').click());
    expect(myCoursesOnly(user.login, 'now')).toBe(false);
    expect(myCoursesOnly(user.login, 'courses')).toBe(true);
    render(null, h);
    const again = mount(catalogueFake());
    await flush();
    expect(boxOf(again, 'h-live').checked).toBe(false);
    expect(boxOf(again, 'h-courses').checked).toBe(true);
    expect(greyed(section(again, 'h-courses') as HTMLElement)).toEqual([]);
  });

  it('lists past semesters newest first, ten at a time, from the first ten again when My courses flips', async () => {
    const OLD = 'hertie-old-e1000';
    const terms = Array.from({ length: 12 }, (_, i) => `hertie-old-${i % 2 ? 'f' : 's'}${2014 + Math.floor(i / 2)}`);
    const f = new FakeGitHub()
      .on('GET', ORGS_URL, fileBody('orgs.yml', `course_orgs:\n  - ${OLD}\n`))
      .on('GET', `/repos/${OLD}/.github/contents/dsl-course.yml`, fileBody('dsl-course.yml', 'course_name: Old Course\n'))
      .on('GET', `/repos/${OLD}/.github/contents/semesters.yml`, fileBody('semesters.yml', `semesters:\n${terms.map((t) => `- ${t}\n`).join('')}`));
    for (const t of terms) f.on('GET', `/repos/${t}/.github`, { name: '.github', archived: true });
    const h = mount(f);
    await flush();
    await act(async () => boxOf(h, 'h-past').click());
    const past = section(h, 'h-past');
    const shown = names(past);
    expect(shown).toHaveLength(10);
    expect(shown[0]).toBe('Old Course, Fall 2019');
    expect(shown[1]).toBe('Old Course, Spring 2019');
    expect(shown[9]).toBe('Old Course, Spring 2015');
    await act(async () => past.querySelector<HTMLButtonElement>('button')!.click());
    expect(names(past)).toHaveLength(12);
    expect(names(past).at(-1)).toBe('Old Course, Spring 2014');
    expect(past.querySelector('button')).toBeNull();
    await act(async () => boxOf(h, 'h-past').click());
    await act(async () => boxOf(h, 'h-past').click());
    expect(names(section(h, 'h-past'))).toHaveLength(10);
  });

  it('puts the person’s own past semester ahead of another course’s from the same semester', async () => {
    const mineOld = { org: 'hertie-dsl-demo-f2025', term: 'f2025', termLabel: 'Fall 2025' };
    const done = { kind: 'ready', status: { semester: { live: false, archive_date: null, timezone: 'Europe/Berlin' }, problems: [], this_week: [] } } as unknown as Loaded;
    saveMyCoursesOnly(user.login, 'past', false);
    const h = mount(catalogueFake(), { courses: [{ ...course, cohorts: [mineOld] }], cohortStates: { [mineOld.org]: done } });
    await flush();
    const rows = [...section(h, 'h-past').querySelectorAll('.cohort-card')];
    const own = rows.findIndex((r) => r.getAttribute('href') === `?cohort=${mineOld.org}#dashboard`);
    const theirs = rows.findIndex((r) => r.textContent?.startsWith('Natural Language Processing, Fall 2025'));
    expect(own).toBeGreaterThan(-1);
    expect(theirs).toBe(own + 1);
  });

  it('puts another course’s semester that is not archived but whose end is past under Past semesters', async () => {
    const OTHER = 'hertie-geo-e2000';
    const f = new FakeGitHub()
      .on('GET', ORGS_URL, fileBody('orgs.yml', `course_orgs:\n  - ${OTHER}\n`))
      .on('GET', `/repos/${OTHER}/.github/contents/dsl-course.yml`, fileBody('dsl-course.yml', 'course_name: Geography\n'))
      .on('GET', `/repos/${OTHER}/.github/contents/semesters.yml`, fileBody('semesters.yml', 'semesters:\n- hertie-geo-f2026\n'))
      .on('GET', '/repos/hertie-geo-f2026/.github', { name: '.github', archived: false })
      .on('GET', '/repos/hertie-geo-f2026/.github/contents/.system/student-status.json', fileBody('student-status.json', JSON.stringify({ archive_datetime: '2026-09-01T00:00:00Z' })));
    saveMyCoursesOnly(user.login, 'past', false);
    saveMyCoursesOnly(user.login, 'now', false);
    const h = mount(f);
    await flush();
    expect(names(section(h, 'h-past'))).toEqual(['Geography, Fall 2026']);
    expect(section(h, 'h-live').textContent).not.toContain('Geography');
  });

  it('never greys a semester the person studies in: it shows under Your semesters only', async () => {
    const studied: Semester = { org: 'hertie-nlp-f2026', term: 'f2026', termLabel: 'Fall 2026', courseOrg: NLP, courseName: 'Natural Language Processing', archived: false, role: 'student' };
    saveMyCoursesOnly(user.login, 'now', false);
    const h = mount(catalogueFake(), { semesters: [studied] });
    await flush();
    expect(names(section(h, 'h-live'))).toEqual(['Natural Language Processing, Winter 2026']);
    await act(async () => boxOf(h, 'h-live').click());
    expect(head(h, 'h-live')).toContain('This semester (+1 other)');
    expect(section(h, 'h-semesters').querySelector(`a[href="?semester=${studied.org}#week"]`)).not.toBeNull();
  });

  it('shows no My courses switch while every course is yours, and reads nothing for a person with no course', async () => {
    const h = mount(catalogueFake('course_orgs:\n  - hertie-dsl-demo-course-e1234\n'));
    await flush();
    expect(h.querySelector('.my-only')).toBeNull();
    render(null, h);
    const f = catalogueFake();
    const env = { client: client(f), user } as unknown as Env;
    act(() => render(<EnvCtx.Provider value={env}><HomeScreen courses={[]} semesters={[]} cohortStates={{}} now={NOW} user={user} /></EnvCtx.Provider>, h));
    await flush();
    expect(f.seen).toEqual([]);
  });

  it('explains what a course org is, and puts no Open the course on the cards', async () => {
    const h = mount(catalogueFake());
    await flush();
    expect(h.querySelector('h1 .hint-pop')!.textContent).toContain('A course org acts as a persistent staging area to hold your in-development materials and assignment templates for every semester. Each semester runs in its own org, which students join and to which materials are released, assignments handed out and marks returned.');
    expect(h.textContent).not.toContain('Open the course');
  });

  it('says so when the catalogue cannot be read, and still shows the person’s courses', async () => {
    const h = mount(catalogueFake(null));
    await flush();
    expect(h.textContent).toContain('The catalogue could not be read.');
    expect(greyed(h)).toEqual([]);
    expect(h.querySelector('a.cohort-card')?.getAttribute('href')).toBe(`?course=${course.org}#course`);
  });

  it('puts the person’s own semester that ended but is not archived under Past semesters', async () => {
    const ended = { org: 'hertie-dsl-demo-f2025', term: 'f2025', termLabel: 'Fall 2025' };
    const st = { kind: 'ready', status: { semester: { live: true, ended: true, week: 15, weeks: 15, archive_date: null, timezone: 'Europe/Berlin' }, problems: [], this_week: [] } } as unknown as Loaded;
    const h = mount(catalogueFake(), { courses: [{ ...course, cohorts: [ended] }], cohortStates: { [ended.org]: st } });
    await flush();
    expect(section(h, 'h-live').querySelector(`a[href="?cohort=${ended.org}#dashboard"]`)).toBeNull();
    const own = section(h, 'h-past').querySelector(`a[href="?cohort=${ended.org}#dashboard"]`)!;
    expect(own.textContent).toContain('Ended, not archived');
    expect(section(h, 'h-past').textContent).not.toContain('You have no past semesters yet.');
  });

  it('judges a semester with no status by its key: Fall 2025 has ended by October 2026', async () => {
    const old = { org: 'hertie-dsl-demo-f2025', term: 'f2025', termLabel: 'Fall 2025' };
    const h = mount(catalogueFake(), { courses: [{ ...course, cohorts: [old] }] });
    await flush();
    expect(section(h, 'h-past').querySelector(`a[href="?cohort=${old.org}#dashboard"]`)).not.toBeNull();
  });

  it('always shows Past semesters, saying when the person has none, with the others greyed once My courses is off', async () => {
    const h = mount(catalogueFake());
    await flush();
    expect(section(h, 'h-past').textContent).toContain('You have no past semesters yet.');
    await act(async () => boxOf(h, 'h-past').click());
    expect(section(h, 'h-past').textContent).toContain('You have no past semesters yet.');
    expect(greyed(section(h, 'h-past') as HTMLElement)).toHaveLength(4);
    render(null, h);
    // Every course is the person's: no others, no checkbox, the section still there.
    const alone = mount(catalogueFake('course_orgs:\n  - hertie-dsl-demo-course-e1234\n'));
    await flush();
    expect(section(alone, 'h-past').querySelector('.my-only')).toBeNull();
    expect(section(alone, 'h-past').querySelector('.footnote')!.textContent).toBe('You have no past semesters yet.');
  });

  describe('the role on a course card', () => {
    const sem = { org: 'hertie-dsl-demo-f2026', term: 'f2026', termLabel: 'Fall 2026' };
    const yml = (role: string) => new StaticFiles({ [`${sem.org}/${CONFIG_REPO}/${INSTRUCTORS_FILE}`]: `instructors:\n  - github_handle: Octo\n    name: Octo Cat\n    role: ${role}\n` });
    const sub = (h: HTMLElement) => section(h, 'h-courses').querySelector('.cc-name span')!.textContent;
    it('is course admin from dsl-course.yml', async () => {
      const h = mount(catalogueFake(), { courses: [{ ...course, admins: ['octo'], cohorts: [sem] }], files: yml('teaching_assistant') });
      await flush();
      expect(sub(h)).toBe('E1234; you are a course admin');
    });
    it('is the role in the newest running semester’s instructors.yml otherwise', async () => {
      const h = mount(catalogueFake(), { courses: [{ ...course, cohorts: [sem] }], files: yml('instructor') });
      await flush();
      expect(sub(h)).toBe('E1234; you are an instructor');
      render(null, h);
      const ta = mount(catalogueFake(), { courses: [{ ...course, cohorts: [sem] }], files: yml('teaching_assistant') });
      await flush();
      expect(sub(ta)).toBe('E1234; you are a teaching assistant');
    });
    it('says the person teaches on it when no running semester names their role', async () => {
      const h = mount(catalogueFake(), { courses: [{ ...course, cohorts: [sem] }], files: new StaticFiles() });
      await flush();
      expect(sub(h)).toBe('E1234; you teach on this course');
    });
  });

  it('says it is reading the catalogue only while DSL courses shows the others', async () => {
    saveMyCoursesOnly(user.login, 'courses', false);
    const h = mount(catalogueFake());
    expect(h.textContent).toContain('Reading the catalogue…');
    await flush();
    expect(h.textContent).not.toContain('Reading the catalogue');
  });
});
