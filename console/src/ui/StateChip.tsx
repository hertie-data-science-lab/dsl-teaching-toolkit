// The Files panel's chips (plan 2026-11-04, decision 3): where a course file stands against the
// semester, in GitHub's fork words, and the names a release leaves behind. Behind, ahead and
// diverged are buttons with a popover (the `Hint` pattern): one sentence and one action, never a
// Fix button. The words' meanings, for a panel's `?`, are `ChipMeanings`. Decision B splits the
// institution's names in two: never released (no route ships one, its own chip) and the content
// names (withheld, like a `.releaseignore` line's row, until a `!` line releases one).

import { STATE_WORDS, type FileState as ReleaseState } from '../model/releaseState';
import { Hint } from './Hint';

export type ChipKind = Exclude<ReleaseState, 'unknown'> | 'withheld' | 'never_copied' | 'never_released';

/** The title on a never-released row, its chip and its disabled tick. */
export const NEVER_RELEASED_TITLE = 'Never released: on the institution’s list.';

const CHIPS: Record<ChipKind, { word: string; cls: string; title: string }> = {
  up_to_date: { word: STATE_WORDS.up_to_date, cls: 'muted', title: 'The semester has this version.' },
  behind: { word: STATE_WORDS.behind, cls: 'amber', title: 'The course copy changed since the release; a release updates the semester.' },
  ahead: { word: STATE_WORDS.ahead, cls: 'semester', title: 'Edited in the semester since the release; a release merges onto that edit.' },
  diverged: { word: STATE_WORDS.diverged, cls: 'bad', title: 'Changed in both the course and the semester; a release opens a pull request for the instructors.' },
  not_released: { word: STATE_WORDS.not_released, cls: '', title: 'Not in the semester yet; a release adds it.' },
  withheld: { word: 'withheld', cls: 'muted', title: 'A .releaseignore line, or the institution’s default, keeps it from students.' },
  never_copied: { word: 'never copied', cls: 'muted', title: 'Clutter such as .DS_Store: never course material.' },
  never_released: { word: 'never released', cls: 'muted', title: NEVER_RELEASED_TITLE },
};

export interface StateChipProps {
  kind: ChipKind;
  /** behind: ticks the row ("Tick it to release the course version."). */
  onTick?: () => void;
  /** ahead: opens Keep for future semesters (`release.propagate_back`). */
  onKeep?: () => void;
  /** diverged: the open pull request, when known. */
  prUrl?: string;
}

export function StateChip({ kind, onTick, onKeep, prUrl }: StateChipProps) {
  const c = CHIPS[kind];
  const cls = `chip${c.cls ? ` ${c.cls}` : ''}`;
  const label = `${c.word}: what a release does`;
  if (kind === 'behind') {
    return (
      <Hint label={label} trigger={{ text: c.word, cls }}>
        A release updates the semester to the course version.{' '}
        {onTick ? <button class="textlink" type="button" onClick={onTick}>Tick it to release the course version.</button> : null}
      </Hint>
    );
  }
  if (kind === 'ahead') {
    return (
      <Hint label={label} trigger={{ text: c.word, cls }}>
        A release merges the course version onto the semester’s edit.{' '}
        {onKeep ? <><button class="textlink" type="button" onClick={onKeep}>Keep for future semesters</button> <span class="footnote">carries the semester’s edit back to the course</span></> : null}
      </Hint>
    );
  }
  if (kind === 'diverged') {
    return (
      <Hint label={label} trigger={{ text: c.word, cls }}>
        A release opens a pull request for the instructors to resolve.{' '}
        {prUrl ? <a class="textlink" href={prUrl} target="_blank" rel="noopener">Open the pull request →</a> : null}
      </Hint>
    );
  }
  // `data-kind`: an up to date row greys its name (CSS).
  return <span class={cls} title={c.title} data-kind={kind}>{c.word}</span>;
}

/** Every chip word and what it means, one paragraph each, for a panel's `?`. */
export function ChipMeanings() {
  const order: ChipKind[] = ['up_to_date', 'behind', 'ahead', 'diverged', 'not_released'];
  return (
    <>
      {order.map((k) => <span class="para"><b>{CHIPS[k].word}</b>: {CHIPS[k].title}</span>)}
      <span class="para"><b>withheld</b>: a .releaseignore line keeps it from students (“withheld by” that line), or the institution’s default for solutions and tests (“withheld by default”): a ! line in .releaseignore releases those with their folder (“released by” that line), and a release that names one outright ships it.</span>
      <span class="para"><b>never released</b>: secrets, git metadata, grading files and .system/; no release carries one, even one that names it.</span>
      <span class="para"><b>never copied</b>: clutter such as .DS_Store, never course material.</span>
    </>
  );
}
