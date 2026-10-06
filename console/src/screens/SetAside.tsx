// Setting optional Setup & To do items aside (decision 0032). The engine says which items are
// optional (`stage_optional`, a to-do's `optional`); the course's dsl-course.yml lists the ids set
// aside (`set_aside:`), shared by everyone on the course. The console reads that list as it last
// read or wrote the file, so an item moves the moment the save lands, before the status is
// rewritten; until the file is read it takes the engine's `stage_set_aside` / `set_aside`.

import courseSchema from '../../schemas/dsl_course.schema.json';
import { invalidText } from '../edit/save';
import { YamlText } from '../edit/yamlText';
import type { FileState } from '../model/files';
import type { CourseStatus, Todo } from '../model/types';
import { validator } from '../model/validate';
import { CheckLine } from '../ui/bits';
import { Ext } from '../ui/icons';
import { MODAL_TEXT_ID, Modal } from '../ui/Modal';

const validCourse = validator(courseSchema);
export const SET_ASIDE_KEY = 'set_aside';
export const COURSE_FILE = 'dsl-course.yml';

/** The ids dsl-course.yml sets aside: none when there is no file; null while it is not read (or does not parse). */
export function asideList(file: FileState): string[] | null {
  if (file.kind === 'absent') return [];
  if (file.kind !== 'ready') return null;
  const y = new YamlText(file.text);
  if (y.errors.length) return null;
  const raw = y.get([SET_ASIDE_KEY]);
  return Array.isArray(raw) ? raw.filter((x): x is string => typeof x === 'string').map((x) => x.trim()) : [];
}

/** Whether a setup step is set aside: optional (the engine's word), not done, and listed. */
export function stepAside(c: CourseStatus, id: string, list: string[] | null): boolean {
  if (c.stage_optional?.[id] !== true || c.stages[id] === 'done') return false;
  return list ? list.includes(id) : !!c.stage_set_aside?.[id];
}

/** Whether a to-do is set aside: optional (the engine's word) and listed. A done one is not a to-do. */
export function todoAside(t: Todo, list: string[] | null): boolean {
  if (t.optional !== true) return false;
  return list ? list.includes(t.id) : !!t.set_aside;
}

/** dsl-course.yml with `id` added to (or removed from) `set_aside:`, the rest untouched; or why it would not be valid. */
export function setAsideText(text: string, id: string, on: boolean): { text: string } | { error: string } {
  const y = new YamlText(text);
  const raw = y.get([SET_ASIDE_KEY]);
  const rest = (Array.isArray(raw) ? raw : []).filter((x): x is string => typeof x === 'string' && x.trim() !== id);
  const next = on ? [...rest, id] : rest;
  y.assign([SET_ASIDE_KEY], next.length ? next : undefined);
  return validCourse(y.toJS()) ? { text: y.text } : { error: invalidText('dsl-course.yml', validCourse) };
}

/** What the circle before a line asks about: an optional item, a required setup step, or a required to-do. */
export type Ask =
  | { kind: 'optional'; id: string; label: string }
  | { kind: 'step'; id: string; label: string; text: string; page: { href: string; label: string; ext?: boolean } }
  | { kind: 'todo'; id: string; label: string; todo: Todo; href: string };

/** A sentence as the tail of "Still missing: ...": first letter lower-case, no full stop. */
export function missingClause(why: string): string {
  const s = why.trim().replace(/\.$/, '');
  return s ? s[0].toLowerCase() + s.slice(1) : 'not done yet';
}

/** The circle's question, in decision 0032 rule 4's words. */
export function SetAsideDialog({ ask, busy, error, onSetAside, onClose, fallback }: { ask: Ask; busy: boolean; error?: string; onSetAside: () => void; onClose: () => void; fallback?: () => HTMLElement | null | undefined }) {
  if (ask.kind === 'optional') {
    return (
      <Modal small title={`Set aside “${ask.label}”?`} onClose={onClose} fallback={fallback}>
        <p id={MODAL_TEXT_ID}>It’s optional: the course is ready without it. It moves to Set aside at the end of this list, for everyone on the course. You can bring it back at any time.</p>
        {error ? <CheckLine cls="bad">{error}</CheckLine> : null}
        <div class="actions">
          <button class="btn" type="button" disabled={busy} onClick={onSetAside}>{busy ? 'Setting aside…' : 'Set aside'}</button>
          <button class="btn quiet" type="button" onClick={onClose}>Cancel</button>
        </div>
      </Modal>
    );
  }
  const title = `“${ask.label}” can’t be set aside`;
  if (ask.kind === 'step') {
    const { page } = ask;
    return (
      <Modal small title={title} onClose={onClose} fallback={fallback}>
        <p id={MODAL_TEXT_ID}>{ask.text}</p>
        <div class="actions">
          <a class="btn" href={page.href} onClick={onClose} {...(page.ext ? { target: '_blank', rel: 'noopener' } : {})}>{page.label}{page.ext ? <Ext /> : null}</a>
          <button class="btn quiet" type="button" onClick={onClose}>Close</button>
        </div>
      </Modal>
    );
  }
  const materials = ask.todo.kind === 'materials';
  return (
    <Modal small title={title} onClose={onClose} fallback={fallback}>
      <p id={MODAL_TEXT_ID}>{ask.todo.repo} can’t be {materials ? 'released' : 'handed out'} without it. If you no longer need this {materials ? 'materials repo' : 'assignment template'}, archive its repo on GitHub and it leaves this list.</p>
      <div class="actions">
        <a class="btn" href={ask.href} onClick={onClose}>Open settings</a>
        <button class="btn quiet" type="button" onClick={onClose}>Close</button>
      </div>
    </Modal>
  );
}

/** The circle before an open line: a button that asks whether it can be set aside. */
export function Circle({ label, onClick }: { label: string; onClick: () => void }) {
  return <button class="s-mark s-circle" type="button" title="Set aside…" aria-label={`Set aside ${label}…`} aria-haspopup="dialog" onClick={onClick} />;
}

/** The fold at the end of a section: what is set aside, each with Bring back (none for a read-only viewer). */
export function AsideFold({ rows, busy, onBack }: { rows: { id: string; label: string; repo?: string }[]; busy: boolean; onBack?: (id: string) => void }) {
  if (!rows.length) return null;
  return (
    <details class="fold aside-fold">
      <summary>Set aside ({rows.length})</summary>
      <ul class="fold-body">
        {rows.map((r) => (
          <li key={r.id}>
            {r.label}{r.repo ? <> (<span class="slug">{r.repo}</span>)</> : null}
            {onBack ? <> · <button class="textlink" type="button" disabled={busy} aria-label={`Bring back ${r.label}`} onClick={() => onBack(r.id)}>Bring back</button></> : null}
          </li>
        ))}
      </ul>
    </details>
  );
}
