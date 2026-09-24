// S14 Site (home text and announcements, Update site) and S18 Operations list.

import { useState } from 'preact/hooks';
import { parse } from 'yaml';
import { useEnv } from '../env';
import { useSave } from '../edit/save';
import { render } from '../edit/yamlText';
import { Field } from '../forms/Form';
import { ago, fmtWhen } from '../model/format';
import { parsePeople } from '../model/people';
import type { Outcome } from '../model/types';
import { outcomePath } from '../ops/adapter';
import { updateSite } from '../ops/defs';
import { OpButtons } from '../ops/Panel';
import { CheckLine, Crumbs, Help, Lives, Loading, OpsList } from '../ui/bits';
import { SaveBar } from '../ui/edit';
import { Ext } from '../ui/icons';
import { WithStatus, cohortCrumbs, cohortScope, todayOf, tzOf, useOperations, yearOf } from './common';
import type { CohortProps, ReadyProps } from './types';

const FRONT = /^---\n([\s\S]*?)\n---\n?/;

/** index.md without its Jekyll front matter. */
export function stripFrontMatter(text: string): string {
  return text.replace(FRONT, '');
}

/** A hand-written announcement's date and text, from its front matter (or its body). */
export function readAnnouncement(text: string): { date: string; text: string } {
  const m = FRONT.exec(text);
  let fm: Record<string, unknown> = {};
  try {
    fm = (m ? parse(m[1]) : {}) ?? {};
  } catch {
    fm = {};
  }
  const body = stripFrontMatter(text).trim();
  return { date: fm.date == null ? '' : String(fm.date).slice(0, 10), text: fm.details == null ? body : String(fm.details) };
}

export function announcementFile(date: string, text: string): { path: string; content: string } {
  const slug = text.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '').split('-').slice(0, 6).join('-') || 'update';
  return { path: `_announcements/${date}-${slug}.md`, content: `---\n${render({ date, details: text })}---\n` };
}

function Announcement({ p, repo, path, sha }: { p: ReadyProps; repo: string; path: string; sha: string }) {
  const env = useEnv();
  const [save, runSave] = useSave(env);
  const f = p.files.file(p.cohort.org, repo, path);
  const a = f.kind === 'ready' ? readAnnouncement(f.text) : null;
  return (
    <li>
      <span class="r-title">{a?.date || path.replace(/^_announcements\//, '').slice(0, 10)}</span>
      <span class="r-sub">{a ? a.text : path.replace(/^_announcements\//, '').replace(/\.md$/, '')}</span>
      <span class="r-side">
        {save.kind !== 'idle' ? <CheckLine cls={save.kind === 'busy' ? 'busy' : save.kind}>{save.text}</CheckLine> : null}
        <button class="btn small quiet" type="button" disabled={save.kind === 'busy'} onClick={() => void runSave({ owner: p.cohort.org, repo, path }, null, f.kind === 'ready' ? f.sha : sha, { message: `site: remove the announcement ${path}, from the Instructor Console` })}>Remove</button>
      </span>
    </li>
  );
}

function Site(p: ReadyProps) {
  const { status, now } = p;
  const env = useEnv();
  const tz = tzOf(status), year = yearOf(now, tz);
  const repo = `${p.cohort.org}.github.io`;
  const url = status.site?.url ?? `https://${repo}`;
  const home = p.files.file(p.cohort.org, repo, 'index.md');
  const ann = p.files.dir(p.cohort.org, repo, '_announcements');
  const peopleFile = p.files.file(p.cohort.org, 'classroom-config', 'people.yml');
  const people = peopleFile.kind === 'ready' ? parsePeople(peopleFile.text) : [];
  const site = status.site;
  const [body, setBody] = useState<string | null>(null);
  const [homeSave, runHome] = useSave(env);
  const [newAnn, setNewAnn] = useState<{ date: string; text: string }>({ date: todayOf(now, tz), text: '' });
  const [annSave, runAnn, setAnnSave] = useSave(env);
  const homeText = home.kind === 'ready' ? stripFrontMatter(home.text) : '';
  const shown = body ?? homeText;
  const saveHome = async () => {
    if (home.kind !== 'ready' || body === null) return;
    const front = FRONT.exec(home.text)?.[0] ?? '';
    if (await runHome({ owner: p.cohort.org, repo, path: 'index.md' }, `${front}${body.endsWith('\n') ? body : `${body}\n`}`, home.sha, { message: 'site: edit the home text, from the Instructor Console' })) setBody(null);
  };
  const addAnn = async () => {
    if (!newAnn.text.trim() || !/^\d{4}-\d{2}-\d{2}$/.test(newAnn.date)) return setAnnSave({ kind: 'bad', text: 'Give the date and the text.' });
    const f = announcementFile(newAnn.date, newAnn.text.trim());
    if (await runAnn({ owner: p.cohort.org, repo, path: f.path }, f.content, null, { message: 'site: add an announcement, from the Instructor Console' })) {
      setNewAnn({ date: todayOf(now, tz), text: '' });
    }
  };
  return (
    <>
      <Crumbs items={cohortCrumbs(p, 'Site')} />
      <div class="page-head">
        <div><h1>Student site</h1><p class="lede">Students’ single page for the term. Almost everything on it comes from the schedule, staff and materials.</p></div>
        <div class="actions">
          <OpButtons def={updateSite(cohortScope(p))} verbCls="btn outline" />
          <a class="btn quiet" href={url} target="_blank" rel="noopener">Open the student site <Ext /></a>
        </div>
      </div>
      <Help title="What you edit here" doc="11-configure-cohort-site.md">
        <p>The home text and announcements are yours. The schedule, lectures, assignments and staff pages are generated and rewritten on every update.</p>
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
            <Field id="site-home" k="home" t={{ tier: 'ask', label: 'Home page', widget: 'markdown', reason: 'The first thing students read.' }} value={shown} values={{}} set={(_, v) => setBody(String(v ?? ''))} />
          ) : <p class="footnote">No home page yet: it appears when the site is set up.</p>}
          <SaveBar state={homeSave} onSave={() => void saveHome()} small disabled={body === null || body === homeText} file={{ org: p.cohort.org, repo, path: 'index.md' }} />
          <Lives org={p.cohort.org} repo={repo} path="index.md" />
        </section>
        <section class="panel section">
          <div class="section-head"><h2>Announcements</h2><span class="meta">Shown in the Updates box with released sessions and hand outs</span></div>
          <p style="color:var(--ink-2)">Announcements for releases and hand-outs are posted automatically from the schedule. Add anything else here.</p>
          {ann.kind === 'ready' && ann.entries.length ? (
            <ul class="rows">{ann.entries.filter((e) => e.type === 'file').map((e) => <Announcement p={p} repo={repo} path={e.path} sha={e.sha} />)}</ul>
          ) : ann.kind === 'loading' ? <Loading /> : <p class="footnote">No announcements.</p>}
          <div class="list-edit">
            <div class="le-row two">
              <input type="date" aria-label="Date" value={newAnn.date} onInput={(e) => setNewAnn({ ...newAnn, date: (e.target as HTMLInputElement).value })} />
              <textarea aria-label="Text" style="min-height:40px" placeholder="What students should know" onInput={(e) => setNewAnn({ ...newAnn, text: (e.target as HTMLTextAreaElement).value })}>{newAnn.text}</textarea>
              <span />
            </div>
          </div>
          <SaveBar state={annSave} onSave={() => void addAnn()} label="Add an announcement" small disabled={!newAnn.text.trim()} file={{ org: p.cohort.org, repo, path: '_announcements' }} />
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
  const ops = useOperations(p.status.operations, p.cohort.org);
  const outcomes: Record<string, Outcome | undefined> = {};
  for (const op of new Set(ops.map((o) => o.op))) {
    const f = p.files.file(p.cohort.org, 'classroom-config', outcomePath(op));
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
