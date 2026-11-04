// A repo's files as a clickable tree over one withhold list (decision 0016). A click on a file
// or folder adds or removes its exact line; a path a broader line withholds says which, and a
// click re-includes it with `!path` only where git allows it. The badges follow the draft.
// `exclude` mode (Course materials, Public website) has a right-aligned button per row; `select` mode
// (the import picker) has a tick per row, everything ticked until a line leaves it out. It reads as a
// file tree (decision 0026 rule 5): indent guides, folder and file icons, withheld rows greyed with
// the name struck through (`ft-out`), the kind chip on kind folders. A folder with something
// withheld inside says how much. `control` swaps the
// per-row control for the caller's own. `SelectionEditor` wraps it for every "which files" setting.
// The Files panel (plan 2026-11-04, decisions 1 and 3) adds `chip` (the row's chip in place of the
// withheld word: state chips, roll-ups), `rowClick` (a click on a file row toggles its tick) and
// `nameLink` (the name opening the viewer, in Browse), and hands in the list compiled with the
// institution's defaults (decision B, `rules`) and the rows worked out once (`model`): the content
// names then draw withheld like any other row ("withheld", its note "withheld by default"), a click
// writing the `!` line that releases one (its note "released by `!line`"), and the never-released
// and never-copied names are fixed rows.

import type { ComponentChildren } from 'preact';
import { useMemo, useState } from 'preact/hooks';
import { bucket, buildTree, denylisted, neverMaterial, toggle, treeModel, type RowModel, type Standing, type TreeNode } from '../edit/badges';
import { carriesDefaults, compileAll, type Rule } from '../edit/glob';
import { FileHead, FolderHead } from './FileTree';

export interface PatternTreeProps {
  files: string[];
  /** The draft list, one line per entry (comments and blanks kept). */
  patterns: string[];
  /** `patterns` compiled, when the caller has them (with the institution's defaults for `.releaseignore`); default: compiled here, without. */
  rules?: Rule[];
  /** The tree and its rows worked out once (`buildTree`, `treeModel`), when the caller has them. */
  model?: { tree: TreeNode[]; rows: Map<string, RowModel> };
  onChange: (lines: string[]) => void;
  mode?: 'exclude' | 'select';
  /** What a withheld file is called here, e.g. "withheld" or "kept off the site". */
  withheldWord?: string;
  releasedWord?: string;
  /** A path no list can release, and the word for it (e.g. never-material names), or null. */
  fixed?: (path: string) => string | null;
  /** A kind folder's kind label, by its path. */
  kinds?: Record<string, string>;
  /** The row's own control in place of the withhold toggle (null: none on that row). */
  control?: (path: string, dir: boolean) => ComponentChildren;
  /** The row's chip in place of the withheld word and the folder's count (state chips, roll-ups). */
  chip?: (path: string, dir: boolean) => ComponentChildren;
  /** A click anywhere on a file row toggles it: `true` with the withhold toggle, else the caller's own (`control`'s). Folder summaries still open and close by name. */
  rowClick?: true | ((path: string) => void);
  /** The file's name as the caller draws it (a button opening the viewer); null keeps the plain name. */
  nameLink?: (path: string) => ComponentChildren;
}

interface Ctx extends Required<Pick<PatternTreeProps, 'patterns' | 'onChange' | 'mode' | 'withheldWord' | 'releasedWord' | 'kinds'>>, Pick<PatternTreeProps, 'control' | 'chip' | 'rowClick' | 'nameLink'> {
  rules: Rule[];
  rows: Map<string, RowModel>;
  fixed: (path: string, s: Standing) => string | null;
  note: { path: string; text: string } | null;
  setNote: (n: { path: string; text: string } | null) => void;
}

/** What no list can release (`selection.publishable`): solutions, tests, grading files, system files. */
const defaultFixed = (path: string) => (neverMaterial(path) || denylisted(path) ? 'never copied' : null);
const NEVER: Partial<Record<Standing['kind'], string>> = { never_copied: 'never copied', never_released: 'never released' };

/** A click on a row: the list after it, or the note saying why it cannot change. */
function clickRow(c: Ctx, path: string, dir: boolean) {
  const t = toggle(c.patterns, path, dir, { defaults: carriesDefaults(c.rules) });
  if ('blocked' in t) {
    const text = t.blocked.byDefault
      ? `Withheld by default with its folder ${t.blocked.at}/. Tick the folder to release it.`
      : t.blocked.at === path
      ? `${t.blocked.rule} also covers everything inside this folder. Remove or narrow that rule to include it.`
      : `${c.withheldWord[0].toUpperCase()}${c.withheldWord.slice(1)} with its folder ${t.blocked.at}/ by ${t.blocked.rule}. Remove that rule to include it.`;
    c.setNote({ path, text });
    return;
  }
  c.setNote(null);
  c.onChange(t.lines);
}

function Control({ n, dir, s, c }: { n: TreeNode; dir: boolean; s: Standing; c: Ctx }) {
  const out = bucket(s) !== 'released';
  const click = (e: Event) => {
    e.preventDefault();
    clickRow(c, n.path, dir);
  };
  const what = `${n.path}${dir ? '/' : ''}`;
  if (c.mode === 'select') {
    const blocked = (s.kind === 'broader' && !s.legal) || (s.kind === 'default' && s.at !== n.path);
    return <input type="checkbox" class="ft-tick" checked={!out} aria-label={`Include ${what}`} title={blocked ? 'Inside a folder that is left out: include the folder first.' : undefined} onClick={click} />;
  }
  return <button class="btn small quiet ft-toggle" type="button" aria-label={`${out ? 'Include' : 'Withhold'} ${what}`} onClick={click}>{out ? 'Include' : 'Withhold'}</button>;
}

/** Why a row stands as it does: the broader line that withholds it, the content default, or the `!` line that re-includes it. */
function Why({ s, word }: { s: Standing; word: string }) {
  if (s.kind === 'broader') return <span class="footnote">{word} by <code>{s.rule}</code></span>;
  if (s.kind === 'default') return <span class="footnote">{word} by default</span>;
  if (s.kind === 'overridden' && s.rule) return <span class="footnote">released by <code>{s.rule}</code></span>;
  return null;
}

/** What a row click leaves to its own target: the tick itself, links, buttons, chips with popovers, the note. */
const OWN_CLICK = 'input, button, a, select, textarea, label, .hint-wrap, .ft-note';

function Node({ n, depth, c }: { n: TreeNode; depth: number; c: Ctx }) {
  const dir = !!n.children;
  const row = c.rows.get(n.path)!;
  const s = row.s;
  const fixed = c.fixed(n.path, s);
  const out = bucket(s) !== 'released';
  const note = c.note?.path === n.path ? <span class="ft-note">{c.note.text}</span> : null;
  const control = c.control ? c.control(n.path, dir) : fixed ? null : <Control n={n} dir={dir} s={s} c={c} />;
  if (!dir) {
    const word = fixed ?? (out ? c.withheldWord : c.releasedWord);
    // A click on the row is its control's: the withhold toggle's, or the caller's own.
    const onRow = c.rowClick === true ? (fixed ? undefined : () => clickRow(c, n.path, false)) : c.rowClick ? () => (c.rowClick as (path: string) => void)(n.path) : undefined;
    return (
      <li class={`ft-file${fixed || out ? ' ft-out' : ''}`} onClick={onRow ? (e) => !(e.target as HTMLElement | null)?.closest(OWN_CLICK) && onRow() : undefined}>
        {c.mode === 'select' ? control : null}
        <FileHead name={n.name}>{c.nameLink?.(n.path) ?? undefined}</FileHead>
        {c.chip ? c.chip(n.path, false) : word ? <span class={`chip ${fixed || out ? 'amber' : ''}`}>{word}</span> : null}
        <Why s={s} word={c.withheldWord} />
        {c.mode === 'exclude' ? control : null}
        {note}
      </li>
    );
  }
  return (
    <li class={`ft-dir${fixed || out ? ' ft-out' : ''}`}>
      <details open={depth === 0}>
        <summary>
          {c.mode === 'select' ? control : null}
          <FolderHead name={n.name} />
          {c.kinds[n.path] ? <span class="chip">{c.kinds[n.path]}</span> : null}
          {c.chip ? c.chip(n.path, true)
            : fixed ? <span class="chip amber">{fixed}</span> : out ? <span class="chip amber">{c.withheldWord}</span> : row.withheld ? <span class="chip amber">{row.withheld} {c.withheldWord}</span> : null}
          <Why s={s} word={c.withheldWord} />
          {c.mode === 'exclude' ? control : null}
          {note}
        </summary>
        <ul>{n.children!.map((x) => <Node n={x} depth={depth + 1} c={c} />)}</ul>
      </details>
    </li>
  );
}

export function PatternTree(p: PatternTreeProps) {
  const [note, setNote] = useState<{ path: string; text: string } | null>(null);
  const rules = useMemo(() => p.rules ?? compileAll(p.patterns), [p.rules, p.patterns]);
  // Under the institution's defaults the standing says which rows are fixed; any other list falls back to the denylist.
  const own = p.fixed ?? (carriesDefaults(rules) ? () => null : defaultFixed);
  const fixed = (path: string, s: Standing) => NEVER[s.kind] ?? own(path);
  const model = useMemo(() => {
    if (p.model) return p.model;
    const tree = buildTree(p.files);
    return { tree, rows: treeModel(tree, rules, p.patterns, { fixed: (path) => own(path) }) };
  }, [p.model, p.files, rules, p.patterns, p.fixed]);
  if (!p.files.length) return <p class="footnote">The repo has no files yet.</p>;
  const c: Ctx = {
    patterns: p.patterns, onChange: p.onChange, mode: p.mode ?? 'exclude',
    withheldWord: p.withheldWord ?? 'withheld', releasedWord: p.releasedWord ?? 'released to students',
    fixed, kinds: p.kinds ?? {}, rules, rows: model.rows,
    control: p.control, chip: p.chip, rowClick: p.rowClick, nameLink: p.nameLink, note, setNote,
  };
  return <ul class={`file-tree${c.mode === 'select' ? ' select' : ''}`}>{model.tree.map((n) => <Node n={n} depth={0} c={c} />)}</ul>;
}
