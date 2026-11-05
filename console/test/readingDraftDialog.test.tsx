// @vitest-environment happy-dom
// Generate draft's dialog (decision 0044 point 6): a tick per line, always clickable; while a
// ticked line still has a free-standing ?, the notice stays; the footer counts; Add puts the
// ticked lines into the reading list under their sections. And the button in the editor's slot.

import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import type { Draft } from '../src/model/readingDraft';
import { DRAFT_TEXT, DraftButton, DraftDialog, type DraftAdd } from '../src/ui/ReadingDraft';
import { ReadingList } from '../src/ui/ReadingList';
import type { ListDraft } from '../src/model/scheduleEdit';
import { StaticFiles } from './staticFiles';

// pdf.js does not run here: each file "reads" as these facts.
vi.mock('../src/model/pdfText', () => ({ readPdf: async () => ({ pages: 10, title: 'Deep learning', author: 'Ian Goodfellow', first: [], second: [], last: [], labels: null }) }));

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

  it('shows the first five notes a file; Show all / Show fewer per file', async () => {
    const notes = ['Book', 'Authors', 'Year', 'Publisher', 'Chapter', 'Pages', 'Section'].map((part) => ({ part, level: 'check' as const, text: 't' }));
    await mount(<DraftDialog drafts={[{ ...DRAFTS[0], notes }, { ...DRAFTS[1], notes }, { ...DRAFTS[0], path: 'r/few.pdf', notes: notes.slice(0, 5) }]} onAdd={() => {}} onClose={() => {}} />);
    const lists = () => [...root!.querySelectorAll('.rd-notes')].map((u) => u.children.length);
    const sixth = (i: number) => [...root!.querySelectorAll('.rd-notes')[i].querySelectorAll('b')].some((b) => b.textContent === 'Pages');
    const toggles = () => [...root!.querySelectorAll<HTMLButtonElement>('.rd-more')];
    // Collapsed: the first five only, the sixth not in the DOM; only a file with more than five has the toggle.
    expect(lists()).toEqual([5, 5, 5]);
    expect(sixth(0)).toBe(false);
    expect(toggles().map((b) => b.textContent)).toEqual([DRAFT_TEXT.showAll(7), DRAFT_TEXT.showAll(7)]);
    await act(() => toggles()[0].click());
    expect(lists()).toEqual([7, 5, 5]);
    expect(sixth(0)).toBe(true);
    expect(sixth(1)).toBe(false);
    expect(toggles().map((b) => [b.textContent, b.getAttribute('aria-expanded')])).toEqual([[DRAFT_TEXT.showFewer, 'true'], [DRAFT_TEXT.showAll(7), 'false']]);
    await act(() => toggles()[0].click());
    expect(lists()).toEqual([5, 5, 5]);
    expect(sixth(0)).toBe(false);
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
    await mount(<DraftButton org="o" pdfs={[{ repo: 'm', path: 'r/a.pdf', sha: 's' }]} onAdd={() => {}} />, env);
    expect(button(DRAFT_TEXT.button)!.disabled).toBe(false);
    expect(root!.textContent).toContain(DRAFT_TEXT.hint);
    if (root) render(null, root);
    await mount(<DraftButton org="o" pdfs={[]} onAdd={() => {}} />, env);
    expect(button(DRAFT_TEXT.button)!.disabled).toBe(true);
    expect(root!.textContent).toContain(DRAFT_TEXT.noPdfs);
  });
});

describe('Generate draft in the editor', () => {
  it('drafts from the PDF reading files only, and puts the added lines under their headings, linked relative to the list file', async () => {
    let list: ListDraft = { repo: 'm', path: 'readings/04/READINGS.md', inline: false, text: '# Week 4\n\n## Required readings\n\n- Bishop.\n' };
    const files = new StaticFiles({}, {}, {});
    const read: string[] = [];
    const env = { client: { renderMarkdown: () => new Promise(() => {}), fetchBytes: async (_o: string, _r: string, path: string) => { read.push(path); return new Uint8Array(); } } } as unknown as Env;
    const readingFiles = [{ repo: 'm', path: 'readings/04/a.pdf', sha: 's1' }, { repo: 'm', path: 'readings/04/notes.md', sha: 's2' }, { repo: 'm', path: 'readings/04/b.pdf' }];
    const view = () => <ReadingList id="t" list={list} defaultRepo="m" defaultPath="readings/04/READINGS.md" org="o" repos={['m']} files={files} readingFiles={readingFiles} onChange={(l) => (list = l)} />;
    await mount(view(), env);
    await act(() => button(DRAFT_TEXT.button)!.click());
    for (let i = 0; i < 5; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    // b.pdf is not in the tree yet (no sha), notes.md is no PDF: only a.pdf is read.
    expect(read).toEqual(['readings/04/a.pdf']);
    expect(root!.textContent).toContain(DRAFT_TEXT.title(1));
    await act(() => tick(0).click());
    await act(() => button(DRAFT_TEXT.add(1))!.click());
    expect(list.text).toMatch(/^# Week 4\n\n## Required readings\n\n- Bishop\.\n- Goodfellow, I\. .*\[PDF\]\(a\.pdf\)\n$/);
  });

  it('fetches at most four files at once, uncached, and lists them in order', async () => {
    let flying = 0, most = 0;
    const asked: string[] = [];
    const env = { client: { fetchBytes: async (_o: string, _r: string, path: string) => {
      asked.push(path);
      most = Math.max(most, ++flying);
      await new Promise((r) => setTimeout(r, 0));
      flying--;
      if (path.endsWith('c.pdf')) throw new Error('Not Found');
      return new Uint8Array();
    } } } as unknown as Env;
    const pdfs = ['a', 'b', 'c', 'd', 'e', 'f'].map((n) => ({ repo: 'm', path: `r/${n}.pdf`, sha: `cap-${n}` }));
    await mount(<DraftButton org="o" pdfs={pdfs} onAdd={() => {}} />, env);
    await act(() => button(DRAFT_TEXT.button)!.click());
    for (let i = 0; i < 12; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
    expect(asked).toEqual(pdfs.map((x) => x.path));
    expect(most).toBe(4);
    expect([...root!.querySelectorAll('.rd-file .rd-head .mono')].map((x) => x.textContent)).toEqual(['a.pdf', 'b.pdf', 'd.pdf', 'e.pdf', 'f.pdf', 'c.pdf']);
    expect(root!.textContent).toContain(DRAFT_TEXT.failed('Not Found'));
  });
});
