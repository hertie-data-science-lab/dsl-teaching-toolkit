// A status with a held release at each time (decision 0034 §6), for the Dashboard and Schedule tests.

import type { Status } from '../src/model/types';

/** Four held releases at NOW (Wed 23 Sep, horizon 7 days): one skipped, one inside the horizon, s5 beyond it, one late with no fault. */
export function heldStatus(base: Status, org: string): Status {
  const rel = (id: string, when: string, state: 'late' | 'will_be_skipped') => ({ ...base.releases![0], id, when, state, number: Number(id.slice(1)), source: { repo: 'course-materials-f2026', path: `lectures/0${id.slice(1)}` } });
  const fix = (entry: string) => ({ repo: `${org}/semester-config`, path: 'schedule.yml', line: 9, screen: 'schedule', entry });
  // As the engine writes them: `kind` the code, `release` the entry it holds back.
  const prob = (id: string, entry: string, when: string) => ({ id, scope: 'semester' as const, stage: 'K4', kind: id.split(':').pop(), release: entry, text: `${entry} problem.`, stops: '', fix: fix(entry), when });
  return {
    ...base,
    releases: [rel('s2', '2026-09-17T10:00:00+02:00', 'late'), rel('s3', '2026-09-21T10:00:00+02:00', 'late'), rel('s4', '2026-09-28T10:00:00+02:00', 'will_be_skipped'), ...base.releases!],
    problems: [
      prob('schedule:s2:SOURCE_MISSING', 's2', '2026-09-17T10:00:00+02:00'),
      prob('schedule:s3:LATE', 's3', '2026-09-21T10:00:00+02:00'),
      prob('schedule:s4:SOURCE_MISSING', 's4', '2026-09-28T10:00:00+02:00'),
      // A kind problem on s4 does not hold its release: it is not the row's mark.
      { ...prob('kinds:s4', 's4', '2026-09-28T10:00:00+02:00'), kind: 'NO_KIND', release: undefined, when: undefined },
      ...base.problems!,
    ],
  };
}
