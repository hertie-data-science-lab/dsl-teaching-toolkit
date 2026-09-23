// S14 Site (read) and S18 Operations list.

import { ago, fmtWhen } from '../model/format';
import { parsePeople } from '../model/people';
import type { Outcome } from '../model/types';
import { CheckLine, Crumbs, EditFile, Help, Lives, Loading, Md, OpsList, Prop, Soon } from '../ui/bits';
import { Ext } from '../ui/icons';
import { tzOf, yearOf } from './Cohort';
import { WithStatus, cohortCrumbs } from './common';
import type { CohortProps, ReadyProps } from './types';

/** index.md without its Jekyll front matter. */
export function stripFrontMatter(text: string): string {
  return text.replace(/^---\n[\s\S]*?\n---\n?/, '');
}

function Site(p: ReadyProps) {
  const { status, now } = p;
  const tz = tzOf(status), year = yearOf(now, tz);
  const repo = `${p.cohort.org}.github.io`;
  const url = status.site?.url ?? `https://${repo}`;
  const home = p.files.file(p.cohort.org, repo, 'index.md');
  const ann = p.files.dir(p.cohort.org, repo, '_announcements');
  const peopleFile = p.files.file(p.cohort.org, 'classroom-config', 'people.yml');
  const people = peopleFile.kind === 'ready' ? parsePeople(peopleFile.text) : [];
  const site = status.site;
  const section = (path: string) => (
    <div class="savebar"><Soon label="Save" cls="btn small" /><EditFile org={p.cohort.org} repo={repo} path={path} /></div>
  );
  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Site')} />
      <div class="page-head">
        <div><h1>Student site</h1><p class="lede">Students’ single page for the term. Almost everything on it comes from the schedule, staff and materials.</p></div>
        <div class="actions">
          <Soon label="Preview" /><Prop /><Soon label="Update site" cls="btn outline" />
          <a class="btn quiet" href={url} target="_blank" rel="noopener">Open the student site <Ext /></a>
        </div>
      </div>
      <Help title="What you edit here" doc="11-configure-cohort-site.md">
        <p>The home text, announcements, the late policy and past offerings are yours. The schedule, lectures, assignments and staff pages are generated and rewritten on every update.</p>
      </Help>
      <div class="stack">
        <section class="panel section">
          <h2>Last update</h2>
          {site?.stale ? (
            <CheckLine cls="bad"><b>Out of date.</b> The last update was {site.last_update ? `${fmtWhen(site.last_update, tz, year)} (${ago(site.last_update, now)})` : 'never'}, before the latest change to the schedule, staff or materials. Update site brings it up to date.</CheckLine>
          ) : (
            <CheckLine cls="ok">Up to date{site?.last_update ? `; last updated ${fmtWhen(site.last_update, tz, year)}` : ''}.</CheckLine>
          )}
        </section>
        <section class="panel section">
          <h2>Home text</h2>
          {home.kind === 'loading' ? <Loading what="Reading the home page" /> : home.kind === 'ready' ? (
            <div class="md-prev"><span class="lbl">What students see</span><Md src={stripFrontMatter(home.text)} /></div>
          ) : <p class="footnote">No home page text yet.</p>}
          {section('index.md')}
          <Lives org={p.cohort.org} repo={repo} path="index.md" />
        </section>
        <section class="panel section">
          <div class="section-head"><h2>Announcements</h2><span class="meta">Shown in the Updates box with released sessions and hand outs</span></div>
          {ann.kind === 'ready' && ann.entries.length ? (
            <ul class="rows">{ann.entries.filter((e) => e.type === 'file').map((e) => <li><span class="r-title">{e.name.replace(/\.md$/, '')}</span></li>)}</ul>
          ) : ann.kind === 'loading' ? <Loading /> : <p class="footnote">No announcements.</p>}
          <div><Soon label="Add an announcement" cls="btn small quiet" /></div>
          <Lives org={p.cohort.org} repo={repo} path="_announcements" />
        </section>
        <section class="panel section">
          <h2>Staff photos</h2>
          <ul class="rows">
            {people.map((x) => (
              <li>
                <span class="r-title">{x.name || x.handle} {x.photo ? <span class="chip ok">Photo</span> : <span class="chip">No photo</span>}</span>
                <span class="r-sub">{x.photo || 'Add one on Staff; upload the image to the site repo’s images folder.'}</span>
                <span class="r-side"><a class="btn small quiet" href="#staff">Staff</a></span>
              </li>
            ))}
          </ul>
        </section>
        <section class="panel section">
          <h2>Site settings</h2>
          <dl class="kv">
            <dt>Course name</dt><dd>{p.course.name}{p.course.code ? ` (${p.course.code})` : ''} <span class="footnote">rewritten on every update</span></dd>
            <dt>Term</dt><dd>{p.cohort.termLabel} <span class="footnote">rewritten</span></dd>
            <dt>GitHub org</dt><dd>{p.cohort.org} <span class="footnote">rewritten</span></dd>
          </dl>
          <Lives org={p.cohort.org} repo={repo} path="_config.yml" />
        </section>
      </div>
    </>
  );
}

export function SiteScreen(p: CohortProps) {
  return <WithStatus props={p} title="Student site" crumbs={cohortCrumbs(p, 'Site')}>{(r) => <Site {...r} />}</WithStatus>;
}

function Operations(p: ReadyProps) {
  const ops = p.status.operations ?? [];
  const outcomes: Record<string, Outcome | undefined> = {};
  for (const op of new Set(ops.map((o) => o.op))) {
    const f = p.files.file(p.cohort.org, 'classroom-config', `.dsl/outcomes/${op}.json`);
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
      <Crumbs items={cohortCrumbs(p, 'All operations')} />
      <div class="page-head">
        <div><h1>All operations</h1><p class="lede">Everything automation and you have done in {p.cohort.termLabel}, newest first. Outcomes stay here after the panel closes.</p></div>
      </div>
      <Help title="Reading an outcome" doc="reference/actions-reference.md">
        <p>Each line says what happened and how many. Open Details for the reason codes behind a count, and the run on GitHub.</p>
      </Help>
      <section class="panel"><OpsList list={ops} now={p.now} full runRepo={`${p.course.org}/.github`} outcomes={outcomes} /></section>
    </>
  );
}

export function OperationsScreen(p: CohortProps) {
  return <WithStatus props={p} title="All operations" crumbs={cohortCrumbs(p, 'All operations')}>{(r) => <Operations {...r} />}</WithStatus>;
}
