// New course (`#new-course-1..4`): create the org on GitHub, set it up through the central
// Bootstrap Course Org workflow, write the course's details and defaults into its
// dsl-course.yml, then check. Revision brief v3 section 2, design/inputs.md "Set up a course".

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { useSave } from '../edit/save';
import { YamlText, deepEqual, obj } from '../edit/yamlText';
import { SchemaForm, effective, fieldErrors } from '../forms/Form';
import type { Files } from '../model/files';
import { ABOUT, ASSIGNMENT_DEFAULTS, COHORT_DEFAULTS } from '../tiers/course';
import type { Values } from '../tiers/types';
import { COURSE_ORG } from '../tiers/wizard';
import { CheckLine, Crumbs, Help } from '../ui/bits';
import { SaveLine } from '../ui/edit';
import { CENTRAL_ACTIONS, runBootstrap, type CentralRun } from '../wizards/central';
import { useDraft } from '../wizards/drafts';
import { courseOrgName, openAt } from '../wizards/model';
import { allOk, checkCourseSetUp, checkOrg, useLive, type Check } from '../wizards/verify';
import { Checks, LiveChecks, OrgLinks, Rail, StepCard, Verified, WizError } from '../wizards/Wizard';
import { AdminRows, courseFileAfter, detailsOf, missingAdmin, type Admin, type Details } from './CourseEdit';

const STEPS = [
  { t: 'Org', s: 'Create it on GitHub' },
  { t: 'Course details', s: 'Name, code, admins' },
  { t: 'Defaults', s: 'For every assignment and cohort' },
  { t: 'Check', s: 'Ready for a cohort', check: true },
];

export interface NcDraft {
  course_name?: string;
  course_code?: string;
  org?: string; // set only when the instructor edits the derived name
  org_name?: string;
  course_description?: string;
  admins: Admin[];
  orgVerified?: string;
  setUp?: string;
  detailsSaved?: string;
  defaultsSaved?: string;
  defaults?: Values;
  cohort?: Values;
  links?: string;
}

export function ncOrg(d: NcDraft): string {
  return d.org ?? courseOrgName(d.course_name ?? '', d.course_code ?? '');
}

/** Which steps are done: live checks when they have answered for this org, else what the draft remembers. */
export function ncDone(d: NcDraft, org: string, orgChecks: Check[] | null, setUp: Check[] | null): boolean[] {
  const orgOk = orgChecks ? allOk(orgChecks) : d.orgVerified === org;
  const setOk = setUp ? allOk(setUp) : d.setUp === org;
  return [orgOk, orgOk && setOk && d.detailsSaved === org, orgOk && setOk && d.detailsSaved === org && d.defaultsSaved === org, false];
}

export function NewCourseScreen({ files, step: asked }: { files: Files; step?: number }) {
  const env = useEnv();
  const me = env?.user;
  const [d, set, clear] = useDraft<NcDraft>('new-course', () => ({ admins: [{ github_handle: me?.login ?? '', email: me?.email ?? '', start: '', end: '' }] }));
  const org = ncOrg(d);
  const orgLive = useLive(env && org ? async () => ({ org, checks: await checkOrg(env.client, org) }) : null, [asked]);
  const setupLive = useLive(env && org ? async () => ({ org, checks: await checkCourseSetUp(env.client, org) }) : null, [org, asked]);
  const orgChecks = orgLive.value?.org === org ? orgLive.value.checks : null;
  const setUp = setupLive.value?.org === org ? setupLive.value.checks : null;
  const done = ncDone(d, org, orgChecks, setUp);
  const step = openAt(done, asked);
  const [run, setRun] = useState<CentralRun | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [save, runSave, setSave] = useSave(env);
  const file = step >= 2 && setUp && allOk(setUp) ? files.file(org, '.github', 'dsl-course.yml') : null;
  const y = file?.kind === 'ready' ? new YamlText(file.text) : null;
  const meta = y && !y.errors.length ? obj(y.toJS()) : null;
  const before: Details | null = meta ? detailsOf(meta) : null;
  const go = (k: number) => {
    if (typeof location !== 'undefined') location.hash = `#new-course-${k}`;
  };

  const writeCourse = async (after: Details, what: string): Promise<boolean> => {
    if (!file || file.kind !== 'ready' || !before || !meta) return false;
    const out = courseFileAfter(file.text, before, after, meta);
    if ('error' in out) {
      setSave({ kind: 'bad', text: out.error });
      return false;
    }
    const missing = await missingAdmin(env, before.admins, after.admins);
    if (missing) {
      setSave({ kind: 'bad', text: `There is no GitHub account called ${missing}.` });
      return false;
    }
    if (out.text === file.text) return true;
    return runSave({ owner: org, repo: '.github', path: 'dsl-course.yml' }, out.text, file.sha, { message: `course: ${what}, from the Instructor Console` });
  };

  const setUpCourse = async () => {
    if (!env) return;
    setErr(null);
    try {
      const r = await runBootstrap(env.client, { org, orgName: d.org_name || d.course_name || org, code: d.course_code ?? '', admins: d.admins.map((a) => a.github_handle) }, setRun);
      if (r.conclusion !== 'success') setErr('The set-up run did not finish cleanly. Open it on GitHub to see why; running it again is safe.');
      files.refresh(org, '.github', 'dsl-course.yml');
      setupLive.run();
      void env.rediscover?.();
    } catch (e) {
      setErr(`Could not start the set-up: ${e instanceof Error ? e.message : String(e)} Starting it needs membership of the lab’s faculty, instructors or admin team.`);
    }
  };

  let title = '', body, foot;
  if (step === 1) {
    title = 'Create the org on GitHub, install the app, then I check';
    const vals: Values = { course_name: d.course_name, course_code: d.course_code, org };
    const errs = fieldErrors(null, COURSE_ORG, vals);
    body = (
      <>
        <SchemaForm id="nc" schema={null} tiers={COURSE_ORG} values={vals} onChange={(v) => {
          const derived = courseOrgName(String(v.course_name ?? ''), String(v.course_code ?? ''));
          const edited = v.org !== org ? String(v.org ?? '') : d.org;
          set({ course_name: v.course_name as string | undefined, course_code: v.course_code as string | undefined, org: edited && edited !== derived ? edited : undefined });
        }} />
        <OrgLinks org={org} />
        <LiveChecks live={{ value: orgChecks, busy: orgLive.busy, run: orgLive.run }} pending={[`The org ${org} exists`, 'hertie-dsl-bot can manage it']} />
        {d.course_name || d.orgVerified ? <div><button class="btn small quiet" type="button" onClick={clear}>Start a different course</button></div> : null}
      </>
    );
    foot = <button class="btn" type="button" disabled={!done[0] || Object.keys(errs).length > 0} onClick={() => { set({ orgVerified: org }); go(2); }}>Continue</button>;
  } else if (step === 2) {
    title = 'Course details';
    const ready = setUp ? allOk(setUp) : false;
    const about: Values = { course_name: d.course_name, course_code: d.course_code, course_description: d.course_description, org_name: d.org_name };
    const tiers = { ...ABOUT, org_name: { ...ABOUT.org_name, placeholder: d.course_name, defaultLabel: 'default: the course name' } };
    const errs = fieldErrors(null, ABOUT, about);
    const running = run !== null && run.state !== 'completed';
    body = (
      <>
        <SchemaForm id="nc2" schema={null} tiers={tiers} values={about} onChange={(v) => set({ course_name: v.course_name as string, course_code: v.course_code as string, course_description: v.course_description as string, org_name: v.org_name as string })} />
        <div class="field">
          <span class="label">Course admins <span class="default">default: you</span></span>
          <p class="footnote">Course admins keep every button for this course across years. They can differ from a given term’s instructors, who are set per cohort under Staff.</p>
          <AdminRows admins={d.admins} id="nca" onChange={(admins) => set({ admins })} />
        </div>
        <dl class="kv"><dt>Org</dt><dd>{org}</dd><dt>Engine version</dt><dd>release <span class="footnote">set by the lab</span></dd><dt>Bot token</dt><dd>Set up for you</dd></dl>
        {run ? (
          run.state === 'completed' && run.conclusion === 'success' ? null : (
            <CheckLine cls={running ? 'busy' : 'bad'}>
              {running ? 'Setting up the course: settings, teams and automation. This takes a few minutes. ' : 'The set-up run ended. '}
              <a href={run.htmlUrl || CENTRAL_ACTIONS} target="_blank" rel="noopener">Open the run</a>
            </CheckLine>
          )
        ) : null}
        {err ? <WizError>{err}</WizError> : null}
        <Checks list={setUp} busy={setupLive.busy} pending={['Course settings are in place (dsl-course.yml)', 'The Console workflow is in place']} />
        {ready ? <Verified>Set up: settings and automation are in place.</Verified> : null}
        <SaveLine state={save} />
      </>
    );
    foot = ready ? (
      <button class="btn" type="button" disabled={Object.keys(errs).length > 0 || save.kind === 'busy' || !before} onClick={async () => {
        const after = { ...before!, about: { ...before!.about, course_name: d.course_name, course_code: d.course_code, course_description: d.course_description || undefined, ...(d.org_name ? { org_name: d.org_name } : {}) }, admins: d.admins };
        if (await writeCourse(after, 'write the course details')) {
          set({ setUp: org, detailsSaved: org });
          go(3);
        }
      }}>Save and continue</button>
    ) : (
      <button class="btn" type="button" disabled={running || Object.keys(errs).length > 0 || !env} onClick={() => void setUpCourse()}>{running ? 'Setting up…' : run ? 'Set up again' : 'Set up course'}</button>
    );
  } else if (step === 3) {
    title = 'Defaults';
    const defaults = d.defaults ?? before?.defaults ?? {};
    const cohort = d.cohort ?? before?.cohort ?? { archive_auto: true };
    const links = d.links ?? before?.links ?? '';
    const errs = fieldErrors(null, ASSIGNMENT_DEFAULTS, defaults);
    body = (
      <>
        <p>These are the course’s defaults. Every one can be overridden per assignment or per cohort.</p>
        <div class="form-section"><h3>Assignment defaults</h3><SchemaForm id="ncx" schema={null} tiers={ASSIGNMENT_DEFAULTS} values={defaults} onChange={(v) => set({ defaults: v })} /></div>
        <div class="form-section"><h3>Cohort defaults</h3><p class="footnote">Written into each new cohort’s schedule when it is set up.</p><SchemaForm id="ncc" schema={null} tiers={COHORT_DEFAULTS} values={cohort} onChange={(v) => set({ cohort: v })} /></div>
        <div class="form-section">
          <h3>Site links</h3>
          <div class="field">
            <label for="nck">File types the student site links to</label>
            <input type="text" id="nck" value={links} placeholder="pdf, html" onInput={(e) => set({ links: (e.target as HTMLInputElement).value })} />
            <p class="why">Released files of these types get a direct link on every cohort’s student site. Separate them with commas.</p>
          </div>
        </div>
        <SaveLine state={save} />
      </>
    );
    foot = (
      <button class="btn" type="button" disabled={Object.keys(errs).length > 0 || save.kind === 'busy' || !before} onClick={async () => {
        const after = { ...before!, defaults, cohort: effective(COHORT_DEFAULTS, cohort), links };
        if (deepEqual(after, before) || (await writeCourse(after, 'set the course defaults'))) {
          set({ defaultsSaved: org });
          go(4);
        }
      }}>Save and continue</button>
    );
  } else {
    title = 'Check';
    const people = obj(meta?.people);
    const admins = Array.isArray(people.course_admins) ? people.course_admins.length : 0;
    const summary: Check[] = [
      { text: `Name${meta?.course_name ? ` (${String(meta.course_name)})` : ''}, code${meta?.course_code ? ` (${String(meta.course_code)})` : ''} and ${admins} admin${admins === 1 ? '' : 's'} set`, ok: !!meta?.course_name && !!meta?.course_code && admins > 0 },
      { text: 'Defaults saved', ok: d.defaultsSaved === org },
    ];
    body = (
      <>
        <Checks list={[...(orgChecks ?? []), ...(setUp ?? []), ...summary]} busy={orgLive.busy || setupLive.busy} />
        <ul class="checks"><li><span class="ck no" /><span>No materials yet</span></li><li><span class="ck no" /><span>No assignment templates yet</span></li></ul>
        <p>The course is set up. It is ready for a cohort once it has materials and at least one template.</p>
        <div class="actions">
          <a class="btn" href={`?course=${org}#new-materials`}>Add materials</a>
          <a class="btn outline" href={`?course=${org}#new-assignment-1`}>New assignment</a>
        </div>
      </>
    );
    foot = <button class="btn small outline" type="button" onClick={() => { orgLive.run(); setupLive.run(); }}>Check again</button>;
  }
  const back = step > 1 ? <a class="btn quiet" href={`#new-course-${step - 1}`}>Back</a> : <a class="btn quiet" href="#home">Cancel</a>;
  return (
    <>
      <Crumbs items={[{ t: 'All courses', href: '#home' }, { t: 'New course' }]} />
      <div class="page-head"><div><h1>New course</h1></div></div>
      <Help title="What a course is" doc="01-new-course-org.md">
        <p>A course holds your materials and assignment templates for every term. You set it up once.</p>
      </Help>
      <div class="wizard">
        <Rail steps={STEPS} cur={step} done={done} heading="Three steps, then a check" base="new-course-" />
        <StepCard of={step < 4 ? `Step ${step} of 3` : 'The check'} title={title} back={back} foot={foot}>{body}</StepCard>
      </div>
    </>
  );
}
