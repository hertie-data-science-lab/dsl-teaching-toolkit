// The pieces every editor shares: the save line ("Checking… Valid. Problem cleared."),
// the Save bar with "Edit the file" beside it, and the unsaved-changes bar.

import type { ComponentChildren } from 'preact';
import type { SaveState } from '../edit/save';
import { CheckLine, EditFile } from './bits';

export function SaveLine({ state }: { state: SaveState }) {
  if (state.kind === 'idle') return null;
  return <CheckLine cls={state.kind === 'busy' ? 'busy' : state.kind}>{state.text}</CheckLine>;
}

export interface FileRef {
  org: string;
  repo: string;
  path: string;
  branch?: string;
  line?: number;
}

export function SaveBar({
  state,
  onSave,
  label = 'Save',
  disabled,
  file,
  note,
  small,
  children,
}: {
  state: SaveState;
  onSave: () => void;
  label?: string;
  disabled?: boolean;
  file: FileRef;
  note?: ComponentChildren;
  small?: boolean;
  children?: ComponentChildren;
}) {
  return (
    <>
      <SaveLine state={state} />
      <div class="savebar">
        {note ? <span class="footnote">{note}</span> : null}
        <button class={`btn${small ? ' small' : ''}`} type="button" disabled={disabled || state.kind === 'busy'} onClick={onSave}>{label}</button>
        <EditFile org={file.org} repo={file.repo} path={file.path} branch={file.branch} line={file.line} />
        {children}
      </div>
    </>
  );
}

export function UnsavedBar({ count, onDiscard, onSave, file, busy }: { count: number; onDiscard: () => void; onSave: () => void; file: FileRef; busy?: boolean }) {
  if (!count) return null;
  return (
    <div class="unsaved" role="status">
      <span class="u-text">{count} unsaved change{count > 1 ? 's' : ''}</span>
      <span class="actions">
        <button class="btn small quiet" type="button" onClick={onDiscard}>Discard</button>
        <button class="btn small" type="button" disabled={busy} onClick={onSave}>Save</button>
        <EditFile org={file.org} repo={file.repo} path={file.path} branch={file.branch} />
      </span>
    </div>
  );
}

/** The line a YAML key starts on (1-based), for "Edit the file" to open near it. */
export function lineOf(text: string, key: string, indent = 2): number | undefined {
  const re = new RegExp(`^ {${indent}}${key.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}:`, 'm');
  const m = re.exec(text);
  return m ? text.slice(0, m.index).split('\n').length : undefined;
}
