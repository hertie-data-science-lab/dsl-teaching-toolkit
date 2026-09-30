// New semester (`#new-semester-1..3`): create the semester's org on GitHub, set it up (the
// `semester.bootstrap` operation, through the course's Console), then Instructors, Schedule and
// Students as three cards that open their editors and come back. Revision brief v2 section 7.

import type { ComponentChildren } from 'preact';
import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { SchemaForm, fieldErrors } from '../forms/Form';
import type { Files } from '../model/files';
import { parseInstructors, parseRoster } from '../model/people';
import { parseSchedule } from '../model/schedule';
import { bootstrapCohort } from '../ops/defs';
import type { Values } from '../tiers/types';
import { cohortOrg as cohortOrgTiers } from '../tiers/wizard';
import { Crumbs } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { useDraft } from '../wizards/drafts';
import { cohortOrgName, cohortTerms, openAt, TERM_RE, termLabel } from '../wizards/model';
import { BOT_PENDING_SEMESTER, allOk, checkCohortSetUp, checkOrg, useLive, usePoll, type Check } from '../wizards/verify';
import { Checks, OrgSteps, OrgWhy, Rail, StepCard, Verified, WizError } from '../wizards/Wizard';
import { authorOf } from '../github/client';
import { registerSemester } from '../model/discovery';
import type { CourseProps } from './types';
import { ASSIGNMENTS_FILE, CONFIG_REPO, INSTRUCTORS_FILE, JOIN_REPO } from '../model/names';
import { YamlText, obj } from '../edit/yamlText';

const STEPS = [
  { t: 'Org', s: 'Three things on GitHub' },
  { t: 'Setup', s: 'Site, join form, settings' },
  { t: 'Instructors, schedule, students', s: 'Three editors' },
];

export interface NkDraft {
  term?: string;
  org?: string;
  orgVerified?: string;
  setUp?: string;
  /** The org New semester has listed in the course's registry, so the bot may join it. */
  listed?: string;
}

export function nkDone(d: NkDraft, org: string, orgChecks: Check[] | null, setUp: Check[] | null): boolean[] {
  const orgOk = orgChecks ? allOk(orgChecks) : d.orgVerified === org;
  return [orgOk, orgOk && (setUp ? allOk(setUp) : d.setUp === org), false];
}

const yamlDoc = (text: string): unknown => {
  const y = new YamlText(text);
  return y.errors.length ? null : y.toJS();
};

/** Which of the cards are done, from the semester's own files; `defaults` (optional) once assignments.yml states any. */
export function cardsDone(files: Files, org: string): { staff: boolean; schedule: boolean; students: boolean; defaults: boolean } {
  const f = (path: string) => {
    const s = files.file(org, CONFIG_REPO, path);
    return s.kind === 'ready' ? s.text : '';
  };
  const sched = parseSchedule(f('schedule.yml'));
  return {
    staff: parseInstructors(f(INSTRUCTORS_FILE)).length > 0,
    schedule: !!sched && Object.keys(sched.releases).length + Object.keys(sched.assignments).length > 0,
    students: parseRoster(f('students.csv')).rows.length > 0,
    defaults: Object.keys(obj(obj(yamlDoc(f(ASSIGNMENTS_FILE))).defaults)).length > 0,
  };
}

export function NewCohortScreen({ course, files, now, step: asked }: Pick<CourseProps, 'course' | 'files' | 'now'> & { step?: number }) {
  const env = useEnv();
  const terms = cohortTerms(course.cohorts.map((c) => c.term), now);
  const [d, set] = useDraft<NkDraft>(`new-semester:${course.org}`, () => ({}));
  const term = d.term && TERM_RE.test(d.term) ? d.term : terms[0];
  const org = d.org ?? cohortOrgName(course.org, course.code, term);
  const label = termLabel(term);
  const runs = env?.ops.runs.value.length ?? 0;
  const orgLive = useLive(env ? async () => ({ org, ...(await checkOrg(env.client, org, { kind: env.kind, pending: BOT_PENDING_SEMESTER })) }) : null, [asked]);
  const setupLive = useLive(env ? async () => ({ org, checks: await checkCohortSetUp(env.client, course.org, org) }) : null, [org, asked, runs]);
  const orgChecks = orgLive.value?.org === org ? orgLive.value.checks : null;
  const setUp = setupLive.value?.org === org ? setupLive.value.checks : null;
  const done = nkDone(d, org, orgChecks, setUp);
  const step = openAt(done, asked);
  usePoll(orgLive, !!env && step === 1 && !allOk(orgChecks));
  // Continue lists the org in the course's registry, once the person has shown they own it:
  // the course's admins vouch for their semesters, and that is what lets the bot join it by
  // itself. Never on a poll, never for an org someone else owns.
  const [listing, setListing] = useState<{ org: string; busy: boolean; error?: string } | null>(null);
  const list = async () => {
    if (!env) return;
    setListing({ org, busy: true });
    const fail = (error: string) => setListing({ org, busy: false, error });
    try {
      const m = await env.client.getMyMembership(org);
      if (m?.state !== 'active' || m.role !== 'admin') return fail(`You must be an owner of ${org}.`);
      await registerSemester(env.client, course.org, org, authorOf(env.user));
      set({ listed: org, term });
      setListing(null);
    } catch (e) {
      fail(`Could not list ${org} with the course: ${e instanceof Error ? e.message : String(e)}`);
    }
  };
  const go = (k: number) => {
    if (typeof location !== 'undefined') location.hash = `#new-semester-${k}`;
  };
  const q = `?course=${course.org}`;

  let title: ComponentChildren = '', body, foot;
  if (step === 1) {
    title = <>Three things on GitHub <OrgWhy doc="04-new-cohort-org.md" /></>;
    const tiers = cohortOrgTiers(terms.includes(term) ? terms : [term, ...terms]);
    const vals: Values = { term, org };
    const errs = fieldErrors(null, tiers, vals);
    body = (
      <>
        <SchemaForm id="nk" schema={null} tiers={tiers} values={vals} onChange={(v) => {
          const t = String(v.term ?? term);
          const derived = cohortOrgName(course.org, course.code, t);
          const edited = v.org !== org ? String(v.org ?? '') : d.org;
          set({ term: t, org: edited && edited !== derived && t === term ? edited : undefined });
        }} />
        <OrgSteps org={org} check={orgLive.value?.org === org ? orgLive.value : null} busy={orgLive.busy} run={orgLive.run} back={`${q}#new-semester-1`} doc="04-new-cohort-org.md" />
        {listing?.org === org && listing.error ? <WizError>{listing.error}</WizError> : null}
        {d.listed === org && !done[0] ? <p class="footnote">Listed with {course.name}.</p> : null}
      </>
    );
    // Everything but the bot passes: Continue lists the org, which is what the bot waits for.
    const checks = orgChecks ?? [];
    const toList = !done[0] && d.listed !== org && checks.length > 1 && allOk(checks.slice(0, -1));
    const busy = listing?.org === org && listing.busy;
    foot = <button class="btn" type="button" disabled={(!done[0] && !toList) || busy || Object.keys(errs).length > 0} onClick={() => (done[0] ? (set({ orgVerified: org, term }), go(2)) : void list())}>Continue</button>;
  } else if (step === 2) {
    title = `Set up ${label}`;
    const ok = setUp ? allOk(setUp) : false;
    const def = bootstrapCohort({ courseOrg: course.org, cohortOrg: org, where: label }, course.name);
    body = (
      <>
        <p class="footnote">For {course.name}, {label}, in <code>{org}</code>.</p>
        <p>This makes the student site, the join form and the semester’s settings. It takes about a minute.</p>
        <Checks list={setUp} busy={setupLive.busy} pending={[`The semester’s settings repo is in place (${CONFIG_REPO})`, `The join form is in place (${JOIN_REPO})`, 'The course lists the semester']} />
        {ok ? <Verified>Set up: student site, join form and settings are in place.</Verified> : null}
      </>
    );
    foot = ok ? (
      <button class="btn" type="button" onClick={async () => { set({ setUp: org }); await env?.rediscover?.(); go(3); }}>Continue</button>
    ) : (
      <>
        <button class="btn small outline" type="button" disabled={setupLive.busy} onClick={() => setupLive.run()}>Check again</button>
        <button class="btn" type="button" disabled={!env} onClick={() => env?.ops.open(def, 'run')}>Set up {label}</button>
      </>
    );
  } else {
    title = 'Instructors, schedule and students';
    const c = cardsDone(files, org);
    const n = [c.staff, c.schedule, c.students].filter(Boolean).length;
    const back = encodeURIComponent('new-semester-3');
    const card = (name: string, screen: string, isDone: boolean, text: string, doneText: string) => (
      <div class={`scard${isDone ? ' done' : ''}`}>
        <h3>{name}{isDone ? <span class="chip ok">Done</span> : <span class="chip">To do</span>}</h3>
        <p>{isDone ? doneText : text}</p>
        <div><a class={`btn small${isDone ? ' quiet' : ''}`} href={`?cohort=${org}&wizard=${back}#${screen}`}>{isDone ? 'Open again' : `Open the ${name.toLowerCase()} editor`}</a></div>
      </div>
    );
    body = (
      <>
        <p>Do these in any order. Each opens its editor and brings you back here.</p>
        <div class="setup-cards">
          {card('Instructors', 'instructors', c.staff, 'Who gets the instructor buttons and the problem emails.', 'At least one person listed.')}
          {card('Schedule', 'schedule-semester', c.schedule, 'Semester dates, releases, assignments, events.', 'Releases or assignments scheduled.')}
          {card('Students', 'students', c.students, 'The roster; adding a row sends a code.', 'Roster started.')}
          {card('Assignment defaults', 'assignments', c.defaults, 'Optional. Late work, teams and who sees each repo for this semester’s assignments; left alone, the course’s defaults apply.', 'This semester’s defaults set.')}
        </div>
        {n === 3 ? <Verified>{label} is live. Automation takes it from here.</Verified> : <p class="footnote">{n} of 3 done. The semester goes live when instructors, schedule and students are done.</p>}
      </>
    );
    foot = <a class="btn outline" href={`?cohort=${org}#dashboard`}>Open {label}</a>;
  }
  const backBtn = step > 1 ? <a class="btn quiet" href={`${q}#new-semester-${step - 1}`}>Back</a> : <a class="btn quiet" href={`${q}#course`}>Cancel</a>;
  return (
    <>
      <Crumbs items={[{ t: course.name, href: `${q}#course` }, { t: 'New semester' }]} />
      <div class="page-head"><div><h1>New semester: {label} <Hint doc="04-new-cohort-org.md">One org per semester: students join it, never the course. It gets its own student site, join form and schedule.</Hint></h1></div></div>
      <div class="wizard">
        <Rail steps={STEPS} cur={step} done={done} heading="Two steps, then three editors" base="new-semester-" query={q} />
        <StepCard ctx={`For ${course.name}`} of={`Step ${step} of 3`} title={title} back={backBtn} foot={foot}>{body}</StepCard>
      </div>
    </>
  );
}
