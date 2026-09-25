// The template's `formats`: every starter format students hand in, ticked, and which one is
// the runnable one (listed first: autograde and the completion check read it; decision 0009
// rule 10). Used by New assignment and the template's Settings form.

import { DEFAULT_FORMATS } from '../model/policy';
import { FORMATS, formatWord } from '../tiers/grading';
import type { Values } from '../tiers/types';
import { autogradeBlock, formatBlock, formatError, toggleFormat } from '../wizards/model';

/** `formats` with `f` moved to the front, the runnable place. */
export function runnableFirst(formats: string[], f: string): string[] {
  return formats.includes(f) ? [f, ...formats.filter((x) => x !== f)] : formats;
}

export function FormatPicker({ v, set, id = 'na' }: { v: Values; set: (v: Values) => void; id?: string }) {
  const formats = (v.formats as string[] | undefined) ?? [];
  const auto = v.autograde === 'true' && !autogradeBlock(v);
  const offs = FORMATS.map(([f]) => formatBlock(formats, f, auto)).filter((x): x is string => !!x);
  const err = formatError(v);
  const runnable = formats.filter((f) => f !== 'none');
  return (
    <div class="field">
      <span class="label">What students hand in <span class="default">institution default: {DEFAULT_FORMATS.map(formatWord).join(' + ')}</span></span>
      <div class="fmt-grid">
        {FORMATS.map(([f, label]) => {
          const why = formatBlock(formats, f, auto);
          return (
            <label class={`check${why ? ' off' : ''}`} title={why ?? undefined}>
              <input type="checkbox" id={`${id}-fmt-${f}`} checked={formats.includes(f)} disabled={!!why} onChange={() => set({ ...v, formats: toggleFormat(formats, f) })} />
              <span>{label}</span>
            </label>
          );
        })}
      </div>
      {offs.length ? <p class="off-why">{[...new Set(offs)].join(' ')}</p> : null}
      {err ? <span class="invalid-msg"><span>{err}</span></span> : null}
      {runnable.length > 1 ? (
        <div class="field">
          <label for={`${id}-runnable`}>Runnable format <span class="default">the first one listed</span></label>
          <select id={`${id}-runnable`} onChange={(e) => set({ ...v, formats: runnableFirst(formats, (e.target as HTMLSelectElement).value) })}>
            {runnable.map((f) => <option value={f} selected={formats[0] === f}>{formatWord(f)}</option>)}
          </select>
          <p class="why">Automatic tests and the completion check read this one; the others are marked by hand.</p>
        </div>
      ) : null}
      <p class="why">Seeds the starter files and decides how markers see submissions. One mark sheet covers them all.</p>
    </div>
  );
}
