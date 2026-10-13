// An assignment's phase as the console counts it, from the engine's state.

import type { Assignment, AssignmentState } from './types';

/**
 * The step an assignment is on (decision 0037): one Marking step from the due date until
 * marks are returned, while late work still arrives in its first part (`late_window`); a
 * blocked hand out is still forming teams.
 */
export type Phase = 'declared' | 'teams_forming' | 'open' | 'marking' | 'returned';

const PHASE: Record<AssignmentState, Phase> = {
  declared: 'declared', teams_forming: 'teams_forming', blocked: 'teams_forming', open: 'open',
  late_window: 'marking', marking: 'marking', returned: 'returned',
};

export const phaseOf = (a: Pick<Assignment, 'state'>): Phase => PHASE[a.state];

/** Whether an assignment's due date has passed, by the engine's state. */
export const pastDue = (a: Pick<Assignment, 'state'>): boolean => phaseOf(a) === 'marking' || phaseOf(a) === 'returned';

/** Submissions so far; a gradebook that does not say counts none. */
export const submissionsOf = (a: Pick<Assignment, 'submissions'>): number => a.submissions ?? 0;
