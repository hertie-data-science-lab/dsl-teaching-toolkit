// @vitest-environment happy-dom
// The Files panel (plan 2026-11-04, decisions 1-8): Browse | Withhold | Release over one tree that
// keeps its open folders, a name opening the viewer in Browse, an untick writing its line and
// enabling Save, Release ticks grouped in the manifest with the op's paired lists, the chips per
// view against the semester's two branches, a destination override highlighted, the semester
// page's Release by hand, and nothing for a read-only viewer. Decision B: `.env` never released in
// any view, `solutions/` withheld by default until a tick or a typed `!` line releases it.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { GitHubClient } from '../src/github/client';
import type { TreeState } from '../src/model/files';
import type { Loaded } from '../src/model/status';
import type { Status } from '../src/model/types';
import { FilesPanel, type FilesPanelProps } from '../src/ui/FilesPanel';
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
  constructor(private readonly refs: Record<string, TreeState>) {
    super(
      { [`${COURSE_ORG}/${REPO}/.releaseignore`]: 'drafts/\n' },
      {},
      { [`${COURSE_ORG}/${REPO}`]: COURSE, [`${SEM}/materials`]: STUDENTS },
      { [SEM]: [{ name: 'materials' }, { name: 'lecture-code' }, { name: 'semester-config' }], [COURSE_ORG]: [{ name: REPO }] },
      {},
      { ...COURSE_SHAS, ...SEM_SHAS },
    );
  }
  tree(owner: string, repo: string, ref?: string): TreeState {
    return ref ? (this.refs[`${owner}/${repo}@${ref}`] ?? { kind: 'absent' }) : super.tree(owner, repo);
  }
}

const loaded = { kind: 'ready', sha: 's', stale: [], status: { ...(example as unknown as Status), releases: [] } } as unknown as Loaded;
const semester = { org: SEM, termLabel: 'Fall 2026', loaded, defaultDest: 'materials' };

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
    semesters: [semester], scheduleHref: ({ semOrg }) => `?cohort=${semOrg}#schedule`, ...over,
  };
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{<FilesPanel {...props} />}</EnvCtx.Provider>, root!));
  await settle();
  return { el: root, open };
}

const tab = (el: HTMLElement, name: string) => [...el.querySelectorAll<HTMLButtonElement>('[role="tab"]')].find((b) => b.textContent === name)!;
const click = (x: Element) => act(() => (x as HTMLElement).click());
/** A file row by its path's last part, and a folder's summary by its name. */
const fileRow = (el: HTMLElement, name: string) => [...el.querySelectorAll<HTMLElement>('.file-tree li.ft-file')].find((li) => li.querySelector('.ft-name')?.textContent === name)!;
const folder = (el: HTMLElement, name: string) => [...el.querySelectorAll<HTMLElement>('.file-tree li.ft-dir')].find((li) => li.querySelector(':scope > details > summary .ft-name')?.textContent === `${name}/`)!;
const chips = (li: HTMLElement) => [...li.querySelectorAll(':scope > .chip, :scope > .chip-hint > .chip, :scope > details > summary > .chip, :scope > details > summary > .chip-hint > .chip')].map((c) => c.textContent);
/** Text as drawn: a closed popover waits hidden in place. */
const shown = (x: HTMLElement) => {
  const c = x.cloneNode(true) as HTMLElement;
  c.querySelectorAll('.hint-pop').forEach((h) => h.remove());
  return c.textContent;
};
const tick = (li: HTMLElement) => li.querySelector<HTMLInputElement>(':scope > input.ft-tick, :scope > details > summary > input.ft-tick');

describe('the Files panel', () => {
  it('opens on Browse: no ticks, the viewer placeholder, withheld, default withheld and never released chips only', async () => {
    const { el } = await mount();
    expect(el.querySelector('[role="tablist"]')).not.toBeNull();
    expect(tab(el, 'Browse').getAttribute('aria-selected')).toBe('true');
    expect(el.querySelector('.materials-file')!.textContent).toContain('Choose a file to view it here.');
    expect(el.querySelectorAll('.file-tree input.ft-tick')).toHaveLength(0);
    expect(chips(folder(el, 'drafts'))).toEqual(['withheld']);
    expect(chips(folder(el, 'solutions'))).toEqual(['default withheld']);
    expect(chips(fileRow(el, '.env'))).toEqual(['never released']);
    expect(chips(fileRow(el, 'slides.pdf'))).toEqual([]);
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
    expect(chips(folder(el, 'solutions'))).toEqual(['default withheld']);
    const save = () => [...el.querySelectorAll<HTMLButtonElement>('.savebar button')].find((b) => b.textContent === 'Save')!;
    expect(save().disabled).toBe(true);
    await click(fileRow(el, 'README.md').querySelector('.ft-name')!);
    expect(el.querySelector<HTMLTextAreaElement>('#ign-pat')!.value).toBe('drafts/\n/README.md');
    expect(tick(fileRow(el, 'README.md'))!.checked).toBe(false);
    expect(el.textContent).toContain('2 never released · 1 default withheld · 2 withheld · 5 released');
    expect(save().disabled).toBe(false);
    await click(save());
    expect(onSaveIgnore).toHaveBeenCalledWith('drafts/\n/README.md\n');
  });

  it('Release: the chips against the semester, disabled ticks, and folder roll-ups', async () => {
    const { el } = await mount();
    await click(tab(el, 'Release'));
    expect(chips(fileRow(el, 'README.md'))).toEqual(['up to date']);
    expect(tick(fileRow(el, 'README.md'))!.disabled).toBe(true);
    expect(chips(fileRow(el, 'slides.pdf'))).toEqual(['behind']);
    expect(chips(fileRow(el, 'lab.ipynb'))).toEqual(['not released']);
    expect(chips(folder(el, '01_intro'))).toEqual(['1 behind · 1 ahead']);
    expect(chips(folder(el, '02_linear'))).toEqual(['1 diverged']);
    expect(chips(folder(el, 'drafts'))).toEqual(['withheld']);
    expect(tick(folder(el, 'drafts'))).toBeNull();
    expect(chips(folder(el, 'solutions'))).toEqual(['only if named']);
    expect(tick(folder(el, 'solutions'))!.disabled).toBe(true);
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
    expect(chips(folder(el, 'solutions'))).toEqual(['released · overrides the default']);
    expect(el.textContent).toContain('2 never released · 0 default withheld · 1 withheld · 7 released');
    await click(tick(folder(el, 'solutions'))!);
    expect(box().value).toBe('drafts/');
    expect(chips(folder(el, 'solutions'))).toEqual(['default withheld']);
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
    expect(chips(folder(el, 'solutions'))).toEqual(['released · overrides the default']);
    expect(tick(fileRow(el, 'week01.ipynb'))!.checked).toBe(true);
  });

  it('Withhold: a never-released name has no tick, and a file in a default-withheld folder says why it cannot be ticked', async () => {
    const { el } = await mount();
    await click(tab(el, 'Withhold'));
    expect(tick(fileRow(el, '.env'))).toBeNull();
    expect(chips(fileRow(el, '.env'))).toEqual(['never released']);
    await click(tick(fileRow(el, 'week01.ipynb'))!);
    expect(el.querySelector<HTMLTextAreaElement>('#ign-pat')!.value).toBe('drafts/\n'); // unchanged
    expect(fileRow(el, 'week01.ipynb').querySelector('.ft-note')!.textContent).toBe('Withheld by default with its folder solutions/. Tick the folder to release it.');
  });

  it('Withhold: the institution’s defaults above the box, and the patterns hint rendered from the export', async () => {
    const { el } = await mount();
    await click(tab(el, 'Withhold'));
    const block = el.querySelector<HTMLElement>('.withhold-defaults')!;
    expect(block.querySelector('.label')!.textContent).toBe('Withheld by default (institution)');
    const rows = [...block.querySelectorAll('li')].map((li) => li.textContent);
    expect(rows).toContain('.env never released');
    expect(rows).toContain('solutions override with a ! line');
    expect(el.textContent).toContain('One pattern per line, as in .gitignore. Click the tree, or write them here.');
    const hint = [...el.querySelectorAll<HTMLElement>('.hint-pop')].map((h) => h.innerHTML).find((h) => h.startsWith('Guards for this repo'))!;
    expect(hint).toContain('<code>.git</code>, <code>.system</code>');
    expect(hint).toContain('the content names <code>solution</code>, <code>solutions</code>, <code>tests</code>, which a <code>!</code> line here overrides for this repo.');
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
    expect(el.querySelector<HTMLAnchorElement>('.rh-sched a')!.getAttribute('href')).toBe(`?cohort=${SEM}#schedule`);
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

  it('semester mode: Release by hand only, From the course repos', async () => {
    const onAdd = vi.fn();
    const { el } = await mount({ mode: 'semester', semester, semesters: undefined, sources: { materials: [REPO], others: ['lecture-code-f2026'], addable: [{ name: 'guest-lectures', note: 'no releases yet' }], onAdd } });
    expect(el.querySelector('[role="tablist"]')).toBeNull();
    expect(el.querySelector('summary h2')!.textContent).toContain('Release by hand');
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
    expect(onAdd).toHaveBeenCalledWith('guest-lectures');
  });

  it('is not drawn for a read-only viewer', async () => {
    const { el } = await mount({ write: false });
    expect(el.innerHTML).toBe('');
  });
});
