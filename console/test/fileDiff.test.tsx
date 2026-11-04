// @vitest-environment happy-dom
// A file's diff in the console (`ui/FileDiff.tsx`): the course blob against the semester's, one
// unified diff with its hunks headed, a binary or unreadable side said so, the cap, and GitHub's
// per-file compare anchor.

import { createHash } from 'node:crypto';
import { render } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it } from 'vitest';
import { FileDiff, NOT_DIFFABLE, diffAnchor, diffRows } from '../src/ui/FileDiff';
import { StaticFiles } from './staticFiles';

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(files: StaticFiles, course: [string, string, string] | null, semester: [string, string, string] | null) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<FileDiff files={files} course={course} semester={semester} />, root!));
  for (let i = 0; i < 10; i++) await act(async () => { await new Promise((r) => setTimeout(r, 0)); });
  return root;
}

const C: [string, string, string] = ['c', 'course', 'a.md'];
const S: [string, string, string] = ['s', 'materials', 'a.md'];

describe('FileDiff', () => {
  it('renders a unified diff of two differing texts, + and - lines classed, hunks headed', async () => {
    const lines = Array.from({ length: 30 }, (_, i) => `line ${i}`);
    const edited = lines.map((l, i) => (i === 2 ? 'line two, edited' : i === 25 ? 'line 25!' : l));
    const files = new StaticFiles({ 'c/course/a.md': `${lines.join('\n')}\n`, 's/materials/a.md': `${edited.join('\n')}\n` });
    const el = await mount(files, C, S);
    const pre = el.querySelector('pre.file-diff')!;
    expect([...pre.querySelectorAll('.fd-hunk')].map((h) => h.textContent!.trim())).toEqual(['@@ -1,6 +1,6 @@', '@@ -23,7 +23,7 @@']);
    expect([...pre.querySelectorAll('.fd-del')].map((x) => x.textContent!.trim())).toEqual(['-line 2', '-line 25']);
    expect([...pre.querySelectorAll('.fd-add')].map((x) => x.textContent!.trim())).toEqual(['+line two, edited', '+line 25!']);
    expect(el.textContent).not.toContain('Diff truncated');
  });

  it('diffs a file only one side has against nothing', async () => {
    const el = await mount(new StaticFiles({ 's/materials/a.md': 'new\n' }), null, S);
    expect([...el.querySelectorAll('.fd-add')].map((x) => x.textContent!.trim())).toEqual(['+new']);
  });

  it('says a binary file is no text to diff', async () => {
    const el = await mount(new StaticFiles({ 'c/course/a.md': '%PDF\u0000\u0001', 's/materials/a.md': 'x' }), C, S);
    expect(el.querySelector('pre.file-diff')).toBeNull();
    expect(el.textContent).toBe(NOT_DIFFABLE);
  });

  it('caps a long diff and says so', () => {
    const hunk = { oldStart: 1, oldLines: 3000, newStart: 1, newLines: 3000, lines: Array.from({ length: 3000 }, (_, i) => `+${i}`) };
    const out = diffRows({ hunks: [hunk] });
    expect(out.rows).toHaveLength(2000);
    expect(out.truncated).toBe(true);
  });

  it('anchors a file on a GitHub compare page by its path’s sha256', async () => {
    const path = 'lectures/05_trees/notes.md';
    expect(await diffAnchor(path)).toBe(`diff-${createHash('sha256').update(path).digest('hex')}`);
  });
});
