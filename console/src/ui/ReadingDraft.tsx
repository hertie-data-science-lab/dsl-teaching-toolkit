// Generate draft (decision 0044 point 6), in the reading-list editor's draft slot in both mounts:
// each PDF reading file read in the browser (its bytes through the console's GitHub client, pdf.js
// loaded on the first click), one APA line drafted per file, and a dialog where the instructor
// ticks the lines that go in. Nothing is sent anywhere but the GitHub reads the console makes anyway.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { plural } from '../model/format';
import { draftReadings, hasUnknown, type Draft, type PdfFacts, type Section, type Level } from '../model/readingDraft';
import { CheckLine } from './bits';
import { Modal } from './Modal';

export const DRAFT_TEXT = {
  button: 'Generate draft',
  hint: 'Reads each file’s details and printed page numbers in your browser; nothing is sent anywhere. You check every line before it goes in.',
  noPdfs: 'Generate draft reads PDF reading files; this list has none yet.',
  title: (n: number) => `Draft from ${plural(n, 'reading file')}`,
  lede: 'A best guess per file, APA citation format.',
  reading: (i: number, n: number) => `Reading ${i} of ${n}…`,
  failed: (msg: string) => `Could not read this file: ${msg}`,
  unknown: 'Still has a ? where a detail is unknown. It will show as written on the site.',
  footer: (k: number, n: number, m: number) => `${k} of ${n} ticked · ${m} with a ? go in as written. Unticked lines are left out.`,
  add: (k: number) => `Add ${plural(k, 'line')} to the reading list`,
};

/** A PDF reading file in a course repo: `sha` and `size` from the repo tree (the client reads a file over 1 MB by blob). */
export interface PdfRef {
  repo: string;
  path: string;
  sha: string;
  size?: number;
}

/** A line the dialog adds: what `insertLines` takes. */
export interface DraftAdd {
  section: Section;
  line: string;
  path: string;
}

const isPdf = (path: string) => /\.pdf$/i.test(path);
const nameOf = (path: string) => path.split('/').pop() ?? path;

/** What each PDF yielded, by blob sha: reopening the dialog reads nothing again. */
const read = new Map<string, PdfFacts>();

/** The tick-chip colour of a provenance tag. */
const LEVEL_CLS: Record<Level, string> = { found: 'ok', check: 'amber', guess: 'amber', '?': 'bad' };

/** The draft slot: the button, its hint, and the dialog once clicked. */
export function DraftButton({ org, pdfs, disabled = false, onAdd }: { org: string; pdfs: PdfRef[]; disabled?: boolean; onAdd: (adds: DraftAdd[]) => void }) {
  const env = useEnv();
  const files = pdfs.filter((p) => isPdf(p.path));
  const [state, setState] = useState<null | { kind: 'reading'; at: number } | { kind: 'ready'; drafts: Draft[]; failed: { path: string; message: string }[] }>(null);
  const run = async () => {
    if (!env) return;
    setState({ kind: 'reading', at: 1 });
    const got: { path: string; facts: PdfFacts }[] = [];
    const failed: { path: string; message: string }[] = [];
    for (const [i, f] of files.entries()) {
      setState({ kind: 'reading', at: i + 1 });
      try {
        let facts = read.get(f.sha);
        if (!facts) {
          const bytes = await env.client.getBytes(org, f.repo, f.path, f.sha, f.size ?? 0);
          facts = await (await import('../model/pdfText')).readPdf(bytes);
          read.set(f.sha, facts);
        }
        got.push({ path: f.path, facts });
      } catch (e) {
        failed.push({ path: f.path, message: e instanceof Error ? e.message : String(e) });
      }
    }
    setState({ kind: 'ready', drafts: draftReadings(got), failed });
  };
  return (
    <div class="rd-slot">
      <span class="actions"><button class="btn small" type="button" disabled={disabled || !env || !files.length || state?.kind === 'reading'} onClick={() => void run()}>{DRAFT_TEXT.button}</button></span>
      <span class="hint">{files.length ? DRAFT_TEXT.hint : DRAFT_TEXT.noPdfs}</span>
      {state?.kind === 'reading' ? <CheckLine cls="busy">{DRAFT_TEXT.reading(state.at, files.length)}</CheckLine> : null}
      {state?.kind === 'ready' ? <DraftDialog drafts={state.drafts} failed={state.failed} onAdd={onAdd} onClose={() => setState(null)} /> : null}
    </div>
  );
}

interface Row {
  section: Section;
  line: string;
  ticked: boolean;
}

/** The dialog: per file its name, a Section select, the line to edit, a tick, and where each part came from. */
export function DraftDialog({ drafts, failed = [], onAdd, onClose }: { drafts: Draft[]; failed?: { path: string; message: string }[]; onAdd: (adds: DraftAdd[]) => void; onClose: () => void }) {
  const [rows, setRows] = useState<Row[]>(() => drafts.map((d) => ({ section: d.section, line: d.line, ticked: false })));
  const set = (i: number, x: Partial<Row>) => setRows((r) => r.map((row, j) => (j === i ? { ...row, ...x } : row)));
  const ticked = rows.filter((r) => r.ticked);
  const unknown = ticked.filter((r) => hasUnknown(r.line)).length;
  const add = () => {
    onAdd(rows.flatMap((r, i) => (r.ticked ? [{ section: r.section, line: r.line.trim(), path: drafts[i].path }] : [])));
    onClose();
  };
  return (
    <Modal title={DRAFT_TEXT.title(drafts.length + failed.length)} onClose={onClose}>
      <p class="hint">{DRAFT_TEXT.lede}</p>
      {drafts.map((d, i) => {
        const r = rows[i];
        const name = nameOf(d.path);
        return (
          <div class="rd-file" key={d.path}>
            <div class="rd-head">
              <span class="mono">{name}</span>
              <label class="rd-sec">Section{' '}
                <select aria-label={`Section of ${name}`} onChange={(e) => set(i, { section: (e.target as HTMLSelectElement).value as Section })}>
                  <option value="required" selected={r.section === 'required'}>Required</option>
                  <option value="additional" selected={r.section === 'additional'}>Additional</option>
                </select>
              </label>
            </div>
            <div class="rd-line">
              <input type="text" class="mono" aria-label={`Line for ${name}`} value={r.line} onInput={(e) => set(i, { line: (e.target as HTMLInputElement).value })} />
              <button class={`rd-tick${r.ticked ? ' on' : ''}`} type="button" aria-pressed={r.ticked} aria-label={`Tick ${name}`} onClick={() => set(i, { ticked: !r.ticked })}>✓</button>
            </div>
            {r.ticked && hasUnknown(r.line) ? <p class="rd-unknown"><span class="rd-bang" aria-hidden="true">!</span>{DRAFT_TEXT.unknown}</p> : null}
            <ul class="rd-notes">
              {d.notes.map((n) => <li><span class={`chip ${LEVEL_CLS[n.level]}`}>{n.level}</span> <b>{n.part}</b>: {n.text}</li>)}
            </ul>
          </div>
        );
      })}
      {failed.map((f) => (
        <div class="rd-file" key={f.path}>
          <div class="rd-head"><span class="mono">{nameOf(f.path)}</span></div>
          <CheckLine cls="bad">{DRAFT_TEXT.failed(f.message)}</CheckLine>
        </div>
      ))}
      <div class="savebar rd-foot">
        <span class="rd-count">{DRAFT_TEXT.footer(ticked.length, rows.length, unknown)}</span>
        <button class="btn" type="button" onClick={onClose}>Cancel</button>
        <button class="btn primary" type="button" disabled={!ticked.length} onClick={add}>{DRAFT_TEXT.add(ticked.length)}</button>
      </div>
    </Modal>
  );
}
