// Comment-preserving edits to a YAML file the instructor also edits by hand.
//
// `yaml`'s Document keeps comments but re-flows the whole file when stringified: every
// inline comment loses its column and long lines fold. So edits are made to the TEXT,
// splicing only the bytes of the node that changed, with the parsed Document used to find
// where those bytes are. Everything not edited stays byte-identical, and a value replaced
// beside an aligned comment keeps the comment's column.

import { Document, isCollection, isMap, isScalar, isSeq, parseDocument, visit, type Node, type Pair, type YAMLMap, type YAMLSeq } from 'yaml';

export type Path = (string | number)[];

// Strings PyYAML (YAML 1.1, what the engine reads with) would take as a boolean or null.
const YAML11_SPECIAL = /^(y|Y|yes|Yes|YES|n|N|no|No|NO|true|True|TRUE|false|False|FALSE|on|On|ON|off|Off|OFF|null|Null|NULL|~)$/;

/** A value as block YAML at column 0, the way the engine's own files spell it. */
export function render(value: unknown): string {
  const d = new Document(value);
  visit(d, {
    Scalar(_, node) {
      if (typeof node.value !== 'string') return;
      if (node.value.includes('\n')) node.type = 'BLOCK_LITERAL';
      else if (YAML11_SPECIAL.test(node.value)) node.type = 'QUOTE_DOUBLE';
    },
  });
  return d
    .toString({ lineWidth: 0, nullStr: '' })
    .replace(/:[ ]+$/gm, ':')
    .replace(/^(\s*-)[ ]+$/gm, '$1');
}

/** One scalar as it is written after `key: `; '' for null. */
function scalarText(v: unknown): string {
  if (v === null || v === undefined) return '';
  if (typeof v === 'string' && v === '') return '""';
  return render(v).replace(/\n$/, '');
}

const isPlain = (v: unknown) => v === null || ['string', 'number', 'boolean'].includes(typeof v);
const isObj = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);

function keyOf(p: Pair): string {
  return isScalar(p.key) ? String(p.key.value) : String(p.key);
}

function jsOf(n: unknown): unknown {
  if (n && typeof n === 'object' && 'toJSON' in n && typeof (n as { toJSON: unknown }).toJSON === 'function') return (n as { toJSON(): unknown }).toJSON();
  return n === undefined ? null : n;
}

export function deepEqual(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if ((a === null || a === undefined) && (b === null || b === undefined)) return true;
  if (typeof a !== typeof b || a === null || b === null || typeof a !== 'object') return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) return a.length === (b as unknown[]).length && a.every((x, i) => deepEqual(x, (b as unknown[])[i]));
  const ka = Object.keys(a as object), kb = Object.keys(b as object);
  return ka.length === kb.length && ka.every((k) => deepEqual((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]));
}

export class YamlText {
  private parsed: Document | null = null;

  constructor(public text: string) {}

  get doc(): Document {
    return (this.parsed ??= parseDocument(this.text));
  }

  /** Parse errors, as messages; an editor refuses to write over a file that has any. */
  get errors(): string[] {
    return this.doc.errors.map((e) => e.message);
  }

  toJS(): unknown {
    return this.doc.toJS();
  }

  /** The plain value at `path`, or undefined. */
  get(path: Path): unknown {
    const n = this.node(path);
    return n === undefined ? undefined : jsOf(n);
  }

  private node(path: Path): unknown {
    if (!path.length) return this.doc.contents;
    return this.doc.getIn(path, true);
  }

  private splice(start: number, end: number, s: string): void {
    this.text = this.text.slice(0, start) + s + this.text.slice(end);
    this.parsed = null;
  }

  private lineStart(pos: number): number {
    return this.text.lastIndexOf('\n', pos - 1) + 1;
  }

  private col(pos: number): number {
    return pos - this.lineStart(pos);
  }

  private lineEnd(pos: number): number {
    const i = this.text.indexOf('\n', pos);
    return i < 0 ? this.text.length : i;
  }

  /** Past the newline that ends the line `pos` is on (or the end of the text). */
  private nextLine(pos: number): number {
    if (pos > 0 && this.text[pos - 1] === '\n') return pos;
    const i = this.text.indexOf('\n', pos);
    return i < 0 ? this.text.length : i + 1;
  }

  /** Make the value at `path` equal `value`, touching only what differs. */
  assign(path: Path, value: unknown): void {
    const cur = this.node(path);
    if (value === undefined) {
      if (cur !== undefined) this.delete(path);
      return;
    }
    if (isObj(value) && isMap(cur) && !cur.flow) {
      const want = new Set(Object.keys(value));
      for (const k of (cur as YAMLMap).items.map(keyOf)) if (!want.has(k)) this.delete([...path, k]);
      for (const [k, v] of Object.entries(value)) this.assign([...path, k], v);
      return;
    }
    if (Array.isArray(value) && isSeq(cur) && !cur.flow && value.length) {
      const have = (cur as YAMLSeq).items.length;
      for (let i = have - 1; i >= value.length; i--) this.delete([...path, i]);
      value.forEach((v, i) => this.assign([...path, i], v));
      return;
    }
    if (cur !== undefined && deepEqual(jsOf(cur), value)) return;
    this.set(path, value);
  }

  /** Replace the value at `path` (creating its parents), or delete it when `value` is undefined. */
  set(path: Path, value: unknown): void {
    if (value === undefined) return this.delete(path);
    if (!path.length) throw new Error('set needs a path');
    const parentPath = path.slice(0, -1), key = path[path.length - 1];
    const parent = this.node(parentPath);
    if (!isCollection(parent)) {
      if (!parentPath.length) {
        // An empty document (or one holding only comments).
        const sep = this.text && !this.text.endsWith('\n') ? '\n' : '';
        this.splice(this.text.length, this.text.length, sep + render({ [key]: value }));
        return;
      }
      return this.set(parentPath, typeof key === 'number' ? [value] : { [key]: value });
    }
    if (parent.flow) {
      const js = jsOf(parent) as Record<string, unknown> | unknown[];
      (js as Record<string | number, unknown>)[key] = value;
      return this.set(parentPath, js);
    }
    if (isMap(parent)) {
      const pair = (parent as YAMLMap).items.find((p) => keyOf(p) === String(key));
      if (pair) return this.replacePairValue(pair as Pair, value);
      return this.insertPair(parent as YAMLMap, parentPath, String(key), value);
    }
    const seq = parent as YAMLSeq;
    const i = Number(key);
    if (i < seq.items.length) return this.replaceItem(seq, i, value);
    return this.appendItem(seq, value);
  }

  delete(path: Path): void {
    const parentPath = path.slice(0, -1), key = path[path.length - 1];
    const parent = this.node(parentPath);
    if (!isCollection(parent)) return;
    if (parent.flow) {
      const js = jsOf(parent);
      if (Array.isArray(js)) js.splice(Number(key), 1);
      else delete (js as Record<string, unknown>)[String(key)];
      return this.set(parentPath, js);
    }
    let start: number, end: number;
    if (isMap(parent)) {
      const pair = (parent as YAMLMap).items.find((p) => keyOf(p) === String(key)) as Pair<Node, Node | null> | undefined;
      if (!pair) return;
      const k = pair.key as Node;
      start = this.lineStart(k.range![0]);
      end = this.nextLine(pair.value?.range ? pair.value.range[2] : k.range![2]);
    } else {
      const item = (parent as YAMLSeq).items[Number(key)] as Node | undefined;
      if (!item?.range) return;
      start = this.lineStart(item.range[0]);
      end = this.nextLine(item.range[2]);
    }
    this.splice(start, end, '');
  }

  // ------------------------------------------------------------------ splices

  private block(value: unknown, indent: number): string {
    const pad = ' '.repeat(indent);
    return render(value)
      .split('\n')
      .map((l, i, all) => (i === all.length - 1 && l === '' ? '' : l ? pad + l : l))
      .join('\n');
  }

  private literal(s: string, indent: number): { header: string; body: string } {
    const pad = ' '.repeat(indent);
    const lines = s.replace(/\n$/, '').split('\n');
    return { header: s.endsWith('\n') ? '|' : '|-', body: lines.map((l) => (l ? pad + l : '')).join('\n') + '\n' };
  }

  private replacePairValue(pair: Pair, value: unknown): void {
    const k = pair.key as Node;
    const v = pair.value as Node | null;
    const seg = this.text.indexOf(':', k.range![1]) + 1;
    const keyIndent = this.col(k.range![0]);
    const eol = this.lineEnd(seg);
    const blockScalar = !!v && isScalar(v) && String(v.type ?? '').startsWith('BLOCK');
    const blockColl = !!v && isCollection(v) && !v.flow;
    const onLine = !v || (!blockScalar && !blockColl && v.range![1] <= eol);
    const comment = v && !blockColl ? (v.comment ?? '') : '';
    const tail = comment ? ` #${comment}` : '';

    // A multi-line string becomes a literal block.
    if (typeof value === 'string' && value.includes('\n')) {
      const { header, body } = this.literal(value, keyIndent + 2);
      const end = onLine ? this.nextLine(seg) : v!.range![1];
      this.splice(seg, end, ` ${header}${tail}\n${body}`);
      return;
    }

    const empty = (isObj(value) && !Object.keys(value).length) || (Array.isArray(value) && !value.length);
    if (isPlain(value) || empty) {
      const s = isPlain(value) ? scalarText(value) : Array.isArray(value) ? '[]' : '{}';
      if (onLine) {
        // Keep an aligned comment in its column.
        const valEnd = v ? v.range![1] : seg;
        const hash = this.text.indexOf('#', valEnd);
        const commentAt = hash >= 0 && hash < eol ? hash : -1;
        const stop = commentAt >= 0 ? commentAt : eol;
        const width = stop - seg;
        const out = s === '' ? (commentAt >= 0 ? ' '.repeat(Math.max(1, width)) : '') : ` ${s}${commentAt >= 0 ? ' '.repeat(Math.max(1, width - 1 - s.length)) : ''}`;
        this.splice(seg, stop, out);
        return;
      }
      this.splice(seg, v!.range![1], `${s ? ` ${s}` : ''}${tail}\n`);
      return;
    }

    // A collection.
    const body = this.block(value, keyIndent + 2);
    const withNl = body.endsWith('\n') ? body : `${body}\n`;
    if (blockColl) this.splice(this.lineStart(v!.range![0]), v!.range![1], withNl);
    else if (onLine) this.splice(seg, eol, `${tail}\n${withNl.replace(/\n$/, '')}`);
    else this.splice(seg, v!.range![1], `${tail}\n${withNl}`);
  }

  private insertPair(map: YAMLMap, mapPath: Path, key: string, value: unknown): void {
    const first = map.items[0] as Pair<Node> | undefined;
    let indent: number;
    if (first?.key?.range) indent = this.col(first.key.range[0]);
    else if (mapPath.length) {
      const parent = this.node(mapPath.slice(0, -1));
      const pair = isMap(parent) ? ((parent as YAMLMap).items.find((p) => keyOf(p) === String(mapPath[mapPath.length - 1])) as Pair<Node> | undefined) : undefined;
      indent = pair?.key?.range ? this.col(pair.key.range[0]) + 2 : 0;
    } else indent = 0;
    let at = map.range ? map.range[2] : this.text.length;
    if (at > this.text.length) at = this.text.length;
    const text = this.block({ [key]: value }, indent);
    const sep = at > 0 && this.text[at - 1] !== '\n' ? '\n' : '';
    this.splice(at, at, sep + (text.endsWith('\n') ? text : `${text}\n`));
  }

  private replaceItem(seq: YAMLSeq, i: number, value: unknown): void {
    const item = seq.items[i] as Node;
    if (isPlain(value) && !(typeof value === 'string' && value.includes('\n')) && isScalar(item) && !String(item.type ?? '').startsWith('BLOCK')) {
      this.splice(item.range![0], item.range![1], scalarText(value));
      return;
    }
    const dash = this.text.lastIndexOf('-', item.range![0]);
    const dashCol = this.col(dash);
    const start = this.lineStart(dash);
    const end = this.nextLine(item.range![2]);
    const text = this.block([value], dashCol);
    this.splice(start, end, text.endsWith('\n') ? text : `${text}\n`);
  }

  private appendItem(seq: YAMLSeq, value: unknown): void {
    const first = seq.items[0] as Node | undefined;
    const dashCol = first?.range ? this.col(this.text.lastIndexOf('-', first.range[0])) : 0;
    let at = seq.range ? seq.range[2] : this.text.length;
    if (at > this.text.length) at = this.text.length;
    const text = this.block([value], dashCol);
    const sep = at > 0 && this.text[at - 1] !== '\n' ? '\n' : '';
    this.splice(at, at, sep + (text.endsWith('\n') ? text : `${text}\n`));
  }
}
