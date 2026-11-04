// A file's diff in the console (the Released materials page's "Released materials that differ from their source files" panel): the course
// blob against the semester's default-branch blob, both read with the signed-in token
// (`Files.file`), as one unified diff (the `diff` package, imported on first draw). Text files
// only; a binary or very large file says so. Capped at `DIFF_CAP` lines. `useDiff` is the
// reading on its own, so a row can show its line counts (`lineCounts`) once its diff is read. `diffAnchor` is GitHub's
// per-file anchor on a compare page (`#diff-<sha256 hex of the path>`).

import { useEffect, useState } from 'preact/hooks';
import type { StructuredPatch } from 'diff';
import type { FileState, Files } from '../model/files';

export const DIFF_CAP = 2000;
/** Above this many characters a side is not diffed. */
const MAX_CHARS = 512_000;
export const NOT_DIFFABLE = 'Binary or too large to diff';

/** GitHub's anchor for one file on a compare or pull request page. */
export async function diffAnchor(path: string): Promise<string> {
  const d = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(path));
  return `diff-${[...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, '0')).join('')}`;
}

/** A side's text: '' when the file is absent there, null when it is no text to diff, undefined while it loads. */
export function sideText(f: FileState): string | null | undefined {
  if (f.kind === 'loading') return undefined;
  if (f.kind === 'absent') return '';
  if (f.kind === 'error') return null;
  return f.text.length > MAX_CHARS || /[\u0000�]/.test(f.text) ? null : f.text;
}

export interface DiffRow {
  cls: 'fd-hunk' | 'fd-add' | 'fd-del' | 'fd-ctx';
  text: string;
}

/** A patch's hunks as rows, each hunk headed, capped at `cap` rows (`truncated` then). */
export function diffRows(patch: Pick<StructuredPatch, 'hunks'>, cap = DIFF_CAP): { rows: DiffRow[]; truncated: boolean } {
  const rows: DiffRow[] = [];
  for (const h of patch.hunks) {
    rows.push({ cls: 'fd-hunk', text: `@@ -${h.oldStart},${h.oldLines} +${h.newStart},${h.newLines} @@` });
    for (const l of h.lines) {
      if (l.startsWith('\\')) continue; // "\ No newline at end of file"
      rows.push({ cls: l[0] === '+' ? 'fd-add' : l[0] === '-' ? 'fd-del' : 'fd-ctx', text: l });
    }
    if (rows.length > cap) return { rows: rows.slice(0, cap), truncated: true };
  }
  return { rows, truncated: false };
}

type DiffModule = typeof import('diff');
let loaded: Promise<DiffModule> | null = null;
const loadDiff = () => (loaded ??= import('diff'));

export type Side = [string, string, string];

/** A diff as worked out: loading, no text to diff, or its rows with the lines added and deleted. */
export type DiffResult = { kind: 'loading' } | { kind: 'not_diffable' } | { kind: 'ready'; rows: DiffRow[]; truncated: boolean; add: number; del: number };

/** "+12 −3": a diff's lines added and deleted. */
export const lineCounts = (d: { add: number; del: number }) => `+${d.add} −${d.del}`;

/**
 * The course copy (`course`) against the semester's (`semester`), each `[org, repo, path]` (null:
 * absent on that side); read only while `want`, loading otherwise.
 */
export function useDiff(files: Files, course: Side | null, semester: Side | null, want = true): DiffResult {
  const [mod, setMod] = useState<DiffModule | null>(null);
  useEffect(() => {
    if (!want) return;
    let live = true;
    void loadDiff().then((m) => live && setMod(m));
    return () => void (live = false);
  }, [want]);
  if (!want) return { kind: 'loading' };
  const a = course ? sideText(files.file(...course)) : '';
  const b = semester ? sideText(files.file(...semester)) : '';
  if (a === null || b === null) return { kind: 'not_diffable' };
  if (a === undefined || b === undefined || !mod) return { kind: 'loading' };
  const patch = mod.structuredPatch('course', 'semester', a, b, '', '', { context: 3 });
  const lines = patch.hunks.flatMap((h) => h.lines);
  return { kind: 'ready', ...diffRows(patch), add: lines.filter((l) => l[0] === '+').length, del: lines.filter((l) => l[0] === '-').length };
}

/** The course copy against the semester's, as `useDiff` reads them. */
export function FileDiff({ files, course, semester }: { files: Files; course: Side | null; semester: Side | null }) {
  return <DiffView d={useDiff(files, course, semester)} />;
}

/** A diff `useDiff` worked out, drawn. */
export function DiffView({ d }: { d: DiffResult }) {
  if (d.kind === 'not_diffable') return <p class="footnote">{NOT_DIFFABLE}</p>;
  if (d.kind === 'loading') return <p class="footnote">Reading the diff…</p>;
  if (!d.rows.length) return <p class="footnote">No difference in the text.</p>;
  return (
    <>
      <pre class="file-diff">{d.rows.map((r) => <span class={r.cls}>{r.text}{'\n'}</span>)}</pre>
      {d.truncated ? <p class="footnote">Diff truncated</p> : null}
    </>
  );
}
