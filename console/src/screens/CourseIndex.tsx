// The course's Handout materials and Assignment templates index screens.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { YamlText, obj } from '../edit/yamlText';
import type { GhRepo } from '../github/client';
import { TEMPLATE_TOPIC, termOf } from '../model/discovery';
import type { Files } from '../model/files';
import { ago, fmtDay, templateName } from '../model/format';
import { MATERIALS_TOPIC } from '../model/materialsRules';
import { DEFAULT_FORMATS } from '../model/policy';
import { formatWord, formatsList } from '../tiers/grading';
import { CheckLine, Crumbs, Loading } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { Ext } from '../ui/icons';
import { OpenButton } from '../ui/OpenButton';
import { StateChip, Whys, courseView } from './Course';
import type { CourseProps } from './types';
import { COURSE_REPO } from '../model/names';

/** "Fall 2026" from a repo named `...-f2026`, else null. */
function termLabel(repo: string): string | null {
  return /-[fswu]\d{4}$/.test(repo) ? termOf(repo).label : null;
}

/**
 * The course org's repos that are none of infra, materials or templates, but that a schedule
 * can still release from: not archived, not `.github` or the org's `.github.io`, not an
 * assignment template (by topic, decision 0014) and not a repo the course status already
 * lists (its materials repos and templates, and any not migrated yet).
 */
export function otherRepos(org: string, repos: GhRepo[], known: string[]): GhRepo[] {
  const skip = new Set([COURSE_REPO, `${org}.github.io`.toLowerCase(), ...known.map((k) => k.toLowerCase())]);
  return repos
    .filter((r) => !r.archived && !skip.has(r.name.toLowerCase()) && !r.topics?.includes(TEMPLATE_TOPIC))
    .sort((a, b) => a.name.localeCompare(b.name));
}

/** The org's repo list on GitHub, filtered to one topic. */
export const topicUrl = (org: string, topic: string) => `https://github.com/orgs/${org}/repositories?q=${encodeURIComponent(`topic:${topic}`)}`;

function repoOf(files: Files, org: string, name: string): GhRepo | undefined {
  const r = files.repos(org);
  return r.kind === 'ready' ? r.repos.find((x) => x.name === name) : undefined;
}

// --------------------------------------------------------------------------- materials

/** What "Treat as handout materials" says once it has run, or why it could not. */
type TreatState = { kind: 'idle' | 'busy' | 'done' } | { kind: 'bad'; text: string };

/**
 * One Other repos row (decision 0026 rule 2): Open, and on a course the person can change,
 * "Treat as handout materials", which adds the `dsl-materials` topic to the repo's topics as
 * GitHub has them at the click.
 */
export function OtherRepoRow({ org, repo, write }: { org: string; repo: GhRepo; write: boolean }) {
  const env = useEnv();
  const [st, setSt] = useState<TreatState>({ kind: 'idle' });
  const treat = async () => {
    if (!env) return;
    setSt({ kind: 'busy' });
    try {
      // Read the topics just before the write: the repo list may be old, and a PUT replaces them all.
      const topics = await env.client.getRepoTopics(org, repo.name);
      await env.client.setTopics(org, repo.name, [...new Set([...topics, MATERIALS_TOPIC])]);
      setSt({ kind: 'done' });
    } catch (e) {
      setSt({ kind: 'bad', text: `Could not add the topic: ${e instanceof Error ? e.message : String(e)}` });
    }
  };
  return (
    <li>
      <span class="r-title">{repo.name}</span>
      <span class="r-sub">{st.kind === 'done' ? 'Added. It shows under Handout materials after the next Refresh.' : 'Not handout materials, so nothing to set up here. Can be released to a semester from the schedule.'}</span>
      {st.kind === 'bad' ? <CheckLine cls="bad">{st.text}</CheckLine> : null}
      <span class="r-side">
        <OpenButton org={org} repo={repo.name} small quiet />
        {write && st.kind !== 'done' ? <button class="btn small quiet" type="button" disabled={st.kind === 'busy' || !env} onClick={() => void treat()}>Treat as handout materials</button> : null}
      </span>
    </li>
  );
}

export function MaterialsIndexScreen(p: CourseProps) {
  const { course, files } = p;
  const v = courseView(p);
  const materials = v.course?.materials ?? [];
  const repos = files.repos(course.org);
  const known = [...materials.map((m) => m.repo), ...(v.course?.templates ?? []).map((t) => t.repo)];
  const others = repos.kind === 'ready' ? otherRepos(course.org, repos.repos, known) : [];
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Handout materials' }]} />
      <div class="page-head">
        <div><h1>Handout materials <Hint doc="02-add-materials-to-course.md">Handout materials live here privately until a scheduled release copies them to a semester. Files can be withheld from students.</Hint></h1><p class="lede">The course’s handout materials repos. A scheduled or manual release copies their folders to a semester. This page checks that the set-up files are in place, not their content; change content by pushing to the repo’s main branch.</p></div>
        <div class="actions"><a class="btn quiet" href={topicUrl(course.org, MATERIALS_TOPIC)} target="_blank" rel="noopener">See on GitHub <Ext /></a></div>
      </div>
      <div class="stack">
        <section class="panel section">
          <div class="section-head"><h2>Handout materials repos</h2><a class="btn small outline" href={`?course=${course.org}#new-materials`}>New handout materials</a></div>
          {materials.length ? (
            <ul class="rows">
              {materials.map((m) => {
                const gh = repoOf(files, course.org, m.repo);
                const term = termLabel(m.repo);
                return (
                  <li>
                    <span class="r-title">{m.repo} <StateChip state={m.state} todo="Not ready yet" />{term ? <span class="chip term">{term}</span> : null}</span>
                    <Whys m={m} />
                    {gh?.pushed_at ? <span class="r-sub">Last change {fmtDay(gh.pushed_at)} ({ago(gh.pushed_at, p.now)}).</span> : null}
                    <span class="r-side"><OpenButton org={course.org} repo={m.repo} small quiet /><a class="btn small quiet" href={`#materials-${m.repo}`}>Settings</a></span>
                  </li>
                );
              })}
            </ul>
          ) : <p class="footnote">{v.computed ? 'No handout materials repos yet.' : 'Handout materials appear once the course has been checked.'}</p>}
        </section>
        <section class="panel section">
          <h2>Other repos</h2>
          <p class="footnote">Repos in the course that are neither handout materials nor assignment templates. Can be released to a semester from the schedule.</p>
          {repos.kind === 'loading' ? <Loading what="Listing the course’s repos" /> : others.length ? (
            <ul class="rows">
              {others.map((r) => <OtherRepoRow org={course.org} repo={r} write={course.write} />)}
            </ul>
          ) : <p class="footnote">{repos.kind === 'absent' ? 'Could not list the course’s repos.' : 'No other repos.'}</p>}
        </section>
      </div>
    </>
  );
}

// --------------------------------------------------------------------------- templates


/** The semesters whose schedules use `repo`, as loaded: "Fall 2026", newest first as the course lists them. */
export function usedIn(p: Pick<CourseProps, 'course' | 'cohortStates'>, repo: string): string[] {
  return p.course.cohorts.filter((k) => {
    const l = p.cohortStates[k.org];
    return l?.kind === 'ready' && (l.status.assignments ?? []).some((a) => a.template === repo);
  }).map((k) => k.termLabel);
}

export const VERSIONS_HINT =
  'A template is reused every semester. Each hand-out freezes a copy in that semester, so edits here never change one already handed out. Want a different variant? Copy it: New assignment can start from it.';

export function TemplatesIndexScreen(p: CourseProps) {
  const { course, files } = p;
  const v = courseView(p);
  const templates = v.course?.templates ?? [];
  return (
    <>
      <Crumbs items={[{ t: course.name, href: '#course' }, { t: 'Assignment templates' }]} />
      <div class="page-head">
        <div><h1>Assignment templates <Hint label="About templates and semesters">{VERSIONS_HINT}</Hint></h1><p class="lede">The course’s assignment templates. A scheduled or manual hand out gives students a copy in a semester. This page checks how each assignment is worked and marked, not its content; change content by pushing to the repo.</p></div>
        <div class="actions"><a class="btn quiet" href={topicUrl(course.org, TEMPLATE_TOPIC)} target="_blank" rel="noopener">See on GitHub <Ext /></a></div>
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
              const used = usedIn(p, t.repo);
              return (
                <li>
                  <span class="r-title">{templateName(title)} <StateChip state={t.state} todo="Not written yet" /></span>
                  <span class={`r-sub${bad ? ' flag' : ''}`}>
                    {bad ? `${v.problems.find((x) => x.fix?.entry === t.repo)?.stops ?? 'Has a problem.'} ` : t.state !== 'ready' ? 'The brief (README.md) is not written yet. ' : ''}
                    {how.length ? `${how.join(', ')}. ` : ''}
                    <span class="slug">{t.repo}</span>
                  </span>
                  <span class="r-used">{used.length ? `Used in ${used.join(', ')}` : 'Not used in a semester yet'}</span>
                  <span class="r-side"><OpenButton org={course.org} repo={t.repo} small quiet /><a class={`btn small ${bad ? '' : 'quiet'}`} href={`#template-${t.repo}`}>{bad ? 'Fix' : 'Settings'}</a></span>
                </li>
              );
            })}
          </ul>
        ) : <p class="footnote">{v.computed ? 'No assignment templates yet.' : 'Templates appear once the course has been checked.'}</p>}
      </section>
    </>
  );
}
