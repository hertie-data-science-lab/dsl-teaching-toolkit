// One editor for every "which files" question (decision 0040): what students never get
// (`.releaseignore`, no chip row), what the semester site hosts (`student_site.host`) and
// what the open course site publishes (`open_course_site.publish`). A kind-chip row (In/Out
// per kind, with how many files each shows) above the PatternTree; clicking the tree writes
// `exclude` lines, and every file's verdict comes from the engine's rule (`model/selection`).

import { KINDS } from '../model/policy';
import { READINGS, SELECTION_KINDS, denied, hostable, select, withDefaults, type Floor, type KindOf, type Sel } from '../model/selection';
import { PatternTree } from './PatternTree';

export interface SelectionEditorProps {
  files: string[];
  /** The selection as written. */
  value: Sel;
  onChange: (sel: Sel) => void;
  /** The use's defaults for a key left out; absent = the withhold use (exclude only, no chips). */
  defaults?: Sel;
  kindOf?: KindOf;
  floor?: Floor;
  /** Hosting: only a file a browser renders gets a copy (`site._hostable`). */
  rendered?: boolean;
  /** Patterns always shown whatever the selection says (a readings file marked `public: true`). */
  also?: string[];
  /** What a shown file is called ("hosted", "published", "released to students"). */
  inWord: string;
  /** What a file a line leaves out is called. */
  outWord?: string;
}

const LABEL: Record<string, string> = Object.fromEntries(KINDS.map((k) => [k.plural ?? k.key, k.label]));
const noKind: KindOf = () => null;

/** The files the selection shows, by the engine's rule. */
export function shownFiles(p: Pick<SelectionEditorProps, 'files' | 'value' | 'defaults' | 'kindOf' | 'floor' | 'rendered' | 'also'>): string[] {
  const base = p.defaults ? withDefaults(p.value, p.defaults) : { exclude: p.value.exclude ?? [] };
  const use = p.also?.length ? { ...base, include: [...(base.include ?? []), ...p.also] } : base;
  const kindOf = p.kindOf ?? noKind;
  return p.rendered ? hostable(p.files, use, kindOf, p.floor) : select(p.files, kindOf, { use, floor: p.floor });
}

export function SelectionEditor(p: SelectionEditorProps) {
  const kindOf = p.kindOf ?? noKind;
  const shown = new Set(shownFiles(p));
  const files = new Set(p.files);
  const eff = p.defaults ? withDefaults(p.value, p.defaults) : p.value;
  const lines = p.value.exclude ?? (p.defaults ? (p.defaults.exclude ?? []) : []);
  const outWord = p.outWord ?? 'left out';
  // Out for a reason the tree's lines do not give - its kind, its type, the floor - says so.
  const byKind = p.defaults ? new Set(shownFiles({ ...p, value: { ...p.value, exclude: [] } })) : null;
  const fixed = (path: string): string | null => {
    // A release leaves the denylist behind unless a schedule entry names the path itself.
    if (denied(path)) return p.defaults ? 'never public' : 'only if named';
    return byKind && files.has(path) && !byKind.has(path) ? 'not shown' : null;
  };
  const toggleKind = (kind: string) => {
    const now = eff.kinds ?? SELECTION_KINDS;
    p.onChange({ ...p.value, kinds: now.includes(kind) ? now.filter((k) => k !== kind) : SELECTION_KINDS.filter((k) => k === kind || now.includes(k)) });
  };
  const count = (kind: string) => p.files.filter((f) => shown.has(f) && kindOf(f) === kind).length;
  return (
    <div class="selection-editor">
      {p.defaults ? (
        <div class="chips sel-kinds" role="group" aria-label="Kinds">
          {SELECTION_KINDS.map((kind) => {
            const on = eff.kinds === undefined || eff.kinds.includes(kind);
            const n = count(kind);
            const red = kind === READINGS && on;
            return (
              <button type="button" class={`chip${red ? ' bad' : on ? ' ok' : ''}`} aria-pressed={on} title={red ? 'Readings are copyrighted: shown publicly only with their licence.' : undefined} onClick={() => toggleKind(kind)}>
                {LABEL[kind] ?? kind}: {on ? 'In' : 'Out'}{on ? ` · ${n}` : ''}
              </button>
            );
          })}
        </div>
      ) : null}
      <PatternTree files={p.files} patterns={lines} onChange={(exclude) => p.onChange({ ...p.value, exclude })} withheldWord={outWord} releasedWord={p.inWord} fixed={fixed} />
      {p.defaults ? (
        <div class="field">
          <label for="sel-include">Also show <span class="default">patterns, as in .gitignore; beats the kinds, not the lines that leave a file out</span></label>
          <textarea class="code" id="sel-include" value={(p.value.include ?? []).join('\n')} onInput={(e) => {
            const include = (e.target as HTMLTextAreaElement).value.split('\n').map((l) => l.trim()).filter(Boolean);
            p.onChange({ ...p.value, include: include.length ? include : undefined });
          }} />
        </div>
      ) : null}
    </div>
  );
}
