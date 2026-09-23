// Every dispatch operation as the panel offers it, with the mockup's copy. A screen builds
// the def for the thing on it (a release, an assignment, the cohort) and hands it to the
// panel; the op name and args follow the registry (schemas/ops.json).

import { HANDOUT, RETURN_MARKS, RETURN_MARKS_ALWAYS, publishWebsite as publishTiers, releaseAdhoc as adhocTiers, releaseDest, updateCopies as copiesTiers } from '../tiers/ops';
import type { OpDef } from './session';

export interface Scope {
  courseOrg: string;
  cohortOrg?: string;
  /** The eyebrow's second half: the term ("Fall 2026") or the course name. */
  where: string;
}

const base = (s: Scope, op: string, key: string) => ({ op, key, courseOrg: s.courseOrg, cohortOrg: s.cohortOrg });

export function checkNow(s: Scope): OpDef {
  return {
    ...base(s, 'cohort.check', 'cohort'), name: 'Check now', title: 'Every check', where: s.where,
    intro: 'Re-reads every file and re-runs every check now instead of at the next automatic check.',
    verb: 'Check now', running: 'Checking everything', cancel: 'Stop', args: {},
  };
}

export function previewNext(s: Scope): OpDef {
  return {
    ...base(s, 'cohort.preview_automation', 'cohort'), name: 'Preview the next automatic run', title: 'The next automatic run', where: s.where,
    intro: 'Shows what automation would release, hand out or collect at its next check, and why anything would not happen.',
    verb: 'Preview the next run', running: 'Previewing the next run', cancel: 'Stop', args: {},
  };
}

export function scheduledPreview(s: Scope): OpDef {
  return {
    ...previewNext(s), name: 'Preview scheduled releases', title: 'The next scheduled release run',
    intro: 'Runs automation’s release check without releasing anything.', verb: 'Preview', running: 'Previewing scheduled releases',
  };
}

export function keepFuture(s: Scope): OpDef {
  return {
    ...base(s, 'release.propagate_back', 'cohort'), name: 'Keep cohort edits for future terms', title: `Every edit made in ${s.where}`, where: `${s.where} to the course`,
    intro: 'Proposes the edits made to the cohort’s copies back to the course materials, as changes for you to accept on GitHub.',
    verb: 'Propose the changes', running: 'Proposing changes', cancel: 'Stop', args: {},
  };
}

export interface ReleaseRef {
  id: string;
  ident: string; // "Session 5"
  title: string;
  when: string; // "Thu 8 Oct 10:00"
  source: { repo: string; path: string };
}

function releaseDef(s: Scope, op: string, r: ReleaseRef, copy: Pick<OpDef, 'name' | 'intro' | 'verb' | 'running' | 'cancel' | 'where'>): OpDef {
  return { ...base(s, op, r.id), ...copy, title: `${r.ident}: ${r.title}`, args: { entry: r.id }, options: releaseDest(r.source.path), previewProposed: false };
}

export function releaseEarly(s: Scope, r: ReleaseRef): OpDef {
  return releaseDef(s, 'release.early', r, {
    name: 'Release early', where: `Scheduled ${r.when}`, intro: `Copies ${r.ident} to students now instead of at its time. The scheduled release then finds nothing left to do.`,
    verb: `Release ${r.ident} early`, running: `Releasing ${r.ident} early`, cancel: 'Stop before copying',
  });
}

export function releaseAgain(s: Scope, r: ReleaseRef): OpDef {
  return releaseDef(s, 'release.rerun', r, {
    name: 'Release again', where: `Released ${r.when}`, intro: `Copies the course’s current version of ${r.ident} to students again, for a fixed file.`,
    verb: `Release ${r.ident} again`, running: `Releasing ${r.ident} again`, cancel: 'Stop before copying',
  });
}

export function releaseNow(s: Scope, r: ReleaseRef): OpDef {
  return releaseDef(s, 'release.now', r, {
    name: 'Release now', where: `Due ${r.when}`, intro: `${r.ident} is late: copies it to students now.`,
    verb: `Release ${r.ident} now`, running: `Releasing ${r.ident}`, cancel: 'Stop before copying',
  });
}

export function releaseAdhoc(s: Scope, repos: string[]): OpDef {
  return {
    ...base(s, 'release.adhoc', 'adhoc'), name: 'Release something unscheduled', title: 'A folder not in the schedule', where: s.where,
    intro: 'Copies one or more folders to students now. Nothing is added to the schedule.',
    verb: 'Release folders', running: 'Releasing the folders', cancel: 'Stop before copying',
    args: { course_source_repo: repos[0] ?? '' }, options: adhocTiers(repos),
  };
}

export interface AsgRef {
  slug: string;
  title: string; // "Assignment 2: Regression"
  template: string;
  units: number;
  group: boolean;
  when: string; // for the eyebrow
}

export function handout(s: Scope, a: AsgRef): OpDef {
  return {
    ...base(s, 'assignment.handout_now', a.slug), name: 'Hand out', title: a.title, where: a.when,
    intro: 'Creates each student’s or team’s repo now instead of at the scheduled time.',
    verb: `Hand out to ${a.units} ${a.group ? 'teams' : 'students'}`, running: 'Handing out', cancel: 'Stop after the current repo; repos already made stay',
    args: { course_source_repo: a.template }, options: HANDOUT,
  };
}

export function updateCopies(s: Scope, a: AsgRef, files: string[]): OpDef {
  return {
    ...base(s, 'assignment.update_copies', a.slug), name: 'Update every copy', title: a.title, where: `${a.units} student repos`,
    intro: 'Pushes a template file to every student copy and posts a note on each receipts thread.',
    verb: `Update ${a.units} copies`, running: 'Updating every copy', cancel: 'Stop; copies already updated stay updated',
    args: { course_source_repo: a.template, slug: a.slug }, options: copiesTiers(files),
  };
}

export function collect(s: Scope, a: AsgRef): OpDef {
  return {
    ...base(s, 'assignment.collect_now', a.slug), name: 'Collect now', title: a.title, where: a.when,
    intro: 'Pulls the latest work from every repo into the mark sheet now.',
    verb: 'Collect now', running: 'Collecting submissions', cancel: 'Stop; the mark sheet keeps what it has',
    args: { course_source_repo: a.template, slug: a.slug },
  };
}

export function returnMarks(s: Scope, a: AsgRef, marked: number): OpDef {
  return {
    ...base(s, 'grades.return', a.slug), name: 'Return marks', title: a.title, where: 'Marking',
    intro: 'Sends each student their marks and feedback. Your private notes stay private.',
    verb: `Return marks to ${marked} ${a.group ? 'teams' : 'students'}`, running: 'Returning marks', cancel: 'Stop; marks already returned stay returned',
    args: {}, options: RETURN_MARKS, fixed: RETURN_MARKS_ALWAYS,
  };
}

export function sendCodes(s: Scope, waiting: number): OpDef {
  return {
    ...base(s, 'roster.send_codes', 'roster'), name: 'Send new codes', title: 'Students who have not joined', where: s.where,
    intro: 'Sends a fresh code to each student who has a code but has not joined.', confirm: 'Their old codes stop working.',
    verb: `Send ${waiting} new code${waiting === 1 ? '' : 's'}`, running: 'Sending new codes', cancel: 'Stop sending; codes already sent stay valid',
    args: {}, fixed: [{ label: `Everyone with a code who has not joined (${waiting})`, note: 'default' }],
  };
}

export function updateSite(s: Scope): OpDef {
  return {
    ...base(s, 'site.update', 'site'), name: 'Update site', title: 'Student site', where: s.where,
    intro: 'Rebuilds the student site from the schedule, staff and materials. Automation does this after every change; do it by hand after a failure.',
    verb: 'Update site', running: 'Updating the student site', cancel: 'Stop; the site keeps its current version', args: {},
  };
}

export function checkAccess(s: Scope): OpDef {
  return {
    ...base(s, 'access.check', 'access'), name: 'Check staff access', title: 'Staff access', where: s.where,
    intro: 'Makes GitHub match the staff list. It never removes access.',
    verb: 'Check staff access', running: 'Checking staff access', cancel: 'Stop', args: {},
  };
}

export function archive(s: Scope, scheduled: string | null, passed: boolean): OpDef {
  return {
    ...base(s, 'cohort.archive', 'cohort'), name: 'Archive', title: s.where, where: scheduled ? `Scheduled ${scheduled}` : 'Not scheduled',
    intro: 'Every repo becomes read-only. Students keep access. Nothing is deleted.',
    verb: `Archive ${s.where}`, running: `Archiving ${s.where}`, cancel: 'Stop; repos already archived stay archived', args: {},
    needsCheck: passed ? undefined : { label: 'Archive before the scheduled date', sub: scheduled ? `Needed because ${scheduled} has not passed.` : 'Needed because no archive date has passed.', arg: 'force' },
  };
}

export function publishWebsite(s: Scope, repos: string[], values: { source_repo?: string; readings_mode?: string; include_lectures?: boolean }, published: boolean): OpDef {
  return {
    ...base(s, 'course.publish_website', 'website'), name: 'Publish public website', title: `${s.courseOrg}.github.io`, where: s.where,
    intro: 'Publishes an open website from your materials and updates it daily. Withheld folders stay private.',
    verb: published ? 'Publish again' : 'Publish public website', running: 'Publishing the public website', cancel: 'Stop; nothing is public until the last step',
    args: { source_repo: values.source_repo ?? repos[0] ?? '', readings_mode: values.readings_mode ?? 'reading-list', include_lectures: values.include_lectures ?? true },
    options: publishTiers(repos),
    needsCheck: { label: 'This replaces the live public site', sub: 'The engine has no preview for publishing, so confirm instead.' },
  };
}

export function teamsWindow(s: Scope, a: AsgRef, closes: string): OpDef {
  return {
    ...base(s, 'teams.open_window', a.slug), name: 'Email students without a team', title: a.title, where: `Window open until ${closes}`,
    intro: 'Emails every joined student who is not in a team yet, with the link to the team list on the student site.',
    verb: 'Send the emails', running: 'Emailing students without a team', cancel: 'Stop; emails already sent stay sent',
    args: { assignment: a.slug },
  };
}

export function derive(s: Scope, slug: string, repo: string, title: string): OpDef {
  return {
    ...base(s, 'assignment.derive_starter', slug), name: 'Derive student version', title, where: 'Template',
    intro: 'Builds the student starter on main from the solution branch by removing answers between the markers.',
    verb: 'Derive student version', running: 'Deriving the student version', cancel: 'Stop', args: { course_source_repo: repo },
  };
}

export function generateSyllabus(s: Scope, repo: string): OpDef {
  return {
    ...base(s, 'assignment.generate_syllabus', repo), name: 'Generate the session list', title: repo, where: `From ${s.where}’s schedule`,
    intro: 'Writes SYLLABUS.sessions.md: one line per session from the cohort’s schedule.',
    verb: 'Write the session list', running: 'Writing the session list', cancel: 'Stop', args: { course_source_repo: repo },
  };
}
