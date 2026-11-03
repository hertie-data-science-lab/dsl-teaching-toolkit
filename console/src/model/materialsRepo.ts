// A materials repo's rules the console applies itself, as the engine exports them
// (`console/schemas/materials.json`): the topic that marks one, and the line a withhold list
// carries when somebody looked and chose to withhold nothing.

import rules from '../../schemas/materials.json';

export const MATERIALS_TOPIC = rules.topic;
/** The line a `.releaseignore` carries when somebody looked and chose to withhold nothing. */
export const REVIEWED_MARK: string = rules.reviewed_mark;

/** A withhold list as it is saved: one that withholds nothing gets the reviewed mark, so the
 * checklist counts it as reviewed (`status_json._reviewed`). */
export function withMark(text: string): string {
  const lines = text.split('\n').map((l) => l.trim());
  if (lines.includes(REVIEWED_MARK) || lines.some((l) => l && !l.startsWith('#'))) return text;
  const body = text.replace(/\n*$/, '');
  return body ? `${body}\n${REVIEWED_MARK}\n` : `${REVIEWED_MARK}\n`;
}
