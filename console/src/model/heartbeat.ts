// The automation heartbeat, computed from the Scheduled release workflow's run list rather
// than stored in status.json (a field that moved every tick would commit every 15 minutes).

import type { GitHubClient, WorkflowRun } from '../github/client';
import { COURSE_REPO } from './names';

export const SCHEDULER_WORKFLOW = 'scheduled-release.yml';
/** A gap longer than this between executed ticks means automation is late (cadence.HEALTHY_GAP). */
export const HEALTHY_GAP_MIN = 20;
const DRIVERS = new Set(['schedule', 'repository_dispatch']);
const EXECUTED = new Set(['success', 'failure']);

export interface Heartbeat {
  lastTick: string | null;
  late: boolean;
}

/** The newest run a driver fired that actually did the work. */
export function heartbeatOf(runs: WorkflowRun[], now: number): Heartbeat {
  const tick = runs
    .filter((r) => DRIVERS.has(r.event ?? '') && EXECUTED.has(r.conclusion ?? ''))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  if (!tick) return { lastTick: null, late: true };
  return { lastTick: tick.created_at, late: now - Date.parse(tick.created_at) > HEALTHY_GAP_MIN * 60000 };
}

export async function loadHeartbeat(client: GitHubClient, courseOrg: string, now: number): Promise<Heartbeat | null> {
  try {
    return heartbeatOf(await client.listWorkflowRuns(courseOrg, COURSE_REPO, SCHEDULER_WORKFLOW, 30), now);
  } catch {
    return null;
  }
}
