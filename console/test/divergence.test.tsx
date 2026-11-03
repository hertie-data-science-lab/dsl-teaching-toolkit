// Decision 0043 rule 6: a released folder that differs from the course copy. The release
// detail's panel (files, links, remedies), Release again's facts and "also remove" choice,
// and the set-aside id's name.

import { render } from 'preact-render-to-string';
import { describe, expect, it } from 'vitest';
import type { Course } from '../src/model/discovery';
import type { Loaded } from '../src/model/status';
import type { Problem, Status } from '../src/model/types';
import { deletedCount, divergedLabel, divergenceOf } from '../src/model/divergence';
import { gapLabel } from '../src/model/format';
import { releaseAgain } from '../src/ops/defs';
import { ReleaseScreen } from '../src/screens/Schedule';
import { releaseAgainDrift } from '../src/screens/Divergence';
import { SetAsideDialog } from '../src/screens/SetAside';
import type { CohortProps } from '../src/screens/types';
import { StaticFiles } from './staticFiles';
import example from './fixtures/status.example.json';

const STATUS = example as unknown as Status;
const COURSE_ORG = 'hertie-dsl-demo-course-e1234';
const COHORT_ORG = 'hertie-dsl-demo-f2026';
const NOW = Date.parse('2026-09-23T10:00:00+02:00');
const cohort = { org: COHORT_ORG, term: 'f2026', termLabel: 'Fall 2026' };
const course: Course = { org: COURSE_ORG, name: 'Machine Learning', code: 'E1234', description: '', write: true, admins: [], cohorts: [cohort], meta: {} };

const copy = (over: Partial<{ added: string[]; changed: string[]; deleted: string[] }>) => ({
  source: { repo: 'course-materials-f2026', path: 'lectures/05_trees', branch: 'main' },
  dest: { repo: 'materials', path: 'lectures/05_trees', branch: 'main' },
  added: [], changed: [], deleted: [], ...over,
});
const problem = (kind: string, fp: string, text: string, c: ReturnType<typeof copy>): Problem => ({
  id: `schedule:s5:${kind}:${fp}`, scope: 'semester', stage: 'K4', kind, release: 's5', aside: true, label: kind,
  text, stops: '', bites: 'later', fix: { repo: `${COHORT_ORG}/semester-config`, path: 'schedule.yml', screen: 'release', entry: 's5' },
  ...({ diverged: [c] } as object),
});
const DRIFT = [
  problem('DIVERGED_RELEASED', 'cccccccc', 's5 was edited in the semester.', copy({ changed: ['notes.md'] })),
  problem('DIVERGED_COURSE', 'aaaaaaaa', 's5 has changed in the course.', copy({ changed: ['slides.pdf'], added: ['extra.md'] })),
  problem('DIVERGED_DELETED', 'bbbbbbbb', '2 files of s5 were deleted.', copy({ deleted: ['old.md', 'data/a.csv'] })),
];
const released: Status = {
  ...STATUS,
  releases: (STATUS.releases ?? []).map((r) => (r.id === 's5' ? { ...r, state: 'released' } : r)),
  problems: [...(STATUS.problems ?? []).filter((p) => p.release !== 's5'), ...DRIFT],
};
const loaded: Loaded = { kind: 'ready', status: released, sha: 's', stale: [] };
const files = new StaticFiles({}, {}, {});
const props = (over: Partial<CohortProps> = {}): CohortProps => ({ course, cohort, loaded, files, now: NOW, heartbeat: { lastTick: '2026-09-23T07:48:00Z', late: false }, entry: 's5', ...over });
const html = (v: preact.VNode) => render(v);
const text = (v: preact.VNode) => html(v).replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

describe('a released folder that differs from the course copy', () => {
  it('reads the problems in kind order and counts what Release again may remove', () => {
    const list = divergenceOf(released, 's5');
    expect(list.map((p) => p.kind)).toEqual(['DIVERGED_COURSE', 'DIVERGED_DELETED', 'DIVERGED_RELEASED']);
    expect(deletedCount(list)).toBe(2);
    expect(divergenceOf(released, 's3')).toEqual([]);
  });

  it('shows each file with its mark, both copies on GitHub and the remedies', () => {
    const out = html(<ReleaseScreen {...props()} />);
    const t = text(<ReleaseScreen {...props()} />);
    expect(t).toContain('Differs from the course copy');
    expect(out).toContain('<span class="plus">+ extra.md</span>');
    expect(out).toContain('<span class="chg">~ slides.pdf</span>');
    expect(out).toContain('<span class="chg">− data/a.csv</span>');
    expect(out).toContain(`https://github.com/${COURSE_ORG}/course-materials-f2026/tree/main/lectures/05_trees`);
    expect(out).toContain(`https://github.com/${COHORT_ORG}/materials/tree/main/lectures/05_trees`);
    expect(t).toContain('Release again');
    expect(t).toContain('Keep for future semesters');
    // The panel holds the remedies; the Actions box does not repeat them, nor the outcome box the problems.
    expect(t).toContain('Release again or keep the edits from Differs from the course copy, above.');
    expect(t).not.toContain('s5 has changed in the course.');
  });

  it('shows no panel when nothing differs', () => {
    const plain: Loaded = { kind: 'ready', sha: 's', stale: [], status: { ...released, problems: [] } };
    expect(text(<ReleaseScreen {...props({ loaded: plain })} />)).not.toContain('Differs from the course copy');
  });

  it('Release again lists what differs and offers to remove deleted files, unticked', () => {
    const scope = { courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' };
    const ref = { id: 's5', ident: 'Lecture 5', title: 'Trees', when: 'Thu 8 Oct', source: { repo: 'course-materials-f2026', path: 'lectures/05_trees' } };
    const def = releaseAgain(scope, ref, releaseAgainDrift(divergenceOf(released, 's5')));
    expect(def.facts).toEqual(['s5 has changed in the course.', '2 files of s5 were deleted.', 's5 was edited in the semester.']);
    expect(def.options?.remove_deleted).toMatchObject({ widget: 'checkbox', default: false, label: 'Also remove the 2 files deleted from the course copy' });
    const plain = releaseAgain(scope, ref);
    expect(plain.options).toBeUndefined();
    expect(plain.facts).toBeUndefined();
  });

  it('names a set-aside id by its kind and release, and says what setting it aside means', () => {
    expect(divergedLabel('schedule:lab-08:DIVERGED_RELEASED:0a1b2c3d')).toBe('Released copy edited, lab-08');
    expect(gapLabel('schedule:lab-08:DIVERGED_COURSE:0a1b2c3d')).toBe('Course copy changed, lab-08');
    expect(divergedLabel('schedule:lab-08:LATE')).toBeNull();
    const t = text(<SetAsideDialog ask={{ id: 'schedule:lab-08:DIVERGED_COURSE:0a1b2c3d', label: 'Course copy changed, lab-08' }} busy={false} onSetAside={() => {}} onClose={() => {}} />);
    expect(t).toContain('Both copies stay as they are');
  });
});

describe('review follow-ups', () => {
  it('offers Release again even when only the released copy was edited, and warns what it does to those edits', () => {
    const only: Loaded = { kind: 'ready', sha: 's', stale: [], status: { ...released, problems: [DRIFT[0]] } };
    const t = text(<ReleaseScreen {...props({ loaded: only })} />);
    expect(t).toContain('Release again');
    expect(t).toContain('Keep for future semesters');
    const scope = { courseOrg: COURSE_ORG, cohortOrg: COHORT_ORG, where: 'Fall 2026' };
    const ref = { id: 's5', ident: 'Lecture 5', title: 'Trees', when: 'Thu 8 Oct', source: { repo: 'course-materials-f2026', path: 'lectures/05_trees' } };
    expect(releaseAgain(scope, ref, releaseAgainDrift(divergenceOf(only.status, 's5'))).confirm).toContain('waits in a pull request');
  });
});

describe('a file removed from the released copy', () => {
  it('lists it as removed from the released copy', () => {
    const gone = problem('DIVERGED_RELEASED', 'dddddddd', 'removed.', { ...copy({}), removed: ['data/a.csv'] } as ReturnType<typeof copy>);
    const one: Loaded = { kind: 'ready', sha: 's', stale: [], status: { ...released, problems: [gone] } };
    expect(html(<ReleaseScreen {...props({ loaded: one })} />)).toContain('<span class="chg">− data/a.csv (removed from the released copy)</span>');
  });
});
