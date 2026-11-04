// @vitest-environment happy-dom
// What Release copies (plan 2026-11-04, decisions 5-6): the manifest's groups with their heads,
// rows and greyed leftovers, picks from two repos under their names, a group sent elsewhere
// highlighted, the empty state, and the footer's one button opening Release by hand.

import { render, type VNode } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { EnvCtx, type Env } from '../src/env';
import { compileAll } from '../src/edit/glob';
import { groupPicks, type Group } from '../src/model/releaseState';
import { releasePicked } from '../src/ops/defs';
import { ReleaseFoot, ReleaseManifest, RepoSelect, groupKey, type Dests } from '../src/ui/ReleaseManifest';

const SEM = 'hertie-dsl-demo-f2026';
const A = 'course-materials-f2026';
const B = 'lecture-code-f2026';
const trees = {
  [A]: { files: ['README.md', 'SYLLABUS.md', 'labs/week01/lab.ipynb', 'labs/week01/data.csv', 'labs/week01/solutions/lab.ipynb', 'labs/notes.md', 'drafts/x.md'], rules: compileAll(['labs/notes.md'], { defaults: true }) },
  [B]: { files: ['demo.py'], rules: compileAll([], { defaults: true }) },
};
const dests: Dests = { options: ['materials', 'lecture-code'], rows: [{ name: 'materials', note: 'the default' }], reserved: { org: SEM, students: [] } };
const scope = { courseOrg: 'hertie-dsl-demo-course-e1234', cohortOrg: SEM, where: 'Fall 2026' };

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(v: VNode) {
  const open = vi.fn();
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(<EnvCtx.Provider value={{ ops: { open } } as unknown as Env}>{v}</EnvCtx.Provider>, root!));
  return { el: root, open };
}
const shown = (x: Element) => {
  const c = x.cloneNode(true) as HTMLElement;
  c.querySelectorAll('.hint-pop').forEach((h) => h.remove());
  return c.textContent;
};
const manifest = (groups: Group[], over: Partial<Parameters<typeof ReleaseManifest>[0]> = {}) => (
  <ReleaseManifest groups={groups} runRepo="materials" dests={dests} onGroupDest={() => undefined} stateOf={() => 'not_released'} {...over} />
);

describe('the manifest', () => {
  it('says nothing is picked yet', async () => {
    const { el } = await mount(manifest([]));
    expect(el.textContent).toBe('Nothing picked yet. Tick files or folders in the tree.');
  });

  it('groups a picked folder, sibling files and top-level files, with the leftovers greyed', async () => {
    const groups = groupPicks([
      { repo: A, path: 'labs', dir: true }, { repo: A, path: 'README.md', dir: false },
    ], trees);
    const { el } = await mount(manifest(groups, { stateOf: (_g, p) => (p === 'README.md' ? 'behind' : 'not_released') }));
    expect([...el.querySelectorAll('.mf-head')].map((h) => h.textContent)).toEqual(['labs/ · all 2 files', '(top level) · 1 of 2 files']);
    const rows = [...el.querySelectorAll('.mf-rows li')].map(shown);
    expect(rows).toEqual([
      'labs/week01/lab.ipynb→labs/week01/lab.ipynbnot released', 'labs/week01/data.csv→labs/week01/data.csvnot released',
      'labs/week01/solutions/lab.ipynbwithheld by default', 'labs/notes.mdwithheld by labs/notes.md',
      'README.md→README.mdbehind',
    ]);
    expect(el.querySelectorAll('.mf-rows li.mf-out')).toHaveLength(2);
    expect(el.querySelector('.sub-h')).toBeNull();
  });

  it('ships solutions a ! line re-includes, and lists a never-released name as never released', async () => {
    const t = { [A]: { files: ['labs/a.md', 'labs/solutions/s.py', 'labs/.env'], rules: compileAll(['!solutions/'], { defaults: true }) } };
    const { el } = await mount(manifest(groupPicks([{ repo: A, path: 'labs', dir: true }], t)));
    expect([...el.querySelectorAll('.mf-rows li')].map(shown)).toEqual([
      'labs/a.md→labs/a.mdnot released', 'labs/solutions/s.py→labs/solutions/s.pynot released', 'labs/.envnever released',
    ]);
  });

  it('lists picks from two course repos under each repo’s name', async () => {
    const groups = groupPicks([{ repo: A, path: 'README.md', dir: false }, { repo: B, path: 'demo.py', dir: false }], trees);
    const { el } = await mount(manifest(groups));
    expect([...el.querySelectorAll('.mf-repo-h')].map((h) => h.textContent)).toEqual([A, B]);
  });

  it('highlights a group sent to another repo, and edits a group’s destination', async () => {
    const onGroupDest = vi.fn();
    const [g] = groupPicks([{ repo: A, path: 'labs/week01', dir: true }], trees);
    const { el } = await mount(manifest([{ ...g, destRepo: 'lecture-code', destPath: 'week1' }], { onGroupDest }));
    const card = el.querySelector('.mf-group')!;
    expect(card.classList.contains('other')).toBe(true);
    expect(shown(card.querySelector('.mf-rows li')!)).toBe('labs/week01/lab.ipynb→week1/lab.ipynb(!)not released');
    expect(card.querySelector('.mf-warn')!.getAttribute('title')).toBe('This file is being released to a different repo: lecture-code');
    const path = card.querySelector<HTMLInputElement>('.mf-adv input')!;
    expect(path.value).toBe('week1');
    await act(() => {
      path.value = 'labs/one';
      path.dispatchEvent(new Event('input', { bubbles: true }));
    });
    expect(onGroupDest).toHaveBeenCalledWith(groupKey(g), { destPath: 'labs/one' });
    const sel = card.querySelector<HTMLSelectElement>('.mf-adv select')!;
    await act(() => {
      sel.value = 'materials';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    // Back to the run's repo: no override.
    expect(onGroupDest).toHaveBeenLastCalledWith(groupKey(g), { destRepo: undefined });
  });

  it('the To row’s select offers the repos and the picker', async () => {
    const onRunRepo = vi.fn();
    const { el } = await mount(<RepoSelect label="To repo" value="materials" dests={dests} onPick={onRunRepo} />);
    const sel = el.querySelector<HTMLSelectElement>('select')!;
    expect([...sel.options].map((o) => o.textContent)).toEqual(['materials', 'lecture-code', 'Choose a repo…']);
    await act(() => {
      sel.value = '__choose';
      sel.dispatchEvent(new Event('change', { bubbles: true }));
    });
    expect(document.querySelector('#modal-title')!.textContent).toBe('Choose a destination repo');
    expect(onRunRepo).not.toHaveBeenCalled();
  });
});

describe('the Release footer', () => {
  it('counts the picks and opens Release by hand with the paired lists', async () => {
    const groups = groupPicks([{ repo: A, path: 'labs/week01', dir: true }, { repo: B, path: 'demo.py', dir: false }], trees);
    const def = releasePicked(scope, groups, { defaultDestRepo: 'materials' });
    const { el, open } = await mount(<ReleaseFoot paths={2} files={3} def={def} scheduleHref="?cohort=x#schedule" />);
    expect(el.querySelector('.rh-count')!.textContent).toBe('2 paths picked · 3 files');
    const btn = el.querySelector<HTMLButtonElement>('.btn')!;
    expect(btn.textContent).toBe('Release 2 paths');
    await act(() => btn.click());
    expect(open.mock.calls[0][0].args).toEqual({ course_source_repo: `${A},${B}`, course_source_path: 'labs/week01,demo.py', semester_dest_repo: 'materials,materials', semester_dest_path: 'labs/week01,demo.py' });
    expect(el.querySelector('.rh-sched')!.textContent).toBe('Want to pre-automate these releases? Schedule on the calendar →');
  });

  it('is disabled with nothing picked, and offers no Schedule on the calendar then', async () => {
    const { el } = await mount(<ReleaseFoot paths={0} files={0} def={releasePicked(scope, [], { defaultDestRepo: 'materials' })} scheduleHref="?cohort=x#schedule" />);
    expect(el.querySelector<HTMLButtonElement>('.btn')!.disabled).toBe(true);
    expect(el.querySelector('.rh-sched')).toBeNull();
  });
});
