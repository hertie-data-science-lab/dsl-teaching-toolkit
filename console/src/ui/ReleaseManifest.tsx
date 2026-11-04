// What Release copies (plan 2026-11-04, decisions 5-6): the Release view's right column. The
// picks grouped by their immediate parent folder (`groupPicks`), each row `path → dest path` with
// its state chip, the files a picked folder leaves behind greyed with why, and per group an Edit
// destination fold (To repo, To path); a group sent to a repo other than the run's is amber with
// a (!). Above it the run's To row, below it the footer with the one Release button and the way
// to the Schedule. Props only: the Files panel reads the trees and holds the picks.

import { useState } from 'preact/hooks';
import { plural } from '../model/format';
import { destPathOf, type FileState as ReleaseState, type Group, type Reserved } from '../model/releaseState';
import type { OpDef } from '../ops/session';
import { OpOpen } from '../ops/Panel';
import { Hint } from './Hint';
import { RepoPickerModal, type RepoRow } from './RepoPickerModal';
import { StateChip } from './StateChip';

const CHOOSE = '__choose';

/** A group's identity across renders (its overrides are kept by it). */
export const groupKey = (g: Pick<Group, 'repo' | 'kind' | 'folder'>) => `${g.repo}:${g.kind}:${g.folder}`;

/** The destination choices every repo select shares: the rows the picker lists, the names the select offers. */
export interface Dests {
  /** The select's options, the run's default first. */
  options: string[];
  /** The picker's radio rows. */
  rows: RepoRow[];
  reserved: Reserved;
}

/** A repo select: the options, the current value if it is not one of them, and "Choose a repo…" opening the picker. */
export function RepoSelect({ id, value, dests, onPick, cls, label }: { id?: string; value: string; dests: Dests; onPick: (name: string) => void; cls?: string; label?: string }) {
  const [picking, setPicking] = useState(false);
  const options = [...new Set([value, ...dests.options].filter(Boolean))];
  return (
    <>
      <select id={id} class={cls} aria-label={label} onChange={(e) => {
        const s = e.target as HTMLSelectElement;
        if (s.value !== CHOOSE) return onPick(s.value);
        s.value = value;
        setPicking(true);
      }}>
        {options.map((r) => <option value={r} selected={r === value}>{r}</option>)}
        <option value={CHOOSE}>Choose a repo…</option>
      </select>
      {picking ? <RepoPickerModal kind="dest" rows={dests.rows} current={value} reserved={dests.reserved} onChoose={onPick} onClose={() => setPicking(false)} /> : null}
    </>
  );
}

/** The run's To row over the manifest: the semester org, then its repo. */
export function ToRow({ org, runRepo, dests, onRunRepo }: { org: string; runRepo: string; dests: Dests; onRunRepo: (name: string) => void }) {
  return (
    <>
      <span class="split-lbl">To</span>
      <span class="mf-into">{org} /</span>
      <RepoSelect cls="mf-repo" label="To repo" value={runRepo} dests={dests} onPick={onRunRepo} />
    </>
  );
}

export interface ManifestProps {
  groups: Group[];
  /** The run's destination repo: where a group with no override lands. */
  runRepo: string;
  dests: Dests;
  onGroupDest: (key: string, patch: { destRepo?: string; destPath?: string }) => void;
  /** A shipping file's state at its group's destination. */
  stateOf: (g: Group, path: string) => ReleaseState | undefined;
  /** The ahead chip's action and the diverged chip's pull request, per row. */
  onKeep?: () => void;
  prUrl?: (g: Group, path: string) => string | undefined;
}

function GroupRows({ g, p }: { g: Group; p: ManifestProps }) {
  const to = g.destRepo || p.runRepo;
  const other = to !== p.runRepo;
  const warn = other ? <span class="mf-warn" title={`This file is being released to a different repo: ${to}`}>(!)</span> : null;
  return (
    <ul class="mf-rows">
      {g.ship.map((path) => {
        const s = p.stateOf(g, path);
        return (
          <li>
            <code class="mf-src">{path}</code>
            <span class="mf-arrow" aria-hidden="true">→</span>
            <span class={`mf-dest${other ? ' mf-diff' : ''}`}>{destPathOf(g, path)}</span>
            {warn}
            {s && s !== 'unknown' ? <StateChip kind={s} onKeep={p.onKeep} prUrl={p.prUrl?.(g, path)} /> : null}
          </li>
        );
      })}
      {g.withheld.map((w) => (
        <li class="mf-out">
          <code class="mf-src">{w.path}</code>
          <span class="footnote">{w.rule === null ? 'only if named' : <>withheld by <code>{w.rule}</code></>}</span>
        </li>
      ))}
    </ul>
  );
}

function GroupCard({ g, p }: { g: Group; p: ManifestProps }) {
  const key = groupKey(g);
  const id = `mf-${key.replace(/[^a-z0-9]+/gi, '-')}`;
  const to = g.destRepo || p.runRepo;
  return (
    <li class={`mf-group${to !== p.runRepo ? ' other' : ''}`} key={key}>
      <div class="mf-head">
        <code>{g.head}</code> · {g.kind === 'all' ? `all ${plural(g.files, 'file')}` : `${g.files} of ${plural(g.of ?? g.files, 'file')}`}
      </div>
      <GroupRows g={g} p={p} />
      <details class="fold mf-adv">
        <summary>Edit destination</summary>
        <div class="fold-body mf-dest-fields">
          <div class="field">
            <label for={`${id}-r`}>To repo</label>
            <RepoSelect id={`${id}-r`} value={to} dests={p.dests} onPick={(name) => p.onGroupDest(key, { destRepo: name === p.runRepo ? undefined : name })} />
          </div>
          <div class="field">
            <label for={`${id}-p`}>To path</label>
            <input type="text" id={`${id}-p`} value={g.destPath ?? g.folder} onInput={(e) => p.onGroupDest(key, { destPath: (e.target as HTMLInputElement).value })} />
          </div>
        </div>
      </details>
    </li>
  );
}

export function ReleaseManifest(p: ManifestProps) {
  if (!p.groups.length) {
    return <div class="manifest-wrap" aria-label="What Release copies"><div class="mf-scroll"><p class="empty-note">Nothing picked yet. Tick files or folders in the tree.</p></div></div>;
  }
  // Picks from more than one course repo: each repo's groups under its name (decision 6).
  const repos = [...new Set(p.groups.map((g) => g.repo))];
  return (
    <div class="manifest-wrap" aria-label="What Release copies">
      <div class="mf-scroll">
        {repos.map((repo) => (
          <>
            {repos.length > 1 ? <p class="sub-h mf-repo-h">{repo}</p> : null}
            <ul class="manifest">{p.groups.filter((g) => g.repo === repo).map((g) => <GroupCard g={g} p={p} />)}</ul>
          </>
        ))}
      </div>
    </div>
  );
}

/** The Release footer: the count, the one verb and its `?`, then the way to the Schedule. */
export function ReleaseFoot({ paths, files, def, scheduleHref }: { paths: number; files: number; def: OpDef; scheduleHref?: string }) {
  return (
    <div class="rh-foot">
      <span class="rh-count">{plural(paths, 'path')} picked · {plural(files, 'file')}</span>
      <OpOpen def={def} label={`Release ${plural(paths, 'path')}`} disabled={!paths} />
      <Hint label="About Release">
        <b class="para">Release</b>
        <span class="para">Copies every listed file now, to its destination in the semester. A file already up to date is left as it is; a semester edit is merged with, and an edit on both sides opens a pull request. Nothing is deleted, and nothing is added to the schedule.</span>
      </Hint>
      {scheduleHref ? <p class="footnote rh-sched">Want to pre-automate these releases? <a class="textlink" href={scheduleHref}>Schedule on the calendar →</a></p> : null}
    </div>
  );
}
