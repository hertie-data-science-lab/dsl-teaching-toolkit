// The Open split button for one file (the Released materials page's differing-files panel): the
// repo Open button's markup and classes (`ui/OpenButton.tsx`, `.split`), its main part opening
// the file's GitHub blob page in a new tab, its arrow the same menu (`useMenu`) with what a
// file row's "More ways to open" offers (`ui/FileRow.tsx`): In the browser, on GitHub and on
// github.dev; On your computer, the editor Profile names, or "Set up your editor in Profile"
// until Profile has a folder and an editor that takes a path. A document no editor opens (pdf,
// docx, ...) has no github.dev or editor row; it says so instead.

import { editableFile, editorName, fileLocal, fileOnline, isWeb, type Setup } from '../model/open';
import { ghUrl } from './bits';
import { newTabIf } from './FileRow';
import { Ext } from './icons';
import { useMenu } from './OpenButton';

export interface OpenSplitProps {
  org: string;
  repo: string;
  path: string;
  /** The signed-in person's Profile (`useYourSetup`). */
  setup: Setup | null;
  /** Profile, where "Set up your editor in Profile" goes. */
  profile: string;
}

export function OpenSplit({ org, repo, path, setup, profile }: OpenSplitProps) {
  const { open, setOpen, id, wrap, caret, show, onCaretKey, onCaretKeyUp, onMenuKey } = useMenu();
  const blob = ghUrl(org, repo, path, 'HEAD');
  const edit = editableFile(path);
  const local = edit ? fileLocal(setup, org, repo, path) : null;
  const named = editorName(setup ?? { folder: '', editor: 'vscode' });
  const close = () => setOpen(false);
  const item = (href: string, label: string) => (
    <a href={href} role="menuitem" tabIndex={-1} {...newTabIf(isWeb(href))} onClick={close}><span>{label}</span>{isWeb(href) ? <Ext /> : null}</a>
  );
  return (
    <div class="split small" ref={wrap}>
      <a class="btn quiet small split-main" href={blob} target="_blank" rel="noopener" title="Open on GitHub" aria-label={`Open ${path} on GitHub`}>Open</a>
      <button ref={caret} type="button" class="btn quiet small split-caret" aria-haspopup="menu" aria-expanded={open} aria-controls={id} aria-label={`More ways to open ${path}`}
        onClick={() => (open ? close() : show('first'))} onKeyDown={onCaretKey} onKeyUp={onCaretKeyUp}>
        <span class="caret" aria-hidden="true" />
      </button>
      <div class="popmenu open-menu" id={id} role="menu" aria-label={`Open ${path}`} hidden={!open} onKeyDown={onMenuKey}>
        <div class="pm-h" role="presentation">In the browser</div>
        {item(blob, 'On GitHub')}
        {edit ? item(fileOnline(org, repo, path, blob), 'On github.dev') : null}
        <hr />
        <div class="pm-h" role="presentation">On your computer</div>
        {!edit ? <span class="pm-sub pm-off">Not an editable format</span>
          : local ? item(local, named ? `In ${named}` : 'In your editor')
          : item(profile, 'Set up your editor in Profile')}
      </div>
    </div>
  );
}
