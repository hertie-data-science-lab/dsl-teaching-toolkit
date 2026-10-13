// @vitest-environment happy-dom
// The course materials index's folder tree: collapsed by default with a count, opened folders
// kept per repo for the session, a `?folder=` deep link opening its folder, and GitHub links.

import { render as mount } from 'preact';
import { act } from 'preact/test-utils';
import { beforeEach, describe, expect, it } from 'vitest';
import { focusIn } from '../src/screens/CourseIndex';
import { parseSearch } from '../src/router';
import { MaterialsTree, folderHref } from '../src/ui/MaterialsTree';

const ORG = 'demo-course';
const REPO = 'course-materials-f2026';
const PATHS = ['SYLLABUS.md', '.github/x.yml', 'lectures/01/slides.pdf', 'lectures/01/notes.md', 'lectures/02/a.ipynb', 'labs/lab.ipynb'].map((path) => ({ path, dir: false }));

async function mounted(focus?: string) {
  const root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => mount(<MaterialsTree org={ORG} repo={REPO} paths={PATHS} focus={focus} />, root));
  const folder = (path: string) => root.querySelector<HTMLLIElement>(`li[data-folder="${path}"]`)!;
  const details = (path: string) => folder(path).querySelector('details')!;
  // What a click on a summary does in a browser: flip `open`, then fire `toggle`.
  const toggle = async (path: string) => {
    const d = details(path);
    await act(() => {
      d.open = !d.open;
      d.dispatchEvent(new Event('toggle'));
    });
  };
  return { root, folder, details, toggle, unmount: () => act(() => mount(null, root)) };
}

describe('the materials folder tree', () => {
  beforeEach(() => sessionStorage.clear());

  it('shows top-level folders collapsed, with their file counts, and hides dot folders', async () => {
    const { root, details, folder } = await mounted();
    expect(details('lectures').open).toBe(false);
    expect(details('labs').open).toBe(false);
    expect(folder('lectures').querySelector('summary')!.textContent).toContain('3 files');
    expect(folder('labs').querySelector('summary')!.textContent).toContain('1 file');
    expect(root.querySelector('li[data-folder=".github"]')).toBeNull();
    expect(details('lectures').querySelector('summary')).not.toBeNull();
  });

  it('expands and collapses, the count showing only while collapsed', async () => {
    const { details, folder, toggle } = await mounted();
    await toggle('lectures');
    expect(details('lectures').open).toBe(true);
    expect(folder('lectures').querySelector(':scope > details > summary .ft-count')).toBeNull();
    expect(details('lectures/01').open).toBe(false);
    await toggle('lectures/01');
    expect(details('lectures/01').open).toBe(true);
    await toggle('lectures');
    expect(details('lectures').open).toBe(false);
    expect(folder('lectures').querySelector('.ft-count')!.textContent).toBe('3 files');
  });

  it('remembers the opened folders for the repo in sessionStorage', async () => {
    const first = await mounted();
    await first.toggle('labs');
    await first.unmount();
    const again = await mounted();
    expect(again.details('labs').open).toBe(true);
    expect(again.details('lectures').open).toBe(false);
  });

  it('opens a deep-linked folder and its parents', async () => {
    const { details } = await mounted('lectures/01');
    expect(details('lectures').open).toBe(true);
    expect(details('lectures/01').open).toBe(true);
    expect(details('lectures/02').open).toBe(false);
    expect(details('labs').open).toBe(false);
  });

  it('links files to their blob page and folders to their tree page on GitHub', async () => {
    const { root, folder } = await mounted();
    const file = root.querySelector<HTMLAnchorElement>('.materials-tree > li.ft-file a')!;
    expect(file.getAttribute('href')).toBe(`https://github.com/${ORG}/${REPO}/blob/HEAD/SYLLABUS.md`);
    expect(file.getAttribute('target')).toBe('_blank');
    expect(folder('lectures').querySelector('a.ft-gh')!.getAttribute('href')).toBe(`https://github.com/${ORG}/${REPO}/tree/HEAD/lectures`);
  });
});

describe('the folder deep link', () => {
  it('round-trips through the query string to the repo it names', () => {
    const q = folderHref(ORG, REPO, 'lectures/01').split('#')[0];
    const folder = parseSearch(q).folder;
    expect(focusIn(folder, REPO)).toBe('lectures/01');
    expect(focusIn(folder, 'other-repo')).toBeUndefined();
    expect(focusIn(`${REPO}/labs/`, REPO)).toBe('labs');
  });
});
