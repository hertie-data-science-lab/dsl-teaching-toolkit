// The one file picker of the schedule editor (forms review: "file picking everywhere uses the
// shared picker (the materials tree in select mode)"): a course repo's files as the shared
// file tree (`PatternTree`), a tick on every file and folder, each withheld file marked from
// the repo's `.releaseignore`. Several at once, across repos. A path that is not there yet may
// be typed: copies run at the release time, so a folder still being written is fine. Returns
// what was picked as {repo, path}; the caller turns each into a chip.

import { useState } from 'preact/hooks';
import type { Files } from '../model/files';
import { Modal } from './Modal';
import { neverMaterial } from '../edit/badges';
import { PatternTree } from './PatternTree';

export interface Picked {
  repo: string;
  path: string;
}

/** Whether `path` names a file or folder in a repo tree; null when the tree cannot say (not read yet, too large). */
export function pathVerdict(files: Files, org: string, repo: string, path: string): boolean | null {
  const tree = files.tree(org, repo);
  if (tree.kind !== 'ready') return tree.kind === 'absent' ? false : null;
  const p = path.replace(/^\/+|\/+$/g, '');
  const hit = tree.paths.some((x) => x.path === p || x.path.startsWith(`${p}/`));
  return hit ? true : tree.truncated ? null : false;
}

const noChange = () => {};
/** A schedule entry that names a denylisted path ships it (`selection.deny`), so only never-material names are fixed here. */
const pickFixed = (path: string) => (neverMaterial(path) ? 'never copied' : null);

export function FilePicker({ files, org, repos, others = [], onPick, onClose }: { files: Files; org: string; repos: string[]; others?: string[]; onPick: (p: Picked[]) => void; onClose: () => void }) {
  const all = [...repos, ...others];
  const [repo, setRepo] = useState(all[0] ?? '');
  const [chosen, setChosen] = useState<Picked[]>([]);
  const [typed, setTyped] = useState('');
  const tree = repo ? files.tree(org, repo) : null;
  const ign = repo ? files.file(org, repo, '.releaseignore') : null;
  const patterns = ign?.kind === 'ready' ? ign.text.split('\n') : [];
  const paths = tree?.kind === 'ready' ? tree.paths.filter((x) => !x.dir && !x.path.startsWith('.')).map((x) => x.path) : [];
  const isOn = (path: string) => chosen.some((c) => c.repo === repo && c.path === path);
  const toggle = (path: string) => setChosen(isOn(path) ? chosen.filter((c) => !(c.repo === repo && c.path === path)) : [...chosen, { repo, path }]);
  const tick = (path: string, dir: boolean) => <input type="checkbox" class="ft-tick" checked={isOn(path)} aria-label={`Pick ${path}${dir ? '/' : ''}`} onChange={() => toggle(path)} />;
  return (
    <Modal title="Pick from course materials" onClose={onClose}>
      <div class="picker">
        <div class="field">
          <label for="pick-repo">Repo</label>
          <select id="pick-repo" onChange={(e) => setRepo((e.target as HTMLSelectElement).value)}>
            {repos.length ? <optgroup label="Course materials repos">{repos.map((r) => <option value={r} selected={r === repo}>{r}</option>)}</optgroup> : null}
            {others.length ? <optgroup label="Other repos">{others.map((r) => <option value={r} selected={r === repo}>{r}</option>)}</optgroup> : null}
          </select>
        </div>
        {tree?.kind === 'loading' ? <p class="footnote">Reading {repo}…</p>
          : tree?.kind === 'error' ? <p class="footnote">Could not read {repo}; it is tried again shortly. A path can still be typed below.</p>
          : tree?.kind === 'absent' ? <p class="footnote">{repo} was not found in the course, or you cannot read it.</p>
          : <PatternTree files={paths} patterns={patterns} onChange={noChange} mode="select" releasedWord="" fixed={pickFixed} control={tick} />}
        <div class="field">
          <label for="pick-typed">Or type a path <span class="default">it need not exist yet</span></label>
          <div class="pick-typed">
            <input id="pick-typed" type="text" value={typed} placeholder="lectures/05_regularisation" onInput={(e) => setTyped((e.target as HTMLInputElement).value)} />
            <button class="btn small quiet" type="button" disabled={!typed.trim() || !repo} onClick={() => { setChosen([...chosen, { repo, path: typed.trim().replace(/^\/+|\/+$/g, '') }]); setTyped(''); }}>Add</button>
          </div>
        </div>
        {chosen.length ? <div class="chips">{chosen.map((c) => <span class="chip">{c.path} <span class="sub">· {c.repo}</span></span>)}</div> : null}
        <div class="savebar">
          <button class="btn" type="button" onClick={onClose}>Cancel</button>
          <button class="btn primary" type="button" disabled={!chosen.length} onClick={() => { onPick(chosen); onClose(); }}>{chosen.length ? `Add ${chosen.length}` : 'Add'}</button>
        </div>
      </div>
    </Modal>
  );
}
