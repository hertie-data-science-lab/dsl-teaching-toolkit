// A semester's Home, the site's landing page (decision 0035 rule 4): the Updates box (the
// site's `announcements.html`: what has reached students, newest first, seven), the syllabus
// pinned, the course description, the instructors' own home text, previous offerings, and the
// instructors and teaching assistants in two columns.

import { DEFAULT_TIMEZONE } from '../model/policy';
import { instant, startOfDay, type Announcement, type ScheduleRow, type SemesterAssignment, type SemesterFacts } from '../model/student';
import { studentHref } from '../router';
import { Md } from '../ui/bits';
import { Ext } from '../ui/icons';
import { GhMd } from '../ui/rendered';
import { fileHref } from './StudentFiles';
import { PersonCard } from './StudentInstructors';

/** Display-only rows (the site's `_events`): never news, and on a kind tab only as the kind's own event. */
const EVENT_KINDS = ['assignment', 'due', 'exam', 'special_event', 'term_date'];
export const isEventRow = (r: ScheduleRow) => EVENT_KINDS.includes(r.kind);

/** The archive row's ids: the status file's, and an old site's. */
const ARCHIVE_ROWS = ['semester-archived', 'cohort-archived'];
/** How long before the archive the Updates box warns of it (site.py `ARCHIVE_NOTICE`). */
const ARCHIVE_NOTICE_DAYS = 14;
/** How many updates the box shows. */
const UPDATES_SHOWN = 7;

export type Update =
  | { at: number; row: ScheduleRow }
  | { at: number; assignment: SemesterAssignment }
  | { at: number; archive: string }
  | { at: number; news: Announcement };

/**
 * The Updates box's bullets, as the site's `announcements.html` picks them: sessions whose
 * files shipped (dated, on the schedule), assignments handed out, the archive row's text in the
 * fortnight before it, and the instructors' announcements; newest first, seven.
 */
export function homeUpdates(facts: SemesterFacts, now: number): Update[] {
  const tz = facts.timezone || DEFAULT_TIMEZONE;
  const out: Update[] = [];
  for (const row of facts.rows) if (!isEventRow(row) && row.released) out.push({ at: instant(row.when, tz), row });
  for (const a of facts.assignments) {
    const when = a.handout ?? a.due;
    if (a.handedOut && when) out.push({ at: instant(when, tz), assignment: a });
  }
  const archived = facts.rows.find((r) => ARCHIVE_ROWS.includes(r.id) && r.details);
  if (facts.archive && archived) {
    const at = instant(facts.archive, tz), today = startOfDay(now, tz);
    if (at >= today && at - today <= ARCHIVE_NOTICE_DAYS * 864e5) out.push({ at, archive: archived.details });
  }
  for (const n of facts.announcements) out.push({ at: instant(n.when, tz), news: n });
  return out.sort((a, b) => b.at - a.at).slice(0, UPDATES_SHOWN);
}

function Bullet({ u, facts, org }: { u: Update; facts: SemesterFacts; org: string }) {
  if ('row' in u) {
    const r = u.row;
    const noun = (facts.kinds?.[r.kind]?.label || r.kind).toLowerCase();
    return (
      <li>
        New {noun} {r.kind === 'readings' ? 'are' : 'is'} up: {r.title}
        {r.links.map((l) => {
          const h = fileHref(org, facts.materialsRepos, l);
          return <> [<a href={h.href} {...(h.ext ? { target: '_blank', rel: 'noopener' } : {})}>{l.name || 'file'}</a>]</>;
        })}
      </li>
    );
  }
  if ('assignment' in u) return <li>New Assignment released: [<a href={studentHref(org, `assignment-${u.assignment.slug}`)}>{u.assignment.title}</a>]</li>;
  if ('archive' in u) return <li><Md src={u.archive} /></li>;
  return <li>{u.news.title ? <b>{u.news.title}</b> : null}{u.news.details ? <Md src={u.news.details} /> : null}</li>;
}

export function HomeView({ facts, org, now }: { facts: SemesterFacts; org: string; now: number }) {
  const updates = homeUpdates(facts, now);
  const syl = facts.syllabus ? fileHref(org, facts.materialsRepos, facts.syllabus) : null;
  const instructors = facts.instructors.filter((c) => c.role === 'instructor');
  const assistants = facts.instructors.filter((c) => c.role === 'teaching_assistant');
  const description = facts.courseDescription ?? '';
  const previous = facts.previousOfferings ?? [];
  return (
    <div class="stack home">
      {updates.length ? (
        <section class="panel section updates" aria-labelledby="h-updates">
          <h2 id="h-updates">Updates</h2>
          <ul>{updates.map((u) => <Bullet u={u} facts={facts} org={org} />)}</ul>
        </section>
      ) : null}
      {syl ? <p class="syllabus-link"><a class="btn outline" href={syl.href} {...(syl.ext ? { target: '_blank', rel: 'noopener' } : {})}><b>Syllabus</b>{syl.ext ? <> <Ext /></> : null}</a></p> : null}
      {description ? (
        <section aria-labelledby="h-description">
          <h2 id="h-description">Course description</h2>
          <p>{description}</p>
        </section>
      ) : null}
      {facts.homeMarkdown ? <GhMd src={facts.homeMarkdown} context={`${org}/${org}.github.io`} /> : null}
      {previous.length ? (
        <section aria-labelledby="h-previous">
          <h2 id="h-previous">Previous offerings</h2>
          <ul>{previous.map((o) => <li>{o.url ? <a href={o.url} target="_blank" rel="noopener">{o.title}</a> : o.title}</li>)}</ul>
        </section>
      ) : null}
      {instructors.length || assistants.length ? (
        <div class="home-people">
          <section aria-labelledby="h-home-instructors">
            <h2 id="h-home-instructors">Instructors</h2>
            <ul class="people-grid">{instructors.map((c) => <PersonCard card={c} org={org} />)}</ul>
          </section>
          {assistants.length ? (
            <section aria-labelledby="h-home-tas">
              <h2 id="h-home-tas">Teaching assistants</h2>
              <ul class="people-grid">{assistants.map((c) => <PersonCard card={c} org={org} />)}</ul>
            </section>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
