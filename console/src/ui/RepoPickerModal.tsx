// The repo pickers of Release by hand (plan 2026-11-04, decision 7). Destination ("Choose a
// destination repo"): the semester's eligible repos as radio rows, or a new name typed in, its
// refusal inline when the semester already uses that name for something else (config, `.github`,
// site, join, marks and student repos: `eligibleDestination`). Source ("Add a course repo"): the
// course repos the caller lists; picking one marks it as course materials (the caller sets the topic).

import { useState } from 'preact/hooks';
import { eligibleDestination, type Reserved } from '../model/releaseState';
import { Alert } from './icons';
import { MODAL_TEXT_ID, Modal } from './Modal';

/** One radio row: the repo's name and a short note ("the default", "2 releases", "no releases yet"). */
export interface RepoRow {
  name: string;
  note: string;
}

export type RepoPickerProps =
  | { kind: 'dest'; rows: RepoRow[]; current?: string; reserved: Reserved; onChoose: (name: string) => void; onClose: () => void }
  | { kind: 'source'; rows: RepoRow[]; onChoose: (name: string) => void; onClose: () => void };

export function RepoPickerModal(p: RepoPickerProps) {
  const dest = p.kind === 'dest';
  const first = dest && p.current && p.rows.some((r) => r.name === p.current) ? p.current : (p.rows[0]?.name ?? '');
  const [picked, setPicked] = useState(first);
  const [typed, setTyped] = useState('');
  const [why, setWhy] = useState<string | null>(null);
  const name = typed.trim();
  const choose = () => {
    if (dest && name) {
      const no = eligibleDestination(name, p.reserved);
      if (no) return setWhy(no);
    }
    const out = (dest && name) || picked;
    if (!out) return;
    p.onChoose(out);
    p.onClose();
  };
  return (
    <Modal title={dest ? 'Choose a destination repo' : 'Add a course repo'} onClose={p.onClose} small>
      <div class="picker">
        {p.rows.length ? (
          <div class="rp-list" role="radiogroup" aria-label={dest ? 'Repos in the semester' : 'Repos in the course'}>
            {p.rows.map((r) => (
              <label class="rp-row">
                <input type="radio" name="rp" value={r.name} checked={!name && picked === r.name} onChange={() => { setPicked(r.name); setTyped(''); setWhy(null); }} />
                <span class="rp-name">{r.name}</span>
                <span class="rp-note">{r.note}</span>
              </label>
            ))}
          </div>
        ) : <p class="footnote">{dest ? 'The semester has no other repo a release may write into.' : 'Every course repo is already listed.'}</p>}
        {dest ? (
          <div class="rp-new">
            <p class="sub-h">Or create a new repo</p>
            <div class="field">
              <label for="rp-new-name">Name of the new repo</label>
              <input type="text" id="rp-new-name" autocomplete="off" value={typed} onInput={(e) => { setTyped((e.target as HTMLInputElement).value); setWhy(null); }} />
              <p class="hint" id={MODAL_TEXT_ID}>Created private; students and auditors read.</p>
              {why ? <span class="invalid-msg"><Alert /><span>{why}</span></span> : null}
            </div>
          </div>
        ) : <p class="footnote" id={MODAL_TEXT_ID}>Picking one marks it as course materials.</p>}
        <div class="savebar">
          <button class="btn quiet" type="button" onClick={p.onClose}>Cancel</button>
          <button class="btn" type="button" disabled={!name && !picked} onClick={choose}>{dest ? 'Choose' : 'Add'}</button>
        </div>
      </div>
    </Modal>
  );
}
