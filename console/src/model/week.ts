// This week for a student: what is due, handed out, released or happening from the start of
// today to seven days on (the engine's own window, status_json.this_week), and what came
// back in the last seven days (materials released, marks returned, announcements), plus every
// team formation still open and every file the instructors updated in the student's repo
// since their last visit. One line each; Home merges the lines of every semester shown, each
// line in its own semester's timezone. An auditor gets no marks or team lines.

import { isMarked, type Mine } from './mine';
import { DEFAULT_TZ, instant, startOfDay, type SemesterFacts } from './student';

export type WeekKind = 'due' | 'hand_out' | 'release' | 'exam' | 'event' | 'marks' | 'teams' | 'news' | 'patch';

/** A note on a Submission receipts thread that the instructors updated files in the student's repo. */
export interface PatchLine {
  slug: string;
  when: string;
}

export interface WeekItem {
  at: number;
  /** The row's own time, as the schedule writes it (for the date shown). */
  when: string;
  kind: WeekKind;
  /** The chip: "due", "lecture", "lab", "exam"... and its colour class (the site's palette). */
  label: string;
  cls: string;
  text: string;
  /** The student screen the line opens. */
  screen: string;
  /** A second, quieter clause: "you have no team yet". */
  note?: string;
  /** The semester's timezone, for the date shown. */
  tz?: string;
}

const DAY = 864e5;

const name = (title: string, subtitle: string) => (subtitle ? `${title}: ${subtitle}` : title);

/**
 * `patches` are the student's patch notes; one shows when it is newer than `lastVisit` (the
 * start of the previous visit), or from the last seven days when there was none.
 */
export function weekItems(facts: SemesterFacts, mine: Mine | null, now: number, patches: PatchLine[] = [], lastVisit: number | null = null): WeekItem[] {
  const tz = facts.timezone || DEFAULT_TZ;
  const start = startOfDay(now, tz);
  const end = start + 7 * DAY;
  const recent = start - 7 * DAY;
  const out: WeekItem[] = [];
  const add = (i: Omit<WeekItem, 'label' | 'cls'> & Partial<Pick<WeekItem, 'label' | 'cls'>>) =>
    out.push({ label: WORD[i.kind], cls: CLASS[i.kind], tz, ...i });
  const auditor = mine?.auditor === true;
  for (const r of facts.rows) {
    const at = instant(r.when, tz);
    const ahead = at >= start && at < end;
    const what = name(r.title, r.subtitle);
    const session = r.kind === 'lecture' || r.kind === 'lab';
    const shipped = r.released && r.links.length > 0;
    const look = { label: r.kind, cls: r.kind === 'lab' ? 'lab' : 'lec' };
    if (r.kind === 'due' && ahead) add({ at, when: r.when, kind: 'due', text: `${what} is due`, screen: 'assignments' });
    else if (r.kind === 'assignment' && at >= recent && at < end) add({ at, when: r.when, kind: 'hand_out', text: at <= now ? `${what} was handed out` : `${what} is handed out`, screen: 'assignments' });
    else if (session && ahead) add({ at, when: r.when, kind: 'release', ...look, text: shipped ? `${what}: materials released` : what, screen: shipped ? 'materials' : 'schedule' });
    else if (session && shipped && at >= recent && at < start) add({ at, when: r.when, kind: 'release', ...look, text: `${what}: materials released`, screen: 'materials' });
    else if (r.kind === 'exam' && ahead) add({ at, when: r.when, kind: 'exam', text: what, screen: 'schedule' });
    else if ((r.kind === 'special_event' || r.kind === 'term_date') && ahead) add({ at, when: r.when, kind: 'event', text: what, screen: 'schedule', ...(r.kind === 'term_date' ? { cls: 'term', label: 'term date' } : {}) });
  }
  for (const n of facts.announcements) {
    const at = instant(n.when, tz);
    if (at >= recent && at < end) add({ at, when: n.when, kind: 'news', text: n.title || 'Announcement', screen: 'week' });
  }
  const titles = new Map(facts.assignments.map((a) => [a.slug, a.title]));
  const since = lastVisit ?? recent;
  for (const p of auditor ? [] : patches) {
    const at = Date.parse(p.when);
    if (at > since && at <= now) add({ at, when: p.when, kind: 'patch', text: `Your instructors updated files in your ${titles.get(p.slug) ?? p.slug} repo: pull before you continue`, screen: 'assignments' });
  }
  const updated = auditor ? null : mine?.gradebook?.updated;
  if (updated) {
    const at = Date.parse(updated);
    const marked = facts.assignments.filter((a) => isMarked(mine!.gradebook, a.slug));
    if (at >= recent && at <= now && marked.length) {
      add({ at, when: updated, kind: 'marks', text: `Marks returned (${marked.map((a) => a.title).join(', ')})`, screen: 'marks' });
    }
  }
  for (const a of facts.assignments) {
    if (!a.teamFormation || auditor) continue;
    const u = mine?.units[a.slug];
    add({
      at: now,
      when: '',
      kind: 'teams',
      text: `Team formation is open for ${a.title}${a.teamFormation.closes ? ` until ${a.teamFormation.closes}` : ''}`,
      screen: 'join',
      note: mine && !u?.team ? 'you have no team yet' : undefined,
    });
  }
  return out.sort((x, y) => x.at - y.at);
}

const WORD: Record<WeekKind, string> = {
  due: 'due', hand_out: 'hand out', release: 'session', exam: 'exam', event: 'event', marks: 'marks', teams: 'teams', news: 'news', patch: 'update',
};
const CLASS: Record<WeekKind, string> = {
  due: 'asg', hand_out: 'asg', release: 'lec', exam: 'exam', event: 'evt', marks: 'asg', teams: 'term', news: 'evt', patch: 'asg',
};
