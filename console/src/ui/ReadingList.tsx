// The reading-list editor (decision 0044 point 4): ONE component, mounted in the schedule
// entry sheet (a readings entry, a lecture's or lab's "Readings for this session") and in the
// course Materials file view when a reading-list file is opened. Both mounts show the same
// layout and words and save the same course file through the shared save helper; a change
// here changes both. The list is a markdown file in a course materials repo (default
// `READINGS.md` beside the reading files); text written in the schedule is the legacy form,
// shown and kept, with `Move to a file`.

import type { ComponentChildren } from 'preact';
import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { useSave } from '../edit/save';
import type { Files } from '../model/files';
import type { Loaded } from '../model/status';
import type { Release } from '../model/types';
import { plural } from '../model/format';
import { READINGS_FILE, type ListDraft } from '../model/scheduleEdit';
import { CheckLine } from './bits';
import { SaveBar } from './edit';
import { FilePicker } from './FilePicker';
import { GhMd } from './rendered';
import { RepoFile } from './Viewer';

/** Every word the editor shows, the same in both mounts. */
export const LIST_TEXT = {
  label: 'Reading list',
  hint: '(optional) what to read & how: citations, chapters & pages, additional guidance and/or URLs. Publicly rendered markdown.',
  newFile: 'New file',
  existingFile: 'Existing file',
  inline: 'Written in the schedule',
  choose: 'Choose another…',
  move: 'Move to a file',
  footNew: (repo: string) => `In ${repo}. Saving creates this file next to the reading files; edit the path to put it elsewhere (a lecture folder, say).`,
  footExisting: 'Saving writes your changes to this file.',
  preview: 'As students will see it',
  empty: 'No reading list: students see the reading files only. That is fine.',
  missing: (names: string[]) => `${plural(names.length, 'reading file')} not in the reading list: ${names.join(', ')}. Students still get the file; it just isn’t cited here.`,
};

/** Whether `path` is a reading list by its name (`READINGS.md`, any case) or because a readings entry names it. */
export function isReadingList(path: string, named: readonly string[] = []): boolean {
  return (path.split('/').pop() ?? '').toLowerCase() === READINGS_FILE.toLowerCase() || named.includes(path);
}

/** A readings entry's reading-list course file, or null (none, or text in the schedule). */
export const listFileOf = (r: Release) => (r.reading_list && 'path' in r.reading_list ? r.reading_list : null);

/** The paths in course repo `repo` that loaded semesters' readings entries name as reading lists. */
export function namedLists(states: Record<string, Loaded>, repo: string): string[] {
  return Object.values(states).flatMap((l) => (l?.kind === 'ready' ? l.status.releases ?? [] : []))
    .flatMap((r) => { const f = listFileOf(r); return f && f.repo === repo ? [f.path] : []; });
}

const isMarkdown = (path: string) => /\.(md|markdown)$/i.test(path);

/** `v`, once it has stood still for `ms`: the preview renders through GitHub, not on every key. */
function useSettled(v: string, ms: number): string {
  const [settled, setSettled] = useState(v);
  useEffect(() => {
    const t = setTimeout(() => setSettled(v), ms);
    return () => clearTimeout(t);
  }, [v]);
  return settled;
}

/** The reading files whose names the list does not mention. */
export const notCited = (text: string, names: string[]) => (text.trim() ? names.filter((n) => !text.includes(n)) : []);

export function ReadingList({ id, list, defaultRepo, defaultPath, org, repos, files, readingFiles, onChange, fixedPath = false, draft }: {
  id: string;
  /** The list as the draft holds it; null: none yet (shown at the default path, a New file). */
  list: ListDraft | null;
  defaultRepo: string;
  defaultPath: string;
  /** The course org and its materials repos: where a list file may be chosen. */
  org: string;
  repos: string[];
  files: Files;
  /** The entry's reading files, by name, for the not-cited notice. */
  readingFiles: string[];
  onChange: (l: ListDraft) => void;
  /** The Materials file view: the file is the one opened; nothing to choose or move. */
  fixedPath?: boolean;
  /** RL-C's Generate draft button. */
  draft?: ComponentChildren;
}) {
  const [picking, setPicking] = useState(false);
  const cur: ListDraft = list ?? { repo: defaultRepo, path: defaultPath, text: '', sha: null, inline: false, loaded: true };
  const set = (x: Partial<ListDraft>) => onChange({ ...cur, ...x });
  const badge = cur.inline ? LIST_TEXT.inline : !cur.loaded ? null : cur.sha ? LIST_TEXT.existingFile : LIST_TEXT.newFile;
  const isNew = !cur.inline && cur.loaded && !cur.sha;
  const missing = notCited(cur.text, readingFiles);
  const rows = Math.max(6, cur.text.split('\n').length + 1);
  const shown = useSettled(cur.text, 400);
  return (
    <div class="field reading-list-editor">
      <span class="lbl-row"><label for={`${id}-rl`}><b>{LIST_TEXT.label}</b></label></span>
      <span class="hint">{LIST_TEXT.hint}</span>
      <div class="rl-file">
        <span>File: </span>
        {cur.inline ? <span class="footnote">none</span>
          : isNew && !fixedPath ? <><span class="mono">{cur.repo} / </span><input id={`${id}-rl-path`} class="mono" type="text" aria-label="Reading list path" value={cur.path} onInput={(e) => set({ path: (e.target as HTMLInputElement).value.replace(/^\/+/, '') })} /></>
          : <span class="mono">{cur.repo} / {cur.path}</span>}
        {badge ? <span class={`chip${cur.inline ? ' amber' : ' muted'}`}>{badge}</span> : <span class="footnote">Reading {cur.path}…</span>}
        {fixedPath ? null : (
          <span class="actions">
            {cur.inline ? <button class="btn small" type="button" onClick={() => set({ inline: false, repo: defaultRepo, path: defaultPath, sha: null, loaded: true })}>{LIST_TEXT.move}</button> : null}
            <button class="btn small quiet" type="button" onClick={() => setPicking(true)}>{LIST_TEXT.choose}</button>
          </span>
        )}
      </div>
      {cur.inline ? null : <p class="footnote">{isNew ? LIST_TEXT.footNew(cur.repo) : LIST_TEXT.footExisting}</p>}
      <div class="rl-edit">
        <textarea id={`${id}-rl`} class="mono" rows={rows} value={cur.text} disabled={!cur.loaded} onInput={(e) => set({ text: (e.target as HTMLTextAreaElement).value })} />
        <div class="rl-preview" aria-label={LIST_TEXT.preview}>
          <span class="hint">{LIST_TEXT.preview}</span>
          {shown.trim() ? <GhMd src={shown} class="reading-list" /> : <p class="footnote">{LIST_TEXT.empty}</p>}
        </div>
      </div>
      {missing.length ? <CheckLine cls="warn">{LIST_TEXT.missing(missing)}</CheckLine> : null}
      {draft}
      {picking ? <FilePicker files={files} org={org} repos={repos} only={isMarkdown} onClose={() => setPicking(false)}
        onPick={(got) => { const g = got[got.length - 1]; if (g) set({ repo: g.repo, path: g.path, text: '', sha: null, inline: false, loaded: false }); }} /> : null}
    </div>
  );
}

/** The commit message of a reading-list save, from either mount. */
export const listMessage = (path: string) => `readings: edit ${path}, from the DSL Teaching Console`;

/**
 * A course materials file in the viewer (the materials index's `?file=`, the Files panel's
 * Browse pane). A reading list gets `Edit reading list` in its header, which swaps the pane to
 * the editor on that file, saved on its own, and `Back to the file`.
 */
export function CourseFile({ org, at, files, named, write }: { org: string; at: { repo: string; path: string }; files: Files; named: readonly string[]; write: boolean }) {
  const [editing, setEditing] = useState<string | null>(null);
  const here = `${at.repo}/${at.path}`;
  const list = write && isReadingList(at.path, named);
  if (list && editing === here) return <ListFileEditor org={org} at={at} files={files} onBack={() => setEditing(null)} />;
  return <RepoFile org={org} at={at} actions={list ? <button class="btn small" type="button" onClick={() => setEditing(here)}>Edit reading list</button> : undefined} />;
}

/** The reading-list editor on one course file, its path fixed; the reading files are the folder's PDFs. */
function ListFileEditor({ org, at, files, onBack }: { org: string; at: { repo: string; path: string }; files: Files; onBack: () => void }) {
  const env = useEnv();
  const [save, run] = useSave(env);
  const [text, setText] = useState<string | null>(null);
  const f = files.file(org, at.repo, at.path);
  const saved = f.kind === 'ready' ? f.text : '';
  const tree = files.tree(org, at.repo);
  const dir = at.path.includes('/') ? `${at.path.slice(0, at.path.lastIndexOf('/'))}/` : '';
  const pdfs = tree.kind === 'ready' ? tree.paths.filter((x) => !x.dir && x.path.startsWith(dir) && !x.path.slice(dir.length).includes('/') && /\.pdf$/i.test(x.path)).map((x) => x.path.slice(dir.length)) : [];
  const list: ListDraft = { repo: at.repo, path: at.path, text: text ?? saved, sha: f.kind === 'ready' ? f.sha : null, inline: false, loaded: f.kind === 'ready' || f.kind === 'absent' };
  const doSave = async () => {
    if (text !== null && (await run({ owner: org, repo: at.repo, path: at.path }, text, list.sha, { message: listMessage(at.path), checks: false }))) setText(null);
  };
  return (
    <section class="panel section" aria-label={at.path}>
      <div class="a-head">
        <h2 class="mono">{at.path}</h2>
        <span class="actions"><button class="btn small quiet" type="button" onClick={onBack}>Back to the file</button></span>
      </div>
      <ReadingList id="mf" list={list} defaultRepo={at.repo} defaultPath={at.path} org={org} repos={[at.repo]} files={files} readingFiles={pdfs} onChange={(l) => setText(l.text)} fixedPath />
      <SaveBar state={save} small onSave={() => void doSave()} disabled={text === null || text === saved} file={{ org, repo: at.repo, path: at.path, exists: f.kind !== 'absent' }} />
    </section>
  );
}
