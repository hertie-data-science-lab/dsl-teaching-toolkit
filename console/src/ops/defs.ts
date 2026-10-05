// Every dispatch operation as the panel offers it, with the mockup's copy. A screen builds
// the def for the thing on it (a release, an assignment, the semester) and hands it to the
// panel; the op name and args follow the registry (schemas/ops.json). `target` is where on
// GitHub the change shows, for the panel's "See on GitHub" once the run ends; an op that
// changes nothing on GitHub (a check, an email) has none.

import { COURSE_REPO } from '../model/names';
import { ghUrl } from '../ui/bits';
import { DEFAULT_DEST_REPO } from '../model/policy';
import { HANDOUT, RETURN_MARKS, RETURN_MARKS_ALWAYS, releaseAdhoc as adhocTiers, updateCopies as copiesTiers } from '../tiers/ops';
import type { OpDef } from './session';

export interface Scope {
  courseOrg: string;
  cohortOrg?: string;
  /** The eyebrow's second half: the semester ("Fall 2026") or the course name. */
  where: string;
}

const base = (s: Scope, op: string, key: string) => ({ op, key, courseOrg: s.courseOrg, cohortOrg: s.cohortOrg });

/** The semester's repos whose name contains `q` (student copies, marks repos). */
const reposLike = (org: string, q = '') => `https://github.com/orgs/${org}/repositories${q ? `?q=${encodeURIComponent(q)}` : ''}`;
/** A folder in a semester repo (its default branch), or the repo itself. */
const destUrl = (org: string, repo: string, path = '') => ghUrl(org, repo, path.replace(/^\/+|\/+$/g, ''), 'main', 'tree');

export function checkNow(s: Scope): OpDef {
  return {
    ...base(s, 'semester.check', 'cohort'), name: 'Refresh', title: 'Every check', where: s.where,
    intro: 'Re-reads every file and re-runs every check now instead of at the next automatic check.',
    verb: 'Refresh', running: 'Checking everything', cancel: 'Stop', args: {},
  };
}

export function previewNext(s: Scope): OpDef {
  return {
    ...base(s, 'semester.preview_automation', 'cohort'), name: 'Preview the next automatic run', title: 'The next automatic run', where: s.where,
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
    ...base(s, 'release.propagate_back', 'cohort'), name: 'Keep for future semesters', title: `Every edit made in ${s.where}`, where: `${s.where} to the course`,
    intro: 'Proposes the edits made to the semester’s copies back to the course materials, as changes for you to accept on GitHub.',
    verb: 'Propose the changes', running: 'Proposing changes', cancel: 'Stop', args: {},
    target: `https://github.com/search?q=${encodeURIComponent(`org:${s.courseOrg} is:pr is:open`)}&type=pullrequests`,
  };
}

export interface ReleaseRef {
  id: string;
  ident: string; // "Session 5"
  title: string;
  when: string; // "Thu 8 Oct 10:00"
  source: { repo: string; path: string };
  /** Where it lands in the semester (`status.json`); absent: the default release repo. */
  dest?: { repo: string; path: string } | null;
}

// One engine op, `release.entry`, in three wordings: early, again, now. No Options: the
// engine rebuilds a schedule entry's source and destination from schedule.yml
// (`console.entry_requests`), so a destination typed here would be dropped.
function releaseDef(s: Scope, r: ReleaseRef, copy: Pick<OpDef, 'name' | 'intro' | 'verb' | 'running' | 'cancel' | 'where'>): OpDef {
  const target = destUrl(s.cohortOrg ?? s.courseOrg, r.dest?.repo || DEFAULT_DEST_REPO, r.dest?.path ?? '');
  return { ...base(s, 'release.entry', r.id), ...copy, title: `${r.ident}: ${r.title}`, args: { entry: r.id }, previewProposed: false, target };
}

export function releaseEarly(s: Scope, r: ReleaseRef): OpDef {
  return releaseDef(s, r, {
    name: 'Release early', where: `Scheduled ${r.when}`, intro: `Copies ${r.ident} to students now instead of at its time. The scheduled release then finds nothing left to do.`,
    verb: `Release ${r.ident} early`, running: `Releasing ${r.ident} early`, cancel: 'Stop before copying',
  });
}

export function releaseAgain(s: Scope, r: ReleaseRef): OpDef {
  return releaseDef(s, r, {
    name: 'Release again', where: `Released ${r.when}`, intro: `Copies the course’s current version of ${r.ident} to students again, for a fixed file.`,
    verb: `Release ${r.ident} again`, running: `Releasing ${r.ident} again`, cancel: 'Stop before copying',
  });
}

export function releaseNow(s: Scope, r: ReleaseRef): OpDef {
  return releaseDef(s, r, {
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
    target: (a) => {
      // A blank destination path means the source path; a comma means several (deploy.parse_path_pairs): the repo then.
      const path = String(a.semester_dest_path || a.course_source_path || '');
      return destUrl(s.cohortOrg ?? s.courseOrg, String(a.semester_dest_repo || DEFAULT_DEST_REPO), path.includes(',') ? '' : path);
    },
  };
}

export interface AsgRef {
  slug: string;
  title: string; // "Assignment 2: Regression"
  template: string;
  units: number;
  group: boolean;
  when: string; // for the eyebrow
  /** The semester-side name its repos are called after (`semester_dest_repo`, else the key: cascade `semesterName`). */
  name?: string;
}

export function handout(s: Scope, a: AsgRef): OpDef {
  return {
    ...base(s, 'assignment.handout_now', a.slug), name: 'Hand out', title: a.title, where: a.when,
    intro: 'Creates each student’s or team’s repo now instead of at the scheduled time.',
    verb: `Hand out to ${a.units} ${a.group ? 'teams' : 'students'}`, running: 'Handing out', cancel: 'Stop after the current repo; repos already made stay',
    args: { course_source_repo: a.template }, options: HANDOUT, target: reposLike(s.cohortOrg ?? s.courseOrg, a.name || a.slug),
  };
}

export function updateCopies(s: Scope, a: AsgRef, files: string[]): OpDef {
  return {
    ...base(s, 'assignment.update_copies', a.slug), name: 'Update every copy', title: a.title, where: `${a.units} student repos`,
    intro: 'Pushes an assignment template file to every student copy and posts a note on each Submission receipts issue.',
    verb: `Update ${a.units} copies`, running: 'Updating every copy', cancel: 'Stop; copies already updated stay updated',
    // The schedule key: which entry, when two hand out from one template.
    args: { course_source_repo: a.template, assignment: a.slug }, options: copiesTiers(files),
    target: reposLike(s.cohortOrg ?? s.courseOrg, a.name || a.slug),
  };
}

export function collect(s: Scope, a: AsgRef): OpDef {
  return {
    ...base(s, 'assignment.collect_now', a.slug), name: 'Collect now', title: a.title, where: a.when,
    intro: 'Pulls the latest work from every repo into the mark sheet now.',
    verb: 'Collect now', running: 'Starting the collection', cancel: 'Stop',
    args: { course_source_repo: a.template, assignment: a.slug },
    // The Console run only starts the collection; it runs on in its own workflow, followed there.
    target: `${ghUrl(s.courseOrg, COURSE_REPO)}/actions/workflows/collect-submissions.yml`,
  };
}

/** `name`: the assignment's semester-side name (`semester_dest_repo`, else its key), what the engine returns by. */
export function returnMarks(s: Scope, a: AsgRef, marked: number, name: string): OpDef {
  return {
    ...base(s, 'grades.return', a.slug), name: 'Return marks', title: a.title, where: 'Marking',
    intro: 'Sends each student their marks and feedback for this assignment, beside the ones already returned. Your private notes stay private.',
    verb: `Return marks to ${marked} ${a.group ? 'teams' : 'students'}`, running: 'Returning marks', cancel: 'Stop; marks already returned stay returned',
    args: { assignment: name }, options: RETURN_MARKS, fixed: RETURN_MARKS_ALWAYS,
    target: reposLike(s.cohortOrg ?? s.courseOrg, 'grades-'),
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
    intro: 'Rebuilds the student site from the schedule, instructors and materials. Automation does this after every change; do it by hand after a failure.',
    verb: 'Update site', running: 'Updating the student site', cancel: 'Stop; the site keeps its current version', args: {},
    target: ghUrl(s.cohortOrg ?? s.courseOrg, `${s.cohortOrg ?? s.courseOrg}.github.io`),
  };
}

export function checkAccess(s: Scope): OpDef {
  return {
    ...base(s, 'access.check', 'access'), name: 'Check instructor access', title: 'Instructor access', where: s.where,
    intro: 'Makes GitHub match the instructors list. It never removes access.',
    verb: 'Check instructor access', running: 'Checking instructor access', cancel: 'Stop', args: {},
    target: `https://github.com/orgs/${s.cohortOrg ?? s.courseOrg}/teams`,
  };
}

export function archive(s: Scope, scheduled: string | null, passed: boolean): OpDef {
  return {
    ...base(s, 'semester.archive', 'cohort'), name: 'Archive', title: s.where, where: scheduled ? `Scheduled ${scheduled}` : 'Not scheduled',
    intro: 'Every repo becomes read-only. Students keep access. Nothing is deleted.',
    verb: `Archive ${s.where}`, running: `Archiving ${s.where}`, cancel: 'Stop; repos already archived stay archived', args: {},
    target: reposLike(s.cohortOrg ?? s.courseOrg),
    needsCheck: passed ? undefined : { label: 'Archive before the scheduled date', sub: scheduled ? `Needed because ${scheduled} has not passed.` : 'Needed because no archive date has passed.', arg: 'force' },
  };
}

export function publishWebsite(s: Scope, published: boolean): OpDef {
  return {
    ...base(s, 'course.publish_website', 'website'), name: 'Publish website', title: `${s.courseOrg}.github.io`, where: s.where,
    intro: 'Publishes the public website as its saved settings say. A daily update keeps it current.',
    verb: published ? 'Republish website' : 'Publish website', running: 'Publishing the public website', cancel: 'Stop; nothing is public until the last step',
    args: {}, target: ghUrl(s.courseOrg, `${s.courseOrg}.github.io`),
    needsCheck: { label: 'This replaces the live public site', sub: 'Publishing has no preview, so confirm instead.' },
  };
}

export function teamsWindow(s: Scope, a: AsgRef, closes: string): OpDef {
  return {
    ...base(s, 'teams.open_window', a.slug), name: 'Email students without a team', title: a.title, where: `Window open until ${closes}`,
    intro: 'Emails every joined student who is not in a team yet, with the link to the team list on the Join screen of the student console.',
    verb: 'Send the emails', running: 'Emailing students without a team', cancel: 'Stop; emails already sent stay sent',
    args: { assignment: a.slug },
  };
}

export function derive(s: Scope, slug: string, repo: string, title: string): OpDef {
  return {
    ...base(s, 'assignment.derive_starter', slug), name: 'Derive student version', title, where: 'Template',
    intro: 'Builds the student starter on main from the solution branch by removing answers between the markers.',
    verb: 'Derive student version', running: 'Deriving the student version', cancel: 'Stop', args: { course_source_repo: repo },
    target: `${ghUrl(s.courseOrg, repo)}/tree/main`,
  };
}

/** The weekly plan (decision 0031 rule 9): Copy previews it, Write puts it between the markers in `syllabus`, then links the file. */
export function generateSyllabus(s: Scope, repo: string, syllabus: string): OpDef {
  const file = `https://github.com/${s.courseOrg}/${repo}/blob/HEAD/${syllabus.split('/').map(encodeURIComponent).join('/')}`;
  return {
    ...base(s, 'assignment.generate_syllabus', repo), name: 'Weekly plan', title: syllabus, where: `From ${s.where}’s schedule`,
    intro: `Every session with its date and readings, from the semester’s schedule. Write puts it into ${syllabus} between the weekly-plan markers; nothing else in the file changes.`,
    verb: 'Write the weekly plan', running: 'Writing the weekly plan', cancel: 'Stop', args: { course_source_repo: repo, syllabus },
    target: file, previewLabel: 'Copy',
  };
}

// ------------------------------------------------------------------ the wizards' operations

export function bootstrapCohort(s: Scope & { cohortOrg: string }, courseName: string): OpDef {
  return {
    ...base(s, 'semester.bootstrap', s.cohortOrg), name: 'Set up', title: `${courseName}, ${s.where}`, where: s.cohortOrg,
    intro: 'Makes the student site, the join form and the semester’s settings in the new org. It takes about a minute.',
    verb: `Set up ${s.where}`, running: `Setting up ${s.where}`, cancel: 'Stop; what is already made stays and a second run finishes it', args: {},
    target: ghUrl(s.cohortOrg ?? s.courseOrg),
  };
}

/** `from`: the repo an import copies from; the engine makes the template, the console copies the files after. */
export function createAssignment(s: Scope, repo: string, title: string, args: Record<string, unknown>, from?: string): OpDef {
  return {
    ...base(s, 'assignment.create', repo), name: 'New assignment', title, where: s.where,
    intro: `Creates ${repo}: a main branch for the brief students get and a solution branch for marking.${from ? ` The files you ticked in ${from} are copied into it next, as you.` : ''} Nothing reaches students until it is on a schedule.`,
    verb: 'Create assignment', running: `Creating ${repo}`, cancel: 'Stop', args, target: ghUrl(s.courseOrg, repo),
  };
}

export function createMaterials(s: Scope, repo: string, args: Record<string, unknown>): OpDef {
  return {
    ...base(s, 'materials.create', repo), name: 'New handout materials', title: repo, where: s.where,
    intro: `Creates ${repo}, private to instructors until releases copy it to a semester.`,
    verb: 'Create handout materials repo', running: `Creating ${repo}`, cancel: 'Stop', args, target: ghUrl(s.courseOrg, repo),
  };
}
