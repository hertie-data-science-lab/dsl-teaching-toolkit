// The marking grid's side panel for one row (WP-M3): the long text a row carries (overall
// feedback, feedback per question, private notes) with the row's submission and test links, the
// assignment's feedback snippets, and what the student will see. The grid keeps marks and numbers.
// Built on the operation panel's drawer; every box is the same path-keyed cell edit as the grid's.

import type { ComponentChildren } from 'preact';
import { useEffect, useRef, useState } from 'preact/hooks';
import type { Path } from '../edit/yamlText';
import { insertSnippet } from '../model/marks';

/** Whether the browser grows a textarea with its text by itself (CSS `field-sizing`, Chromium only). */
const sizesItself = () => typeof CSS !== 'undefined' && typeof CSS.supports === 'function' && CSS.supports('field-sizing', 'content');
/** Grow a textarea to its text where the browser does not (Firefox, Safari); its CSS min-height still holds. */
export function grow(el: HTMLTextAreaElement): void {
  if (sizesItself()) return;
  el.style.height = 'auto';
  el.style.height = `${el.scrollHeight + 2}px`;
}

/** One text box in the panel: the sheet cell it edits, and how it is named. */
export interface DrawerBox {
  path: Path;
  /** Which box this is on any row (`fb`, `pn`, `qfb:Q1`): Ctrl/Cmd+Enter keeps it on the next row. */
  field: string;
  heading: string;
  label: string;
  value: string;
  /** Private notes: never sent, so styled apart and offered no snippets. */
  private?: boolean;
}

export interface DrawerLink {
  href: string;
  text: string;
}

export interface MarksDrawerProps {
  /** Changes when the panel moves to another row. */
  rowKey: string;
  eyebrow: string;
  title: string;
  sub?: string;
  boxes: DrawerBox[];
  links: DrawerLink[];
  onText: (path: Path, text: string) => void;
  snippets: string[];
  onSnippets: (list: string[]) => void;
  /** The Student view for this row's person (or a team's members). */
  view: ComponentChildren;
  /** The box to focus when the panel opens or moves (`field`), else the first. */
  focus: string | null;
  /** Whether to take focus at all: not when the panel follows focus moving in the grid. */
  grab: boolean;
  onClose: () => void;
  /** Ctrl/Cmd+Enter in a box: the same box on the next row. */
  onNext: (field: string) => void;
}

export function MarksDrawer(p: MarksDrawerProps) {
  const box = useRef<HTMLElement>(null);
  // The box a snippet goes into: the one last focused in this panel, else the first that takes snippets.
  const last = useRef<{ path: Path; el: HTMLTextAreaElement } | null>(null);
  const [editing, setEditing] = useState(false);
  const [adding, setAdding] = useState('');
  useEffect(() => {
    last.current = null;
    const areas = [...(box.current?.querySelectorAll<HTMLTextAreaElement>('textarea[data-field]') ?? [])];
    areas.forEach(grow);
    if (p.grab) (areas.find((a) => a.dataset.field === p.focus) ?? areas[0])?.focus();
  }, [p.rowKey, p.focus, p.grab]);
  useEffect(() => {
    box.current?.querySelectorAll<HTMLTextAreaElement>('textarea[data-field]').forEach(grow);
  });
  const insert = (s: string) => {
    const target = last.current && box.current?.contains(last.current.el) ? last.current : null;
    const first = p.boxes.find((b) => !b.private);
    const el = target?.el ?? box.current?.querySelector<HTMLTextAreaElement>(`textarea[data-field="${first?.field}"]`) ?? null;
    const path = target?.path ?? first?.path;
    if (!el || !path) return;
    const at = target ? [el.selectionStart ?? el.value.length, el.selectionEnd ?? el.value.length] : [el.value.length, el.value.length];
    const out = insertSnippet(el.value, at[0], at[1], s);
    p.onText(path, out.text);
    el.value = out.text;
    el.focus();
    el.setSelectionRange?.(out.caret, out.caret);
    last.current = { path, el };
  };
  const onKey = (e: KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.stopPropagation();
      return p.onClose();
    }
    const el = e.target as HTMLElement;
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey) && el.tagName === 'TEXTAREA' && el.dataset.field) {
      e.preventDefault();
      p.onNext(el.dataset.field);
    }
  };
  const add = () => {
    if (!adding.trim()) return;
    p.onSnippets([...p.snippets, adding.trim()]);
    setAdding('');
  };
  return (
    <aside class="drawer marks-drawer" aria-labelledby="marks-drawer-title" ref={box} onKeyDown={onKey}>
      <div class="drawer-head">
        <div>
          <div class="eyebrow">{p.eyebrow}</div>
          <h2 id="marks-drawer-title">{p.title}</h2>
          {p.sub ? <div class="slug footnote">{p.sub}</div> : null}
        </div>
        <div class="dh-btns">
          <button class="x" type="button" aria-label="Close the side panel" title="Close (Esc)" onClick={p.onClose}>&times;</button>
        </div>
      </div>
      <div class="drawer-body">
        {p.links.length ? <p class="md-links">{p.links.map((l, i) => <>{i ? ' · ' : ''}<a href={l.href} target="_blank" rel="noopener">{l.text}</a></>)}</p> : null}
        {p.boxes.map((b) => (
          <div class={`field md-box${b.private ? ' private' : ''}`}>
            <label for={`md-${b.field}`}>{b.heading}</label>
            <textarea id={`md-${b.field}`} class="md-text" aria-label={b.label} data-field={b.field} value={b.value}
              onFocus={(e) => { last.current = b.private ? last.current : { path: b.path, el: e.target as HTMLTextAreaElement }; }}
              onInput={(e) => { const t = e.target as HTMLTextAreaElement; grow(t); p.onText(b.path, t.value); }} />
          </div>
        ))}
        <section class="md-snippets" aria-label="Feedback snippets">
          <div class="md-snip-head">
            <h3>Snippets</h3>
            <button class="btn small quiet" type="button" aria-expanded={editing} onClick={() => setEditing(!editing)}>{editing ? 'Done' : 'Edit snippets'}</button>
          </div>
          {editing ? (
            <div class="md-snip-edit">
              {p.snippets.map((s, i) => (
                <div class="md-snip-row">
                  <input type="text" aria-label={`Snippet ${i + 1}`} value={s} onInput={(e) => p.onSnippets(p.snippets.map((x, j) => (j === i ? (e.target as HTMLInputElement).value : x)))} />
                  <button class="x" type="button" aria-label={`Remove snippet ${i + 1}`} onClick={() => p.onSnippets(p.snippets.filter((_, j) => j !== i))}>&times;</button>
                </div>
              ))}
              <div class="md-snip-row">
                <input type="text" aria-label="New snippet" placeholder="A comment you give often" value={adding} onInput={(e) => setAdding((e.target as HTMLInputElement).value)} onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); add(); } }} />
                <button class="btn small outline" type="button" onClick={add}>Add</button>
              </div>
              <p class="footnote">Kept in this assignment’s mark sheet, for every marker; saved with Save marks.</p>
            </div>
          ) : p.snippets.length ? (
            <div class="md-snip-list">
              {p.snippets.map((s) => <button class="chip md-snip" type="button" title="Insert into the feedback box you were last in" onMouseDown={(e) => e.preventDefault()} onClick={() => insert(s)}>{s}</button>)}
            </div>
          ) : <p class="footnote">None yet. Edit snippets to add comments you give often; one click puts one into a feedback box.</p>}
        </section>
        {p.view}
      </div>
    </aside>
  );
}
