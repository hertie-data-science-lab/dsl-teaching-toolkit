// Profile (`#profile`, from the avatar; decisions 0017, 0021 rule 3, 0024 rule 6): the folder
// a person keeps the course repos in and the editor the Open button uses. Kept per login in
// this browser only (`model/prefs.ts`), never sent anywhere, and kept across sign-out. The
// login is known from sign-in, so there is no handle field; instructors push to the course
// repos, so there is no fork step. A saved setup shows as text with a pencil; the form shows
// while editing or while no folder is saved, and nothing is stored until Save. Where the
// browser allows it, the folder check (decision 0023) shows in both.

import { useEffect, useRef, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { canCheckFolders, folderHandle, forgetFolder, pickFolder } from '../model/localFolder';
import { folderExample, lastSegment, platformOf, schemeOk, type Editor, type Setup } from '../model/open';
import { saveYourSetup, yourSetup } from '../model/prefs';

const EDITOR_WORD: Record<Editor, string> = { vscode: 'VS Code', desktop: 'GitHub Desktop', other: 'Another editor' };

type Draft = Pick<Setup, 'folder' | 'editor'> & { scheme: string };

const draftOf = (s: Setup | null): Draft => ({ folder: s?.folder ?? '', editor: s?.editor ?? 'vscode', scheme: s?.scheme ?? '' });
const same = (a: Draft, b: Draft) => a.folder.trim() === b.folder.trim() && a.editor === b.editor && (a.editor !== 'other' || a.scheme.trim() === b.scheme.trim());

/** The picked repos folder: pick it, see its name, forget it. Chrome and Edge only. */
function FolderCheck({ login, typed, org }: { login: string; typed: string; org?: string }) {
  const [name, setName] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    void folderHandle(login).then((h) => live && setName(h?.name ?? null));
    return () => {
      live = false;
    };
  }, [login]);
  const pick = async () => {
    const h = await pickFolder(login);
    if (h) setName(h.name);
  };
  const forget = async () => {
    await forgetFolder(login);
    setName(null);
  };
  const end = lastSegment(typed).toLowerCase();
  const picked = name?.toLowerCase();
  // Picking the course's own folder under the typed one is a setup `courseFolder` supports.
  const differs = !!end && !!picked && end !== picked && picked !== org?.toLowerCase();
  return (
    <div class="field folder-check">
      {name ? (
        <>
          <p class="saved">Checking: <code>{name}</code> <button class="btn small quiet" type="button" onClick={forget}>Forget</button></p>
          {differs ? <p class="invalid-msg">The folder you picked is <code>{name}</code>; the path above ends in <code>{lastSegment(typed)}</code>.</p> : null}
        </>
      ) : (
        <>
          <button class="btn outline" type="button" onClick={pick}>Let the console see which repos are cloned</button>
          <p class="hint">Chrome and Edge only. The console only checks which folders exist; it reads nothing.</p>
        </>
      )}
    </div>
  );
}


const Pencil = () => (
  <svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M11 2.5l2.5 2.5L5.5 13H3v-2.5z" /><path d="M9.5 4l2.5 2.5" /></svg>
);

// `repos` is still passed by the App (the course's repos, for the clone block 0024 removed);
// it is not used.
export function SetupScreen({ org }: { org?: string; repos?: string[] }) {
  const env = useEnv();
  const login = env?.user.login ?? '';
  const [saved, setSaved] = useState<Setup | null>(() => yourSetup(login));
  const [draft, setDraft] = useState<Draft>(() => draftOf(saved));
  const [done, setDone] = useState<'stored' | 'kept' | null>(null);
  // A setup counts as saved once it names a folder: the Open button's remembered choice alone
  // stores a setup without one.
  const [editing, setEditing] = useState(() => !saved?.folder.trim());
  const pencil = useRef<HTMLButtonElement>(null);
  const folderInput = useRef<HTMLInputElement>(null);
  const moved = useRef(false);
  useEffect(() => {
    if (!moved.current) return;
    moved.current = false;
    (editing ? folderInput : pencil).current?.focus();
  }, [editing]);
  const example = folderExample(platformOf());
  const badScheme = draft.editor === 'other' && !schemeOk(draft.scheme);
  const change = (d: Partial<Draft>) => {
    setDraft({ ...draft, ...d });
    setDone(null);
  };
  const switchTo = (edit: boolean) => {
    moved.current = true;
    setEditing(edit);
  };
  const edit = () => {
    setDraft(draftOf(saved));
    setDone(null);
    switchTo(true);
  };
  const cancel = () => {
    setDraft(draftOf(saved));
    switchTo(false);
  };
  const save = () => {
    // A new folder or editor makes the editor the Open button's default again.
    const keepLast = saved?.lastOpen && saved.folder === draft.folder.trim() && saved.editor === draft.editor;
    const next: Setup = { ...(keepLast ? { lastOpen: saved.lastOpen } : {}), folder: draft.folder.trim(), editor: draft.editor };
    if (draft.editor === 'other') next.scheme = draft.scheme.trim();
    setDone(saveYourSetup(login, next) ? 'stored' : 'kept');
    setSaved(next);
    if (next.folder) switchTo(false);
  };
  const status = done === 'stored' ? <span class="valid-msg" role="status">Saved.</span> : done === 'kept' ? <span class="footnote" role="status">This browser does not keep it: it lasts until you reload.</span> : null;
  const shown = editing ? draft.folder : saved?.folder ?? '';
  const view = !editing && !!saved;
  const check = shown.trim() && canCheckFolders() ? <FolderCheck login={login} typed={shown} org={org} /> : null;
  return (
    <>
      <div class="page-head">
        <div><h1>Profile</h1></div>
      </div>
      {/* One section and one folder check for both views, so the check does not remount (and
          flash its pick button) on Edit, Save or Cancel. */}
      <section class={`panel section${view ? ' setup-view' : ''}`}>
        {view && saved ? (
          <>
            <button ref={pencil} class="btn small quiet setup-edit" type="button" aria-label="Edit" title="Edit" onClick={edit}><Pencil /></button>
            <dl class="kv">
              <dt>Folder for your course repos</dt><dd><code>{saved.folder}</code></dd>
              <dt>Editor</dt><dd>{EDITOR_WORD[saved.editor]}{saved.editor === 'other' && saved.scheme ? <>, <code>{saved.scheme}</code></> : null}</dd>
            </dl>
          </>
        ) : (
          <div class="setup-form">
            <div class="field">
              <label for="ys-folder">Folder for your course repos</label>
              <input ref={folderInput} type="text" id="ys-folder" value={draft.folder} placeholder={example} spellcheck={false} autocomplete="off" autocapitalize="off"
                onInput={(e) => change({ folder: (e.target as HTMLInputElement).value })} />
              <p class="hint">Each course gets a folder inside it, named after its organisation.</p>
            </div>
            <fieldset class="field">
              <legend class="label">Editor</legend>
              <div class="choices" role="radiogroup" aria-label="Editor">
                <label class="choice"><input type="radio" name="ys-editor" checked={draft.editor === 'vscode'} onChange={() => change({ editor: 'vscode' })} /><b>VS Code</b><span>Opens the repo’s folder, or clones the repo when there is no folder yet.</span></label>
                <label class="choice"><input type="radio" name="ys-editor" checked={draft.editor === 'desktop'} onChange={() => change({ editor: 'desktop' })} /><b>GitHub Desktop</b><span>Opens the repo, and clones it the first time.</span></label>
                <label class="choice"><input type="radio" name="ys-editor" checked={draft.editor === 'other'} onChange={() => change({ editor: 'other' })} /><b>Another editor</b><span>Its link, with <code>{'{path}'}</code> where the folder goes.</span></label>
              </div>
              {draft.editor === 'other' ? (
                <div class="scheme">
                  <label class="sr" for="ys-scheme">Your editor’s link</label>
                  <input type="text" id="ys-scheme" value={draft.scheme} placeholder="myeditor://open/{path}" spellcheck={false} autocomplete="off" autocapitalize="off"
                    aria-invalid={draft.scheme.trim() && badScheme ? true : undefined} onInput={(e) => change({ scheme: (e.target as HTMLInputElement).value })} />
                  <p class={draft.scheme.trim() && badScheme ? 'invalid-msg' : 'hint'}>Put <code>{'{path}'}</code> after a slash or a colon, as in <code>{'vscode://file/{path}'}</code>.</p>
                </div>
              ) : null}
            </fieldset>
            <div class="actions">
              <button class="btn" type="button" disabled={badScheme || (!!saved && same(draft, draftOf(saved)))} onClick={save}>Save</button>
              {saved?.folder.trim() ? <button class="textlink" type="button" onClick={cancel}>Cancel</button> : null}
              {status}
            </div>
          </div>
        )}
        {check}
        {view && status ? <p>{status}</p> : null}
      </section>
      <p class="footnote">Kept in this browser, for your GitHub login.</p>
    </>
  );
}
