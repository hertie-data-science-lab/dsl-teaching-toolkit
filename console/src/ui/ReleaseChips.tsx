// The Releases settings' chip lists (plan 2026-11-04, decision 7), on Course details and
// Semester defaults: Release from (the course's materials repos, the `dsl-materials` topic) and
// Release into (`release_destinations`, merged institution, course, semester). An inherited
// repo is a grey chip naming where it comes from and cannot be removed here; the level's own
// carry a ×. "Add…" opens the repo picker.

import { useState } from 'preact/hooks';
import type { GhRepo } from '../github/client';
import { DEFAULTS } from '../model/policy';
import { eligibleDestination, type Reserved } from '../model/releaseState';
import type { Effective } from '../model/settings';
import type { Status } from '../model/types';
import { Hint } from './Hint';
import { RepoPickerModal, type RepoRow } from './RepoPickerModal';

/** An inherited chip: the repo and who set it ("the institution", "the course"). */
export interface FixedChip {
  name: string;
  from: string;
}

const FROM: Record<string, string> = { institution: 'the institution', course: 'the course', semester: 'this semester' };

/**
 * The Release into chips a level inherits: the default destination it inherits (always allowed),
 * then the inherited `release_destinations`, each named by the institution when the institution
 * lists it, else by `nearest` (the level below that added it: the course, for a semester).
 */
export function inheritedInto(dest: Effective, into: Effective | undefined, nearest: 'the course' | 'the institution'): FixedChip[] {
  const out: FixedChip[] = [];
  const add = (name: string, from: string) => name && !out.some((c) => c.name === name) && out.push({ name, from });
  add(String(dest.value ?? ''), FROM[dest.source] ?? nearest);
  const inst = DEFAULTS.release_destinations ?? [];
  for (const n of Array.isArray(into?.value) ? (into.value as unknown[]).map(String) : []) add(n, inst.includes(n) ? FROM.institution : nearest);
  return out;
}

/** A level's own `release_destinations`, as its form holds them. */
export const ownInto = (v: unknown): string[] => (Array.isArray(v) ? v.map(String).filter(Boolean) : []);

/** Student, team and marks repo names in a semester's repo list (the stems of its assignments), refused as destinations. */
export function studentRepos(names: string[], status: Status | null | undefined): string[] {
  const stems = (status?.assignments ?? []).map((a) => `${(a.semester_name ?? a.slug).toLowerCase()}-`);
  return names.filter((n) => stems.some((s) => n.toLowerCase().startsWith(s)));
}

/** The picker's rows: the semester's repos a release may write into, not listed already. */
export function destinationRows(repos: GhRepo[], listed: string[], reserved: Reserved): RepoRow[] {
  const have = new Set(listed.map((n) => n.toLowerCase()));
  return repos
    .filter((r) => !r.archived && !have.has(r.name.toLowerCase()) && eligibleDestination(r.name, reserved) === null)
    .map((r) => ({ name: r.name, note: 'in the semester' }))
    .sort((a, b) => a.name.localeCompare(b.name));
}

interface ChipsProps {
  id: string;
  label: string;
  hint: string;
  /** The grey "this course’s default: …" beside the label. */
  defaultLabel?: string;
  fixed: FixedChip[];
  own: string[];
  /** Without it the own chips have no × and there is no Add (a read-only viewer). */
  onRemove?: (name: string) => void;
  addLabel: string;
  onAdd?: () => void;
  footnote?: string;
  /** What a remove or an add is doing, or why it could not. */
  note?: string | null;
  busy?: boolean;
}

/** One chip list: the inherited chips grey, the level's own with ×, then Add. */
export function RepoChips(p: ChipsProps) {
  return (
    <div class="field">
      <span class="label" id={`${p.id}-label`}>{p.label} <Hint label={`About ${p.label}`} small>{p.hint}</Hint>{p.defaultLabel ? <span class="default"> {p.defaultLabel}</span> : null}</span>
      <div class="chips rel-chips" id={p.id} role="group" aria-labelledby={`${p.id}-label`}>
        {p.fixed.map((c) => <span class="chip file-chip fixed" title={`Set by ${c.from}; it cannot be removed here.`}>{c.name} <span class="sub">· from {c.from}</span></span>)}
        {p.own.map((n) => (
          <span class="chip file-chip">
            <span class="chip-open">{n}</span>
            {p.onRemove ? <button class="x" type="button" aria-label={`Remove ${n}`} disabled={p.busy} onClick={() => p.onRemove!(n)}>&times;</button> : null}
          </span>
        ))}
        {p.onAdd ? <button class="btn small quiet" type="button" disabled={p.busy} onClick={p.onAdd}>{p.addLabel}</button> : null}
      </div>
      {p.note ? <p class="footnote" aria-live="polite">{p.note}</p> : null}
      {p.footnote ? <p class="footnote">{p.footnote}</p> : null}
    </div>
  );
}

/**
 * Release into: the inherited chips, the level's own (`own`, written back through `onChange`)
 * and Add…, which opens "Choose a destination repo" over `rows` or a typed name that
 * `eligibleDestination` accepts.
 */
export function ReleaseInto({ id, hint, defaultLabel, fixed, own, onChange, rows, reserved }: {
  id: string;
  hint: string;
  defaultLabel?: string;
  fixed: FixedChip[];
  own: string[];
  /** Absent: read-only. An emptied list is undefined (the key goes back to its comment). */
  onChange?: (own: string[] | undefined) => void;
  rows: RepoRow[];
  reserved: Reserved;
}) {
  const [adding, setAdding] = useState(false);
  const listed = [...fixed.map((c) => c.name), ...own];
  const set = (next: string[]) => onChange?.(next.length ? next : undefined);
  const add = (name: string) => !listed.some((n) => n.toLowerCase() === name.toLowerCase()) && set([...own, name]);
  return (
    <>
      <RepoChips id={id} label="Release into" hint={hint} defaultLabel={defaultLabel} fixed={fixed} own={own} addLabel="Add…"
        onRemove={onChange ? (n) => set(own.filter((x) => x !== n)) : undefined} onAdd={onChange ? () => setAdding(true) : undefined} />
      {adding ? <RepoPickerModal kind="dest" rows={rows.filter((r) => !listed.includes(r.name))} reserved={reserved} onChoose={add} onClose={() => setAdding(false)} /> : null}
    </>
  );
}
