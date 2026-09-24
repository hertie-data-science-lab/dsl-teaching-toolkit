// S2 Course overview and S17 Template settings (read).

import { useState } from 'preact/hooks';
import gradingSchema from '../../schemas/grading_config.schema.json';
import { useEnv } from '../env';
import { invalidText, useSave } from '../edit/save';
import { YamlText, deepEqual } from '../edit/yamlText';
import { SchemaForm, effective, fieldErrors } from '../forms/Form';
import { assignmentIdent } from '../model/format';
import { checkNow, derive } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { fromConfig, settingsTiers, toConfig, type CourseDefaults } from '../tiers/grading';
import type { Tiers, Values } from '../tiers/types';
import { SaveBar } from '../ui/edit';
import type { CourseStatus, Problem } from '../model/types';
import { validator } from '../model/validate';
import { CheckLine, Crumbs, Help, Legend, Lives, Loading, ProblemCards, Probs, Rail, Soon, ghUrl } from '../ui/bits';
import { Check, Ext } from '../ui/icons';
import { courseScope, newestScope } from './CourseEdit';
import type { CourseProps } from './types';

/** The course block and course-scoped problems: from the course's own status, else a cohort's. */
export function courseView(p: CourseProps): { course: CourseStatus | null; problems: Problem[]; computed: boolean } {
  if (p.loaded.kind === 'ready' && p.loaded.status.course)
    return { course: p.loaded.status.course, problems: (p.loaded.status.problems ?? []).filter((x) => x.scope === 'course'), computed: true };
  for (const l of Object.values(p.cohortStates))
    if (l.kind === 'ready' && l.status.course)
      return { course: l.status.course, problems: (l.status.problems ?? []).filter((x) => x.scope === 'course'), computed: true };
  return { course: null, problems: [], computed: false };
}

function problemsOf(p: CourseProps, cohortOrg: string): number | null {
  const l = p.cohortStates[cohortOrg];
  return l && l.kind === 'ready' ? (l.status.problems ?? []).length : null;
}

export function CourseHeaderActions({ course, ready }: { course: CourseProps['course']; ready: boolean }) {
  return (
    <div class="actions">
      <a class={ready ? 'btn' : 'btn quiet'} href={`?course=${course.org}#new-cohort-1`}>New cohort</a>
      <a class="btn outline" href="#website">Publish website</a>
      {newestScope({ course }) ? <OpButtons def={{ ...checkNow(newestScope({ course })!), where: course.name }} /> : <Soon label="Check now" title="Check now runs on a cohort; this course has none yet." />}
      <a class="btn quiet" href={ghUrl(course.org)} target="_blank" rel="noopener">Course on GitHub <Ext /></a>
    </div>
  );
}

export function dflt(meta: Record<string, unknown> | null, key: string): string {
  const d = (meta?.assignment_defaults ?? {}) as Record<string, unknown>;
  return d[key] == null ? '' : String(d[key]);
}

/** The course's late policy and team size, as the template forms show them. */
export function courseDefaults(meta: Record<string, unknown> | null): CourseDefaults {
  return { lateDays: dflt(meta, 'late_window_days') || '10', latePct: dflt(meta, 'late_penalty_per_day') || '10%', teamSize: dflt(meta, 'max_team_size') || '5' };
}

export function CourseScreen(p: CourseProps) {
  const [showSetup, setShowSetup] = useState(false);
  const { course } = p;
  const v = courseView(p);
  const ready = v.course ? v.course.ready : false;
  const late = dflt(course.meta, 'late_penalty_per_day') || '10%';
  const lateDays = dflt(course.meta, 'late_window_days') || '10';
  const team = dflt(course.meta, 'max_team_size') || '5';
  const pub = v.course?.stages?.C6 === 'done';
  return (
    <>
      <Crumbs items={[{ t: 'All courses', href: '#home' }, { t: course.name }]} />
      <div class="page-head">
        <div>
          <h1>{course.name}</h1>
          <p class="lede">
            {!v.computed ? <span>Status not computed yet.</span> : ready ? <span>Ready for a new cohort.</span> : <span class="amber">Not ready for a new cohort: {v.problems.length === 1 ? 'one problem' : `${v.problems.length} problems`} would stop a cohort.</span>}
            {v.computed ? <button class="textlink" type="button" aria-expanded={showSetup} onClick={() => setShowSetup(!showSetup)}>{showSetup ? 'Hide setup' : 'Show setup'}</button> : null}
          </p>
        </div>
        <CourseHeaderActions course={course} ready={ready} />
      </div>
      {!course.write ? <div class="ro-banner"><b>Read only.</b><span>You cannot change this course on GitHub, so the console shows what your account can see and offers no buttons.</span></div> : null}
      <Help title="What lives in a course" doc="02-add-materials-to-course.md">
        <p>Materials live here privately until a scheduled release copies them to a cohort. Some folders can be withheld, or published openly on the public website.</p>
        <p>One assignment template per assignment. Students get a copy at hand out; marking reads its solution branch.</p>
      </Help>
      <div class="stack">
        {showSetup && v.course ? (
          <section class="panel section"><div class="section-head"><h2>Setup</h2><Legend /></div><Rail scope="course" stages={v.course.stages} problems={v.problems} /></section>
        ) : null}
        <div class="grid-2">
          <section class="panel section">
            <div class="problems-head"><h2>Course problems</h2><span class="footnote">They also appear on every cohort they will affect.</span></div>
            {!v.computed ? <p class="footnote">Status not computed yet.</p> : v.problems.length ? <ProblemCards list={v.problems} /> : <div class="no-problems"><Check /><span>No course problems.</span></div>}
          </section>
          <section class="panel section">
            <h2>Cohorts</h2>
            {course.cohorts.length ? (
              <ul class="rows">
                {course.cohorts.map((c) => {
                  const l = p.cohortStates[c.org];
                  const n = problemsOf(p, c.org);
                  const live = l && l.kind === 'ready' ? l.status.cohort?.live !== false : true;
                  return (
                    <li>
                      <span class="r-title">{c.termLabel} <span class={`chip ${live ? 'ok' : ''}`}>{live ? 'Live' : 'Archived'}</span></span>
                      <span class="r-sub">{l && l.kind === 'ready' && l.status.cohort ? `Week ${l.status.cohort.week} of ${l.status.cohort.weeks}` : l?.kind === 'absent' ? 'Status not computed yet' : c.org}</span>
                      <span class="r-side">{n !== null ? <Probs n={n} /> : null}<a class="btn small quiet" href={`?cohort=${c.org}#cohort`}>Open</a></span>
                    </li>
                  );
                })}
              </ul>
            ) : <p class="footnote">No cohorts yet.</p>}
          </section>
        </div>
        <div class="grid-2">
          <section class="panel section" id="sec-templates">
            <div class="section-head"><h2>Assignment templates</h2><a class="btn small outline" href={`?course=${course.org}#new-assignment-1`}>New assignment</a></div>
            {v.course?.templates?.length ? (
              <ul class="rows">
                {v.course.templates.map((t) => {
                  const bad = t.state !== 'ready';
                  return (
                    <li>
                      <span class="r-title">{assignmentIdent(t.slug)} <span class={`chip ${bad ? 'bad' : 'ok'}`}>{bad ? 'Has a problem' : 'Ready'}</span></span>
                      <span class={`r-sub${bad ? ' flag' : ''}`}>{bad ? v.problems.find((x) => x.fix?.entry === t.slug)?.stops ?? 'Has a problem.' : 'Brief written; settings check out.'} <span class="slug">{t.repo}</span></span>
                      <span class="r-side"><a class={`btn small ${bad ? '' : 'quiet'}`} href={`#template-${t.slug}`}>{bad ? 'Fix' : 'Settings'}</a></span>
                    </li>
                  );
                })}
              </ul>
            ) : <p class="footnote">{v.computed ? 'No assignment templates yet.' : 'Assignment templates appear once the course has been checked.'}</p>}
          </section>
          <section class="panel section" id="sec-materials">
            <div class="section-head"><h2>Materials</h2><a class="btn small outline" href={`?course=${course.org}#new-materials`}>New materials</a></div>
            {v.course?.materials?.length ? (
              <ul class="rows">
                {v.course.materials.map((m) => (
                  <li>
                    <span class="r-title">{m.repo} <span class={`chip ${m.state === 'ready' ? 'ok' : 'amber'}`}>{m.state === 'ready' ? 'Ready' : 'Not written'}</span></span>
                    <span class="r-sub">{m.state === 'ready' ? 'Syllabus written.' : 'The syllabus is still the template text; students would see it at the first release.'}</span>
                    <span class="r-side"><a class="btn small quiet" href={`#materials-${m.repo}`}>Settings</a></span>
                  </li>
                ))}
              </ul>
            ) : <p class="footnote">{v.computed ? 'No materials repos yet.' : 'Materials appear once the course has been checked.'}</p>}
          </section>
        </div>
        <div class="grid-2">
          <section class="panel section">
            <div class="section-head"><h2>Course details</h2><a class="btn small quiet" href="#details">Edit course details</a></div>
            <dl class="kv">
              <dt>Name</dt><dd>{course.name}</dd>
              <dt>Code</dt><dd>{course.code || 'not set'}</dd>
              <dt>Admins</dt><dd>{course.admins.join(', ') || 'none'}</dd>
              <dt>Late work</dt><dd>{late} per day, up to {lateDays} days</dd>
              <dt>Team size</dt><dd>Up to {team}</dd>
            </dl>
            <Lives org={course.org} repo=".github" path="dsl-course.yml" />
          </section>
          <div class="stack">
            <section class="panel section">
              <div class="section-head"><h2>Public website</h2><span class={`chip ${pub ? 'ok' : ''}`}>{pub ? 'Published' : 'Not published'}</span></div>
              <p style="color:var(--ink-2)">Optional: an open version of your materials for anyone, updated daily.</p>
              <a class="textlink" href="#website">Public website settings</a>
            </section>
          </div>
        </div>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- S17

const gradingValid = validator(gradingSchema);

function Questions({ rows, set }: { rows: [string, string][]; set: (r: [string, string][]) => void }) {
  const total = rows.reduce((s, [, n]) => s + (Number(n) || 0), 0);
  return (
    <div class="field">
      <span class="label">Points per question</span>
      {rows.length ? (
        <table class="qtable">
          <thead><tr><th>Question</th><th>Points</th><th /></tr></thead>
          <tbody>
            {rows.map(([q, n], i) => (
              <tr>
                <td><input type="text" value={q} aria-label={`Question ${i + 1} name`} onInput={(e) => set(rows.map((r, j) => (j === i ? [(e.target as HTMLInputElement).value, r[1]] : r)))} /></td>
                <td><input type="number" min="0" value={n} aria-label={`Question ${i + 1} points`} style="max-width:90px" onInput={(e) => set(rows.map((r, j) => (j === i ? [r[0], (e.target as HTMLInputElement).value] : r)))} /></td>
                <td><button class="x" type="button" aria-label={`Remove question ${i + 1}`} onClick={() => set(rows.filter((_, j) => j !== i))}>&times;</button></td>
              </tr>
            ))}
          </tbody>
          <tfoot><tr><td>Total</td><td>{total}</td><td /></tr></tfoot>
        </table>
      ) : <div class="readonly">Not set: the mark sheet takes one flat score.</div>}
      <div><button class="btn small quiet" type="button" onClick={() => set([...rows, [`Q${rows.length + 1}`, '']])}>Add a question</button></div>
      <p class="why">The mark sheet gets one column per question.</p>
    </div>
  );
}

export function TemplateScreen(p: CourseProps) {
  const { course, entry } = p;
  const env = useEnv();
  const slug = entry ?? '';
  const v = courseView(p);
  const fromCourse = v.course?.templates?.find((t) => t.slug === slug)?.repo;
  const fromCohort = Object.values(p.cohortStates)
    .flatMap((l) => (l.kind === 'ready' ? l.status.assignments ?? [] : []))
    .find((a) => a.slug === slug)?.template;
  const repo = fromCourse ?? fromCohort ?? slug;
  const file = p.files.file(course.org, repo, 'grading_config.yml', 'solution');
  const problems = v.problems.filter((x) => x.fix?.entry === slug);
  const [values, setValues] = useState<Values | null>(null);
  const [qdraft, setQdraft] = useState<[string, string][] | null>(null);
  const [save, runSave, setSave] = useSave(env);
  let cfg: Record<string, unknown> = {};
  let parseError = '';
  if (file.kind === 'ready') {
    const y = new YamlText(file.text);
    if (y.errors.length) parseError = y.errors[0];
    else cfg = (y.toJS() ?? {}) as Record<string, unknown>;
  }
  const defaults = courseDefaults(course.meta);
  const newest = course.cohorts[0];
  const tiers = settingsTiers(defaults, newest ? `?cohort=${newest.org}#teams-${slug}` : undefined);
  const base = fromConfig(cfg);
  const cur = values ?? base;
  const baseQ: [string, string][] = cfg.questions && typeof cfg.questions === 'object' ? Object.entries(cfg.questions as Record<string, unknown>).map(([q, n]) => [q, String(n ?? '')]) : [];
  const q = qdraft ?? baseQ;
  const errors = fieldErrors(null, tiers, cur);
  const dirty = (values !== null && !deepEqual(effective(tiers, values), effective(tiers, base))) || (qdraft !== null && !deepEqual(qdraft, baseQ));
  const title = String(cfg.title ?? '');
  const scope = courseScope(p);
  const doSave = async () => {
    if (file.kind !== 'ready') return;
    if (Object.keys(errors).length) return setSave({ kind: 'bad', text: 'Fix the fields marked in red first.' });
    const y = new YamlText(file.text);
    const was = toConfig(effective(tiers, base)), now = toConfig(effective(tiers, cur));
    for (const k of Object.keys({ ...was, ...now })) if (!deepEqual(was[k], now[k])) y.assign([k], now[k]);
    if (qdraft !== null && !deepEqual(qdraft, baseQ)) {
      const rows = qdraft.filter(([name]) => name.trim());
      y.assign(['questions'], rows.length ? Object.fromEntries(rows.map(([name, n]) => [name.trim(), n === '' ? null : Number.isFinite(Number(n)) ? Number(n) : n])) : undefined);
    }
    if (!gradingValid(y.toJS())) return setSave({ kind: 'bad', text: invalidText('grading_config.yml', gradingValid) });
    if (await runSave({ owner: course.org, repo, path: 'grading_config.yml', branch: 'solution' }, y.text, file.sha, { message: `template: edit the settings, from the Instructor Console`, statusRepo: [course.org, '.github'] })) {
      setValues(null);
      setQdraft(null);
    }
  };
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Assignment templates', href: '#templates' }, { t: assignmentIdent(slug) }]} />
      <div class="page-head">
        <div><h1>{assignmentIdent(slug)}{title ? `: ${title}` : ''}</h1><p class="lede">Assignment template settings. <span class="slug">{repo}</span></p></div>
        <div class="actions"><span class={`chip ${problems.length ? 'bad' : 'ok'}`}>{problems.length ? 'Has a problem' : 'Ready'}</span></div>
      </div>
      <Help title="What these settings do" doc="03-add-assignment-to-course.md">
        <p>One assignment template per assignment. Students get a copy at hand out; marking reads its solution branch. These settings apply to every cohort that uses the template; after hand out they reach students only through Update every copy.</p>
      </Help>
      {problems.length ? <div style="margin-bottom:18px"><ProblemCards list={problems} /></div> : null}
      {file.kind === 'loading' ? <Loading what="Reading grading_config.yml" /> : null}
      {file.kind === 'absent' ? <CheckLine cls="bad">There is no grading_config.yml on the solution branch of {repo}.</CheckLine> : null}
      {parseError ? <CheckLine cls="bad">grading_config.yml does not parse: {parseError}</CheckLine> : null}
      {file.kind === 'ready' && !parseError ? (
        <div class="panel">
          <div class="form">
            <div class="form-section">
              <h3>What it is</h3>
              <SchemaForm id="g1" schema={null} tiers={{ title: tiers.title }} values={cur} onChange={(nv) => setValues({ ...cur, ...nv })} />
              <Lives org={course.org} repo={repo} path="README.md" />
            </div>
            <div class="form-section">
              <h3>How students work on it</h3>
              <SchemaForm id="g2" schema={null} tiers={pick(tiers, ['type', 'team_formation', 'max_team_size', 'submit_via', 'submit_url', 'visibility'])} values={cur} onChange={(nv) => setValues({ ...cur, ...nv })} />
            </div>
            <div class="form-section">
              <h3>How it is marked</h3>
              <SchemaForm id="g3" schema={null} tiers={pick(tiers, ['format', 'autograde', 'tests'])} values={cur} onChange={(nv) => setValues({ ...cur, ...nv })} />
              <Questions rows={q} set={(r) => { setQdraft(r); setSave({ kind: 'idle' }); }} />
              <SchemaForm id="g4" schema={null} tiers={pick(tiers, ['completion_check', 'grader_pdf', 'late_window_days', 'late_penalty_per_day'])} values={cur} onChange={(nv) => setValues({ ...cur, ...nv })} advancedOpen={!!errors.late_window_days || !!errors.late_penalty_per_day} />
              <p class="lives"><a href={ghUrl(course.org, repo, 'grading_config.yml', 'solution')} target="_blank" rel="noopener">Lives in {`${course.org}/${repo}/grading_config.yml`}</a> on the solution branch.</p>
            </div>
            <div class="form-section">
              <h3>Student version</h3>
              <p style="font-size:14px;color:var(--ink-2)">Builds the student starter on main from the solution branch, removing marked answers.</p>
              <div class="actions"><OpButtons def={derive(scope, slug, repo, `${assignmentIdent(slug)}${title ? `: ${title}` : ''}`)} small /></div>
            </div>
            <div class="form-section">
              <SaveBar state={save} onSave={() => void doSave()} disabled={!dirty} file={{ org: course.org, repo, path: 'grading_config.yml', branch: 'solution' }} />
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}

function pick(t: Tiers, keys: string[]): Tiers {
  return Object.fromEntries(keys.filter((k) => t[k]).map((k) => [k, t[k]]));
}
