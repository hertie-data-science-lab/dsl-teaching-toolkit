// The session date a TBC release still lacks (decision 0043 rule 5): once an `event_datetime: tbc`
// entry has gone out, its release detail (and the result panel of a Release early, now or again
// from the console) offers "Session date: TBC -> [date] [time]". Set date writes the one entry
// through the schedule editor's own path; Leave as TBC hides the block until the page is opened
// again (the Dashboard's suggested to-do stays, and can be set aside).

import { useMemo, useState } from 'preact/hooks';
import { useDraftSave } from '../edit/save';
import { CONFIG_REPO } from '../model/names';
import { draftErrors, readDraft, type EntryDraft } from '../model/scheduleEdit';
import type { Release } from '../model/types';
import { SaveLine } from '../ui/edit';
import { scheduleAfter, useSchedFile } from './Schedule';
import type { ReadyProps } from './types';

/** Whether a release is one the block is for: no session date (`event_datetime: tbc`), and released. */
export const releasedTbc = (rel: Release): boolean => rel.when === null && rel.state === 'released';

/** `entry` with its first slot on `date` at `time` (a bare date when `time` is empty), no longer TBC. */
export function withSessionDate(entry: EntryDraft, date: string, time: string): EntryDraft {
  const [first, ...rest] = entry.slots;
  return { ...entry, tbc: false, slots: [{ ...first, date, time }, ...rest] };
}

export function SessionDate({ p, rel, id }: { p: ReadyProps; rel: Release; id: string }) {
  const sf = useSchedFile(p);
  const ready = sf && sf !== 'loading' && !sf.error ? sf : null;
  const entry = useMemo(() => {
    const d = ready ? readDraft(ready.doc, rel.id) : null;
    return d?.kind === 'entry' && d.tbc ? d : null;
  }, [ready?.doc, rel.id]);
  const [left, setLeft] = useState(false);
  const { cur, set, save, setSave, run } = useDraftSave({ date: '', time: '' });
  // Saved: the entry is dated now and the block goes, its save line staying to say so.
  if (!entry || left) return save.kind === 'idle' ? null : <SaveLine state={save} />;
  const doSave = async () => {
    if (!ready) return;
    if (!cur.date) return setSave({ kind: 'bad', text: 'Give the session’s date.' });
    const d = withSessionDate(entry, cur.date, cur.time);
    const why = Object.values(draftErrors(d, { doc: ready.doc }))[0];
    if (why) return setSave({ kind: 'bad', text: why });
    const out = scheduleAfter(ready, [d]);
    if ('error' in out) return setSave({ kind: 'bad', text: out.error });
    await run({ owner: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml' }, out.text, ready.sha, { message: `schedule: date the session of ${rel.id}, from the DSL Teaching Console`, statusRepo: [p.cohort.org, CONFIG_REPO] });
  };
  return (
    <div class="session-date">
      <p class="session-date-line">
        <b>Session date:</b> TBC <span aria-hidden="true">→</span>{' '}
        <input id={`${id}-date`} type="date" aria-label="Session date" value={cur.date} onInput={(e) => set({ ...cur, date: (e.target as HTMLInputElement).value })} />{' '}
        <input id={`${id}-time`} type="time" aria-label="Session time" value={cur.time} onInput={(e) => set({ ...cur, time: (e.target as HTMLInputElement).value })} />{' '}
        <button class="btn small" type="button" disabled={save.kind === 'busy'} onClick={() => void doSave()}>Set date</button>{' '}
        <button class="btn small quiet" type="button" onClick={() => setLeft(true)}>Leave as TBC</button>
      </p>
      <p class="footnote">It is released, so students have its files; the schedule and the site still say TBC until it has a date.</p>
      <SaveLine state={save} />
    </div>
  );
}
