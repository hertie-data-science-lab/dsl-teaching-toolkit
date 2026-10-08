// The student's Assignments tab (decision 0035 rules 6-8): "Your marks" first once any mark is
// returned, then every assignment as a card in due order, open while the student can hand it
// in (open or in its late window) and folded otherwise. A card's first line links the assignment's
// own page (`StudentAssignment.tsx`), and its body is that page's. Marks and team joining live
// here: there is no Marks or Join screen. Each assignment states its own late rule; there is no
// course-wide late-work section.

import { DEFAULT_TIMEZONE } from '../model/policy';
import { fmtWhen } from '../model/format';
import { gradebookUrl, isMarked, type Gradebook, type Mine, type Receipts } from '../model/mine';
import { instant, myState, type SemesterFacts } from '../model/student';
import { Ext } from '../ui/icons';
import { AssignmentBody, MarkBody, StateChip, assignmentFiles, assignmentHref } from './StudentAssignment';

export function AssignmentsView({ org, facts, mine, now, studentView, receipts, login = '', unknownRole = false }: {
  org: string; facts: SemesterFacts; mine: Mine | null; now: number; studentView: boolean;
  /** The person's role could not be read: promise them nothing. */
  unknownRole?: boolean;
  /** Receipts per repo; undefined while they are read. */
  receipts?: Record<string, Receipts | null>;
  login?: string;
}) {
  const tz = facts.timezone || DEFAULT_TIMEZONE;
  const year = new Date(now).getFullYear();
  if (!facts.assignments.length) return <p class="footnote">No assignments are planned yet.</p>;
  const list = [...facts.assignments].sort((a, b) => (a.due ? instant(a.due, tz) : Infinity) - (b.due ? instant(b.due, tz) : Infinity));
  const auditor = mine?.auditor === true || unknownRole;
  return (
    <div class="stack">
      {studentView ? <p class="footnote">A student’s marks show here, from their private gradebook; your own account has none in this semester.</p>
        : auditor ? null : <MarksTable org={org} login={login} facts={facts} gradebook={mine?.gradebook ?? null} />}
      {list.map((a) => {
        const st = myState(a, isMarked(mine?.gradebook ?? null, a.slug), now, tz);
        const open = st === 'open' || st === 'late_window';
        return (
          <details class="panel section a-card" open={open} aria-label={a.title}>
            <summary class="a-sum">
              <h2>{a.title}{a.subtitle ? <span>{a.subtitle}</span> : null}</h2>
              {auditor ? null : <StateChip st={st} entry={studentView ? undefined : mine?.gradebook?.entries[a.slug]} />}
              {a.due ? <span class="a-due">Due {fmtWhen(a.due, tz, year)}{a.tbc ? ' (TBC)' : ''}</span> : null}
            </summary>
            <p class="a-open"><a href={assignmentHref(org, a.slug)}>Open the page</a></p>
            <AssignmentBody org={org} a={a} mine={mine} now={now} tz={tz} studentView={studentView} unknownRole={unknownRole} receipts={receipts} login={login} files={assignmentFiles(facts, a.slug)} repos={facts.materialsRepos} />
          </details>
        );
      })}
    </div>
  );
}

const markTitles = (facts: SemesterFacts) => new Map(facts.assignments.map((a) => [a.slug, a.subtitle ? `${a.title}: ${a.subtitle}` : a.title]));

/** "Your marks" (rule 7): one row per returned mark, the semester total and the gradebook; nothing until a mark is returned. */
export function MarksTable({ org, login, facts, gradebook }: { org: string; login: string; facts: SemesterFacts; gradebook: Gradebook | null }) {
  const slugs = Object.keys(gradebook?.entries ?? {}).filter((s) => isMarked(gradebook, s));
  if (!gradebook || !slugs.length) return null;
  const titles = markTitles(facts);
  const known = new Set(facts.assignments.map((a) => a.slug));
  return (
    <section class="panel section your-marks" aria-labelledby="h-your-marks">
      <h2 id="h-your-marks">Your marks</h2>
      <div class="table-wrap">
        <table class="grid">
          <thead><tr><th>Assignment</th><th>Mark</th><th>Late penalty</th></tr></thead>
          <tbody>
            {slugs.map((s) => {
              const e = gradebook.entries[s];
              return (
                <tr>
                  <td>{known.has(s) ? <a href={assignmentHref(org, s)}>{titles.get(s)}</a> : s}</td>
                  <td>{e.finalGrade}{e.maxPoints ? ` / ${e.maxPoints}` : ''}</td>
                  <td>{e.penalty}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {gradebook.total ? <p><b>Semester total:</b> {gradebook.total}</p> : null}
      <p class="footnote"><a href={gradebookUrl(org, login)} target="_blank" rel="noopener">Your gradebook on GitHub <Ext /></a>: private to you.</p>
    </section>
  );
}

/** An archived semester's marks: the table, then each returned mark's feedback. */
export function YourMarks({ org, login, facts, gradebook, auditor = false }: { org: string; login: string; facts: SemesterFacts; gradebook: Gradebook | null; auditor?: boolean }) {
  const slugs = Object.keys(gradebook?.entries ?? {}).filter((s) => isMarked(gradebook, s));
  if (!gradebook || !slugs.length) {
    return (
      <section class="panel section" aria-labelledby="h-your-marks">
        <h2 id="h-your-marks">Your marks</h2>
        <p class="footnote">{auditor ? 'As an auditor you got no marks in this semester.' : 'No marks were returned to you in this semester.'}</p>
      </section>
    );
  }
  const titles = markTitles(facts);
  return (
    <div class="stack">
      <MarksTable org={org} login={login} facts={facts} gradebook={gradebook} />
      {slugs.map((s) => {
        const e = gradebook.entries[s];
        return (
          <section class="panel section" aria-label={titles.get(s) ?? s}>
            <div class="a-head"><h2>{titles.get(s) ?? s}</h2><span class="mark-big">{e.finalGrade}{e.maxPoints ? <span> / {e.maxPoints}</span> : null}</span></div>
            <MarkBody e={e} />
          </section>
        );
      })}
    </div>
  );
}
