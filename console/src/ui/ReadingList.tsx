// The reading-list editor (decision 0044 point 4): ONE component, mounted in the schedule
// entry sheet (a readings entry, a lecture's or lab's "Readings for this session") and in the
// course Materials file view when a reading-list file is opened. Both mounts show the same
// layout and words and save the same course file through the shared save helper; a change
// here changes both. The list is a markdown file in a course materials repo (default
// `READINGS.md` beside the reading files); text written in the schedule is the legacy form,
// shown and kept, with `Move to a file`. A list is offered, never taken unasked (decision 0046):
// with none, the entry shows Use it / Write one / Choose a file, and Detach takes it off again.

import { useEffect, useMemo, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { useSave } from '../edit/save';
import { fileInTree, parentOf, type Files } from '../model/files';
import type { Loaded } from '../model/status';
import type { Release } from '../model/types';
import { plural } from '../model/format';
import { baseName, insertLines } from '../model/readingDraft';
import { isReadingList, listFile, readingFilesOf, type ListDraft, type ReadingFile } from '../model/scheduleEdit';
import { viewKind } from '../model/viewer';
import { CheckLine } from './bits';
import { SaveBar } from './edit';
import { FilePicker } from './FilePicker';
import { DraftButton, type PdfRef } from './ReadingDraft';
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
  none: 'There is a READINGS.md beside these files.',
  useIt: 'Use it',
  writeOne: 'Write one',
  chooseFile: 'Choose a file…',
  detach: 'Detach',
  creates: (repo: string, path: string) => `Creates ${repo} / ${path}.`,
  move: 'Move to a file',
  footNew: (repo: string) => `In ${repo}. Saving creates this file next to the reading files; edit the path to put it elsewhere (a lecture folder, say).`,
  footExisting: 'Saving writes your changes to this file.',
  preview: 'As students will see it',
  empty: 'No reading list: students see the reading files only. That is fine.',
  noRepo: 'Add a course materials repo first: the reading list is a file in one.',
  missing: (names: string[]) => `${plural(names.length, 'reading file')} not in the reading list: ${names.join(', ')}. Students still get the file; it just isn’t cited here.`,
};

/** A readings entry's reading-list course file, or null (none, or text in the schedule). */
export const listFileOf = (r: Release) => (r.reading_list && 'path' in r.reading_list ? r.reading_list : null);

/** A course file a semester's readings entry names as its reading list, and that entry's first reading file (`source`). */
export interface NamedList {
  path: string;
  files: { repo: string; path: string }[];
}

/** The reading lists in course repo `repo` that loaded semesters' readings entries name. */
export function namedLists(states: Record<string, Loaded>, repo: string): NamedList[] {
  return Object.values(states).flatMap((l) => (l?.kind === 'ready' ? l.status.releases ?? [] : []))
    .flatMap((r) => { const f = listFileOf(r); return f && f.repo === repo ? [{ path: f.path, files: r.source ? [r.source] : [] }] : []; });
}

/**
 * `v`, once it has stood still for `ms`: the preview renders through GitHub's markdown API (as the
 * file viewer does, `materials.showFile`), not on every key. A text back where it was sets nothing,
 * and `GhMd` keeps each rendering, so an unchanged text never asks again.
 */
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

export function ReadingList({ id, list, defaultRepo, defaultPath, org, repos, files, readingFiles, onChange, onDetach, fixedPath = false, noRepo = false }: {
  id: string;
  /** The list as the draft holds it; null: none, and the offer of one at the default path (the file already there, or a new one). */
  list: ListDraft | null;
  defaultRepo: string;
  defaultPath: string;
  /** The course org and its materials repos: where a list file may be chosen. */
  org: string;
  repos: string[];
  files: Files;
  /** The entry's reading files (`readingFilesOf`): their names for the not-cited notice, their PDFs for Generate draft. */
  readingFiles: ReadingFile[];
  onChange: (l: ListDraft) => void;
  /** Takes the list off the entry (none again); the file stays. Absent: no Detach. */
  onDetach?: () => void;
  /** The Materials file view: the file is the one opened; nothing to choose or move. */
  fixedPath?: boolean;
  /** No course materials repo to put a file in: text in the schedule stays editable, nothing to choose or move. */
  noRepo?: boolean;
}) {
  const [picking, setPicking] = useState(false);
  const [, reread] = useState(0);
  const cur: ListDraft = list ?? { repo: defaultRepo, path: defaultPath, inline: false, text: null };
  // A file's saved text and sha are the cache's: an absent file is a New file at that path.
  const f = cur.inline ? null : listFile(files, org, cur);
  const saved = f?.kind === 'ready' ? f.text : '';
  const text = cur.text ?? saved;
  const loaded = !f || f.kind === 'ready' || f.kind === 'absent';
  // A file's text back where the file has it is untouched again (null): the sheet is clean.
  const setText = (t: string) => onChange({ ...cur, text: f && t === saved ? null : t });
  // A path already in the repo is that file, read as Choose another reads it: never a New file over it.
  const toFile = (repo: string, path: string, keep: string | null): ListDraft => ({ repo, path, inline: false, text: fileInTree(files, org, repo, path) ? null : keep ?? '' });
  const retry = () => { files.refresh(org, cur.repo, cur.path); reread((n) => n + 1); };
  const badge = cur.inline ? LIST_TEXT.inline : !loaded ? null : f?.kind === 'ready' ? LIST_TEXT.existingFile : LIST_TEXT.newFile;
  const isNew = f?.kind === 'absent';
  const names = useMemo(() => [...new Set(readingFiles.map((x) => baseName(x.path)))], [readingFiles]);
  const pdfs = useMemo(() => readingFiles.flatMap((x): PdfRef[] => (x.sha && viewKind(x.path) === 'pdf' ? [{ repo: x.repo, path: x.path, sha: x.sha, size: x.size }] : [])), [readingFiles]);
  const missing = notCited(text, names);
  const rows = Math.max(6, text.split('\n').length + 1);
  const shown = useSettled(text, 400);
  const picker = picking ? <FilePicker files={files} org={org} repos={repos} only={(path) => viewKind(path) === 'markdown'} onClose={() => setPicking(false)}
    onPick={(got) => { const g = got[got.length - 1]; if (g) onChange({ repo: g.repo, path: g.path, inline: false, text: null }); }} /> : null;
  if (!list && !fixedPath) {
    const there = fileInTree(files, org, defaultRepo, defaultPath);
    const choose = <button class="btn small quiet" type="button" onClick={() => setPicking(true)}>{LIST_TEXT.chooseFile}</button>;
    return (
      <div class="field reading-list-editor">
        <span class="lbl-row"><b>{LIST_TEXT.label}</b></span>
        <span class="hint">{LIST_TEXT.hint}</span>
        {there ? (
          <div class="rl-offer">
            <span>{LIST_TEXT.none}</span>
            <button class="btn small" type="button" onClick={() => onChange({ repo: defaultRepo, path: defaultPath, inline: false, text: null })}>{LIST_TEXT.useIt}</button>
            {choose}
          </div>
        ) : (
          <>
            <div class="rl-offer">
              <button class="btn small" type="button" onClick={() => onChange({ repo: defaultRepo, path: defaultPath, inline: false, text: '' })}>{LIST_TEXT.writeOne}</button>
              {choose}
            </div>
            <p class="footnote">{LIST_TEXT.creates(defaultRepo, defaultPath)}</p>
          </>
        )}
        {picker}
      </div>
    );
  }
  return (
    <div class="field reading-list-editor">
      <span class="lbl-row"><label for={`${id}-rl`}><b>{LIST_TEXT.label}</b></label></span>
      <span class="hint">{LIST_TEXT.hint}</span>
      <div class="rl-edit">
        <textarea id={`${id}-rl`} class="mono" rows={rows} value={text} disabled={!loaded} onInput={(e) => setText((e.target as HTMLTextAreaElement).value)} />
        <div class="rl-preview" aria-label={LIST_TEXT.preview}>
          <span class="hint">{LIST_TEXT.preview}</span>
          {shown.trim() ? <GhMd src={shown} context={cur.repo ? `${org}/${cur.repo}` : undefined} class="reading-list" /> : <p class="footnote">{LIST_TEXT.empty}</p>}
        </div>
      </div>
      {missing.length ? <CheckLine cls="warn">{LIST_TEXT.missing(missing)}</CheckLine> : null}
      <DraftButton org={org} pdfs={pdfs} disabled={!loaded} onAdd={(adds) => setText(insertLines(text, cur.path, adds, cur.repo))} />
      <div class="rl-file">
        <span>File: </span>
        {cur.inline ? <span class="footnote">none</span>
          : isNew && !fixedPath ? <><span class="mono">{cur.repo} / </span><input id={`${id}-rl-path`} class="mono" type="text" aria-label="Reading list path" value={cur.path} onInput={(e) => onChange(toFile(cur.repo, (e.target as HTMLInputElement).value.replace(/^\/+/, ''), cur.text))} /></>
          : <span class="mono">{cur.repo} / {cur.path}</span>}
        {badge ? <span class={`chip${cur.inline ? ' amber' : ' muted'}`}>{badge}</span> : f?.kind === 'error' ? null : <span class="footnote">Reading {cur.path}…</span>}
        {fixedPath ? null : noRepo ? <span class="footnote">{LIST_TEXT.noRepo}</span> : (
          <span class="actions">
            {cur.inline ? <button class="btn small quiet" type="button" onClick={() => onChange({ repo: defaultRepo, path: defaultPath, inline: false, text: cur.text ?? '' })}>{LIST_TEXT.move}</button> : null}
            <button class="btn small quiet" type="button" onClick={() => setPicking(true)}>{LIST_TEXT.choose}</button>
            {onDetach && !cur.inline ? <button class="btn small quiet" type="button" onClick={onDetach}>{LIST_TEXT.detach}</button> : null}
          </span>
        )}
      </div>
      {f?.kind === 'error' ? <CheckLine cls="bad">Could not read {cur.path}: {f.message} <button class="textlink" type="button" onClick={retry}>Retry</button></CheckLine> : null}
      {cur.inline || f?.kind === 'error' ? null : <p class="footnote">{isNew ? LIST_TEXT.footNew(cur.repo) : LIST_TEXT.footExisting}</p>}
      {picker}
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
export function CourseFile({ org, at, files, named, write }: { org: string; at: { repo: string; path: string }; files: Files; named: readonly NamedList[]; write: boolean }) {
  const [editing, setEditing] = useState<string | null>(null);
  const here = `${at.repo}/${at.path}`;
  const entry = named.find((l) => l.path === at.path);
  const list = write && (!!entry || isReadingList(at.path));
  if (list && editing === here) return <ListFileEditor org={org} at={at} files={files} entryFiles={entry?.files ?? null} onBack={() => setEditing(null)} />;
  return <RepoFile org={org} at={at} actions={list ? <button class="btn small" type="button" onClick={() => setEditing(here)}>Edit reading list</button> : undefined} />;
}

/**
 * The reading-list editor on one course file, its path fixed. Its reading files are those of the
 * entry that names it (`entryFiles`), else the PDFs beside it.
 */
function ListFileEditor({ org, at, files, entryFiles, onBack }: { org: string; at: { repo: string; path: string }; files: Files; entryFiles: { repo: string; path: string }[] | null; onBack: () => void }) {
  const env = useEnv();
  const [save, run] = useSave(env);
  const [text, setText] = useState<string | null>(null);
  const f = files.file(org, at.repo, at.path);
  const tree = files.tree(org, at.repo);
  const dir = parentOf(at.path);
  const named = entryFiles?.map((x) => `${x.repo}/${x.path}`).join('\n');
  const readingFiles = useMemo(() => {
    const beside = tree.kind === 'ready' ? tree.paths.filter((x) => !x.dir && parentOf(x.path) === dir && viewKind(x.path) === 'pdf').map((x) => ({ repo: at.repo, path: x.path })) : [];
    return readingFilesOf(files, org, entryFiles ?? beside, at.path);
  }, [tree, dir, named]);
  const list: ListDraft = { repo: at.repo, path: at.path, inline: false, text };
  const doSave = async () => {
    if (text !== null && (await run({ owner: org, repo: at.repo, path: at.path }, text, f.kind === 'ready' ? f.sha : null, { message: listMessage(at.path), checks: false }))) setText(null);
  };
  return (
    <section class="panel section" aria-label={at.path}>
      <div class="a-head">
        <h2 class="mono">{at.path}</h2>
        <span class="actions"><button class="btn small quiet" type="button" onClick={onBack}>Back to the file</button></span>
      </div>
      <ReadingList id="mf" list={list} defaultRepo={at.repo} defaultPath={at.path} org={org} repos={[at.repo]} files={files} readingFiles={readingFiles} onChange={(l) => setText(l.text)} fixedPath />
      <SaveBar state={save} small onSave={() => void doSave()} disabled={text === null} file={{ org, repo: at.repo, path: at.path, exists: f.kind !== 'absent' }} />
    </section>
  );
}
