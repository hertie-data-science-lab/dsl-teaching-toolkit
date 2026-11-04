// The semester repos a release may write into, as the repo picker lists them (the Files
// panel's Edit destination; plan 2026-11-04, decision 7).

import type { GhRepo } from '../github/client';
import { eligibleDestination, type Reserved } from '../model/releaseState';
import type { RepoRow } from './RepoPickerModal';

/** The picker's rows: the semester's repos a release may write into, not listed already, archived ones aside, by name. */
export function destinationRows(repos: GhRepo[], listed: string[], reserved: Reserved): RepoRow[] {
  const have = new Set(listed.map((n) => n.toLowerCase()));
  return repos
    .filter((r) => !r.archived && !have.has(r.name.toLowerCase()) && eligibleDestination(r.name, reserved) === null)
    .map((r) => ({ name: r.name, note: 'in the semester' }))
    .sort((a, b) => a.name.localeCompare(b.name));
}
