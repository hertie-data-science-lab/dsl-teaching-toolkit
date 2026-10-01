// @vitest-environment happy-dom
// All courses shows the institution's catalogue (decision 0021 rule 5): orgs.yml, then each
// other course's public dsl-course.yml and semesters.yml; one failed org is its name only;
// courses the person has no role in are greyed and not links; My courses hides them and is
// remembered per login.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
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
  it('is off by default, round-trips per login, and survives a refusing store', () => {
    const m = new Map<string, string>();
    const store: PrefStore = { getItem: (k) => m.get(k) ?? null, setItem: (k, v) => void m.set(k, v) };
    expect(myCoursesOnly('octo', store)).toBe(false);
    saveMyCoursesOnly('octo', true, store);
    expect(myCoursesOnly('octo', store)).toBe(true);
    expect(myCoursesOnly('other', store)).toBe(false);
    saveMyCoursesOnly('octo', false, store);
    expect(myCoursesOnly('octo', store)).toBe(false);
    const refusing: PrefStore = { getItem: () => { throw new Error('no'); }, setItem: () => { throw new Error('no'); } };
    expect(() => saveMyCoursesOnly('octo', true, refusing)).not.toThrow();
    expect(myCoursesOnly('octo', refusing)).toBe(false);
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

describe('All courses', () => {
  it('shows the catalogue greyed and not as links, and a running semester of another course', async () => {
    const h = mount(catalogueFake());
    expect(h.textContent).toContain('Reading the catalogue…');
    await flush();
    expect(h.textContent).not.toContain('Reading the catalogue');
    const courses = section(h, 'h-courses');
    const names = [...courses.querySelectorAll('.cc-name')].map((n) => n.firstChild?.textContent);
    expect(names).toEqual(['Machine Learning', BROKEN, 'Natural Language Processing']);
    const mine = courses.querySelector('a.cohort-card')!;
    expect(mine.getAttribute('href')).toBe(`?course=${course.org}#course`);
    for (const g of greyed(h)) {
      expect(g.tagName).toBe('DIV');
      expect(g.closest('a')).toBeNull();
      expect(g.getAttribute('aria-disabled')).toBe('true');
    }
    expect(courses.textContent).toContain('Not one of your courses');
    const now = section(h, 'h-live');
    const off = [...now.querySelectorAll('.cohort-card.off')];
    expect(off.map((o) => o.querySelector('.cc-name')?.firstChild?.textContent)).toEqual(['Natural Language Processing, Fall 2024', 'Natural Language Processing, Fall 2026', 'Natural Language Processing, Spring 2026']);
    // The greyed semester says whose it is to a screen reader.
    expect(off[0].querySelector('.sr')?.textContent).toBe('Not one of your courses');
    expect(now.textContent).not.toContain('Fall 2025');
    expect(now.textContent).not.toContain('Summer 2026');
  });

  it('hides every greyed row under My courses, and remembers it for this login', async () => {
    const h = mount(catalogueFake());
    await flush();
    expect(greyed(h).length).toBe(5);
    const box = h.querySelector<HTMLInputElement>('.my-only input')!;
    expect(box.checked).toBe(false);
    await act(async () => box.click());
    expect(greyed(h)).toEqual([]);
    expect(h.textContent).not.toContain('Reading the catalogue');
    expect(h.querySelector('a.cohort-card')).not.toBeNull();
    expect(myCoursesOnly(user.login)).toBe(true);
    render(null, h);
    const again = mount(catalogueFake());
    await flush();
    expect(again.querySelector<HTMLInputElement>('.my-only input')!.checked).toBe(true);
    expect(greyed(again)).toEqual([]);
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
});
