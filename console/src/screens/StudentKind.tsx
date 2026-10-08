// A kind tab (decision 0035 rule 5), as the site's `kind.html` lists it: the rows the kind's
// tab names, dated by date then undated in the engine's order, each with its heading, details,
// reading list and files (on Readings only its reading files, with the pending note), then an
// event of the kind (an exam).

import { DEFAULT_TIMEZONE } from '../model/policy';
import { fmtDay } from '../model/format';
import { instant, type ScheduleRow, type SemesterFacts } from '../model/student';
import { Md } from '../ui/bits';
import { FileList } from './StudentFiles';
import { isEventRow } from './StudentHome';

/** The rows a kind's tab lists, and the kind's own events after them. */
export function kindRows(facts: SemesterFacts, kind: string): { rows: ScheduleRow[]; events: ScheduleRow[] } {
  const tz = facts.timezone || DEFAULT_TIMEZONE;
  const on = (facts.tabRows ?? facts.rows).filter((r) => r.tabs.includes(kind));
  const byDate = (a: ScheduleRow, b: ScheduleRow) => instant(a.when, tz) - instant(b.when, tz);
  const sessions = on.filter((r) => !isEventRow(r));
  return {
    rows: [...sessions.filter((r) => r.when).sort(byDate), ...sessions.filter((r) => !r.when)],
    events: on.filter((r) => isEventRow(r) && r.kind === kind && r.when).sort(byDate),
  };
}

export function KindView({ facts, kind, org, now }: { facts: SemesterFacts; kind: string; org: string; now: number }) {
  const tz = facts.timezone || DEFAULT_TIMEZONE;
  const year = new Date(now).getFullYear();
  const { rows, events } = kindRows(facts, kind);
  if (!rows.length && !events.length) return <p class="footnote">Nothing released yet.</p>;
  const readings = kind === 'readings';
  return (
    <div class="kind-tab">
      {rows.map((r) => {
        const files = readings && r.kind !== 'readings' ? r.readings : r.links;
        return (
          <section class="session" aria-label={r.title}>
            <h2 class="session-title"><b>{r.title}</b>{r.subtitle ? `: ${r.subtitle}` : ''}{r.released ? null : <> <span class="st-chip">not released yet</span></>}</h2>
            {r.details ? <Md class="session-description" src={r.details} /> : null}
            {r.readingList ? <Md class="reading-list" src={r.readingList} /> : null}
            {readings && r.readingsPending && r.released ? <p class="footnote"><em>Readings for this session are not yet released.</em></p> : null}
            {files.length ? <><p class="session-files-label"><b>Files</b></p><FileList org={org} repos={facts.materialsRepos} links={files} /></> : null}
          </section>
        );
      })}
      {events.map((e) => (
        <section class="session" aria-label={e.title}>
          <h2 class="session-title">{e.title}</h2>
          <p>{fmtDay(e.when, tz, year)}{e.tbc ? <> <b>(TBC)</b></> : null}</p>
          {e.details ? <Md src={e.details} /> : null}
        </section>
      ))}
    </div>
  );
}
