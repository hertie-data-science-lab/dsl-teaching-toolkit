// New materials repo (`#new-materials`): one form, per design/inputs.md "New materials
// repo", dispatching `materials.create`; the page then checks the repo is there.

import { useState } from 'preact/hooks';
import { useEnv } from '../env';
import { SchemaForm, effective, fieldErrors } from '../forms/Form';
import { createMaterials } from '../ops/defs';
import type { Values } from '../tiers/types';
import { newMaterials } from '../tiers/wizard';
import { EditFile } from '../ui/bits';
import { Hint } from '../ui/Hint';
import { useDraft } from '../wizards/drafts';
import { contentTerms, materialsArgs, materialsRepo } from '../wizards/model';
import { allOk, checkFree, checkRepoExists, useLive, type Check } from '../wizards/verify';
import { Checks, Verified } from '../wizards/Wizard';
import { courseView } from './Course';
import { courseScope } from './common';
import type { CourseProps } from './types';

export function NewMaterialsScreen(p: CourseProps) {
  const { course, now } = p;
  const env = useEnv();
  const terms = contentTerms(course.cohorts.map((c) => c.term), now);
  const repos = (courseView(p).course?.materials ?? []).map((m) => m.repo);
  const [d, set, clear] = useDraft<{ v: Values; submitted?: string }>(`new-materials:${course.org}`, () => ({ v: { term: terms[0] } }));
  const tiers = newMaterials(terms.includes(String(d.v.term)) ? terms : [String(d.v.term), ...terms], repos);
  const v = effective(tiers, d.v);
  const repo = materialsRepo(String(v.term ?? terms[0]));
  const runs = env?.ops.runs.value.length ?? 0;
  const live = useLive(env && d.submitted === repo ? async () => ({ repo, c: await checkRepoExists(env.client, course.org, repo, `${repo} created`) }) : null, [repo, runs, d.submitted]);
  const made = live.value?.repo === repo && live.value.c.ok === true;
  const [free, setFree] = useState<{ busy: boolean; c: Check | null }>({ busy: false, c: null });
  const errs = fieldErrors(null, tiers, d.v);

  const create = async () => {
    if (!env) return;
    setFree({ busy: true, c: null });
    const c = await checkFree(env.client, course.org, repo, repo);
    setFree({ busy: false, c });
    if (!allOk([c])) return;
    set({ submitted: repo });
    env.ops.open(createMaterials(courseScope(p), repo, materialsArgs(v)), 'run');
  };

  return (
    <>
      <div class="page-head"><div><h2 class="h1">New handout materials <Hint doc="02-add-materials-to-course.md">Handout materials are usually per semester. Each semester’s repo is named after it, so the semester that uses it is clear.</Hint></h2><p class="lede">One repo of lectures, labs and readings, kept private until releases copy it to a semester.</p></div></div>
      <div class="panel">
        <div class="form" style="max-width:640px">
          <p class="footnote ctx">For {course.name}</p>
          <SchemaForm id="nm" schema={null} tiers={tiers} values={d.v} onChange={(nv) => set({ v: nv })} advancedOpen={!!d.v.copy_from} />
          <p class="footnote">Will create <code>{repo}</code> in the course.</p>
          {free.c || free.busy ? <Checks list={free.c ? [free.c] : null} busy={free.busy} pending={[`${repo} is free in the course`]} /> : null}
          {d.submitted === repo ? <Checks list={live.value?.repo === repo ? [live.value.c] : null} busy={live.busy} pending={[`${repo} created`]} /> : null}
          {made ? (
            <>
              <Verified>Created. Write its syllabus, then add its folders to a semester’s schedule.</Verified>
              <div class="actions">
                <EditFile org={course.org} repo={repo} path="SYLLABUS.md" exists={p.files.file(course.org, repo, 'SYLLABUS.md').kind !== 'absent'} />
                <a class="btn outline" href={`#materials-${repo}`}>Handout materials settings</a>
                <button class="btn small quiet" type="button" onClick={clear}>Start another</button>
              </div>
            </>
          ) : (
            <div class="actions">
              {d.submitted === repo ? <button class="btn small outline" type="button" disabled={live.busy} onClick={() => live.run()}>Check again</button> : null}
              <button class="btn" type="button" disabled={!env || free.busy || Object.keys(errs).length > 0} onClick={() => void create()}>Create handout materials repo</button>
              <a class="btn quiet" href="#materials">Cancel</a>
            </div>
          )}
        </div>
      </div>
    </>
  );
}
