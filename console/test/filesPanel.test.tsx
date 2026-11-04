// @vitest-environment happy-dom
// The Files panel (plan 2026-11-04, decisions 1-8): Browse | Withhold | Release over one tree that
// keeps its open folders, a name opening the viewer in Browse, an untick writing its line and
// enabling Save, Release ticks grouped in the manifest with the op's paired lists, the chips per
// view against the semester's two branches, a destination override highlighted, the semester
// page's Release by hand, and Browse alone for a read-only viewer. Decision B: `.env` never released
// in any view, `solutions/` withheld ("withheld by default") until a tick or a typed `!` line releases it.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import * as releaseState from '../src/model/releaseState';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import type { FileState, TreeState } from '../src/model/files';
import type { Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { FilesPanel, SCHEDULE_LINK_LEAD, SCHEDULE_LINK_REST, type FilesPanelProps } from '../src/ui/FilesPanel';
import { FakeGitHub, fileBody } from './fake';
import example from './fixtures/status.example.json';
import { StaticFiles } from './staticFiles';

const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const SEM = 'hertie-dsl-demo-f2026';
const REPO = 'course-materials-f2026';
const COURSE = [
  '.releaseignore', 'README.md', 'drafts/idea.md',
  'lectures/01_intro/notes.md', 'lectures/01_intro/slides.pdf', 'lectures/02_linear/notes.md',
  'labs/week01/lab.ipynb', 'labs/week01/data.csv', 'labs/week01/.env', 'solutions/week01.ipynb',
];
// The course copy: slides and 02 notes changed since the release (sha-<path> otherwise).
const COURSE_SHAS = { [`${COURSE_ORG}/${REPO}/lectures/01_intro/slides.pdf`]: 'new', [`${COURSE_ORG}/${REPO}/lectures/02_linear/notes.md`]: 'new2' };
// What students read: README as released, 01 notes edited in the semester, slides as released, 02 notes edited.
const STUDENTS = ['README.md', 'lectures/01_intro/notes.md', 'lectures/01_intro/slides.pdf', 'lectures/02_linear/notes.md'];
const SEM_SHAS = {
  [`${SEM}/materials/lectures/01_intro/notes.md`]: 'edited',
  [`${SEM}/materials/lectures/01_intro/slides.pdf`]: 'old',
  [`${SEM}/materials/lectures/02_linear/notes.md`]: 'edited2',
};
// Where the last release landed: everything as the course had it then.
const upstream: TreeState = {
  kind: 'ready', truncated: false, paths: [
    { path: 'README.md', dir: false, sha: 'sha-README.md' },
    { path: 'lectures/01_intro/notes.md', dir: false, sha: 'sha-lectures/01_intro/notes.md' },
    { path: 'lectures/01_intro/slides.pdf', dir: false, sha: 'old' },
    { path: 'lectures/02_linear/notes.md', dir: false, sha: 'old' },
  ],
};

/** StaticFiles with a tree per ref (the semester's `upstream` branch). */
class RefFiles extends StaticFiles {
  constructor(private readonly refs: Record<string, TreeState>, course = COURSE, students = STUDENTS) {
    super(
      { [`${COURSE_ORG}/${REPO}/.releaseignore`]: 'drafts/\n' },
      {},
      { [`${COURSE_ORG}/${REPO}`]: course, [`${SEM}/materials`]: students },
      { [SEM]: [{ name: 'materials' }, { name: 'lecture-code' }, { name: 'semester-config' }], [COURSE_ORG]: [{ name: REPO }] },
      {},
      { ...COURSE_SHAS, ...SEM_SHAS },
    );
  }
  // Each read answers the same object until it changes, as LiveFiles' signals do.
  private readonly held = new Map<string, unknown>();
  private once<T>(key: string, read: () => T): T {
    if (!this.held.has(key)) this.held.set(key, read());
    return this.held.get(key) as T;
  }
  tree(owner: string, repo: string, ref?: string): TreeState {
    return this.once(`${owner}/${repo}@${ref ?? ''}`, () => (ref ? (this.refs[`${owner}/${repo}@${ref}`] ?? { kind: 'absent' }) : super.tree(owner, repo)));
  }
  file(owner: string, repo: string, path: string): FileState {
    return this.once(`${owner}/${repo}/${path}`, () => super.file(owner, repo, path));
  }
}

// A pass-through spy: the manifest's states are worked out once per change of their inputs.
vi.mock('../src/model/releaseState', async (importOriginal) => {
  const m = await importOriginal<typeof import('../src/model/releaseState')>();
  return { ...m, fileStates: vi.fn(m.fileStates) };
});

const loaded = { kind: 'ready', sha: 's', stale: [], status: { ...(example as unknown as Status), releases: [] } } as unknown as Loaded;
const semester = { org: SEM, termLabel: 'Fall 2026', loaded };

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
  document.querySelectorAll('.hint-host, .modal-scrim').forEach((x) => x.remove());
});

const settle = async () => {
  for (let i = 0; i < 6; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
};

async function mount(over: Partial<FilesPanelProps> = {}) {
  const gh = new FakeGitHub()
    .on('GET', `/repos/${COURSE_ORG}/${REPO}/git/trees/HEAD?recursive=1`, { sha: 't', truncated: false, tree: COURSE.map((path) => ({ path, mode: '100644', type: 'blob', sha: `sha-${path}`, size: 10 })) })
    .on('GET', `/repos/${COURSE_ORG}/${REPO}/contents/README.md`, fileBody('README.md', '# Welcome'))
    .on('POST', '/markdown', () => new Response('<h1>Welcome</h1>', { status: 200 }));
  const open = vi.fn();
  const env = { user: { login: 'prof', id: 1, name: 'P', email: null, avatar_url: '' }, client: new GitHubClient({ token: () => 't', fetch: gh.fetch }), ops: { open } } as unknown as Env;
  const props: FilesPanelProps = {
    files: new RefFiles({ [`${SEM}/materials@upstream`]: upstream }), courseOrg: COURSE_ORG, repo: REPO, mode: 'course', write: true,
    semesters: [semester], scheduleHref: (semOrg) => `?cohort=${semOrg}#schedule`, ...over,
  };
  root = document.createElement('div');
  document.body.appendChild(root);
  const draw = () => act(() => render(<EnvCtx.Provider value={env}>{<FilesPanel {...props} />}</EnvCtx.Provider>, root!));
  await draw();
  await settle();
  return { el: root, open, rerender: draw };
}

const type = (input: HTMLInputElement, value: string) => act(() => {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
});
const tab = (el: HTMLElement, name: string) => [...el.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find((b) => b.textContent === name)!;
const click = (x: Element) => act(() => (x as HTMLElement).click());
/** A file row by its path's last part, and a folder's summary by its name. */
const fileRow = (el: HTMLElement, name: string) => [...el.querySelectorAll<HTMLElement>('.file-tree li.ft-file')].find((li) => li.querySelector('.ft-name')?.textContent === name)!;
const folder = (el: HTMLElement, name: string) => [...el.querySelectorAll<HTMLElement>('.file-tree li.ft-dir')].find((li) => li.querySelector(':scope > details > summary .ft-name')?.textContent === `${name}/`)!;
const chips = (li: HTMLElement) => [...li.querySelectorAll(':scope > .chip:not(.rule-chip), :scope > .chip-hint > .chip, :scope > details > summary > .chip:not(.rule-chip), :scope > details > summary > .chip-hint > .chip, :scope > details > summary > .ft-rollup > .chip')].map((c) => c.textContent);
/** Text as drawn: a closed popover waits hidden in place. */
const shown = (x: HTMLElement) => {
  const c = x.cloneNode(true) as HTMLElement;
  c.querySelectorAll('.hint-pop').forEach((h) => h.remove());
  return c.textContent;
};
/** Why a row stands as it does: its muted rule chip on the name's line. */
const note = (li: HTMLElement) => li.querySelector(':scope > .chip.muted.rule-chip, :scope > details > summary > .chip.muted.rule-chip')?.textContent ?? null;
const tick = (li: HTMLElement) => li.querySelector<HTMLInputElement>(':scope > input.ft-tick, :scope > details > summary > input.ft-tick');

describe('the Files panel', () => {
  it('opens on Browse: no ticks, the viewer placeholder, and a plain muted withheld chip on every row that does not reach students', async () => {
    const { el } = await mount();
    expect(el.querySelector('[role="tablist"]')).not.toBeNull();
    expect(tab(el, 'Browse').getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('.materials-file')!.textContent).toContain('Choose a file to view it here.');
    expect(el.querySelectorAll('.file-tree input.ft-tick')).toHaveLength(0);
    expect(chips(folder(el, 'drafts'))).toEqual(['withheld']);
    expect(chips(folder(el, 'solutions'))).toEqual(['withheld']);
    // No why, no strike-through, and never released reads withheld too.
    expect(note(folder(el, 'solutions'))).toBeNull();
    expect(note(folder(el, 'drafts'))).toBeNull();
    expect(chips(fileRow(el, '.env'))).toEqual(['withheld']);
    expect(el.querySelectorAll('.file-tree .ft-out')).toHaveLength(0);
    expect(fileRow(el, '.env').querySelector('.chip')!.classList.contains('muted')).toBe(true);
    expect(chips(fileRow(el, 'slides.pdf'))).toEqual([]);
    // No Schedule lede outside Release.
    expect(el.querySelector('.rh-sched')).toBeNull();
  });

  it('opens a file in the viewer in Browse', async () => {
    const { el } = await mount();
    await click(fileRow(el, 'README.md').querySelector('button.ft-name')!);
    await settle();
    expect(el.querySelector('.materials-file h2.mono')!.textContent).toBe('README.md');
    expect(el.querySelector('.materials-file')!.textContent).toContain('Welcome');
    expect(fileRow(el, 'README.md').querySelector('button.ft-name')!.getAttribute('aria-current')).toBe('true');
  });

  it('keeps the folders open across the views', async () => {
    const { el } = await mount();
    const details = folder(el, '01_intro').querySelector('details')!;
    expect(details.open).toBe(false);
    await act(() => { details.open = true; });
    await click(tab(el, 'Withhold'));
    expect(folder(el, '01_intro').querySelector('details')!.open).toBe(true);
    await click(tab(el, 'Release'));
    expect(folder(el, '01_intro').querySelector('details')!.open).toBe(true);
    expect(tab(el, 'Release').getAttribute('aria-selected')).toBe('true');
  });

  it('Withhold: a click on a row withholds the file, writes its line and enables Save', async () => {
    const onSaveIgnore = vi.fn(async () => true);
    const { el } = await mount({ onSaveIgnore });
    await click(tab(el, 'Withhold'));
    expect(chips(folder(el, 'solutions'))).toEqual(['withheld']);
    expect(note(folder(el, 'solutions'))).toBe('withheld by default');
    const save = () => [...el.querySelectorAll<HTMLButtonElement>('.savebar button')].find((b) => b.textContent === 'Save')!;
    expect(save().disabled).toBe(true);
    await click(fileRow(el, 'README.md').querySelector('.ft-name')!);
    expect(el.querySelector<HTMLTextAreaElement>('#ign-pat')!.value).toBe('drafts/\n/README.md');
    expect(tick(fileRow(el, 'README.md'))!.checked).toBe(false);
    expect(el.textContent).toContain('2 never released · 3 withheld · 5 released');
    expect(save().disabled).toBe(false);
    await click(save());
    expect(onSaveIgnore).toHaveBeenCalledWith('drafts/\n/README.md\n');
  });

  it('Release: says how many released files the semester edited, PR into source, and See which narrows the tree to them', async () => {
    const onKeep = vi.fn();
    const { el } = await mount({ onKeep });
    expect(el.querySelector('.edited-note')).toBeNull();
    await click(tab(el, 'Release'));
    const note = el.querySelector<HTMLElement>('.edited-note')!;
    // 01 notes edited in the semester (ahead), 02 notes on both sides (diverged).
    expect(note.querySelector('.step-bang')!.textContent).toBe('!');
    expect(note.querySelector('span:not(.step-bang)')!.textContent).toBe('Fall 2026 has edited 2 released files from this repo.');
    await click([...note.querySelectorAll('button')].find((b) => b.textContent === 'PR into source')!);
    expect(onKeep).toHaveBeenCalledWith(semester);
    const which = () => note.querySelector<HTMLButtonElement>('button.textlink')!;
    const files = () => [...el.querySelectorAll('.file-tree li.ft-file .ft-name')].map((x) => x.textContent);
    expect(files()).toContain('README.md');
    expect(which().textContent).toBe('See which');
    await click(which());
    expect(files()).toEqual(['notes.md', 'notes.md']);
    expect(which().textContent).toBe('Show all');
    await click(which());
    expect(files()).toContain('README.md');
  });

  it('Release: no edited-files notice when the semester edited nothing of this repo', async () => {
    const plain: TreeState = { kind: 'ready', truncated: false, paths: [] };
    const { el } = await mount({ files: new RefFiles({ [`${SEM}/materials@upstream`]: plain }, ['README.md']) });
    await click(tab(el, 'Release'));
    expect(el.querySelector('.edited-note')).toBeNull();
  });

  describe('a file removed in the semester, and one the semester added (R1, R2)', () => {
    // data.csv released as the course has it, then deleted on the semester's main; extra.md added
    // in the semester under the released lectures/01_intro.
    const up: TreeState = upstream.kind === 'ready' ? { ...upstream, paths: [...upstream.paths, { path: 'labs/week01/data.csv', dir: false, sha: 'sha-labs/week01/data.csv' }] } : upstream;
    const releases = [{
      id: 's1', when: '2026-09-10T10:00:00+02:00', kind: 'lecture', number: 1, title: 's1', state: 'released', tbc: false, show_on_site: true,
      source: { repo: REPO, path: 'lectures/01_intro' }, dest: { repo: 'materials', path: 'lectures/01_intro' },
    }];
    const withReleases = { ...loaded, status: { ...(loaded as Extract<Loaded, { kind: 'ready' }>).status, releases } } as unknown as Loaded;
    const files = () => new RefFiles({ [`${SEM}/materials@upstream`]: up }, COURSE, [...STUDENTS, 'lectures/01_intro/extra.md']);
    const sem = { ...semester, loaded: withReleases };

    it('chips the removed row, keeps its tick with why a plain release changes nothing, and the manifest says so', async () => {
      const { el, open } = await mount({ files: files(), semesters: [sem] });
      await click(tab(el, 'Release'));
      expect(chips(fileRow(el, 'data.csv'))).toEqual(['removed in semester']);
      const t = tick(fileRow(el, 'data.csv'))!;
      expect([t.disabled, t.title]).toEqual([false, 'Removed in the semester: a plain release changes nothing; Overwrite (on the semester’s Released materials) restores the course version.']);
      await click(t);
      expect([...el.querySelectorAll<HTMLElement>('.mf-rows li')].map(shown)).toContain('labs/week01/data.csv→labs/week01/data.csvremoved in semester');
      await click(el.querySelector<HTMLButtonElement>('.rh-foot .btn')!);
      expect(open.mock.calls[0][0].nothingExtra).toBe('Nothing copied: the semester removed these files; use Overwrite to restore them.');
    });

    it('counts both in the notice, and See which lists the added file with its chip and Open on the semester copy', async () => {
      const { el } = await mount({ files: files(), semesters: [sem] });
      await click(tab(el, 'Release'));
      const note = el.querySelector<HTMLElement>('.edited-note')!;
      // 01 notes ahead, 02 notes diverged, data.csv removed, extra.md added.
      expect(note.querySelector('span:not(.step-bang)')!.textContent).toBe('Fall 2026 has edited 4 released files from this repo.');
      expect(el.querySelector('.edited-added')).toBeNull();
      await click(note.querySelector<HTMLButtonElement>('button.textlink')!);
      expect([...el.querySelectorAll('.file-tree li.ft-file .ft-name')].map((x) => x.textContent)).toEqual(['data.csv', 'notes.md', 'notes.md']);
      const added = [...el.querySelectorAll<HTMLElement>('.edited-added > li')];
      expect(added.map((li) => li.querySelector('code')!.textContent)).toEqual(['lectures/01_intro/extra.md']);
      expect(added[0].querySelector('.chip')!.textContent).toBe('ahead');
      expect(added[0].querySelector('.split .split-main')!.getAttribute('href')).toBe(`https://github.com/${SEM}/materials/blob/HEAD/lectures/01_intro/extra.md`);
    });

    it('counts and lists a semester-only file when no course file changed', async () => {
      const plain: TreeState = { kind: 'ready', truncated: false, paths: [{ path: 'lectures/01_intro/notes.md', dir: false, sha: 'sha-lectures/01_intro/notes.md' }] };
      const course = ['lectures/01_intro/notes.md'];
      const f = new RefFiles({ [`${SEM}/materials@upstream`]: plain }, course, ['lectures/01_intro/notes.md', 'lectures/01_intro/extra.md']);
      (f as unknown as { shaMap: Record<string, string> }).shaMap[`${SEM}/materials/lectures/01_intro/notes.md`] = 'sha-lectures/01_intro/notes.md';
      const { el } = await mount({ files: f, semesters: [sem] });
      await click(tab(el, 'Release'));
      const note = el.querySelector<HTMLElement>('.edited-note')!;
      expect(note.querySelector('span:not(.step-bang)')!.textContent).toBe('Fall 2026 has edited 1 released file from this repo.');
      await click(note.querySelector<HTMLButtonElement>('button.textlink')!);
      expect([...el.querySelectorAll('.edited-added code')].map((x) => x.textContent)).toEqual(['lectures/01_intro/extra.md']);
      expect(el.textContent).not.toContain('The repo has no files yet.');
    });
  });

  it('Release: the chips against the semester, disabled ticks, and folder roll-ups', async () => {
    const { el } = await mount();
    await click(tab(el, 'Release'));
    // Up to date: greyed with a muted `released`, and left out of a folder's roll-up.
    expect(chips(fileRow(el, 'README.md'))).toEqual(['released']);
    expect(fileRow(el, 'README.md').querySelector('.chip.muted')!.getAttribute('data-kind')).toBe('up_to_date');
    expect(el.querySelector('.files-panel .hint-pop')!.textContent).toContain('released (greyed): the semester already has this version; nothing to release.');
    expect(el.querySelector('.file-tree')!.textContent).not.toContain('up to date');
    expect(tick(fileRow(el, 'README.md'))!.disabled).toBe(true);
    expect(chips(fileRow(el, 'slides.pdf'))).toEqual(['behind']);
    expect(chips(fileRow(el, 'lab.ipynb'))).toEqual(['not released']);
    expect(chips(folder(el, '01_intro'))).toEqual(['1 behind · 1 ahead']);
    expect(chips(folder(el, '02_linear'))).toEqual(['1 diverged']);
    expect(chips(folder(el, 'drafts'))).toEqual(['withheld']);
    // A roll-up is about the folder's contents (`.ft-rollup`, hidden by CSS once open); a folder's own chip is not one.
    expect(folder(el, '01_intro').querySelector(':scope > details > summary > .ft-rollup')!.textContent).toBe('1 behind · 1 ahead');
    expect(folder(el, 'drafts').querySelector(':scope > details > summary > .ft-rollup')).toBeNull();
    expect(tick(folder(el, 'drafts'))).toBeNull();
    // Withheld by default on a folder copy; ticking the folder names it outright, which ships it (`selection.deny`).
    expect(chips(folder(el, 'solutions'))).toEqual(['withheld']);
    expect(note(folder(el, 'solutions'))).toBe('withheld by default');
    expect(tick(folder(el, 'solutions'))!.disabled).toBe(false);
    expect(chips(fileRow(el, '.env'))).toEqual(['never released']);
    expect(tick(fileRow(el, '.env'))!.disabled).toBe(true);
    expect(tick(fileRow(el, '.env'))!.title).toBe('Never released: on the institution’s list.');
    expect(el.querySelector('.manifest-wrap')!.textContent).toContain('Nothing picked yet. Tick files or folders in the tree.');
    expect(el.querySelector<HTMLButtonElement>('.rh-foot .btn')!.disabled).toBe(true);
  });

  it('Withhold: a tick on a default-withheld folder writes its ! line, and a second takes it out', async () => {
    const { el } = await mount({ onSaveIgnore: vi.fn(async () => true) });
    await click(tab(el, 'Withhold'));
    const box = () => el.querySelector<HTMLTextAreaElement>('#ign-pat')!;
    expect(tick(folder(el, 'solutions'))!.checked).toBe(false);
    expect(folder(el, 'solutions').classList.contains('ft-out')).toBe(true);
    await click(tick(folder(el, 'solutions'))!);
    expect(box().value).toBe('drafts/\n!/solutions/');
    expect(tick(folder(el, 'solutions'))!.checked).toBe(true);
    expect(chips(folder(el, 'solutions'))).toEqual([]);
    expect(note(folder(el, 'solutions'))).toBe('released by !/solutions/');
    expect(el.textContent).toContain('2 never released · 1 withheld · 7 released');
    await click(tick(folder(el, 'solutions'))!);
    expect(box().value).toBe('drafts/');
    expect(chips(folder(el, 'solutions'))).toEqual(['withheld']);
    expect(note(folder(el, 'solutions'))).toBe('withheld by default');
  });

  it('Withhold: a ! line typed in the box releases the default on the tree', async () => {
    const { el } = await mount();
    await click(tab(el, 'Withhold'));
    const box = el.querySelector<HTMLTextAreaElement>('#ign-pat')!;
    await act(() => {
      box.value = 'drafts/\n!solutions/';
      box.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(tick(folder(el, 'solutions'))!.checked).toBe(true);
    expect(chips(folder(el, 'solutions'))).toEqual([]);
    expect(note(folder(el, 'solutions'))).toBe('released by !solutions/');
    expect(tick(fileRow(el, 'week01.ipynb'))!.checked).toBe(true);
  });

  it('Withhold: a never-released name has no tick, and a tick on a file in a default-withheld folder releases the folder with it', async () => {
    const { el } = await mount();
    await click(tab(el, 'Withhold'));
    expect(tick(fileRow(el, '.env'))).toBeNull();
    expect(chips(fileRow(el, '.env'))).toEqual(['never released']);
    await click(tick(fileRow(el, 'week01.ipynb'))!);
    expect(el.querySelector<HTMLTextAreaElement>('#ign-pat')!.value).toBe('drafts/\n!/solutions/');
    expect(fileRow(el, 'week01.ipynb').querySelector('.ft-note')).toBeNull();
    expect(tick(fileRow(el, 'week01.ipynb'))!.checked).toBe(true);
    expect(tick(folder(el, 'solutions'))!.checked).toBe(true);
    expect(note(folder(el, 'solutions'))).toBe('released by !/solutions/');
  });

  it('Withhold: a file in a folder a .releaseignore line withholds keeps the refusal', async () => {
    const { el } = await mount();
    await click(tab(el, 'Withhold'));
    await click(tick(fileRow(el, 'idea.md'))!);
    expect(el.querySelector<HTMLTextAreaElement>('#ign-pat')!.value).toBe('drafts/\n'); // unchanged
    expect(fileRow(el, 'idea.md').querySelector('.ft-note')!.textContent).toBe('Withheld with its folder drafts/ by drafts/. Remove that rule to include it.');
  });

  it('Withhold: the institution’s defaults above the box, and the patterns hint rendered from the export', async () => {
    const { el } = await mount();
    await click(tab(el, 'Withhold'));
    const block = el.querySelector<HTMLDetailsElement>('details.fold.withhold-defaults')!;
    expect(block.open).toBe(false);
    expect(block.querySelector('summary')!.textContent).toBe('Withheld by default');
    await act(() => {
      block.open = true;
      block.dispatchEvent(new Event('toggle'));
    });
    expect(block.querySelector('summary')!.textContent).toBe('Withheld by default (set at institutional-level)');
    // Two plain lists, no labels: the never-released names, then the content names as chips.
    expect([...block.querySelectorAll('.defaults-hard li')].map((li) => li.textContent)).toEqual(['.git', '.system', '.env', '.env.*', 'grading_config.yml', 'grading.yml']);
    expect([...block.querySelectorAll('.defaults-content button.chip')].map((b) => b.textContent)).toEqual(['solution', 'solutions', 'tests']);
    expect(block.querySelector('.label')).toBeNull();
    expect(el.textContent).toContain('One pattern per line, as in .gitignore. Click the tree, or write them here.');
    const pop = [...el.querySelectorAll<HTMLElement>('.hint-pop')].find((h) => h.textContent!.startsWith('Guards for this repo'))!;
    expect(pop.firstChild!.textContent).toBe('Guards for this repo: files that must never leave it by mistake.');
    const items = [...pop.querySelectorAll('ul.hint-list > li')].map((li) => li.innerHTML);
    expect(items).toHaveLength(4);
    expect(items[0]).toBe('One pattern per line, as in .gitignore, e.g. <code>solutions/</code>, <code>**/draft-*</code>, <code>exam-*.pdf</code>.');
    expect(items[1]).toBe('A matching file is skipped by every copy out of this repo: scheduled and manual releases, assignment hand-outs and the public website, even when a release names its folder. A release that names the file itself is refused.');
    expect(items[2]).toBe('The institution withholds by default: <code>.git</code>, <code>.system</code>, <code>.env</code>, <code>.env.*</code>, <code>grading_config.yml</code>, <code>grading.yml</code>, which can never be released, and the content names <code>solution</code>, <code>solutions</code>, <code>tests</code>, which a <code>!</code> line here overrides for this repo. A release that names one of those outright still ships it.');
    expect(items[3]).toBe('Patterns stop future copies only: a file already released stays in the semester until you remove it there.');
  });

  it('Withhold: a content-name chip writes its ! line once, and the tree releases every matching folder by it', async () => {
    const files = new RefFiles({ [`${SEM}/materials@upstream`]: upstream }, [...COURSE, 'labs/week01/tests/t.py']);
    const { el } = await mount({ files });
    await click(tab(el, 'Withhold'));
    expect(chips(folder(el, 'tests'))).toEqual(['withheld']);
    const chip = [...el.querySelectorAll<HTMLButtonElement>('.defaults-content button.chip')].find((b) => b.textContent === 'tests')!;
    await click(chip);
    await click(chip);
    expect(el.querySelector<HTMLTextAreaElement>('#ign-pat')!.value).toBe('drafts/\n!tests/');
    expect(tick(folder(el, 'tests'))!.checked).toBe(true);
    expect(note(folder(el, 'tests'))).toBe('released by !tests/');
    expect(el.querySelector('.pm-line.flash')!.textContent).toBe('!tests/');
  });

  it('Release: ticks become groups, counts and the op’s paired lists', async () => {
    const { el, open } = await mount();
    await click(tab(el, 'Release'));
    await click(fileRow(el, 'slides.pdf'));
    await click(tick(folder(el, 'labs'))!);
    // A file inside a picked folder is picked with it.
    expect(tick(fileRow(el, 'data.csv'))!.checked).toBe(true);
    expect(tick(fileRow(el, 'data.csv'))!.disabled).toBe(true);
    const heads = [...el.querySelectorAll('.mf-head')].map((h) => h.textContent);
    expect(heads).toEqual(['labs/ · all 2 files', 'lectures/01_intro/ · 1 of 2 files']);
    const rows = [...el.querySelectorAll<HTMLElement>('.mf-rows li')].map(shown);
    expect(rows).toContain('lectures/01_intro/slides.pdf→lectures/01_intro/slides.pdfbehind');
    expect(rows).toContain('labs/week01/lab.ipynb→labs/week01/lab.ipynbnot released');
    expect(el.querySelector('.rh-count')!.textContent).toBe('2 paths picked · 3 files');
    const release = el.querySelector<HTMLButtonElement>('.rh-foot .btn')!;
    expect(release.textContent).toBe('Release 2 paths');
    await click(release);
    expect(open).toHaveBeenCalledTimes(1);
    const def = open.mock.calls[0][0];
    expect(def.op).toBe('release.adhoc');
    expect(def.args).toEqual({
      course_source_repo: `${REPO},${REPO}`, course_source_path: 'labs,lectures/01_intro/slides.pdf',
      semester_dest_repo: 'materials,materials', semester_dest_path: 'labs,lectures/01_intro/slides.pdf',
    });
  });

  it('Release: the Schedule on the calendar line is the panel’s lede, picks or none', async () => {
    const scheduleHref = vi.fn((semOrg: string, rows: unknown[]) => `?cohort=${semOrg}#schedule-${rows.length}`);
    const { el } = await mount({ scheduleHref });
    await click(tab(el, 'Release'));
    const lede = () => el.querySelector<HTMLElement>('.files-panel > p.lede.rh-sched')!;
    expect(shown(lede())).toBe('Want to automate these releases? Schedule on the calendar → ?');
    // The ? right after the link: the placeholder wording, or the caller's.
    const pop = lede().querySelector('a.textlink + .hint-wrap .hint-pop')!;
    expect(pop.textContent).toBe(`${SCHEDULE_LINK_LEAD} ${SCHEDULE_LINK_REST}`);
    // Its first sentence in bold.
    expect(pop.querySelector('b')!.textContent).toBe(SCHEDULE_LINK_LEAD);
    expect(lede().querySelector('a')!.getAttribute('href')).toBe(`?cohort=${SEM}#schedule-0`);
    // Before the tree and its heads (the edited-files notice between), not in the footer.
    expect(lede().nextElementSibling!.classList.contains('edited-note')).toBe(true);
    expect(lede().nextElementSibling!.nextElementSibling!.querySelector('.file-tree')).not.toBeNull();
    expect(el.querySelector('.rh-foot .rh-sched')).toBeNull();
    await click(fileRow(el, 'slides.pdf'));
    expect(lede().querySelector('a')!.getAttribute('href')).toBe(`?cohort=${SEM}#schedule-1`);
  });

  it('the behind chip’s popover ticks its row', async () => {
    const { el } = await mount();
    await click(tab(el, 'Release'));
    const chip = fileRow(el, 'slides.pdf').querySelector<HTMLButtonElement>('.chip-hint > button.chip')!;
    await act(() => chip.focus());
    const action = [...document.querySelectorAll<HTMLButtonElement>('.hint-host button')].find((b) => b.textContent === 'Tick it to release the course version.')!;
    await click(action);
    expect(tick(fileRow(el, 'slides.pdf'))!.checked).toBe(true);
  });

  it('a group sent to another repo is highlighted with its (!)', async () => {
    const { el, open } = await mount();
    await click(tab(el, 'Release'));
    await click(fileRow(el, 'lab.ipynb'));
    const group = el.querySelector<HTMLElement>('.mf-group')!;
    expect(group.classList.contains('other')).toBe(false);
    const sel = group.querySelector<HTMLSelectElement>('.mf-adv select')!;
    expect([...sel.options].map((o) => o.textContent)).toEqual(['materials', 'Choose a repo…']);
    await act(() => {
      // A repo the picker offers: chosen through the modal.
      sel.value = '__choose';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    const radio = [...document.querySelectorAll<HTMLInputElement>('.rp-row input')].find((r) => r.value === 'lecture-code')!;
    expect([...document.querySelectorAll('.rp-row .rp-name')].map((x) => x.textContent)).toEqual(['materials', 'lecture-code']);
    await act(() => { radio.click(); });
    await click([...document.querySelectorAll<HTMLButtonElement>('.modal .btn')].find((b) => b.textContent === 'Choose')!);
    const g = el.querySelector<HTMLElement>('.mf-group')!;
    expect(g.classList.contains('other')).toBe(true);
    expect(g.querySelector('.mf-dest')!.classList.contains('mf-diff')).toBe(true);
    expect(g.querySelector('.mf-warn')!.getAttribute('title')).toBe('This file is being released to a different repo: lecture-code');
    await click(el.querySelector('.rh-foot .btn')!);
    expect(open.mock.calls[0][0].args.semester_dest_repo).toBe('lecture-code');
  });

  it('a path with a comma has no live tick: the lists the op takes are comma-separated', async () => {
    const files = new RefFiles({ [`${SEM}/materials@upstream`]: upstream }, [...COURSE, 'labs/a,b.md', 'odd,name/x.md']);
    const { el } = await mount({ files });
    await click(tab(el, 'Release'));
    for (const t of [tick(fileRow(el, 'a,b.md'))!, tick(folder(el, 'odd,name'))!]) {
      expect(t.disabled).toBe(true);
      expect(t.title).toBe('A path with a comma cannot be released from here; rename it.');
    }
    expect(tick(fileRow(el, 'lab.ipynb'))!.disabled).toBe(false);
  });

  it('a cleared To path is no override; a To path with a comma stops Release with a note', async () => {
    const { el, open } = await mount();
    await click(tab(el, 'Release'));
    await click(tick(folder(el, 'labs'))!);
    await click(fileRow(el, 'slides.pdf'));
    const to = el.querySelectorAll<HTMLInputElement>('.mf-adv input[type="text"]');
    await type(to[0], '');
    await type(to[1], 'slides');
    await click(el.querySelector('.rh-foot .btn')!);
    // Two paths, two landings: the cleared one at its own path.
    expect(open.mock.calls[0][0].args.semester_dest_path).toBe('labs,slides/slides.pdf');
    await type(to[1], 'a,b');
    expect(el.querySelector<HTMLButtonElement>('.rh-foot .btn')!.disabled).toBe(true);
    expect(el.querySelector('.rh-foot .check-line')!.textContent).toBe('A path with a comma cannot be released from here; rename it.');
  });

  it('works the manifest’s states out once, not on every render', async () => {
    const { el, rerender } = await mount();
    await click(tab(el, 'Release'));
    await click(fileRow(el, 'slides.pdf'));
    const spy = vi.mocked(releaseState.fileStates);
    spy.mockClear();
    await rerender();
    await rerender();
    expect(spy).not.toHaveBeenCalled();
    expect([...el.querySelectorAll<HTMLElement>('.mf-rows li')].map(shown)).toContain('lectures/01_intro/slides.pdf→lectures/01_intro/slides.pdfbehind');
  });

  it('semester mode: Release by hand only, From the course repos', async () => {
    const onAdd = vi.fn();
    const { el } = await mount({ mode: 'semester', semester, semesters: undefined, sources: { materials: [REPO], others: ['lecture-code-f2026'], onAdd } });
    expect(el.querySelector('[role="tablist"]')).toBeNull();
    expect(el.querySelector('summary h2')!.textContent).toContain('Release by hand');
    // The Schedule line sits directly under the title, before the From/To row.
    expect(el.querySelector('details.files-panel > summary')!.nextElementSibling!.classList.contains('rh-sched')).toBe(true);
    const from = el.querySelector<HTMLSelectElement>('#rbh-from')!;
    expect([...from.options].map((o) => o.textContent)).toEqual([REPO, 'lecture-code-f2026', 'Add a course repo…']);
    expect(el.querySelector('.split-heads .mf-into')!.textContent).toBe(`${SEM} /`);
    expect(chips(fileRow(el, 'slides.pdf'))).toEqual(['behind']);
    await act(() => {
      from.value = '__add';
      from.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(document.querySelector('#modal-title')!.textContent).toBe('Add a course repo');
    await click([...document.querySelectorAll<HTMLButtonElement>('.modal .btn')].find((b) => b.textContent === 'Add')!);
    expect(onAdd).toHaveBeenCalledWith('lecture-code-f2026');
  });

  it('shows a read-only viewer Browse alone on the course page, and nothing on the semester’s', async () => {
    const { el } = await mount({ write: false });
    expect(el.querySelector('[role="tablist"]')).toBeNull();
    expect(el.querySelectorAll('.file-tree input.ft-tick')).toHaveLength(0);
    expect(el.querySelector('.rh-foot')).toBeNull();
    expect(chips(folder(el, 'drafts'))).toEqual(['withheld']);
    expect(chips(fileRow(el, '.env'))).toEqual(['withheld']);
    await click(fileRow(el, 'README.md').querySelector('button.ft-name')!);
    await settle();
    expect(el.querySelector('.materials-file h2.mono')!.textContent).toBe('README.md');
    render(null, root!);
    const sem = await mount({ write: false, mode: 'semester', semester, semesters: undefined, sources: { materials: [REPO], others: [] } });
    expect(sem.el.innerHTML).toBe('');
  });
});
