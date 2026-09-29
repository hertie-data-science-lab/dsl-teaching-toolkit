// The course's Materials and Assignment templates index screens.

import { YamlText, obj } from '../edit/yamlText';
import type { GhRepo } from '../github/client';
import { termOf } from '../model/discovery';
import type { Files } from '../model/files';
import { ago, assignmentIdent, fmtDay } from '../model/format';
import { DEFAULT_FORMATS } from '../model/policy';
import { formatWord, formatsList } from '../tiers/grading';
import { Crumbs, Loading, ghUrl } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { Ext } from '../ui/icons';
import { OpenButton } from '../ui/OpenButton';
import { CourseHeaderActions, StateChip, courseView } from './Course';
import type { CourseProps } from './types';
import { COURSE_REPO } from '../model/names';

/** "Fall 2026" from a repo named `...-f2026`, else null. */
function termLabel(repo: string): string | null {
  return /-[fswu]\d{4}$/.test(repo) ? termOf(repo).label : null;
}

/**
 * The course org's repos that are none of infra, materials or templates, but that a schedule
 * can still release from: not archived, not `.github` or the org's `.github.io`, not
 * `assignment-*` and not a repo the course status already lists (its materials repos, by
 * topic, and any `course-materials-*` not migrated yet).
 */
export function otherRepos(org: string, repos: GhRepo[], known: string[]): GhRepo[] {
  const skip = new Set([COURSE_REPO, `${org}.github.io`.toLowerCase(), ...known.map((k) => k.toLowerCase())]);
  return repos
    .filter((r) => !r.archived && !skip.has(r.name.toLowerCase()) && !/^assignment-/i.test(r.name))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** Why a materials repo is or is not ready: the engine calls one ready once its syllabus is written. */
export function materialsSentence(state: string): string {
  return state === 'ready' ? 'Syllabus written.' : 'Not ready yet: the syllabus is not written.';
}

function repoOf(files: Files, org: string, name: string): GhRepo | undefined {
  const r = files.repos(org);
  return r.kind === 'ready' ? r.repos.find((x) => x.name === name) : undefined;
}

// --------------------------------------------------------------------------- materials

export function MaterialsIndexScreen(p: CourseProps) {
  const { course, files } = p;
  const v = courseView(p);
  const materials = v.course?.materials ?? [];
  const repos = files.repos(course.org);
  const known = [...materials.map((m) => m.repo), ...(v.course?.templates ?? []).map((t) => t.repo)];
  const others = repos.kind === 'ready' ? otherRepos(course.org, repos.repos, known) : [];
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Materials' }]} />
      <div class="page-head">
        <div><h1>Materials <Hint doc="02-add-materials-to-course.md">Materials live here privately until a scheduled release copies them to a semester. Files can be withheld from students.</Hint></h1><p class="lede">The course’s materials repos. A scheduled release copies their folders to a semester.</p></div>
        <CourseHeaderActions course={course} ready={v.course?.ready ?? false} />
      </div>
      <div class="stack">
        <section class="panel section">
          <div class="section-head"><h2>Materials repos</h2><a class="btn small outline" href={`?course=${course.org}#new-materials`}>New materials</a></div>
          {materials.length ? (
            <ul class="rows">
              {materials.map((m) => {
                const gh = repoOf(files, course.org, m.repo);
                const term = termLabel(m.repo);
                return (
                  <li>
                    <span class="r-title">{m.repo} <StateChip state={m.state} todo="Not ready yet" />{term ? <span class="chip term">{term}</span> : null}</span>
                    <span class="r-sub">
                      {materialsSentence(m.state)}
                      {gh?.pushed_at ? ` Last change ${fmtDay(gh.pushed_at)} (${ago(gh.pushed_at, p.now)}).` : ''}
                    </span>
                    <span class="r-side"><OpenButton org={course.org} repo={m.repo} small quiet /><a class="btn small quiet" href={`#materials-${m.repo}`}>Settings</a></span>
                  </li>
                );
              })}
            </ul>
          ) : <p class="footnote">{v.computed ? 'No materials repos yet.' : 'Materials appear once the course has been checked.'}</p>}
        </section>
        <section class="panel section">
          <h2>Other repos</h2>
          <p class="footnote">Repos in the course that are neither materials nor assignment templates. Can be released to a semester from the schedule.</p>
          {repos.kind === 'loading' ? <Loading what="Listing the course’s repos" /> : others.length ? (
            <ul class="rows">
              {others.map((r) => (
                <li>
                  <span class="r-title">{r.name}</span>
                  <span class="r-sub">Can be released to a semester from the schedule.</span>
                  <span class="r-side"><a class="btn small quiet" href={r.html_url || ghUrl(course.org, r.name)} target="_blank" rel="noopener">Open on GitHub <Ext /></a></span>
                </li>
              ))}
            </ul>
          ) : <p class="footnote">{repos.kind === 'absent' ? 'Could not list the course’s repos.' : 'No other repos.'}</p>}
        </section>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- templates


export function TemplatesIndexScreen(p: CourseProps) {
  const { course, files } = p;
  const v = courseView(p);
  const templates = v.course?.templates ?? [];
  const scheduledIn = (repo: string) =>
    course.cohorts.filter((k) => {
      const l = p.cohortStates[k.org];
      return l?.kind === 'ready' && (l.status.assignments ?? []).some((a) => a.template === repo);
    }).map((k) => k.termLabel);
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Assignment templates' }]} />
      <div class="page-head">
        <div><h1>Assignment templates</h1><p class="lede">One template per assignment. Students get a copy at hand out; marking reads its solution branch.</p></div>
        <CourseHeaderActions course={course} ready={v.course?.ready ?? false} />
      </div>
      <section class="panel section">
        <div class="section-head"><h2>Templates</h2><a class="btn small outline" href={`?course=${course.org}#new-assignment-1`}>New assignment</a></div>
        {templates.length ? (
          <ul class="rows">
            {templates.map((t) => {
              const bad = t.state === 'problem';
              const f = files.file(course.org, t.repo, 'grading_config.yml', 'solution');
              const y = f.kind === 'ready' ? new YamlText(f.text) : null;
              const cfg = y && !y.errors.length ? obj(y.toJS()) : {};
              const title = cfg.title ? String(cfg.title) : '';
              const how = y && !y.errors.length ? [cfg.type === 'group' ? 'In teams' : 'Alone', formatWord(formatsList(cfg.formats)[0] ?? DEFAULT_FORMATS[0])] : [];
              const term = termLabel(t.repo);
              const cohorts = scheduledIn(t.repo);
              return (
                <li>
                  <span class="r-title">{assignmentIdent(t.slug)}{title ? `: ${title}` : ''} <StateChip state={t.state} todo="Not written yet" />{term ? <span class="chip term">{term}</span> : null}</span>
                  <span class={`r-sub${bad ? ' flag' : ''}`}>
                    {bad ? `${v.problems.find((x) => x.fix?.entry === t.slug)?.stops ?? 'Has a problem.'} ` : t.state !== 'ready' ? 'The brief (README.md) is not written yet. ' : ''}
                    {how.length ? `${how.join(', ')}. ` : ''}
                    {cohorts.length ? `Scheduled in ${cohorts.join(', ')}. ` : ''}
                    <span class="slug">{t.repo}</span>
                  </span>
                  <span class="r-side"><OpenButton org={course.org} repo={t.repo} small quiet /><a class={`btn small ${bad ? '' : 'quiet'}`} href={`#template-${t.slug}`}>{bad ? 'Fix' : 'Settings'}</a></span>
                </li>
              );
            })}
          </ul>
        ) : <p class="footnote">{v.computed ? 'No assignment templates yet.' : 'Templates appear once the course has been checked.'}</p>}
      </section>
    </>
  );
}
