// The release detail's "Differs from the course copy" panel (decision 0043 rule 6): what the
// engine found, per copy and file, links to both copies on GitHub, and the remedies - Release
// again for the course side, Keep for future semesters for the released side, Set aside.

import type { ComponentChildren } from 'preact';
import { plural } from '../model/format';
import { deletedCount, type DivergedKind, type DivergedProblem } from '../model/divergence';
import { ghUrl } from '../ui/bits';

/** One line per kind, for the Release again panel's facts. */
export function divergenceFacts(list: DivergedProblem[]): string[] {
  return list.map((p) => p.text);
}

/** What Release again needs from the divergence: its facts and the count it may remove. */
export const releaseAgainDrift = (list: DivergedProblem[]) => ({ facts: divergenceFacts(list), deleted: deletedCount(list) });

const HEAD: Record<DivergedKind, string> = {
  DIVERGED_COURSE: 'Changed in the course since it was released',
  DIVERGED_DELETED: 'Deleted from the course copy, still released',
  DIVERGED_RELEASED: 'Edited in the released copy',
};

function Files({ p }: { p: DivergedProblem }) {
  return (
    <>
      {(p.diverged ?? []).map((c) => (
        <div class="diff" key={`${c.dest.repo}/${c.dest.path}`}>
          {(p.diverged ?? []).length > 1 ? <b>{c.dest.repo}/{c.dest.path}</b> : null}
          {c.added.map((f) => <span class="plus" key={`+${f}`}>+ {f}</span>)}
          {c.changed.map((f) => <span class="chg" key={`~${f}`}>~ {f}</span>)}
          {c.deleted.map((f) => <span class="chg" key={`-${f}`}>− {f}</span>)}
        </div>
      ))}
    </>
  );
}

/**
 * The panel, when anything differs. `courseOrg` / `cohortOrg` place the links (GitHub cannot
 * compare across two orgs, so the panel links each copy's folder at the branch compared);
 * `onSetAside` is absent for a viewer who cannot write the course; `remedies` are the
 * screen's own op buttons, by side.
 */
export function DivergencePanel({ list, courseOrg, cohortOrg, onSetAside, again, keep }: {
  list: DivergedProblem[];
  courseOrg: string;
  cohortOrg: string;
  onSetAside?: (p: DivergedProblem) => void;
  again: ComponentChildren;
  keep: ComponentChildren;
}) {
  if (!list.length) return null;
  const copy = list.flatMap((p) => p.diverged ?? [])[0];
  const released = list.some((p) => p.kind === 'DIVERGED_RELEASED');
  const course = list.some((p) => p.kind !== 'DIVERGED_RELEASED');
  const files = list.reduce((n, p) => n + (p.diverged ?? []).reduce((m, c) => m + c.added.length + c.changed.length + c.deleted.length, 0), 0);
  return (
    <section class="panel section divergence" style="margin-top:20px" aria-label="Differs from the course copy">
      <div class="section-head"><h2>Differs from the course copy</h2><span class="meta">{plural(files, 'file')}</span></div>
      {list.map((p) => (
        <div class="div-kind" key={p.id}>
          <h3 class="week-h">{HEAD[p.kind]}</h3>
          <Files p={p} />
          {onSetAside ? <button class="textlink" type="button" onClick={() => onSetAside(p)}>Set aside</button> : null}
        </div>
      ))}
      {copy ? (
        <p class="footnote">
          Compare the <a href={ghUrl(courseOrg, copy.source.repo, copy.source.path, copy.source.branch ?? 'main', 'tree')} target="_blank" rel="noopener">course copy</a> with the{' '}
          <a href={ghUrl(cohortOrg, copy.dest.repo, copy.dest.path, copy.dest.branch ?? 'main', 'tree')} target="_blank" rel="noopener">released copy</a> on GitHub.
        </p>
      ) : null}
      <div class="actions">
        {course ? again : null}
        {released ? keep : null}
      </div>
    </section>
  );
}
