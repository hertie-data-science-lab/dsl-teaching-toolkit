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
import { loadCatalogue, parseOrgs, pool, runningNow } from '../src/model/catalogue';
import type { Course } from '../src/model/discovery';
import { myCoursesOnly, saveMyCoursesOnly, type PrefStore } from '../src/model/prefs';
import { HomeScreen } from '../src/screens/Home';
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
  it('reads the old page-wide choice as every section’s until that section is saved', () => {
    const { m, store } = memory();
    m.set('dsl-console-my-courses:octo', '0');
    for (const s of ['courses', 'now', 'past'] as const) expect(myCoursesOnly('octo', s, store)).toBe(false);
    saveMyCoursesOnly('octo', 'now', true, store);
    expect(myCoursesOnly('octo', 'now', store)).toBe(true);
    expect(myCoursesOnly('octo', 'past', store)).toBe(false);
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

function mount(f: FakeGitHub) {
  const env = { client: client(f), user } as unknown as Env;
  host = document.createElement('div');
  document.body.appendChild(host);
  act(() => render(<EnvCtx.Provider value={env}><HomeScreen courses={[course]} semesters={[]} cohortStates={{}} now={NOW} user={user} /></EnvCtx.Provider>, host!));
  return host;
}
const greyed = (h: HTMLElement) => [...h.querySelectorAll('.cohort-card.off')];
const section = (h: HTMLElement, id: string) => h.querySelector(`[aria-labelledby="${id}"]`)!;

const names = (el: Element) => [...el.querySelectorAll('.cc-name')].map((n) => n.firstChild?.textContent);
const boxOf = (h: HTMLElement, id: string) => section(h, id).querySelector<HTMLInputElement>('.my-only input')!;
const head = (h: HTMLElement, id: string) => section(h, id).querySelector('.section-head')!.textContent;

describe('All courses', () => {
  it('says what the page is', async () => {
    const h = mount(catalogueFake());
    await flush();
    expect(h.querySelector('.page-head .lede')!.textContent).toBe('Every course the lab runs, ordered by what needs your attention.');
    // The page head holds New course alone: the checkboxes sit in the section heads.
    expect([...h.querySelectorAll('.page-head .actions > *')].map((e) => e.textContent)).toEqual(['New course']);
    expect([...h.querySelectorAll('.section-head .my-only')].length).toBe(3);
  });

  it('shows only the person’s rows by default, and says how many others each section hides', async () => {
    const h = mount(catalogueFake());
    await flush();
    expect(greyed(h)).toEqual([]);
    for (const id of ['h-courses', 'h-live', 'h-past']) expect(boxOf(h, id).checked).toBe(true);
    expect(head(h, 'h-courses')).toContain('DSL courses (+2 others)');
    expect(head(h, 'h-live')).toContain('This semester (+3 others)');
    expect(head(h, 'h-past')).toContain('Past semesters (+1 other)');
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
    expect(names(now)).toEqual(['Natural Language Processing, Fall 2024', 'Natural Language Processing, Fall 2026', 'Natural Language Processing, Spring 2026']);
    expect(now.textContent).not.toContain('Fall 2025');
    expect(now.textContent).not.toContain('Summer 2026');
    await act(async () => boxOf(h, 'h-past').click());
    expect(names(section(h, 'h-past'))).toEqual(['Natural Language Processing, Fall 2025']);
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

  it('lists past semesters newest first, ten at a time', async () => {
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
    expect(h.querySelector('h1 .hint-pop')!.textContent).toContain('A course org is a standing staging area for the materials and assignment templates you are working on, for every semester. Each semester runs in its own org: students join it, and materials are released, assignments handed out and marks returned there.');
    expect(h.textContent).not.toContain('Open the course');
  });

  it('says so when the catalogue cannot be read, and still shows the person’s courses', async () => {
    const h = mount(catalogueFake(null));
    await flush();
    expect(h.textContent).toContain('The catalogue could not be read.');
    expect(greyed(h)).toEqual([]);
    expect(h.querySelector('a.cohort-card')?.getAttribute('href')).toBe(`?course=${course.org}#course`);
  });

  it('says it is reading the catalogue only while DSL courses shows the others', async () => {
    saveMyCoursesOnly(user.login, 'courses', false);
    const h = mount(catalogueFake());
    expect(h.textContent).toContain('Reading the catalogue…');
    await flush();
    expect(h.textContent).not.toContain('Reading the catalogue');
  });
});
