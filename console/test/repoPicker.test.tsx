// @vitest-environment happy-dom
// The repo pickers (plan 2026-11-04, decision 7): a destination chosen from the semester's rows
// or typed new, a reserved or malformed name refused inline; a course repo added as materials.

import { render, type VNode } from 'preact';
import { act } from 'preact/test-utils';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { RepoPickerModal } from '../src/ui/RepoPickerModal';

const SEM = 'hertie-dsl-demo-f2026';
const rows = [{ name: 'materials', note: 'the default' }, { name: 'lecture-code', note: '2 releases' }, { name: 'datasets', note: 'no releases yet' }];
const reserved = { org: SEM, stems: ['a1'] };

let root: HTMLElement | null = null;
afterEach(() => {
  if (root) render(null, root);
  root?.remove();
  root = null;
});

async function mount(v: VNode) {
  root = document.createElement('div');
  document.body.appendChild(root);
  await act(() => render(v, root!));
  return root;
}
const button = (el: HTMLElement, text: string) => [...el.querySelectorAll<HTMLButtonElement>('.savebar .btn')].find((b) => b.textContent === text)!;
const type = (input: HTMLInputElement, value: string) => act(() => {
  input.value = value;
  input.dispatchEvent(new Event('input', { bubbles: true }));
});

describe('Choose a destination repo', () => {
  it('lists the semester’s repos with their notes, the current one checked', async () => {
    const el = await mount(<RepoPickerModal kind="dest" rows={rows} current="lecture-code" reserved={reserved} onChoose={() => undefined} onClose={() => undefined} />);
    expect(el.querySelector('#modal-title')!.textContent).toBe('Choose a destination repo');
    expect([...el.querySelectorAll('.rp-row')].map((r) => r.textContent)).toEqual(['materialsthe default', 'lecture-code2 releases', 'datasetsno releases yet']);
    expect(el.querySelector<HTMLInputElement>('.rp-row input:checked')!.value).toBe('lecture-code');
    expect(el.textContent).toContain('Or create a new repo');
    expect(el.textContent).toContain('Created private; students and auditors read.');
  });

  it('chooses a row', async () => {
    const onChoose = vi.fn(), onClose = vi.fn();
    const el = await mount(<RepoPickerModal kind="dest" rows={rows} reserved={reserved} onChoose={onChoose} onClose={onClose} />);
    await act(() => el.querySelectorAll<HTMLInputElement>('.rp-row input')[2].click());
    await act(() => button(el, 'Choose').click());
    expect(onChoose).toHaveBeenCalledWith('datasets');
    expect(onClose).toHaveBeenCalled();
  });

  it('refuses a reserved or malformed name inline, and takes a good one', async () => {
    const onChoose = vi.fn();
    const el = await mount(<RepoPickerModal kind="dest" rows={rows} reserved={reserved} onChoose={onChoose} onClose={() => undefined} />);
    const input = el.querySelector<HTMLInputElement>('#rp-new-name')!;
    await type(input, 'semester-config');
    // A typed name takes the place of the rows.
    expect(el.querySelector('.rp-row input:checked')).toBeNull();
    await act(() => button(el, 'Choose').click());
    expect(el.querySelector('.invalid-msg')!.textContent).toBe('That name is reserved for the semester’s config.');
    await type(input, 'a1-octocat');
    expect(el.querySelector('.invalid-msg')).toBeNull();
    await act(() => button(el, 'Choose').click());
    expect(el.querySelector('.invalid-msg')!.textContent).toBe('That name is reserved for a student’s repo.');
    await type(input, 'my repo');
    await act(() => button(el, 'Choose').click());
    expect(el.querySelector('.invalid-msg')!.textContent).toContain('letters, digits');
    expect(onChoose).not.toHaveBeenCalled();
    await type(input, 'datasets-2026');
    await act(() => button(el, 'Choose').click());
    expect(onChoose).toHaveBeenCalledWith('datasets-2026');
  });
});

describe('Add a course repo', () => {
  it('lists the course’s repos and adds the one picked', async () => {
    const onChoose = vi.fn();
    const el = await mount(<RepoPickerModal kind="source" rows={[{ name: 'guest-lectures', note: 'no releases yet' }, { name: 'reading-packs', note: 'updated 2 Oct' }]} onChoose={onChoose} onClose={() => undefined} />);
    expect(el.querySelector('#modal-title')!.textContent).toBe('Add a course repo');
    expect(el.textContent).toContain('Picking one marks it as course materials.');
    expect(el.querySelector('#rp-new-name')).toBeNull();
    await act(() => el.querySelectorAll<HTMLInputElement>('.rp-row input')[1].click());
    await act(() => button(el, 'Add').click());
    expect(onChoose).toHaveBeenCalledWith('reading-packs');
  });
});
