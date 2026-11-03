// The one Selection shape (decision 0040), mirrored from `dsl_course/selection.py`: the
// institution denylist, a repo's `.releaseignore`, the use's selection (`kinds`, `types`,
// `include`, `exclude`; every key optional) and the institution's floor, in that order. The
// rules and the golden vectors come from the engine (`schemas/selection.json`); the tests
// hold this file to them, and to the demo fixture's hosting.

import rules from '../../schemas/selection.json';
import { obj } from '../edit/yamlText';
import { compileAll, withheldBy } from '../edit/glob';
import { CONTENT_BLOCKS } from './scheduleEdit';
import { DEFAULT_DEST_REPO } from './policy';

/** A selection as written: an absent key is undefined (no filter on it); `[]` is written empty. */
export interface Sel {
  kinds?: string[];
  types?: string[];
  include?: string[];
  exclude?: string[];
}

export interface Floor {
  never?: string[];
  exclude?: string[];
}

/** A path's kind: the schedule block of the entry that landed it, or null. */
export type KindOf = (path: string) => string | null;

export const KEYS = ['kinds', 'types', 'include', 'exclude'] as const;
/** The names `kinds:` takes (`lectures`, `labs`, ...), in the institution's order. */
export const SELECTION_KINDS: string[] = rules.kinds;
export const READINGS: string = rules.readings;
export const HOST_DEFAULT: Sel = rules.defaults.host;
export const PUBLISH_DEFAULT: Sel = rules.defaults.publish;
export const FLOORS: Record<string, Floor> = rules.floor;

const fnmatch = (p: string) => new RegExp(`^${p.replace(/[.+^${}()|\\]/g, '\\$&').replace(/\*/g, '.*').replace(/\?/g, '.')}$`);
const DENYLIST = rules.denylist.map(fnmatch);
const CLUTTER = new Set(rules.clutter.map((x) => x.toLowerCase()));
const ALIASES: Record<string, string> = rules.type_aliases;

const parts = (path: string) => path.split('/').filter(Boolean).map((x) => x.toLowerCase());
/** A component that is never course material (`selection.CLUTTER`: `.DS_Store`, `.gitkeep`, ...). */
export const clutter = (path: string) => parts(path).some((x) => CLUTTER.has(x));
/** A component on the institution denylist (solutions, tests, grading files, `.env*`, `.git`, `.system`). */
export const denylisted = (path: string) => parts(path).some((x) => DENYLIST.some((re) => re.test(x)));
/** Layer 1: either. */
export const denied = (path: string) => clutter(path) || denylisted(path);

const normType = (t: string) => {
  const x = t.toLowerCase().replace(/^\./, '');
  return ALIASES[x] ?? x;
};
/** A path's type for `types:`: its extension, lowercased, `htm` read as `html`. */
export function fileType(path: string): string {
  const name = path.split('/').pop() ?? '';
  return name.replace(/^\.+/, '').includes('.') ? normType(name.split('.').pop() ?? '') : '';
}

/** Whether a pattern list names `path` (git's rule, as `releaseignore.Ignore.excludes`); the list file itself always. */
function matches(lines: string[], path: string): boolean {
  if (path.split('/').includes('.releaseignore')) return true;
  return !!withheldBy(compileAll(lines), path);
}

/** Layer 3: exclude beats everything; else the kind is in `kinds` (or no `kinds`) or an include names it; and the type is in `types` (or no `types`). */
export function keeps(sel: Sel, path: string, kind: string | null): boolean {
  if (sel.exclude?.length && matches(sel.exclude, path)) return false;
  const byKind = sel.kinds === undefined || (kind !== null && sel.kinds.includes(kind));
  if (!byKind && !(sel.include?.length && matches(sel.include, path))) return false;
  return sel.types === undefined || sel.types.map(normType).includes(fileType(path));
}

/** Layer 4. */
export function allows(floor: Floor, path: string, kind: string | null): boolean {
  if (kind !== null && floor.never?.includes(kind)) return false;
  return !(floor.exclude?.length && matches(floor.exclude, path));
}

/** `sel` with each key it leaves out taken from `def`. */
export function withDefaults(sel: Sel, def: Sel): Sel {
  return Object.fromEntries(KEYS.map((k) => [k, sel[k] ?? def[k]]).filter(([, v]) => v !== undefined));
}

export function kept(path: string, kindOf: KindOf, o: { use?: Sel; withheld?: (p: string) => boolean; floor?: Floor } = {}): boolean {
  if (denied(path)) return false;
  if (o.withheld?.(path)) return false;
  const kind = kindOf(path);
  if (o.use && !keeps(o.use, path, kind)) return false;
  return !o.floor || allows(o.floor, path, kind);
}

/** `selection.select`: the paths that survive every layer, in order. */
export function select(paths: string[], kindOf: KindOf, o: { use?: Sel; withheld?: (p: string) => boolean; floor?: Floor } = {}): string[] {
  return paths.filter((p) => kept(p, kindOf, o));
}

/** A `.releaseignore` at the root as a layer-2 predicate. */
export const rootIgnore = (lines: string[]) => (path: string) => matches(lines, path);

/** A selection as `selection.parse` reads it from YAML (no validation: the engine is the verdict). */
export function readSel(raw: unknown): Sel {
  const o = obj(raw);
  const list = (v: unknown) => (Array.isArray(v) ? v.map(String) : undefined);
  return Object.fromEntries(KEYS.map((k) => [k, list(o[k])]).filter(([, v]) => v !== undefined));
}

/** The pattern that names exactly `path` (and everything under a folder): anchored, special characters escaped. */
export const exactPattern = (path: string) => `/${path.replace(/^\/+|\/+$/g, '').replace(/[[\]*?\\]/g, '\\$&')}`;

// ------------------------------------------------------------------ hosting (site._hostable)

/** One copy into a semester repo: where it landed, the kind of the entry that landed it, `public: true`. */
export type Landing = [dest: string, kind: string, pub: boolean];

/** Each copy the schedule lands in `repo` (`site._landings`). */
export function landings(doc: Record<string, unknown>, repo: string): Landing[] {
  const out: Landing[] = [];
  for (const block of CONTENT_BLOCKS) {
    for (const entry of Object.values(obj(doc[block]))) {
      const files = obj(entry).files;
      for (const f of Array.isArray(files) ? files : files ? [files] : []) {
        const x = obj(f);
        if (!x.from_course_repo || !x.from_path || String(x.to_semester_repo || DEFAULT_DEST_REPO) !== repo) continue;
        out.push([String(x.to_path || x.from_path).replace(/^\/+|\/+$/g, ''), block, x.public === true]);
      }
    }
  }
  return out;
}

/** `site.kind_of_landed`: the deepest copy that holds a path decides its kind. */
export function kindOfLanded(ls: Landing[]): KindOf {
  return (path) => {
    let best: Landing | null = null;
    for (const l of ls) {
      if (l[0] && path !== l[0] && !path.startsWith(`${l[0]}/`)) continue;
      if (!best || l[0].length > best[0].length) best = l;
    }
    return best ? best[1] : null;
  };
}

/** `host` with every `public: true` copy included. */
export function withPublic(host: Sel, ls: Landing[]): Sel {
  const pub = ls.filter(([d, , p]) => p && d).map(([d]) => exactPattern(d));
  return pub.length ? { ...host, include: [...(host.include ?? []), ...pub] } : host;
}

const RENDERED = ['html', 'htm', 'pdf'];
const DECK = ['html', 'htm'];
const ext = (p: string) => (p.split('/').pop() ?? '').split('.').slice(1).pop()?.toLowerCase() ?? '';

/** `site._hostable`: what a semester site hosts - the selection, only where a browser renders it, plus each hosted deck's `<stem>_files/`. */
export function hostable(paths: string[], host: Sel, kindOf: KindOf, floor: Floor = {}): string[] {
  const hit = new Set(select(paths, kindOf, { use: host, floor }).filter((p) => RENDERED.includes(ext(p))));
  const bundle: Sel = { exclude: host.exclude ?? [] };
  for (const p of [...hit]) {
    if (!DECK.includes(ext(p))) continue;
    const prefix = `${p.slice(0, p.lastIndexOf('.'))}_files/`;
    for (const a of paths) if (a.startsWith(prefix) && kept(a, kindOf, { use: bundle, floor })) hit.add(a);
  }
  return paths.filter((p) => hit.has(p));
}
