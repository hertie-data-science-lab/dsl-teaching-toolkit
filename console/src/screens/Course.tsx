// S2 Course overview and S17 Template settings (read).

import { useState } from 'preact/hooks';
import { parse } from 'yaml';
import { assignmentIdent } from '../model/format';
import type { CourseStatus, Problem } from '../model/types';
import { CheckLine, Crumbs, EditFile, Help, Legend, Lives, Loading, ProblemCards, Probs, Rail, Soon } from '../ui/bits';
import { Ext, Lock } from '../ui/icons';
import { CheckNow } from './common';
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
      <Soon label="New cohort" cls={ready ? 'btn' : 'btn quiet'} />
      <Soon label="Publish website" cls="btn outline" />
      <CheckNow />
      <a class="btn quiet" href={`https://github.com/${course.org}`} target="_blank" rel="noopener">Course on GitHub <Ext /></a>
    </div>
  );
}

function dflt(meta: Record<string, unknown> | null, key: string): string {
  const d = (meta?.assignment_defaults ?? {}) as Record<string, unknown>;
  return d[key] == null ? '' : String(d[key]);
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
        <p>One template per assignment. Students get a copy at hand out; marking reads its solution branch.</p>
      </Help>
      <div class="stack">
        {showSetup && v.course ? (
          <section class="panel section"><div class="section-head"><h2>Setup</h2><Legend /></div><Rail scope="course" stages={v.course.stages} problems={v.problems} /></section>
        ) : null}
        {v.computed ? (
          <section class="section">
            <div class="problems-head"><h2>Problems</h2><span class="footnote">Course problems also appear on every cohort they will affect.</span></div>
            <ProblemCards list={v.problems} />
          </section>
        ) : null}
        <div class="grid-2">
          <section class="panel section" id="sec-templates">
            <div class="section-head"><h2>Templates</h2><Soon label="New assignment" cls="btn small outline" /></div>
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
            ) : <p class="footnote">{v.computed ? 'No assignment templates yet.' : 'Templates appear once the course has been checked.'}</p>}
          </section>
          <section class="panel section" id="sec-materials">
            <div class="section-head"><h2>Materials</h2><Soon label="New materials" cls="btn small outline" /></div>
            {v.course?.materials?.length ? (
              <ul class="rows">
                {v.course.materials.map((m) => (
                  <li>
                    <span class="r-title">{m.repo} <span class={`chip ${m.state === 'ready' ? 'ok' : 'amber'}`}>{m.state === 'ready' ? 'Ready' : 'Not written'}</span></span>
                    <span class="r-sub">{m.state === 'ready' ? 'Syllabus written.' : 'The syllabus is still the template text; students would see it at the first release.'}</span>
                    <span class="r-side"><a class="btn small quiet" href={`https://github.com/${course.org}/${m.repo}`} target="_blank" rel="noopener">Open <Ext /></a></span>
                  </li>
                ))}
              </ul>
            ) : <p class="footnote">{v.computed ? 'No materials repos yet.' : 'Materials appear once the course has been checked.'}</p>}
          </section>
        </div>
        <div class="grid-2">
          <section class="panel section">
            <div class="section-head"><h2>Course details</h2><Soon label="Edit course details" cls="btn small quiet" /></div>
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
            <section class="panel section">
              <div class="section-head"><h2>Public website</h2><span class={`chip ${pub ? 'ok' : ''}`}>{pub ? 'Published' : 'Not published'}</span></div>
              <p style="color:var(--ink-2)">Optional: an open version of your materials for anyone, updated daily.</p>
              {pub ? <a class="textlink" href={`https://${course.org}.github.io`} target="_blank" rel="noopener">{course.org}.github.io</a> : null}
            </section>
          </div>
        </div>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- S17

const FORMAT_WORD: Record<string, string> = { ipynb: 'Jupyter notebook', py: 'Python files', rmd: 'R Markdown', qmd: 'Quarto', latex: 'LaTeX', none: 'No starter file' };
const SUBMIT_WORD: Record<string, string> = { assignment_repo: 'Their own repo', shared_dropbox_repo: 'A shared drop box', external: 'Elsewhere', github: 'Their own repo' };
const VIS_WORD: Record<string, string> = { private: 'Private', public: 'Public', student_choice: 'Student’s choice' };
const yesNo = (v: unknown) => (v === true ? 'On' : v === false ? 'Off' : v == null ? '' : String(v));

function Field({ label, value, hint, bad }: { label: string; value: string; hint?: string; bad?: boolean }) {
  return (
    <div class="field">
      <span class="label">{label}{hint ? <span class="default"> {hint}</span> : null}</span>
      <div class="readonly" style={bad ? 'background:var(--bad-soft);color:var(--bad-ink)' : undefined}>{value || '—'}</div>
    </div>
  );
}

export function TemplateScreen(p: CourseProps) {
  const { course, entry } = p;
  const slug = entry ?? '';
  const v = courseView(p);
  const fromCourse = v.course?.templates?.find((t) => t.slug === slug)?.repo;
  const fromCohort = Object.values(p.cohortStates)
    .flatMap((l) => (l.kind === 'ready' ? l.status.assignments ?? [] : []))
    .find((a) => a.slug === slug)?.template;
  const repo = fromCourse ?? fromCohort ?? slug;
  const file = p.files.file(course.org, repo, 'grading_config.yml', 'solution');
  const problems = v.problems.filter((x) => x.fix?.entry === slug);
  let cfg: Record<string, unknown> = {};
  let parseError = '';
  if (file.kind === 'ready') {
    try {
      cfg = (parse(file.text) ?? {}) as Record<string, unknown>;
    } catch (e) {
      parseError = e instanceof Error ? e.message : String(e);
    }
  }
  const s = (k: string) => (cfg[k] == null ? '' : String(cfg[k]));
  const group = s('type') === 'group';
  const submit = s('submit_via') || 'assignment_repo';
  const questions = cfg.questions && typeof cfg.questions === 'object' ? Object.entries(cfg.questions as Record<string, unknown>) : [];
  const total = questions.reduce((a, [, n]) => a + (Number(n) || 0), 0);
  const autograde = cfg.autograde;
  const title = s('title');
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Templates', href: '#templates' }, { t: assignmentIdent(slug) }]} />
      <div class="page-head">
        <div><h1>{assignmentIdent(slug)}{title ? `: ${title}` : ''}</h1><p class="lede">Template settings. <span class="slug">{repo}</span></p></div>
        <div class="actions"><span class={`chip ${problems.length ? 'bad' : 'ok'}`}>{problems.length ? 'Has a problem' : 'Ready'}</span></div>
      </div>
      <Help title="What these settings do" doc="03-add-assignment-to-course.md">
        <p>One template per assignment. Students get a copy at hand out; marking reads its solution branch. These settings apply to every cohort that uses the template; after hand out they reach students only through Update every copy.</p>
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
              <Field label="Title" value={title} hint="shown to students on the site and in their repo" />
              <Lives org={course.org} repo={repo} path="README.md" />
            </div>
            <div class="form-section">
              <h3>How students work on it</h3>
              <Field label="Alone or in teams" value={group ? 'In teams' : 'Alone'} />
              {group ? (
                <div class="cond">
                  <Field label="How teams form" value={s('team_formation') === 'assigned' ? 'You assign them' : 'Students form their own'} />
                  <Field label="Largest team" value={s('max_team_size') || `course default: ${dflt(course.meta, 'max_team_size') || '5'}`} />
                </div>
              ) : null}
              <Field label="Where students submit" value={SUBMIT_WORD[submit] ?? submit} />
              {submit === 'external' ? <div class="cond"><Field label="Link to where they submit" value={s('submit_url')} /></div> : null}
              <div class="field">
                <span class="label">Who can see each student’s repo</span>
                <div class="readonly"><Lock />{submit === 'assignment_repo' ? `${VIS_WORD[s('visibility') || 'private'] ?? s('visibility')}. Set when the template was created; cannot be changed.` : submit === 'shared_dropbox_repo' ? 'Private: a shared drop box is always private.' : 'Private: the repo holds the brief only.'}</div>
              </div>
            </div>
            <div class="form-section">
              <h3>How it is marked</h3>
              <Field label="What students hand in" value={FORMAT_WORD[s('format')] ?? (s('format') || 'Jupyter notebook')} />
              <Field label="Run automatic tests on submissions" value={yesNo(autograde)} bad={autograde !== undefined && typeof autograde !== 'boolean'} />
              {autograde === true ? <div class="cond"><Field label="Tests folder" value="tests" hint="on the solution branch; students never see it" /></div> : null}
              <div class="field">
                <span class="label">Points per question</span>
                {questions.length ? (
                  <table class="qtable">
                    <thead><tr><th>Question</th><th>Points</th></tr></thead>
                    <tbody>{questions.map(([q, n]) => <tr><td>{q}</td><td>{String(n)}</td></tr>)}</tbody>
                    <tfoot><tr><td>Total</td><td>{total}</td></tr></tfoot>
                  </table>
                ) : <div class="readonly">Not set: the mark sheet takes one flat score.</div>}
              </div>
              <details class="fold">
                <summary>Advanced</summary>
                <div class="fold-body">
                  <Field label="Completion check" value={yesNo(cfg.completion_check) || 'auto'} />
                  <Field label="Marker PDF" value={yesNo(cfg.grader_pdf) || 'Off'} />
                  <Field label="Late work for this assignment" value={s('late_window_days') || s('late_penalty_per_day') ? `${s('late_penalty_per_day') || '10%'} per day, up to ${s('late_window_days') || '10'} days` : 'course default'} />
                </div>
              </details>
              <Lives org={course.org} repo={repo} path="grading_config.yml" branch="solution" />
              <p class="footnote">On the solution branch.</p>
            </div>
            <div class="form-section">
              <h3>Student version</h3>
              <p style="font-size:14px;color:var(--ink-2)">Builds the student starter on main from the solution branch, removing marked answers.</p>
              <div class="actions"><Soon label="Preview" cls="btn small" /><Soon label="Derive student version" cls="btn small outline" /></div>
            </div>
            <div class="form-section">
              <div class="savebar"><Soon label="Save" title="Coming in this build: the template settings form." /><EditFile org={course.org} repo={repo} path="grading_config.yml" branch="solution" /></div>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
