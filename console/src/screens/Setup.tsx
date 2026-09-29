// Your setup (`#setup`, beside Help): the folder a person keeps the course repos in and the
// editor the Open button uses (decision 0017). Kept per login in this browser only
// (`model/prefs.ts`), never sent anywhere. The login is known from sign-in, so there is no
// handle field; instructors push to the course repos, so there is no fork step. Nothing is
// stored until Save, and the line under each field says what is.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { courseFolder, folderExample, platformOf, schemeOk, type Editor, type Setup } from '../model/open';
import { saveYourSetup, yourSetup } from '../model/prefs';

const EDITOR_WORD: Record<Editor, string> = { vscode: 'VS Code', desktop: 'GitHub Desktop', other: 'another editor' };

type Draft = Pick<Setup, 'folder' | 'editor'> & { scheme: string };

const draftOf = (s: Setup | null): Draft => ({ folder: s?.folder ?? '', editor: s?.editor ?? 'vscode', scheme: s?.scheme ?? '' });
const same = (a: Draft, b: Draft) => a.folder.trim() === b.folder.trim() && a.editor === b.editor && (a.editor !== 'other' || a.scheme.trim() === b.scheme.trim());

export function SetupScreen({ org }: { org?: string }) {
  const env = useEnv();
  const login = env?.user.login ?? '';
  const [saved, setSaved] = useState<Setup | null>(() => yourSetup(login));
  const [draft, setDraft] = useState<Draft>(() => draftOf(saved));
  const [done, setDone] = useState(false);
  const example = folderExample(platformOf());
  const badScheme = draft.editor === 'other' && !schemeOk(draft.scheme);
  const change = (d: Partial<Draft>) => {
    setDraft({ ...draft, ...d });
    setDone(false);
  };
  const save = () => {
    const next: Setup = { ...(saved?.lastOpen ? { lastOpen: saved.lastOpen } : {}), folder: draft.folder.trim(), editor: draft.editor };
    if (draft.editor === 'other') next.scheme = draft.scheme.trim();
    saveYourSetup(login, next);
    setSaved(next);
    setDone(true);
  };
  const where = courseFolder(draft.folder || example, org ?? '');
  return (
    <>
      <div class="page-head">
        <div><h1>Your setup</h1><p class="lede">Where the Open button finds the course repos on your computer.</p></div>
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
            <button class="btn" type="button" disabled={badScheme || (!!saved && same(draft, draftOf(saved)))} onClick={save}>Save your setup</button>
            {done ? <span class="valid-msg" role="status">Saved.</span> : null}
          </div>
        </div>
      </section>
      <p class="footnote">Kept in this browser only. Signing out forgets it.</p>
    </>
  );
}
