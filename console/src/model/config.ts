// The `defaults:` block of `course.yml` and `semester.yml` as the console writes it (decision
// 0039 rule 2). The seeds write every key, the ones a level does not state as a comment with
// the value it inherits and where from (`settings.defaults_skeleton`). A save keeps that shape:
// a key set here becomes a live line where its comment was, a key cleared goes back to its
// comment, and every other line stays byte for byte.

import { stringify } from 'yaml';
import { YamlText, blank, deepEqual, isObj, obj } from '../edit/yamlText';
import { DEFAULT_KEYS } from './names';
import { fileKey, fileValue, institutionDefaults, type Block, type Effective, type Effectives } from './settings';

export const DEFAULTS_BLOCK = 'defaults';

/** The engine's skeleton head line (`settings.defaults_skeleton`). */
const HEAD = 'defaults:   # every key present; uncomment to override. The comment shows the inherited value and its source.';
/** The column the source comment starts at (`ljust(44)` in the engine). */
const COMMENT_COLUMN = 44;
const LATE_PAIR = ['late_window_days', 'late_penalty_per_day'];
/** What a commented `team_formation` says when no level below states one (`settings._SKELETON_HINTS`). */
const TEAM_FORMATION_HINT = 'self_select | assigned';

/** One value as a YAML flow scalar on one line, as the engine's `_yaml_scalar` writes it. */
export const scalar = (v: unknown): string => stringify(v, { lineWidth: 0 }).trim();

/** A form's values as the `defaults:` keys they state: file spellings, blanks dropped. */
export function statedDefaults(values: Block): Block {
  const out: Block = {};
  for (const [k, v] of Object.entries(values)) if (!blank(v)) out[fileKey(k)] = fileValue(k, v);
  return out;
}

/** The keys the `defaults:` block of `doc` states, as a form spells them (`visibility`). */
export function defaultsOf(doc: Block): Block {
  return Object.fromEntries(Object.entries(obj(doc[DEFAULTS_BLOCK])).map(([k, v]) => [k === fileKey('visibility') ? 'visibility' : k, v]));
}

/**
 * What a semester's own keys fall back to: the course's effective values (the course's
 * status.json), else the semester's own answer where the semester does not state it, else the
 * institution's.
 */
export function semesterInheritance(course: Effectives | undefined, semester: Effectives | undefined): Effectives {
  const out: Effectives = { ...institutionDefaults() };
  for (const k of DEFAULT_KEYS) {
    const s = semester?.[k];
    if (course?.[k]) out[k] = course[k];
    else if (s && s.source !== 'semester' && s.source !== 'entry') out[k] = s;
  }
  return out;
}

/** The comment line for a key this level does not state. */
function commentLine(indent: string, key: string, inherited: Effective | undefined, pairStated: boolean): string {
  if (LATE_PAIR.includes(key) && pairStated) return `${indent}# ${key}:`.padEnd(COMMENT_COLUMN) + '(none: the late pair is set above)';
  const v = inherited?.value;
  if (key === 'team_formation' && (v === null || v === undefined || v === 'none'))
    return `${indent}# ${key}:`.padEnd(COMMENT_COLUMN) + `(${TEAM_FORMATION_HINT}; not set above)`;
  return `${indent}# ${key}: ${scalar(v ?? null)}`.padEnd(COMMENT_COLUMN) + `(${inherited?.source ?? 'institution'})`;
}

/** The whole block, as the engine seeds it: `stated` live, every other key commented. */
export function skeleton(stated: Block, inherited: Effectives): string {
  const pair = LATE_PAIR.some((k) => k in stated);
  const lines = [HEAD];
  for (const k of DEFAULT_KEYS) lines.push(k in stated ? `  ${k}: ${scalar(stated[k])}` : commentLine('  ', k, inherited[k], pair));
  return `${lines.join('\n')}\n`;
}

const liveRe = (k: string) => new RegExp(`^(\\s+)${k}\\s*:`);
const commentRe = (k: string) => new RegExp(`^(\\s+)#\\s*${k}\\s*:`);

/**
 * `text` with its `defaults:` block stating exactly `after` (file keys; a key absent is not
 * stated). A key whose live value is unchanged is not touched; one set here takes its comment
 * line's place (or the place the skeleton order gives it); one cleared becomes its comment
 * again. A file with no block gets the whole skeleton at its end; a flow-style block is edited
 * as YAML.
 */
export function withDefaults(text: string, after: Block, inherited: Effectives): string {
  const y = new YamlText(text);
  const before = isObj(y.get([DEFAULTS_BLOCK])) ? (y.get([DEFAULTS_BLOCK]) as Block) : {};
  const changed = DEFAULT_KEYS.filter((k) => !deepEqual(before[k], after[k]));
  if (!changed.length) return text;
  const lines = text.split('\n');
  const at = lines.findIndex((l) => /^defaults\s*:/.test(l));
  if (at < 0) {
    const body = text === '' || text.endsWith('\n') ? text : `${text}\n`;
    return `${body}${body.trim() ? '\n' : ''}${skeleton(after, inherited)}`;
  }
  if (lines[at].replace(/#.*$/, '').replace(/^defaults\s*:/, '').trim()) {
    // `defaults: {...}`: no lines to keep, so plain YAML edits.
    for (const k of changed) y.assign([DEFAULTS_BLOCK, k], after[k]);
    return y.text;
  }
  let end = at + 1;
  while (end < lines.length && /^\s+\S/.test(lines[end])) end++;
  const block = lines.slice(at + 1, end);
  const indent = block.map((l) => /^(\s+)/.exec(l)?.[1]).find(Boolean) ?? '  ';
  const pair = LATE_PAIR.some((k) => k in after);
  const find = (k: string, re: (k: string) => RegExp) => block.findIndex((l) => re(k).test(l));
  for (const k of changed) {
    const live = find(k, liveRe), comment = find(k, commentRe);
    if (k in after) {
      const line = `${indent}${k}: ${scalar(after[k])}`;
      if (live >= 0) block[live] = line;
      else if (comment >= 0) block[comment] = line;
      else {
        // After the nearest key before it in skeleton order that has a line, else first.
        const order = DEFAULT_KEYS.indexOf(k);
        let after_ = -1;
        for (let i = order - 1; i >= 0 && after_ < 0; i--) after_ = Math.max(find(DEFAULT_KEYS[i], liveRe), find(DEFAULT_KEYS[i], commentRe));
        block.splice(after_ + 1, 0, line);
      }
    } else if (live >= 0) block[live] = commentLine(indent, k, inherited[k], pair);
  }
  // The other half of the late pair follows the pair: its comment says why it is none, or what it inherits.
  for (const k of LATE_PAIR) {
    if (k in after || !changed.some((c) => LATE_PAIR.includes(c))) continue;
    const comment = find(k, commentRe);
    if (comment >= 0) block[comment] = commentLine(indent, k, inherited[k], pair);
  }
  return [...lines.slice(0, at + 1), ...block, ...lines.slice(end)].join('\n');
}
