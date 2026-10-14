// The student's Instructors (decision 0011 rule 2): a card per instructor and teaching
// assistant, with their picture. Home shows the same cards in two columns (decision 0035 rule 4).

import type { InstructorCard, SemesterFacts } from '../model/student';
import { Avatar } from '../ui/Avatar';

/** One instructor's card: picture, name (linked to their page), role and title, email where they show it. */
export function PersonCard({ card: c, org }: { card: InstructorCard; org: string }) {
  return (
    <li class="person">
      <Avatar picture={c.picture} name={c.name} org={org} />
      <div>
        <b>{c.webpage ? <a href={c.webpage} target="_blank" rel="noopener">{c.name}</a> : c.name}</b>
        <div class="footnote">{c.role === 'instructor' ? 'Instructor' : 'Teaching assistant'}{c.title ? `; ${c.title}` : ''}</div>
        {c.email ? <div class="footnote"><a href={`mailto:${c.email}`}>{c.email}</a></div> : null}
      </div>
    </li>
  );
}

export function InstructorsView({ facts, org = '' }: { facts: SemesterFacts; org?: string }) {
  if (!facts.instructors.length) return <p class="footnote">No instructors are listed yet.</p>;
  return <ul class="people-grid">{facts.instructors.map((c) => <PersonCard card={c} org={org} />)}</ul>;
}
