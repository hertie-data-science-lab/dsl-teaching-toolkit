// S14 Site (home text and announcements, Update site) and S18 Operations list.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { invalidText, useSave } from '../edit/save';
import { YamlText, deepEqual, obj, render } from '../edit/yamlText';
import { Field, SchemaForm, fieldErrors } from '../forms/Form';
import { ago, fmtWhen } from '../model/format';
import { parseInstructors } from '../model/people';
import { bodyOf, frontMatter, homeText } from '../model/student';
import { parse } from 'yaml';
import type { Outcome } from '../model/types';
import { outcomePath } from '../ops/adapter';
import { updateSite } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { CheckLine, Loading, OpsList } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { SaveBar, SaveLine } from '../ui/edit';
import { Ext } from '../ui/icons';
import { WithStatus, cohortScope, todayOf, tzOf, useOperations, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';
import { COURSE_REPO, CONFIG_REPO, INSTRUCTORS_FILE } from '../model/names';
import { validSchedule } from '../model/cascade';
import { draftErrors, readDraft, writeDraft, type SemesterDraft } from '../model/scheduleEdit';
import { ABOUT } from '../tiers/course';
import type { Tiers, Values } from '../tiers/types';
import { courseFileAfter, detailsOf } from './CourseEdit';
import { SemesterForm, useSchedFile } from './Schedule';

/** A hand-written announcement's title, date and text, from its front matter (or its body). */
export function readAnnouncement(text: string): { title?: string; date: string; text: string } {
  const fm = frontMatter(text);
  return { title: fm.title == null ? undefined : String(fm.title), date: fm.date == null ? '' : String(fm.date).slice(0, 10), text: fm.details == null ? bodyOf(text).trim() : String(fm.details) };
}

/** A new `_announcements/` file, named by its date and its title (else its text). The Updates box shows `details`. */
export function announcementFile(date: string, text: string, title = ''): { path: string; content: string } {
  const slug = (title || text).toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').split('-').slice(0, 6).join('-') || 'update';
  return { path: `_announcements/${date}-${slug}.md`, content: `---\n${render({ ...(title ? { title } : {}), date, details: text })}---\n` };
}

function Announcement({ p, repo, path, sha }: { p: ReadyProps; repo: string; path: string; sha: string }) {
  const env = useEnv();
  const [save, runSave] = useSave(env);
  const f = p.files.file(p.cohort.org, repo, path);
  const a = f.kind === 'ready' ? readAnnouncement(f.text) : null;
  return (
    <li>
      <span class="r-title">{a?.date || path.replace(/^_announcements\//, '').slice(0, 10)}</span>
      <span class="r-sub">{a ? <>{a.title ? <b>{a.title}: </b> : null}{a.text}</> : path.replace(/^_announcements\//, '').replace(/\.md$/, '')}</span>
      <span class="r-side">
        {save.kind !== 'idle' ? <CheckLine cls={save.kind === 'busy' ? 'busy' : save.kind}>{save.text}</CheckLine> : null}
        <button class="btn small quiet" type="button" disabled={save.kind === 'busy'} onClick={() => void runSave({ owner: p.cohort.org, repo, path }, null, f.kind === 'ready' ? f.sha : sha, { message: `site: remove the announcement ${path}, from the DSL Teaching Console` })}>Remove</button>
      </span>
    </li>
  );
}

function configOf(text: string): Record<string, unknown> {
  try {
    const v = parse(text);
    return v && typeof v === 'object' ? (v as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

/** The `_config.yml` keys every site update rewrites from the course and the semester (`site.py`), those set. */
function rewritten(p: ReadyProps): Record<string, string> {
  const { course, cohort } = p;
  const all: Record<string, string> = { course_name: course.name, title: course.name, course_code: course.code, course_description: course.description, course_semester: cohort.termLabel, github_org: cohort.org };
  return Object.fromEntries(Object.entries(all).filter(([, v]) => !!v));
}

function Site(p: ReadyProps) {
  const { status, now } = p;
  const env = useEnv();
  const tz = tzOf(status), year = yearOf(now, tz);
  const repo = `${p.cohort.org}.github.io`;
  const url = status.site?.url ?? `https://${repo}`;
  const home = p.files.file(p.cohort.org, repo, 'index.md');
  const ann = p.files.dir(p.cohort.org, repo, '_announcements');
  const peopleFile = p.files.file(p.cohort.org, CONFIG_REPO, INSTRUCTORS_FILE);
  const people = peopleFile.kind === 'ready' ? parseInstructors(peopleFile.text) : [];
  const site = status.site;
  const [body, setBody] = useState<string | null>(null);
  const [homeSave, runHome] = useSave(env);
  const [newAnn, setNewAnn] = useState<{ title: string; date: string; text: string } | null>(null);
  const [annSave, runAnn, setAnnSave] = useSave(env);
  const homeBody = home.kind === 'ready' ? bodyOf(home.text) : '';
  const shown = body ?? homeBody;
  // The site's `_config.yml`, with the keys every update rewrites (`site.py`) as they will be.
  const cfgFile = p.files.file(p.cohort.org, repo, '_config.yml');
  const siteVars = { ...configOf(cfgFile.kind === 'ready' ? cfgFile.text : ''), ...rewritten(p) };
  const saveHome = async () => {
    if (home.kind !== 'ready' || body === null) return;
    const front = home.text.slice(0, home.text.length - bodyOf(home.text).length);
    if (await runHome({ owner: p.cohort.org, repo, path: 'index.md' }, `${front}${body.endsWith('\n') ? body : `${body}\n`}`, home.sha, { message: 'site: edit the home text, from the DSL Teaching Console' })) setBody(null);
  };
  const annFile = newAnn ? announcementFile(newAnn.date, newAnn.text.trim(), newAnn.title.trim()) : null;
  const addAnn = async () => {
    if (!newAnn || !annFile) return;
    if (!newAnn.text.trim() || !/^\d{4}-\d{2}-\d{2}$/.test(newAnn.date)) return setAnnSave({ kind: 'bad', text: 'Give the date and the text.' });
    if (await runAnn({ owner: p.cohort.org, repo, path: annFile.path }, annFile.content, null, { message: 'site: add an announcement, from the DSL Teaching Console' })) setNewAnn(null);
  };
  return (
    <>
      <div class="page-head">
        <div><h2 class="h1">Student site <Hint doc="11-configure-cohort-site.md">The home text and announcements are yours. The schedule, lectures, assignments and instructors pages are rewritten on every update.</Hint></h2><p class="lede">Deployed semester page for the semester. Most of it is auto-populated from the schedule, instructors and materials.</p></div>
        <div class="actions">
          <OpButtons def={updateSite(cohortScope(p))} verbCls="btn outline" />
          <a class="btn quiet" href={url} target="_blank" rel="noopener">Open the student site <Ext /></a>
        </div>
      </div>
      <div class="stack">
        <section class="panel section">
          <h2>Last update</h2>
          {site?.stale ? (
            <CheckLine cls="bad"><b>Out of date.</b> The last update was {site.last_update ? `${fmtWhen(site.last_update, tz, year)} (${ago(site.last_update, now)})` : 'never'}, before the latest change to the schedule, instructors or materials. Update site brings it up to date.</CheckLine>
          ) : (
            <CheckLine cls="ok">Up to date{site?.last_update ? `; last updated ${fmtWhen(site.last_update, tz, year)}` : ''}.</CheckLine>
          )}
        </section>
        <section class="panel section">
          <h2>Home text</h2>
          {home.kind === 'loading' ? <Loading what="Reading the home page" /> : home.kind === 'ready' ? (
            <Field id="site-home" k="home" t={{ tier: 'ask', label: 'Home page', widget: 'markdown', reason: 'The first thing students read.' }} value={shown} values={{}} set={(_, v) => setBody(String(v ?? ''))} shown={(t) => homeText(t, siteVars, true)} />
          ) : <p class="footnote">No home page yet: it appears when the site is set up.</p>}
          <SaveBar state={homeSave} onSave={() => void saveHome()} small disabled={body === null || body === homeBody} file={{ org: p.cohort.org, repo, path: 'index.md', exists: home.kind !== 'absent' }} />
        </section>
        <section class="panel section">
          <div class="section-head"><h2>Announcements</h2><span class="meta">Shown in the Updates box with released sessions and hand outs</span></div>
          <p style="color:var(--ink-2)">Announcements for releases and hand outs are posted automatically from the schedule. Add anything else here.</p>
          {ann.kind === 'ready' && ann.entries.length ? (
            <ul class="rows">{ann.entries.filter((e) => e.type === 'file').map((e) => <Announcement p={p} repo={repo} path={e.path} sha={e.sha} />)}</ul>
          ) : ann.kind === 'loading' ? <Loading /> : <p class="footnote">No announcements.</p>}
          {newAnn && annFile ? (
            <div class="form">
              <div class="form-section">
                <div class="field"><label for="ann-title">Title</label><input id="ann-title" type="text" value={newAnn.title} onInput={(e) => setNewAnn({ ...newAnn, title: (e.target as HTMLInputElement).value })} /></div>
                <div class="field"><label for="ann-date">Date</label><input id="ann-date" type="date" value={newAnn.date} onInput={(e) => setNewAnn({ ...newAnn, date: (e.target as HTMLInputElement).value })} /></div>
                <div class="field"><label for="ann-text">Text</label><textarea id="ann-text" placeholder="What students should know" onInput={(e) => setNewAnn({ ...newAnn, text: (e.target as HTMLTextAreaElement).value })}>{newAnn.text}</textarea></div>
                <SaveBar state={annSave} onSave={() => void addAnn()} small disabled={!newAnn.text.trim()} file={{ org: p.cohort.org, repo, path: annFile.path, exists: false }}>
                  <button class="btn small quiet" type="button" onClick={() => { setNewAnn(null); setAnnSave({ kind: 'idle' }); }}>Cancel</button>
                </SaveBar>
              </div>
            </div>
          ) : (
            <>
              <SaveLine state={annSave} />
              <div class="savebar"><button class="btn small outline" type="button" onClick={() => { setNewAnn({ title: '', date: todayOf(now, tz), text: '' }); setAnnSave({ kind: 'idle' }); }}>Add an announcement</button></div>
            </>
          )}
        </section>
        <section class="panel section">
          <h2>Instructor photos</h2>
          <ul class="rows">
            {people.map((x) => (
              <li>
                <span class="r-title">{x.name || x.handle} {x.photo ? <span class="chip ok">Photo</span> : <span class="chip">No photo</span>}</span>
                <span class="r-sub">{x.photo || 'Add one on Instructors: a URL link, or an image dropped on the Photo field there.'}</span>
                <span class="r-side"><a class="btn small quiet" href="#instructors">Instructors</a></span>
              </li>
            ))}
          </ul>
        </section>
        <SiteSettings p={p} repo={repo} />
      </div>
    </>
  );
}

/** The site's own `_config.yml` keys the sync leaves alone (`site_repo.apply_plan`), saved into the site repo. */
const INSTITUTION: Tiers = {
  schoolname: { tier: 'ask', label: 'School name', reason: 'The institution the site names.' },
  schoolurl: { tier: 'ask', label: 'School link', widget: 'url' },
  dsl_org_url: { tier: 'ask', label: 'Lab link', widget: 'url', reason: 'Where the home text’s lab link points.' },
  address: { tier: 'ask', label: 'Address', widget: 'textarea', reason: 'The site’s footer.' },
};
const COURSE_KEYS = ['course_name', 'course_code', 'course_description'];
const COURSE_FILE = 'dsl-course.yml';
const str = (v: unknown) => (v == null ? '' : String(v));

/** Where a derived group is saved, and that the site follows at the next sync. */
function SavedTo({ href, screen }: { href: string; screen: string }) {
  return <p class="footnote">Saved to <a class="textlink" href={href}>{screen}</a>; the site updates at the next sync.</p>;
}

/** Course name, code and description: the sync copies them from dsl-course.yml, so they are saved there. */
function CourseGroup({ p }: { p: ReadyProps }) {
  const { course } = p;
  const env = useEnv();
  const file = p.files.file(course.org, COURSE_REPO, COURSE_FILE);
  const [draft, setDraft] = useState<Values | null>(null);
  const [save, runSave, setSave] = useSave(env);
  const y = file.kind === 'ready' ? new YamlText(file.text) : null;
  const meta = y && !y.errors.length ? obj(y.toJS()) : null;
  const before = meta ? detailsOf(meta) : null;
  const base: Values = Object.fromEntries(COURSE_KEYS.map((k) => [k, before?.about[k]]));
  const cur = draft ?? base;
  const doSave = async () => {
    if (!before || !meta || file.kind !== 'ready') return;
    if (p.courseMigrated === false) return setSave({ kind: 'bad', text: 'Not saved: the console has not yet confirmed this course uses the current names.' });
    if (Object.keys(fieldErrors(null, ABOUT, cur)).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const out = courseFileAfter(file.text, before, { ...before, about: { ...before.about, ...cur } }, meta);
    if ('error' in out) return setSave({ kind: 'bad', text: out.error });
    if (await runSave({ owner: course.org, repo: COURSE_REPO, path: COURSE_FILE }, out.text, file.sha, { message: 'course: edit the course details, from the DSL Teaching Console', statusRepo: [course.org, COURSE_REPO] })) setDraft(null);
  };
  return (
    <div class="form-section">
      <h3>Course</h3>
      {file.kind === 'loading' ? <Loading what={`Reading ${COURSE_FILE}`} /> : null}
      {y?.errors.length ? <CheckLine cls="bad">{COURSE_FILE} does not parse ({y.errors[0]}); fix it with Edit the file directly.</CheckLine> : null}
      {meta ? <SchemaForm id="ss-course" schema={null} tiers={ABOUT} values={cur} onChange={(v) => { setDraft(v); if (save.kind !== 'busy') setSave({ kind: 'idle' }); }} readOnly={!course.write} /> : null}
      <SavedTo href={`?course=${course.org}#details`} screen="Course details" />
      <SaveBar state={save} onSave={() => void doSave()} small disabled={!meta || !course.write || deepEqual(cur, base)} file={{ org: course.org, repo: COURSE_REPO, path: COURSE_FILE, exists: file.kind !== 'absent' }} />
    </div>
  );
}

/** The semester's dates and timezone: the sync reads them from schedule.yml, so they are saved there. */
function SemesterGroup({ p }: { p: ReadyProps }) {
  const env = useEnv();
  const sf = useSchedFile(p);
  const [draft, setDraft] = useState<SemesterDraft | null>(null);
  const [save, runSave, setSave] = useSave(env);
  const ready = sf && sf !== 'loading' && !sf.error ? sf : null;
  const before = ready ? (readDraft(ready.doc, 'semester') as SemesterDraft) : null;
  const d = draft ?? before;
  const errors = d ? draftErrors(d) : {};
  const doSave = async () => {
    if (!ready || !d) return;
    if (Object.keys(errors).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const y = new YamlText(ready.text);
    writeDraft(y, d, ready.doc);
    if (!validSchedule(y.toJS())) return setSave({ kind: 'bad', text: invalidText('the schedule', validSchedule) });
    if (await runSave({ owner: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml' }, y.text, ready.sha, { message: 'schedule: edit the semester dates, from the DSL Teaching Console', statusRepo: [p.cohort.org, CONFIG_REPO] })) setDraft(null);
  };
  return (
    <div class="form-section">
      <h3>Semester dates</h3>
      {sf === 'loading' ? <Loading what="Reading the schedule" /> : sf === null ? <p class="footnote">No schedule.yml yet.</p> : sf.error ? <CheckLine cls="bad">schedule.yml does not parse ({sf.error}); fix it with Edit the file directly.</CheckLine> : null}
      {d ? <SemesterForm d={d} set={(patch) => { setDraft({ ...d, ...patch }); if (save.kind !== 'busy') setSave({ kind: 'idle' }); }} errors={errors} /> : null}
      <SavedTo href="#schedule-semester" screen="Schedule" />
      <SaveBar state={save} onSave={() => void doSave()} small disabled={!d || deepEqual(d, before)} file={{ org: p.cohort.org, repo: CONFIG_REPO, path: 'schedule.yml', exists: sf !== null }} />
    </div>
  );
}

/** The institution keys of the site's `_config.yml`: the sync never rewrites them, so they are saved in the site repo. */
function InstitutionGroup({ p, repo }: { p: ReadyProps; repo: string }) {
  const env = useEnv();
  const file = p.files.file(p.cohort.org, repo, '_config.yml');
  const [draft, setDraft] = useState<Values | null>(null);
  const [save, runSave, setSave] = useSave(env);
  const y = file.kind === 'ready' ? new YamlText(file.text) : null;
  const doc = y && !y.errors.length ? obj(y.toJS()) : null;
  const base: Values = Object.fromEntries(Object.keys(INSTITUTION).map((k) => [k, str(doc?.[k])]));
  const cur = draft ?? base;
  const doSave = async () => {
    if (!doc || file.kind !== 'ready') return;
    if (Object.keys(fieldErrors(null, INSTITUTION, cur)).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const out = new YamlText(file.text);
    // Only the keys that changed are written; every other byte of the file stays as it is.
    for (const k of Object.keys(INSTITUTION)) if (str(cur[k]) !== base[k]) out.assign([k], str(cur[k]) === '' ? undefined : str(cur[k]));
    if (await runSave({ owner: p.cohort.org, repo, path: '_config.yml' }, out.text, file.sha, { message: 'site: edit the site settings, from the DSL Teaching Console' })) setDraft(null);
  };
  return (
    <div class="form-section">
      <h3>Institution</h3>
      {file.kind === 'loading' ? <Loading what="Reading _config.yml" /> : file.kind === 'absent' ? <p class="footnote">No _config.yml yet: it appears when the site is set up.</p> : null}
      {y?.errors.length ? <CheckLine cls="bad">_config.yml does not parse ({y.errors[0]}); fix it with Edit the file directly.</CheckLine> : null}
      {doc ? <SchemaForm id="ss-inst" schema={null} tiers={INSTITUTION} values={cur} onChange={(v) => { setDraft(v); if (save.kind !== 'busy') setSave({ kind: 'idle' }); }} /> : null}
      <SaveBar state={save} onSave={() => void doSave()} small disabled={!doc || deepEqual(cur, base)} file={{ org: p.cohort.org, repo, path: '_config.yml', exists: file.kind !== 'absent' }} />
    </div>
  );
}

function SiteSettings({ p, repo }: { p: ReadyProps; repo: string }) {
  return (
    <section class="panel section">
      <h2>Site settings</h2>
      <div class="form">
        <CourseGroup p={p} />
        <SemesterGroup p={p} />
        <InstitutionGroup p={p} repo={repo} />
        <div class="form-section">
          <h3>Semester and org</h3>
          <dl class="kv">
            <dt>Semester</dt><dd>{p.cohort.termLabel} <span class="footnote">from the semester org’s name</span></dd>
            <dt>GitHub org</dt><dd>{p.cohort.org} <span class="footnote">the semester org itself</span></dd>
          </dl>
        </div>
      </div>
    </section>
  );
}

export function SiteScreen(p: CohortProps) {
  return <WithStatus props={p} title="Student site">{(r) => <Site {...r} />}</WithStatus>;
}

function Operations(p: ReadyProps) {
  const ops = useOperations(p.status.operations, p.cohort.org);
  const outcomes: Record<string, Outcome | undefined> = {};
  for (const op of new Set(ops.map((o) => o.op))) {
    const f = p.files.file(p.cohort.org, CONFIG_REPO, outcomePath(op));
    if (f.kind === 'ready') {
      try {
        outcomes[op] = JSON.parse(f.text) as Outcome;
      } catch {
        /* an unreadable outcome file only loses the Details fold */
      }
    }
  }
  return (
    <>
      <div class="page-head">
        <div><h2 class="h1">All operations <Hint doc="reference/actions-reference.md">Each line says what happened and how many. Open Details for the reasons behind a count and the run on GitHub.</Hint></h2><p class="lede">Everything automation and you have done in this semester, newest first. Outcomes stay here after the panel closes.</p></div>
      </div>
      <section class="panel"><OpsList list={ops} now={p.now} full runRepo={`${p.course.org}/.github`} outcomes={outcomes} /></section>
    </>
  );
}

export function OperationsScreen(p: CohortProps) {
  return <WithStatus props={p} title="All operations">{(r) => <Operations {...r} />}</WithStatus>;
}
