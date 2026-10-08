// The student's This week (decision 0011 rule 2): one line per thing due, handed out,
// released or happening. The course's own words are on Home (decision 0035 rule 4).

import { fmtDay, fmtTime } from '../model/format';
import type { WeekLine } from '../model/week';
import { studentHref } from '../router';

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
