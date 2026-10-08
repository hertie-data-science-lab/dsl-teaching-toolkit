// Setting suggestions aside (decisions 0032 and 0034). Only a suggested item may be set aside
// (`need`, or on an older status `stage_optional` / a to-do's `optional`: model/readiness.ts);
// the course's dsl-course.yml lists the ids set aside (`set_aside:`), shared by everyone on the
// course. The console reads that list as it last read or wrote the file, so an item moves the
// moment the save lands, before the status is rewritten; until the file is read it takes the
// engine's `stage_set_aside` / `set_aside`.

import courseSchema from '../../schemas/dsl_course.schema.json';
import { useRef, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { invalidText, saveText } from '../edit/save';
import { YamlText } from '../edit/yamlText';
import type { FileState, Files } from '../model/files';
import { COURSE_REPO } from '../model/names';
import { validator } from '../model/validate';
import { CheckLine } from '../ui/bits';
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

/** dsl-course.yml with `id` added to (or removed from) `set_aside:`, the rest untouched; or why it would not be valid. */
export function setAsideText(text: string, id: string, on: boolean): { text: string } | { error: string } {
  const y = new YamlText(text);
  const raw = y.get([SET_ASIDE_KEY]);
  const rest = (Array.isArray(raw) ? raw : []).filter((x): x is string => typeof x === 'string' && x.trim() !== id);
  const next = on ? [...rest, id] : rest;
  y.assign([SET_ASIDE_KEY], next.length ? next : undefined);
  return validCourse(y.toJS()) ? { text: y.text } : { error: invalidText('dsl-course.yml', validCourse) };
}

/** The suggestion the circle asks about. */
export interface Ask {
  id: string;
  label: string;
}

/** The circle's question (decision 0032 rule 4): only a suggestion has a circle. */
export function SetAsideDialog({ ask, busy, error, onSetAside, onClose, fallback }: { ask: Ask; busy: boolean; error?: string; onSetAside: () => void; onClose: () => void; fallback?: () => HTMLElement | null | undefined }) {
  return (
    <Modal small title={`Set aside “${ask.label}”?`} onClose={onClose} fallback={fallback}>
      <p id={MODAL_TEXT_ID}>It’s optional: automation runs without it. It moves to Set aside at the end of this list, for everyone on the course. You can bring it back at any time.</p>
      {error ? <CheckLine cls="bad">{error}</CheckLine> : null}
      <div class="actions">
        <button class="btn" type="button" disabled={busy} onClick={onSetAside}>{busy ? 'Setting aside…' : 'Set aside'}</button>
        <button class="btn quiet" type="button" onClick={onClose}>Cancel</button>
      </div>
    </Modal>
  );
}

/**
 * Set aside and Bring back for one dashboard (course or semester alike: the engine reads the
 * semester's suggestions from the course's list too). `rows` are the panel's items; `onCircle`
 * and `onBack` are undefined for a viewer without write access. `after` (the error and the
 * dialog) goes after the panel inside `ref`'s element, so focus can land on the Set aside tab
 * when the line it came from moved.
 */
export function useSetAside({ org, files, migrated, write }: { org: string; files: Files; migrated?: boolean; write: boolean }) {
  const env = useEnv();
  const [ask, setAsk] = useState<(Ask & { ids: string[] }) | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const file = files.file(org, COURSE_REPO, COURSE_FILE);
  const change = async (ids: string[], on: boolean) => {
    setError('');
    const refuse = (text: string) => setError(text);
    if (!env) return refuse('Sign in to save.');
    if (migrated === false) return refuse('Not saved: the console has not yet confirmed this course uses the current names.');
    if (file.kind !== 'ready') return refuse(`Not saved: ${COURSE_FILE} is not read yet.`);
    let text = file.text;
    for (const id of ids) {
      const out = setAsideText(text, id, on);
      if ('error' in out) return refuse(out.error);
      text = out.text;
    }
    setBusy(true);
    const ok = await saveText(env, { owner: org, repo: COURSE_REPO, path: COURSE_FILE }, text, file.sha, {
      message: `course: ${on ? 'set aside' : 'bring back'} ${ids.join(', ')}, from the DSL Teaching Console`,
      statusRepo: [org, COURSE_REPO],
      onCommit: () => {
        setBusy(false);
        setAsk(null);
      },
    }, (st) => setError(st.kind === 'bad' ? st.text : ''));
    if (!ok) setBusy(false);
  };
  const close = () => {
    setAsk(null);
    setError('');
  };
  return {
    ref,
    busy,
    list: asideList(file),
    onCircle: write ? (it: { id: string; label: string; ids?: string[] }) => setAsk({ id: it.id, label: it.label, ids: it.ids ?? [it.id] }) : undefined,
    onBack: (items: { id: string; ids?: string[] }[]) => (write ? (id: string) => void change(items.find((i) => i.id === id)?.ids ?? [id], false) : undefined),
    after: (
      <>
        {error && !ask ? <CheckLine cls="bad">{error}</CheckLine> : null}
        {ask ? <SetAsideDialog ask={ask} busy={busy} error={error} onSetAside={() => void change(ask.ids, true)} onClose={close} fallback={() => ref.current?.querySelector<HTMLElement>('[role="tab"][data-key="aside"]')} /> : null}
      </>
    ),
  };
}
