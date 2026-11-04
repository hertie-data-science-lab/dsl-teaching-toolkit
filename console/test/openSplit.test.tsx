// @vitest-environment happy-dom
// The Open split button for one file (`ui/OpenSplit.tsx`): the main part the semester copy on
// GitHub, the arrow a menu button opening In the browser (GitHub, github.dev) and On your
// computer (the editor Profile names, or the way to set it up), a document no editor opens
// listing neither github.dev nor an editor.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import type { Setup } from '../src/model/open';
import { OpenSplit } from '../src/ui/OpenSplit';

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(path: string, setup: Setup | null) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<OpenSplit org="sem" repo="materials" path={path} setup={setup} profile="?cohort=sem#profile" />, root!));
  return root;
}
const menu = (el: HTMLElement) => {
  const m = el.querySelector<HTMLElement>('.open-menu')!;
  return { hidden: m.hidden, heads: [...m.querySelectorAll('.pm-h')].map((h) => h.textContent), items: [...m.querySelectorAll<HTMLAnchorElement>('[role="menuitem"]')].map((a) => [a.textContent, a.getAttribute('href')]) };
};

describe('OpenSplit', () => {
  it('opens the file on GitHub, and its arrow the menu of ways to open it', async () => {
    const el = await mount('lectures/05/notes.md', null);
    const main = el.querySelector<HTMLAnchorElement>('.split.small > a.split-main')!;
    expect([main.textContent, main.getAttribute('href'), main.target]).toEqual(['Open', 'https://github.com/sem/materials/blob/HEAD/lectures/05/notes.md', '_blank']);
    const caret = el.querySelector<HTMLButtonElement>('button.split-caret')!;
    expect([caret.getAttribute('aria-haspopup'), caret.getAttribute('aria-expanded')]).toEqual(['menu', 'false']);
    expect(menu(el).hidden).toBe(true);
    await act(() => caret.click());
    expect(caret.getAttribute('aria-expanded')).toBe('true');
    expect(menu(el)).toEqual({
      hidden: false,
      heads: ['In the browser', 'On your computer'],
      items: [
        ['On GitHub', 'https://github.com/sem/materials/blob/HEAD/lectures/05/notes.md'],
        ['On github.dev', 'https://github.dev/sem/materials/blob/HEAD/lectures/05/notes.md'],
        ['Set up your editor in Profile', '?cohort=sem#profile'],
      ],
    });
  });

  it('offers the editor Profile names once it has a folder', async () => {
    const el = await mount('notes.md', { folder: '/Users/a/courses', editor: 'vscode' });
    await act(() => el.querySelector<HTMLButtonElement>('button.split-caret')!.click());
    const last = menu(el).items.at(-1)!;
    expect(last[0]).toBe('In VS Code');
    expect(last[1]).toMatch(/^vscode:\/\/file\/.*\/materials\/notes\.md$/);
  });

  it('lists no github.dev or editor for a document no editor opens', async () => {
    const el = await mount('slides.pdf', { folder: '/Users/a/courses', editor: 'vscode' });
    await act(() => el.querySelector<HTMLButtonElement>('button.split-caret')!.click());
    expect(menu(el).items.map((i) => i[0])).toEqual(['On GitHub']);
    expect(el.querySelector('.open-menu .pm-off')!.textContent).toBe('Not an editable format');
  });
});
