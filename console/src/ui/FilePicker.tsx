// The one file picker of the schedule editor (forms review: "file picking everywhere uses the
// shared picker"): the course's materials repos as a tree in select mode, several folders and
// files at once, each withheld file marked from its repo's `.releaseignore`. A path that is not
// there yet may be typed: copies run at the release time, so a folder still being written is
// fine. Returns what was picked as {repo, path}; the caller turns each into a chip.

import { useMemo, useState } from 'preact/hooks';
import { buildTree, type TreeNode } from '../edit/badges';
import { compileAll, matchRules } from '../edit/glob';
import type { Files } from '../model/files';
import { Modal } from './Modal';

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

export function FilePicker({ files, org, repos, others = [], onPick, onClose }: { files: Files; org: string; repos: string[]; others?: string[]; onPick: (p: Picked[]) => void; onClose: () => void }) {
  const all = [...repos, ...others];
  const [repo, setRepo] = useState(all[0] ?? '');
  const [chosen, setChosen] = useState<Picked[]>([]);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [typed, setTyped] = useState('');
  const tree = repo ? files.tree(org, repo) : null;
  const ign = repo ? files.file(org, repo, '.releaseignore') : null;
  const rules = useMemo(() => compileAll(ign?.kind === 'ready' ? ign.text.split('\n') : []), [ign?.kind === 'ready' ? ign.text : '']);
  const nodes = useMemo(() => (tree?.kind === 'ready' ? buildTree(tree.paths.filter((x) => !x.dir && !x.path.startsWith('.')).map((x) => x.path)) : []), [tree]);
  const isOn = (path: string) => chosen.some((c) => c.repo === repo && c.path === path);
  const toggle = (path: string) => setChosen(isOn(path) ? chosen.filter((c) => !(c.repo === repo && c.path === path)) : [...chosen, { repo, path }]);
  const node = (n: TreeNode) => {
    const withheld = !n.children && matchRules(rules, n.path);
    return (
      <li class={n.children ? 'ft-folder' : 'ft-file'}>
        <span class="pick-row">
          {n.children ? <button type="button" class="pick-twisty" aria-expanded={open.has(n.path)} aria-label={`${open.has(n.path) ? 'Close' : 'Open'} ${n.name}`} onClick={() => { const s = new Set(open); if (s.has(n.path)) s.delete(n.path); else s.add(n.path); setOpen(s); }}>{open.has(n.path) ? '▾' : '▸'}</button> : <span class="pick-twisty" />}
          <label><input type="checkbox" checked={isOn(n.path)} onChange={() => toggle(n.path)} /> {n.name}{n.children ? '/' : ''}</label>
          {withheld ? <span class="chip notready">withheld</span> : null}
        </span>
        {n.children && open.has(n.path) ? <ul>{n.children.map(node)}</ul> : null}
      </li>
    );
  };
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
          : <ul class="file-tree pick-tree">{nodes.map(node)}</ul>}
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
