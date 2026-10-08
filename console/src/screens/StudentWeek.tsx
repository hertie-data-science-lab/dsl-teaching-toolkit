// The student's This week (decision 0011 rule 2): one line per thing due, handed out,
// released or happening, and the course's About block.

import { fmtDay, fmtTime } from '../model/format';
import type { SemesterFacts } from '../model/student';
import type { WeekLine } from '../model/week';
import { studentHref } from '../router';
import { Md } from '../ui/bits';
import { GhMd } from '../ui/rendered';
import { Ext } from '../ui/icons';
import { fileHref } from './StudentFiles';

// --------------------------------------------------------------------------- This week

export function WeekList({ items, tz, org }: { items: WeekLine[]; tz: string; org: string }) {
  if (!items.length) return <p class="footnote">Nothing is due, handed out or released this week.</p>;
  return (
    <ul class="timeline week-list">
      {items.map((i) => (
        <li class={`trow ${i.cls}`}>
          <span class="k">{i.label}</span>
          <span class="d">{i.when ? fmtDay(i.when, i.tz ?? tz) : 'now'}{i.when && fmtTime(i.when, i.tz ?? tz) && !/T00:00(:00)?$/.test(i.when) ? <span>{fmtTime(i.when, i.tz ?? tz)}</span> : null}</span>
          <span class="ttl"><a href={studentHref(org, i.screen)}>{i.text}</a>{i.note ? <span class="w-note">; {i.note}</span> : null}</span>
          <span class="st" />
        </li>
      ))}
    </ul>
  );
}

// --------------------------------------------------------------------------- About

/** The course's own words on This week: its name, the syllabus pinned, the instructors' welcome text and their announcements. */
export function AboutView({ facts, org, tz, now }: { facts: SemesterFacts; org: string; tz: string; now: number }) {
  const { courseName, syllabus, homeMarkdown, announcements } = facts;
  if (!courseName && !syllabus && !homeMarkdown && !announcements.length) return null;
  const year = new Date(now).getFullYear();
  const syl = syllabus ? fileHref(org, facts.materialsRepos, syllabus) : null;
  return (
    <section class="panel section" aria-labelledby="h-about">
      <h2 id="h-about">{courseName ? `About ${courseName}` : 'About the course'}</h2>
      {syllabus && syl ? <p><a class="btn outline small" href={syl.href} {...(syl.ext ? { target: '_blank', rel: 'noopener' } : {})}>Syllabus{syl.ext ? <> <Ext /></> : null}</a></p> : null}
      {homeMarkdown ? <GhMd src={homeMarkdown} context={`${org}/${org}.github.io`} /> : null}
      {announcements.length ? (
        <div class="fb">
          <h3>Announcements</h3>
          <ul class="plain-list">
            {announcements.map((n) => <li><span class="footnote">{fmtDay(n.when, tz, year)}</span> {n.title ? <b>{n.title}</b> : null}{n.details ? <Md src={n.details} /> : null}</li>)}
          </ul>
        </div>
      ) : null}
    </section>
  );
}
