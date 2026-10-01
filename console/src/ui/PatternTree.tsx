// A repo's files as a clickable tree over one withhold list (decision 0016). A click on a file
// or folder adds or removes its exact line; a path a broader line withholds says which, and a
// click re-includes it with `!path` only where git allows it. The badges follow the draft.
// `exclude` mode (Handout materials, Public website) has a right-aligned button per row; `select` mode
// (the import picker) has a tick per row, everything ticked until a line leaves it out. It reads as a
// file tree (decision 0026 rule 5): indent guides, folder and file icons, withheld rows greyed with
// the name struck through (`ft-out`), the kind chip on top folders.

import { useState } from 'preact/hooks';
import { buildTree, neverMaterial, standing, toggle, type Standing, type TreeNode } from '../edit/badges';
import { compileAll, withheldBy, type Rule } from '../edit/glob';
import { File, Folder } from './icons';

export interface PatternTreeProps {
  files: string[];
  /** The draft list, one line per entry (comments and blanks kept). */
  patterns: string[];
  onChange: (lines: string[]) => void;
  mode?: 'exclude' | 'select';
  /** What a withheld file is called here, e.g. "withheld" or "kept off the site". */
  withheldWord?: string;
  releasedWord?: string;
  /** A path no list can release, and the word for it (e.g. never-material names), or null. */
  fixed?: (path: string) => string | null;
  /** A top-level folder's kind label. */
  kinds?: Record<string, string>;
}

interface Ctx extends Required<Pick<PatternTreeProps, 'patterns' | 'onChange' | 'mode' | 'withheldWord' | 'releasedWord' | 'fixed' | 'kinds'>> {
  rules: Rule[];
  note: { path: string; text: string } | null;
  setNote: (n: { path: string; text: string } | null) => void;
}

const defaultFixed = (path: string) => (neverMaterial(path) ? 'never copied' : null);

function withheldCount(n: TreeNode, c: Ctx): number {
  if (!n.children) return c.fixed(n.path) || withheldBy(c.rules, n.path) ? 1 : 0;
  return n.children.reduce((sum, x) => sum + withheldCount(x, c), 0);
}

function Control({ n, dir, s, c }: { n: TreeNode; dir: boolean; s: Standing; c: Ctx }) {
  const out = s.kind === 'own' || s.kind === 'broader';
  const click = (e: Event) => {
    e.preventDefault();
    const t = toggle(c.patterns, n.path, dir);
    if ('blocked' in t) {
      const text = t.blocked.at === n.path
        ? `${t.blocked.rule} also covers everything inside this folder. Remove or narrow that rule to include it.`
        : `${c.withheldWord[0].toUpperCase()}${c.withheldWord.slice(1)} with its folder ${t.blocked.at}/ by ${t.blocked.rule}. Remove that rule to include it.`;
      c.setNote({ path: n.path, text });
      return;
    }
    c.setNote(null);
    c.onChange(t.lines);
  };
  const what = `${n.path}${dir ? '/' : ''}`;
  if (c.mode === 'select') {
    const blocked = s.kind === 'broader' && !s.legal;
    return <input type="checkbox" class="ft-tick" checked={!out} aria-label={`Include ${what}`} title={blocked ? 'Inside a folder that is left out: include the folder first.' : undefined} onClick={click} />;
  }
  return <button class="btn small quiet ft-toggle" type="button" aria-label={`${out ? 'Include' : 'Withhold'} ${what}`} onClick={click}>{out ? 'Include' : 'Withhold'}</button>;
}

function Why({ s, word }: { s: Standing; word: string }) {
  return s.kind === 'broader' ? <span class="footnote">{word} by <code>{s.rule}</code></span> : null;
}

function Node({ n, depth, c }: { n: TreeNode; depth: number; c: Ctx }) {
  const dir = !!n.children;
  const fixed = c.fixed(n.path);
  const s = standing(c.rules, c.patterns, n.path, dir);
  const out = s.kind === 'own' || s.kind === 'broader';
  const note = c.note?.path === n.path ? <span class="ft-note">{c.note.text}</span> : null;
  const control = fixed ? null : <Control n={n} dir={dir} s={s} c={c} />;
  if (!dir) {
    const word = fixed ?? (out ? c.withheldWord : c.releasedWord);
    return (
      <li class={`ft-file${fixed || out ? ' ft-out' : ''}`}>
        {c.mode === 'select' ? control : null}
        <File />
        <span class="ft-name">{n.name}</span>
        <span class={`chip ${fixed || out ? 'amber' : ''}`}>{word}</span>
        <Why s={s} word={c.withheldWord} />
        {c.mode === 'exclude' ? control : null}
        {note}
      </li>
    );
  }
  const count = withheldCount(n, c);
  return (
    <li class={`ft-dir${out ? ' ft-out' : ''}`}>
      <details open={depth === 0}>
        <summary>
          {c.mode === 'select' ? control : null}
          <span class="ft-closed"><Folder /></span><span class="ft-opened"><Folder open /></span>
          <span class="ft-name">{n.name}/</span>
          {depth === 0 && c.kinds[n.name] ? <span class="chip">{c.kinds[n.name]}</span> : null}
          {out ? <span class="chip amber">{c.withheldWord}</span> : count ? <span class="chip amber">{count} {c.withheldWord}</span> : null}
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
  if (!p.files.length) return <p class="footnote">The repo has no files yet.</p>;
  const c: Ctx = {
    patterns: p.patterns, onChange: p.onChange, mode: p.mode ?? 'exclude',
    withheldWord: p.withheldWord ?? 'withheld', releasedWord: p.releasedWord ?? 'released to students',
    fixed: p.fixed ?? defaultFixed, kinds: p.kinds ?? {}, rules: compileAll(p.patterns),
    note, setNote,
  };
  return <ul class={`file-tree${c.mode === 'select' ? ' select' : ''}`}>{buildTree(p.files).map((n) => <Node n={n} depth={0} c={c} />)}</ul>;
}
