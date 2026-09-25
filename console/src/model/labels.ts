// The words for the engine's closed vocabularies (formats, where students submit, who sees
// each repo, how teams form) and the solution warning, as the engine exports them
// (`console/schemas/labels.json`, from `course.LABELS`). The console keeps no copy.

import labels from '../../schemas/labels.json';

export interface Label {
  label: string;
  help: string;
}

type Vocab = 'formats' | 'submit_via' | 'visibility' | 'team_formation';

const L = labels as unknown as Record<Vocab, Record<string, Label>> & { solution_warning: string };

/** What a solution date does, in the hand-out's words (`course.SOLUTION_WARNING`). */
export const SOLUTION_WARNING = L.solution_warning;

/** The label of `value` in `vocab`, or the value itself when the engine has none. */
export function labelOf(vocab: Vocab, value: string): string {
  return Object.hasOwn(L[vocab], value) ? L[vocab][value].label : value;
}

/** The help line of `value` in `vocab`, or ''. */
export function helpOf(vocab: Vocab, value: string): string {
  return Object.hasOwn(L[vocab], value) ? L[vocab][value].help : '';
}

/** `{value: label}` for `values`, in their order. */
export function labelMap(vocab: Vocab, values: string[]): Record<string, string> {
  return Object.fromEntries(values.map((v) => [v, labelOf(vocab, v)]));
}
