// The student's Schedule (decision 0011 rule 2): every row of the semester by week, the
// student's own state on assignment rows, the released files and readings, and the archive
// notice.

import { DEFAULT_TIMEZONE } from '../model/policy';
import { dayKey, fmtDay, fmtTime, sortKey } from '../model/format';
import { isMarked, type Mine } from '../model/mine';
import { weekGroups } from '../model/schedule';
import { MY_STATE_WORD, instant, myState, sortedRows, type ScheduleRow, type SemesterFacts } from '../model/student';
import { ROW_CLASS, ROW_WORD, termOfFacts } from '../model/week';
import { studentHref } from '../router';
import { Md } from '../ui/bits';
import { FileChips } from './StudentFiles';

export function ArchiveNotice({ when, tz, now }: { when: string; tz: string; now: number }) {
  const past = instant(when, tz) <= now;
  return (
    <div class="note" role="note">
      <b>Archive{past ? 'd' : ''}.</b>{' '}
      {past
        ? <>This semester was archived on {fmtDay(when, tz)}: its repositories are read-only.</>
        : <>This semester is archived on {fmtDay(when, tz, new Date(now).getFullYear())}: every repository in it becomes read-only. You keep read access, so you can fork or clone anything you want to keep working on into your own account.</>}
    </div>
  );
}

// --------------------------------------------------------------------------- Schedule

/** Monday of the week `iso` falls in, as yyyy-mm-dd. */
function mondayOf(iso: string, tz: string): string {
  const day = dayKey(iso, tz);
  const [y, m, d] = day.split('-').map(Number);
  const dow = (new Date(Date.UTC(y, m - 1, d)).getUTCDay() + 6) % 7;
  return new Date(Date.UTC(y, m - 1, d - dow)).toISOString().slice(0, 10);
}

/**
 * The rows by week. With the semester's dates, by semester week as the instructor's Schedule
 * groups them (`schedule.weekGroups`: week 1 from `start`, a bucket before and after); without
 * them (an older source), by calendar week, numbered by place.
 */
function rowWeeks(rows: ScheduleRow[], facts: SemesterFacts, tz: string): { label: string; from?: string; rows: ScheduleRow[] }[] {
  const term = termOfFacts(facts);
  if (term) return weekGroups(rows, (r) => r.when, term, tz, 'all').filter((g) => g.rows.length);
  const byMonday = new Map<string, ScheduleRow[]>();
  for (const r of rows) {
    const k = mondayOf(r.when, tz);
    byMonday.set(k, [...(byMonday.get(k) ?? []), r]);
  }
  return [...byMonday].map(([from, list], i) => ({ label: `Week ${i + 1}`, from, rows: list }));
}

export function ScheduleView({ facts, mine, now, org }: { facts: SemesterFacts; mine: Mine | null; now: number; org: string }) {
  const tz = facts.timezone || DEFAULT_TIMEZONE;
  const rows = sortedRows(facts.rows, tz);
  if (!rows.length) return <p class="footnote">The schedule has no entries yet.</p>;
  const year = new Date(now).getFullYear();
  const byAssignment = new Map(facts.assignments.map((a) => [a.slug, a]));
  const nowKey = sortKey(new Date(now).toISOString(), tz);
  let todayDone = false;
  return (
    <div class="stack">
      {rowWeeks(rows, facts, tz).map((g) => (
        <section aria-label={g.label}>
          <h2 class="week-h">{g.label}{g.from ? <> <span>from {fmtDay(g.from, tz, year)}</span></> : null}</h2>
          <ul class="timeline">
            {g.rows.flatMap((r) => {
              const out = [];
              if (!todayDone && sortKey(r.when, tz) >= nowKey) {
                todayDone = true;
                out.push(<li class="today-line">Today, {fmtDay(new Date(now).toISOString(), tz, year)}</li>);
              }
              const a = r.assignment ? byAssignment.get(r.assignment) : undefined;
              const st = a && !mine?.auditor ? myState(a, isMarked(mine?.gradebook ?? null, a.slug), now, tz) : null;
              const yours = a && mine ? mine.units[a.slug] : undefined;
              const files = r.links.filter((l) => !r.readings.includes(l));
              // A kind the console has no class for (a policy kind: readings, drop-in) takes its policy colours.
              const k = ROW_CLASS[r.kind] ? undefined : facts.kinds?.[r.kind];
              out.push(
                <li class={`trow ${ROW_CLASS[r.kind] ?? 'evt'}${yours?.repo ? ' mine' : ''}`} style={k?.background ? { background: k.background } : undefined}>
                  <span class="k" style={k?.colour ? { color: k.colour } : undefined}>{ROW_WORD[r.kind] ?? k?.label.toLowerCase() ?? r.kind}</span>
                  <span class="d">{fmtDay(r.when, tz, year)}{!r.allDay && fmtTime(r.when, tz) ? <span>{fmtTime(r.when, tz)}</span> : null}{r.tbc ? <span class="tbc">TBC</span> : null}</span>
                  <span class="ttl">
                    <b>{r.title}</b>{r.subtitle ? `: ${r.subtitle}` : ''}
                    {r.details ? <Md class="t-details" src={r.details} /> : null}
                    {r.readings.length || r.readingList ? (
                      <span class="t-readings">Readings: <FileChips org={org} repos={facts.materialsRepos} links={r.readings} />{r.readingList ? <a class="st-chip" href={studentHref(org, 'materials')}>reading list</a> : null}</span>
                    ) : r.readingsPending ? <span class="t-readings footnote">Readings to come.</span> : null}
                  </span>
                  <span class="st">
                    {st ? <a class="st-chip" href={studentHref(org, 'assignments')}>{MY_STATE_WORD[st]}</a> : null}
                    {yours?.repo ? <span class="st-chip">yours</span> : null}
                    {files.length ? <FileChips org={org} repos={facts.materialsRepos} links={files} /> : !r.released ? <span class="st-chip">not released yet</span> : null}
                  </span>
                </li>,
              );
              return out;
            })}
          </ul>
        </section>
      ))}
    </div>
  );
}
