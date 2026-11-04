// A file's diff in the console (the Released materials page's "Released materials that differ from their source files" panel): the course
// blob against the semester's default-branch blob, both read with the signed-in token
// (`Files.file`), as one unified diff (the `diff` package, imported on first draw). Text files
// only; a binary or very large file says so. Capped at `DIFF_CAP` lines. `diffAnchor` is GitHub's
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

/** The course copy (`course`) against the semester's (`semester`), each `[org, repo, path]` (null: absent on that side). */
export function FileDiff({ files, course, semester }: { files: Files; course: [string, string, string] | null; semester: [string, string, string] | null }) {
  const [mod, setMod] = useState<DiffModule | null>(null);
  useEffect(() => {
    let live = true;
    void loadDiff().then((m) => live && setMod(m));
    return () => void (live = false);
  }, []);
  const a = course ? sideText(files.file(...course)) : '';
  const b = semester ? sideText(files.file(...semester)) : '';
  if (a === null || b === null) return <p class="footnote">{NOT_DIFFABLE}</p>;
  if (a === undefined || b === undefined || !mod) return <p class="footnote">Reading the diff…</p>;
  const { rows, truncated } = diffRows(mod.structuredPatch('course', 'semester', a, b, '', '', { context: 3 }));
  if (!rows.length) return <p class="footnote">No difference in the text.</p>;
  return (
    <>
      <pre class="file-diff">{rows.map((r) => <span class={r.cls}>{r.text}{'\n'}</span>)}</pre>
      {truncated ? <p class="footnote">Diff truncated</p> : null}
    </>
  );
}
