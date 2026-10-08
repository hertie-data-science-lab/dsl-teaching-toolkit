// The student's Instructors (decision 0011 rule 2): a card per instructor and teaching
// assistant, with their picture. Home shows the same cards in two columns (decision 0035 rule 4).

import { useEnv } from '../env';
import { IMG_HOSTS, type InstructorCard, type SemesterFacts } from '../model/student';
import { useLoad } from '../ui/load';
import { studentData } from './Student';

// --------------------------------------------------------------------------- Instructors

const initials = (name: string) => {
  const n = name.replace(/^(Prof\.|Dr\.)\s+/g, '').split(/[\s,]+/).filter((w) => /^[A-Z]/.test(w));
  return (n.length > 1 ? n[0][0] + n[n.length - 1][0] : (n[0] ?? name).slice(0, 2)).toUpperCase();
};

/** A card's picture: a GitHub avatar straight, else whatever `StudentData` can show (a site-hosted one as data:), else initials. */
function CardPicture({ card, org }: { card: InstructorCard; org: string }) {
  const env = useEnv();
  const direct = IMG_HOSTS.test(card.picture);
  const load = useLoad(env && card.picture && !direct ? () => studentData(env.client).picture(org, card.picture) : null, [card.picture]);
  const src = direct ? card.picture : load.kind === 'ready' ? load.value : '';
  return <span class="p-avatar" aria-hidden="true">{src ? <img src={src} alt="" /> : initials(card.name)}</span>;
}

/** One instructor's card: picture, name (linked to their page), role and title, email where they show it. */
export function PersonCard({ card: c, org }: { card: InstructorCard; org: string }) {
  return (
    <li class="person">
      <CardPicture card={c} org={org} />
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
