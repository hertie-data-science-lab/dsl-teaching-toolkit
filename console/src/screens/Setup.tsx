// Profile (`#profile`, from the avatar; decisions 0017, 0021 rule 3): the folder a person
// keeps the course repos in and the editor the Open button uses. Kept per login in this
// browser only (`model/prefs.ts`), never sent anywhere, and kept across sign-out. The login
// is known from sign-in, so there is no handle field; instructors push to the course repos,
// so there is no fork step. Nothing is stored until Save, and the line under each field says
// what is. Where the browser allows it, the folder check (decision 0023) and, with a course
// in context, one block that clones every repo of it.

import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { canCheckFolders, folderHandle, forgetFolder, lastSegment, pickFolder } from '../model/localFolder';
import { cloneCommand, courseFolder, folderExample, platformOf, repoUrl, schemeOk, type Editor, type Setup } from '../model/open';
import { saveYourSetup, yourSetup } from '../model/prefs';
import { copyText } from '../ui/OpenButton';

const EDITOR_WORD: Record<Editor, string> = { vscode: 'VS Code', desktop: 'GitHub Desktop', other: 'another editor' };

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

/** One `git clone` per repo of the course, into the course's folder, with a Copy button. */
function CloneAll({ org, repos, folder }: { org: string; repos: string[]; folder: string }) {
  const [note, setNote] = useState<string | null>(null);
  const parent = courseFolder(folder, org);
  const text = repos.map((r) => cloneCommand(repoUrl({ org, repo: r }), parent, r)).join('\n');
  const copy = async () => setNote((await copyText(text)) ? 'Copied' : 'Could not copy: select it below');
  useEffect(() => {
    if (!note) return;
    const t = setTimeout(() => setNote(null), 2000);
    return () => clearTimeout(t);
  }, [note]);
  return (
    <div class="field clone-all">
      <div class="clone-all-h"><span class="label">Clone every repo of this course</span><button class="btn small quiet" type="button" onClick={copy}>Copy</button><span class="footnote" role="status">{note ?? ''}</span></div>
      <pre class="cmd"><code>{text}</code></pre>
    </div>
  );
}

export function SetupScreen({ org, repos }: { org?: string; repos?: string[] }) {
  const env = useEnv();
  const login = env?.user.login ?? '';
  const [saved, setSaved] = useState<Setup | null>(() => yourSetup(login));
  const [draft, setDraft] = useState<Draft>(() => draftOf(saved));
  const [done, setDone] = useState<'stored' | 'kept' | null>(null);
  const example = folderExample(platformOf());
  const badScheme = draft.editor === 'other' && !schemeOk(draft.scheme);
  const change = (d: Partial<Draft>) => {
    setDraft({ ...draft, ...d });
    setDone(null);
  };
  const save = () => {
    // A new folder or editor makes the editor the Open button's default again.
    const keepLast = saved?.lastOpen && saved.folder === draft.folder.trim() && saved.editor === draft.editor;
    const next: Setup = { ...(keepLast ? { lastOpen: saved.lastOpen } : {}), folder: draft.folder.trim(), editor: draft.editor };
    if (draft.editor === 'other') next.scheme = draft.scheme.trim();
    setDone(saveYourSetup(login, next) ? 'stored' : 'kept');
    setSaved(next);
  };
  const where = courseFolder(draft.folder || example, org ?? '');
  return (
    <>
      <div class="page-head">
        <div><h1>Profile</h1><p class="lede">Where the Open button finds this course’s repos on your computer, and which editor it uses.</p></div>
      </div>
      <section class="panel section">
        <div class="setup-form">
          <div class="field">
            <label for="ys-folder">Folder for your course repos</label>
            <input type="text" id="ys-folder" value={draft.folder} placeholder={example} spellcheck={false} autocomplete="off" autocapitalize="off"
              onInput={(e) => change({ folder: (e.target as HTMLInputElement).value })} />
            <p class="hint">{org ? <>Each course gets a folder inside it. This course’s repos go in <code>{where}</code>.</> : 'Each course gets a folder inside it, named after its organisation.'}</p>
            <p class="saved">{saved?.folder ? <>Stored: <code>{saved.folder}</code></> : 'No folder stored: Open offers to clone instead.'}</p>
          </div>
          {draft.folder.trim() && canCheckFolders() ? <FolderCheck login={login} typed={draft.folder} org={org} /> : null}
          {draft.folder.trim() && org && repos?.length ? <CloneAll org={org} repos={repos} folder={draft.folder} /> : null}
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
            <p class="saved">{saved ? <>Stored: {EDITOR_WORD[saved.editor]}{saved.editor === 'other' && saved.scheme ? <>, <code>{saved.scheme}</code></> : null}</> : 'Nothing stored: VS Code is used.'}</p>
          </fieldset>
          <div class="actions">
            <button class="btn" type="button" disabled={badScheme || (!!saved && same(draft, draftOf(saved)))} onClick={save}>Save</button>
            {done === 'stored' ? <span class="valid-msg" role="status">Saved.</span> : done === 'kept' ? <span class="footnote" role="status">This browser does not keep it: it lasts until you reload.</span> : null}
          </div>
        </div>
      </section>
      <p class="footnote">Kept in this browser, for your GitHub login.</p>
    </>
  );
}
