// @vitest-environment happy-dom
// The withhold tree in a real DOM: a click hands the new list up, and a click inside a withheld
// folder changes nothing and says which rule to remove.

import { readFileSync } from 'node:fs';
import { options, render as mount } from 'preact';
import { useState } from 'preact/hooks';
import { act } from 'preact/test-utils';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { PatternTree } from '../src/ui/PatternTree';

const FILES = ['SYLLABUS.md', 'labs/01/lab.pdf', 'lectures/01/a.pdf'];

function Harness({ start, seen }: { start: string[]; seen: string[][] }) {
  const [lines, setLines] = useState(start);
  return <PatternTree files={FILES} patterns={lines} onChange={(l) => { seen.push(l); setLines(l); }} />;
}

async function mounted(start: string[]) {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const seen: string[][] = [];
  await act(() => mount(<Harness start={start} seen={seen} />, root));
  const button = (label: string) => root.querySelector<HTMLButtonElement>(`button[aria-label="${label}"]`)!;
  return { root, seen, button };
}

describe('clicking the withhold tree', () => {
  it('withholds a file, then releases it, and the badge follows the draft', async () => {
    const { root, seen, button } = await mounted([]);
    await act(() => button('Withhold lectures/01/a.pdf').click());
    expect(seen.at(-1)).toEqual(['lectures/01/a.pdf']);
    expect(root.textContent).toContain('1 withheld');
    await act(() => button('Include lectures/01/a.pdf').click());
    expect(seen.at(-1)).toEqual([]);
  });

  it('re-includes a file a broader pattern withholds', async () => {
    const { seen, button } = await mounted(['*.pdf']);
    await act(() => button('Include lectures/01/a.pdf').click());
    expect(seen.at(-1)).toEqual(['*.pdf', '!lectures/01/a.pdf']);
  });

  it('inside a withheld folder, changes nothing and names the folder rule', async () => {
    const { root, seen, button } = await mounted(['labs/']);
    await act(() => button('Include labs/01/lab.pdf').click());
    expect(seen).toEqual([]);
    expect(root.querySelector('.ft-note')!.textContent).toBe('Withheld with its folder labs/ by labs/. Remove that rule to include it.');
    await act(() => button('Include labs/').click());
    expect(seen.at(-1)).toEqual([]);
  });

  it('says a /** rule covers a folder’s files, and uses the tree’s own word', async () => {
    const root = document.createElement('div');
    document.body.appendChild(root);
    await act(() => mount(<PatternTree files={FILES} patterns={['lectures/**']} onChange={() => {}} withheldWord="kept off" />, root));
    expect(root.textContent).toContain('kept off by lectures/**');
    await act(() => root.querySelector<HTMLButtonElement>('button[aria-label="Include lectures/01/"]')!.click());
    expect(root.querySelector('.ft-note')!.textContent).toBe('lectures/** also covers everything inside this folder. Remove or narrow that rule to include it.');
  });
});

// A user's click runs the microtask checkpoint (Preact's re-render) after the listener and
// before the browser's activation steps, which revert a prevented checkbox; a scripted click
// runs it after them. Rendering synchronously puts the re-render where a user's click has it.
describe('the select tree’s ticks follow the list (W1)', () => {
  let was: typeof options.debounceRendering;
  beforeEach(() => { was = options.debounceRendering; options.debounceRendering = (cb) => cb(); });
  afterEach(() => { options.debounceRendering = was; });
  function Select({ start, rowClick }: { start: string[]; rowClick?: true }) {
    const [lines, setLines] = useState(start);
    return <PatternTree files={FILES} patterns={lines} mode="select" rowClick={rowClick} onChange={setLines} />;
  }
  async function ticks(start: string[], rowClick?: true) {
    const root = document.createElement('div');
    document.body.appendChild(root);
    mount(<Select start={start} rowClick={rowClick} />, root);
    return { root, tick: (what: string) => root.querySelector<HTMLInputElement>(`input[aria-label="Include ${what}"]`)! };
  }

  it('unticking a folder leaves its box unticked, and ticking it again ticks it', async () => {
    const { tick } = await ticks([]);
    expect(tick('labs/').checked).toBe(true);
    tick('labs/').click();
    expect(tick('labs/').checked).toBe(false);
    expect(tick('labs/01/lab.pdf').checked).toBe(false);
    tick('labs/').click();
    expect(tick('labs/').checked).toBe(true);
  });

  it('a file’s box follows a click on the box and on its row', async () => {
    const { root, tick } = await ticks([], true);
    tick('SYLLABUS.md').click();
    expect(tick('SYLLABUS.md').checked).toBe(false);
    (tick('SYLLABUS.md').closest('li') as HTMLElement).click();
    expect(tick('SYLLABUS.md').checked).toBe(true);
    expect(root.querySelectorAll('input:checked').length).toBe(root.querySelectorAll('input').length);
  });

  it('a blocked tick stays as the list has it', async () => {
    const { root, tick } = await ticks(['labs/']);
    tick('labs/01/lab.pdf').click();
    expect(tick('labs/01/lab.pdf').checked).toBe(false);
    expect(root.querySelector('.ft-note')).not.toBeNull();
  });
});

describe('a long name stays beside its icon (C1)', () => {
  it('draws icon and name in one head that never wraps, the name breaking inside itself', async () => {
    const style = document.createElement('style');
    style.textContent = readFileSync(`${__dirname}/../src/styles/console.css`, 'utf8');
    document.head.appendChild(style);
    try {
      const root = document.createElement('div');
      document.body.appendChild(root);
      const long = '10_policy-approaches-and-responsible-implementation';
      await act(() => mount(<PatternTree files={[`${long}/notes.md`]} patterns={[]} mode="select" onChange={() => {}} />, root));
      const head = root.querySelector<HTMLElement>('summary > .ft-head')!;
      expect([...head.children].map((c) => c.className)).toEqual(['ft-closed', 'ft-opened', 'ft-name']);
      expect(head.previousElementSibling!.classList.contains('ft-tick')).toBe(true);
      const h = getComputedStyle(head);
      expect([h.display, h.flexWrap, parseFloat(h.minWidth)]).toEqual(['inline-flex', 'nowrap', 0]);
      expect(h.maxWidth).toBe('calc(100% - 21px)');
      const n = getComputedStyle(head.querySelector('.ft-name')!);
      expect([parseFloat(n.minWidth), n.overflowWrap]).toEqual([0, 'anywhere']);
      expect(root.querySelector('li.ft-file > .ft-head > .ft-icon, li.ft-file > .ft-head > svg')).not.toBeNull();
    } finally {
      style.remove();
    }
  });
});
