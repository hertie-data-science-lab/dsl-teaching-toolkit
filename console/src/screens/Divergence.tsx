// The release detail's "Differs from the course copy" panel (decision 0043 rule 6): what the
// engine found, per copy and file, links to both copies on GitHub, and the remedies - Release
// again for the course side, Keep for future semesters for the released side, Set aside.

import type { ComponentChildren } from 'preact';
import { plural } from '../model/format';
import { deletedCount, fileCount, type DivergedProblem } from '../model/divergence';
import { ghUrl } from '../ui/bits';

/** What Release again needs from the divergence: one fact line per kind, and the count it may remove. */
export const releaseAgainDrift = (list: DivergedProblem[]) => ({ facts: list.map((p) => p.text), deleted: deletedCount(list) });

function Files({ p }: { p: DivergedProblem }) {
  const copies = p.diverged ?? [];
  return (
    <>
      {copies.map((c) => (
        <div class="diff" key={`${c.dest.repo}/${c.dest.path}`}>
          {copies.length > 1 ? <b>{c.dest.repo}/{c.dest.path}</b> : null}
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
  const copy = list.find((p) => p.diverged?.length)?.diverged?.[0];
  const released = list.some((p) => p.kind === 'DIVERGED_RELEASED');
  const course = list.some((p) => p.kind !== 'DIVERGED_RELEASED');
  const files = list.reduce((n, p) => n + (p.diverged ?? []).reduce((m, c) => m + fileCount(c), 0), 0);
  return (
    <section class="panel section divergence" style="margin-top:20px" aria-label="Differs from the course copy">
      <div class="section-head"><h2>Differs from the course copy</h2><span class="meta">{plural(files, 'file')}</span></div>
      {list.map((p) => (
        <div class="div-kind" key={p.id}>
          <h3 class="week-h">{p.label ?? p.kind}</h3>
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
