// @vitest-environment happy-dom
// Generate draft's dialog (decision 0044 point 6): a tick per line, always clickable; while a
// ticked line still has a free-standing ?, the notice stays; the footer counts; Add puts the
// ticked lines into the reading list under their sections. And the button in the editor's slot.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import type { Draft } from '../src/model/readingDraft';
import { DRAFT_TEXT, DraftButton, DraftDialog, type DraftAdd } from '../src/ui/ReadingDraft';
import { ReadingList } from '../src/ui/ReadingList';
import type { ListDraft } from '../src/model/scheduleEdit';
import { StaticFiles } from './staticFiles';

const DRAFTS: Draft[] = [
  { path: 'readings/04/s4-vajjala-2024.pdf', section: 'required', line: 'Vajjala, S. (2020). *Practical natural language processing* (Ch. 3, pp. 92–106). O\'Reilly Media.', notes: [{ part: 'Pages', level: 'found', text: '92 printed on page 1 and 106 on page 15: 15 pages' }] },
  { path: 'readings/04/s4-hobson-dyshel-2024-opt.pdf', section: 'additional', line: 'Lane, H., & Dyshel, M. (2024). *Natural language processing in action* (2nd ed., Ch. 6, pp. ?). Manning Publications.', notes: [{ part: 'Pages', level: '?', text: 'Read 3 at the start and 2017 on page 57, which does not fit the file\'s 57 pages' }] },
];

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});
async function mount(view: preact.VNode, env: Env | null = null) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={env}>{view}</EnvCtx.Provider>, root!));
  return root;
}
const button = (text: string) => [...root!.querySelectorAll('button')].find((b) => b.textContent?.trim() === text);
const tick = (i: number) => root!.querySelectorAll<HTMLButtonElement>('.rd-tick')[i];
const notices = () => root!.querySelectorAll('.rd-unknown').length;
const footer = () => root!.querySelector('.rd-foot .rd-count')!.textContent;

describe('the draft dialog', () => {
  it('ticks any line; a ticked line with a ? keeps the notice; the footer counts; Add is off at 0', async () => {
    await mount(<DraftDialog drafts={DRAFTS} onAdd={() => {}} onClose={() => {}} />);
    expect(root!.textContent).toContain(DRAFT_TEXT.title(2));
    expect(root!.textContent).toContain(DRAFT_TEXT.lede);
    expect([...root!.querySelectorAll('.rd-notes .chip')].map((c) => [c.textContent, c.className])).toEqual([['found', 'chip ok'], ['?', 'chip bad']]);
    expect(footer()).toBe(DRAFT_TEXT.footer(0, 2, 0));
    expect(button(DRAFT_TEXT.add(0))!.disabled).toBe(true);
    await act(() => tick(1).click());
    expect(tick(1).getAttribute('aria-pressed')).toBe('true');
    expect(notices()).toBe(1);
    expect(root!.querySelector('.rd-unknown')!.textContent).toContain(DRAFT_TEXT.unknown);
    expect(footer()).toBe('1 of 2 ticked · 1 with a ? go in as written. Unticked lines are left out.');
    // Filling the unknown in clears the notice; the tick stays.
    const input = root!.querySelectorAll<HTMLInputElement>('.rd-line input')[1];
    await act(() => { input.value = input.value.replace('pp. ?', 'pp. 1–86'); input.dispatchEvent(new Event('input', { bubbles: true })); });
    expect(notices()).toBe(0);
    expect(footer()).toBe(DRAFT_TEXT.footer(1, 2, 0));
    await act(() => tick(0).click());
    expect(button(DRAFT_TEXT.add(2))!.disabled).toBe(false);
    await act(() => tick(0).click());
    expect(tick(0).getAttribute('aria-pressed')).toBe('false');
    expect(footer()).toBe(DRAFT_TEXT.footer(1, 2, 0));
  });

  it('adds the ticked lines with their chosen sections and closes; Cancel adds nothing', async () => {
    let added: DraftAdd[] | null = null;
    let closed = 0;
    await mount(<DraftDialog drafts={DRAFTS} onAdd={(a) => (added = a)} onClose={() => closed++} />);
    await act(() => tick(0).click());
    const sel = root!.querySelector<HTMLSelectElement>('select[aria-label="Section of s4-vajjala-2024.pdf"]')!;
    await act(() => { sel.value = 'additional'; sel.dispatchEvent(new Event('change', { bubbles: true })); });
    await act(() => button(DRAFT_TEXT.add(1))!.click());
    expect(added).toEqual([{ section: 'additional', line: DRAFTS[0].line, path: DRAFTS[0].path }]);
    expect(closed).toBe(1);
    if (root) render(null, root);
    added = null;
    await mount(<DraftDialog drafts={DRAFTS} onAdd={(a) => (added = a)} onClose={() => closed++} />);
    await act(() => tick(0).click());
    await act(() => button('Cancel')!.click());
    expect(added).toBeNull();
    expect(closed).toBe(2);
  });

  it('names a file it could not read, with no line to tick', async () => {
    await mount(<DraftDialog drafts={[]} failed={[{ path: 'readings/04/broken.pdf', message: 'Invalid PDF structure.' }]} onAdd={() => {}} onClose={() => {}} />);
    expect(root!.textContent).toContain('broken.pdf');
    expect(root!.textContent).toContain(DRAFT_TEXT.failed('Invalid PDF structure.'));
    expect(root!.querySelectorAll('.rd-tick').length).toBe(0);
  });
});

describe('the Generate draft button', () => {
  const env = { client: {} } as unknown as Env;
  it('is on with PDF reading files, with the hint; off with none, saying why', async () => {
    await mount(<DraftButton org="o" pdfs={[{ repo: 'm', path: 'r/a.pdf', sha: 's' }, { repo: 'm', path: 'r/notes.md', sha: 't' }]} onAdd={() => {}} />, env);
    expect(button(DRAFT_TEXT.button)!.disabled).toBe(false);
    expect(root!.textContent).toContain(DRAFT_TEXT.hint);
    if (root) render(null, root);
    await mount(<DraftButton org="o" pdfs={[{ repo: 'm', path: 'r/notes.md', sha: 't' }]} onAdd={() => {}} />, env);
    expect(button(DRAFT_TEXT.button)!.disabled).toBe(true);
    expect(root!.textContent).toContain(DRAFT_TEXT.noPdfs);
  });
});

describe('the editor’s draft slot', () => {
  it('puts added lines into the list under their headings, linked relative to the list file', async () => {
    let list: ListDraft = { repo: 'm', path: 'readings/04/READINGS.md', text: '# Week 4\n\n## Required readings\n\n- Bishop.\n', sha: 's', inline: false, loaded: true };
    const files = new StaticFiles({}, {}, {});
    const view = () => (
      <ReadingList id="t" list={list} defaultRepo="m" defaultPath="readings/04/READINGS.md" org="o" repos={['m']} files={files} readingFiles={[]} onChange={(l) => (list = l)}
        draft={(add, disabled) => <button type="button" disabled={disabled} onClick={() => add([{ section: 'required', line: 'A (2020).', path: 'readings/04/a.pdf' }, { section: 'additional', line: 'B (2021).', path: 'readings/04/b.pdf' }])}>slot</button>} />
    );
    await mount(view());
    await act(() => button('slot')!.click());
    expect(list.text).toBe('# Week 4\n\n## Required readings\n\n- Bishop.\n- A (2020). [PDF](a.pdf)\n\n## Additional readings\n\n- B (2021). [PDF](b.pdf)\n');
  });
});
