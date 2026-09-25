// A materials repo's rules as the engine exports them (`console/schemas/materials.json`,
// from `dsl_course/materials.py`): the topic that marks one, its optional `materials.yml`
// (the syllabus file and folder -> kind aliases), and the kind a folder implies. The console
// holds no list of its own, so its previews cannot disagree with the engine.

import rules from '../../schemas/materials.json';
import { YamlText, obj } from '../edit/yamlText';
import { POLICY } from './policy';

export const MATERIALS_TOPIC = rules.topic;
export const MATERIALS_FILE = rules.file;
export const PUBLISH_FILE = rules.publish_file;
export const DEFAULT_SYLLABUS = rules.default_syllabus;
export const DEFAULT_KIND = rules.default_kind;
const ALIASES: Record<string, string> = rules.aliases;

/** The kinds a release entry or a folder may be: the policy's non-system kinds, in its order. */
export const CONTENT_KINDS: string[] = POLICY.kinds.filter((k) => !k.system).map((k) => k.key);
/** `materials.known_kind`: a content kind, else `other`. */
const known = (kind: string) => (CONTENT_KINDS.includes(kind.trim().toLowerCase()) ? kind.trim().toLowerCase() : 'other');

/** What one repo's `materials.yml` declares, defaults filled in. */
export interface Declared {
  syllabus: string;
  /** Whether `syllabus:` is written (else it is the default). */
  declared: boolean;
  /** Folder (lowercased) -> kind. */
  kinds: Record<string, string>;
}

export const NOTHING_DECLARED: Declared = { syllabus: DEFAULT_SYLLABUS, declared: false, kinds: {} };

/** `materials.parse` over the file's text: null text is the defaults; null back means it does not parse. */
export function readDeclared(text: string | null): Declared | null {
  if (text === null) return NOTHING_DECLARED;
  const y = new YamlText(text);
  if (y.errors.length) return null;
  const doc = obj(y.toJS());
  const syllabus = String(doc.syllabus ?? '').trim().replace(/^\/+|\/+$/g, '');
  const kinds = Object.fromEntries(Object.entries(obj(doc.kinds)).map(([k, v]) => [k.trim().replace(/^\/+|\/+$/g, '').toLowerCase(), known(String(v))]));
  return { syllabus: syllabus || DEFAULT_SYLLABUS, declared: !!syllabus, kinds };
}

/** `materials.alias_kind`: the kind a section NAMES (the repo's alias, else a built-in one), or null. */
export function aliasKind(section: string, aliases: Record<string, string> = {}): string | null {
  const key = section.toLowerCase();
  const found = aliases[key] ?? ALIASES[key];
  return found ? known(found) : null;
}

/** `materials.infer_kind`: the section's alias, else the default; `named` says an alias gave it. */
export function inferKind(section: string, aliases: Record<string, string> = {}): { kind: string; named: boolean } {
  const kind = aliasKind(section, aliases);
  return kind ? { kind, named: true } : { kind: DEFAULT_KIND, named: false };
}

/** `schedule_plan.deploy_section`: the top folder a copy lands in, or its repo when it lands at the root. */
export function landingSection(copy: { folder: string; path: string; dest: string }, defaultRepo: string): string {
  const dest = (copy.path || copy.folder).replace(/^\/+|\/+$/g, '');
  return dest.includes('/') ? dest.split('/')[0] : copy.dest || defaultRepo;
}
