// @vitest-environment happy-dom
// The withhold tree in a real DOM: a click hands the new list up, and a click inside a withheld
// folder changes nothing and says which rule to remove.

import { render as mount } from 'preact';
import { useState } from 'preact/hooks';
import { act } from 'preact/test-utils';
import { describe, expect, it } from 'vitest';
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
});
