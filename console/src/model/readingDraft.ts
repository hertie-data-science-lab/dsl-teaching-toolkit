// Generate draft (decision 0044 point 6): one APA 7 line per reading PDF, made in the browser from
// what the file itself carries (its PDF details, the printed page numbers on pages 1-2 and the last
// page, a "Chapter N" opener or running head) and its filename. Deterministic: no model, nothing
// leaves the browser. Every part says where it came from (`found` / `check` / `guess` / `?`), and
// an unknown part is a free-standing `?` in the line, for the instructor to fill or delete.
// The heuristics are the 5 Nov trial's (det.py, scored on NLP's 24 PDFs), ported.

/** How sure a part is: read off the file / read but worth a look / inferred / unknown. */
export type Level = 'found' | 'check' | 'guess' | '?';
export interface Note {
  part: string;
  level: Level;
  text: string;
}

/** What `pdfText.readPdf` reads from one PDF. */
export interface PdfFacts {
  pages: number;
  /** The PDF's details (its Info dictionary), '' when absent. */
  title: string;
  author: string;
  /** Text lines of page 1, page 2 and the last page. */
  first: string[];
  second: string[];
  last: string[];
  /** The PDF's own labels of its first and last page, when it has page labels. */
  labels: [string, string] | null;
}

export type Section = 'required' | 'additional';

/** One drafted line: the file it cites (path in its repo), its section, the APA line and its provenance. */
export interface Draft {
  path: string;
  section: Section;
  line: string;
  notes: Note[];
}

interface Person {
  family: string;
  /** Given names, for initials; empty when unknown (a surname from the filename). */
  given: string[];
}

/** A book as drafted from its details: each part null when unknown. */
interface Book {
  authors: Person[] | null;
  year: string | null;
  title: string | null;
  edition: string | null;
  publisher: string | null;
  notes: Note[];
}

/** A `?` standing free (not attached to a letter or digit): a part the draft does not know. One in a title (`models?`) is text. */
const UNKNOWN = /(?<![\p{L}\p{N}])\?+(?![\p{L}\p{N}])/u;
export const hasUnknown = (line: string) => UNKNOWN.test(line);

export const baseName = (path: string) => path.split('/').pop() ?? path;
const stem = (path: string) => baseName(path).replace(/\.[^.]+$/, '');
/** The filename's words: `07_session-7__s7-raschka-2022-opt-1.pdf` -> s7, raschka, 2022, opt, 1 (the folder prefix before `__` dropped). */
const words = (path: string) => stem(path).split('__').pop()!.toLowerCase().split(/[-_ .]+/).filter(Boolean);
const alnum = (s: string) => s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, '');

/** Required, unless the filename carries `opt`. */
export function sectionOf(path: string): { section: Section; note: Note } {
  return words(path).includes('opt')
    ? { section: 'additional', note: { part: 'Section', level: 'found', text: '"opt" in the filename: Additional' } }
    : { section: 'required', note: { part: 'Section', level: 'found', text: 'No "opt" in the filename: Required' } };
}

/**
 * A metadata title without a trailing ` - <domain>` or ` | <site>` (a shadow-library copy carries
 * "- libgen.li"); `cut` is what was taken off.
 */
export function cleanTitle(raw: string): { title: string; cut: string | null } {
  const t = raw.trim().replace(/\s+/g, ' ');
  const m = t.match(/^(.*\S)\s+(?:[-–—]\s+[\w-]+(?:\.[\w-]+)+|\|\s+[^|]+)$/);
  return m ? { title: m[1], cut: t.slice(m[1].length).trim() } : { title: t, cut: null };
}

/** Whether a metadata title is no title: a URL or path, a filename, or the file's own name. */
function junkTitle(title: string, path: string): boolean {
  if (!/\s/.test(title) || /https?|[/\\]|\.(pdf|docx?|pptx?|tex|indd)$/i.test(title) || /^microsoft (word|powerpoint) - /i.test(title)) return true;
  const a = alnum(title);
  return !a || alnum(stem(path)).includes(a);
}

const PARTICLES = new Set(['von', 'van', 'der', 'den', 'de', 'del', 'della', 'da', 'di', 'du', 'la', 'le', 'ter', 'ten', 'dos', 'das']);
const isCaps = (s: string) => /\p{Lu}/u.test(s) && !/\p{Ll}/u.test(s);
const titleWord = (w: string) => (isCaps(w) && w.length > 1 ? w[0] + w.slice(1).toLowerCase() : w);

/** One name, "Given Family" or "Family, Given"; null when it does not look like one person's name. */
function person(raw: string): Person | null {
  const s = raw.replace(/\([^)]*\)/g, (p) => p.slice(1, -1)).replace(/\s+/g, ' ').trim();
  if (!s) return null;
  if (s.includes(',')) {
    const [family, given] = s.split(',', 2).map((x) => x.trim());
    return family && family.split(' ').length <= 3 ? { family: family.split(' ').map(titleWord).join(' '), given: given ? given.split(' ').map(titleWord) : [] } : null;
  }
  const w = s.split(' ').map(titleWord);
  if (w.length > 4 || w.some((x) => !/^[\p{L}][\p{L}.'’-]*$/u.test(x))) return null;
  let i = w.length - 1;
  while (i > 1 && PARTICLES.has(w[i - 1].toLowerCase())) i--;
  return { family: w.slice(i).join(' '), given: w.slice(0, i) };
}

/**
 * The people in an author string: "A B, C D and E F", "A B & C D", "Family, Given; Family, Given",
 * or "Family, Given" alone. Null when any part is not a name (a string with no separators between
 * several people: "SEBASTIAN LIU YUXI (HAYDEN) MIRJALILI VAHID RASCHKA").
 */
export function parseNames(raw: string): Person[] | null {
  const s = raw.trim();
  if (!s) return null;
  let parts: string[];
  if (s.includes(';')) parts = s.split(';');
  else {
    parts = s.split(/\s*,\s*|\s+(?:and|&)\s+/i).filter(Boolean);
    // "Raschka, Sebastian": two one-word parts are one person, family first.
    if (parts.length === 2 && parts.every((p) => !/\s/.test(p.trim())) && s.includes(',')) parts = [s];
  }
  const people = parts.map(person);
  return people.length && people.every((p): p is Person => p !== null) ? people : null;
}

const initials = (given: string[]) =>
  given.some((g) => g.replace(/[-.]/g, '')) ? given.map((g) => g.split('-').map((x) => x.replace(/\.$/, '')).filter(Boolean).map((x) => `${x[0].toUpperCase()}.`).join('-')).filter(Boolean).join(' ') : '?';

/** The surnames among `people` without given names: their initials are a `?` in the line. */
const bare = (people: Person[]) => people.filter((p) => initials(p.given) === '?').map((p) => p.family);

/** The Authors note, `?` when a surname has no given names (its initials are a `?` in the line). */
export function authorsNote(people: Person[], level: Level, text: string): Note {
  const b = bare(people);
  if (!b.length) return { part: 'Authors', level, text };
  return { part: 'Authors', level: '?', text: `${text}; no given names${b.length < people.length ? ` for ${b.join(', ')}` : ''}, so the initials are ?` };
}

/** APA 7 author list: "Family, G. G., Family, G., & Family, G."; a surname without given names gets a `?`. */
export function apaAuthors(people: Person[] | null): string {
  if (!people?.length) return '?';
  const one = (p: Person) => `${p.family}, ${initials(p.given)}`;
  if (people.length === 1) return one(people[0]);
  const list = people.length > 20 ? [...people.slice(0, 19).map(one), '…', one(people[people.length - 1])] : people.map(one);
  return people.length > 20 ? list.join(', ') : `${list.slice(0, -1).join(', ')}, & ${list[list.length - 1]}`;
}

/**
 * APA sentence case: the first word and the first after a colon capitalised, a word with a capital
 * past its first letter (NLP, PyTorch, Scikit-Learn) kept as written, the rest lower case. A title
 * set in capitals throughout is all lowered (its acronyms are lost: the note says check).
 */
export function sentenceCase(title: string): string {
  const caps = isCaps(title.replace(/[^\p{L}]/gu, '').slice(0, 40));
  let start = true;
  return title.split(' ').map((w) => {
    const keep = !caps && /^.[^\s]*\p{Lu}/u.test(w);
    let out = keep ? w : w.toLowerCase();
    if (start) out = out.replace(/\p{L}/u, (c) => c.toUpperCase());
    start = /[:?!]$/.test(w);
    return out;
  }).join(' ');
}

const ORDINALS: Record<string, string> = { first: '1st', second: '2nd', third: '3rd', fourth: '4th', fifth: '5th', sixth: '6th', seventh: '7th', eighth: '8th', ninth: '9th', tenth: '10th' };

/** A title with its edition ("Second Edition", "2nd ed.") and other trailing brackets ("(MEAP V12)") taken out. */
function splitTitle(t: string): { title: string; edition: string | null; dropped: string[] } {
  let title = t;
  let edition: string | null = null;
  const dropped: string[] = [];
  const ed = title.match(/,?\s*\(?\b(first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|\d+(?:st|nd|rd|th))\s+ed(?:ition|\.)?\)?/i);
  if (ed) {
    const w = ed[1].toLowerCase();
    edition = ORDINALS[w] ?? w;
    title = (title.slice(0, ed.index) + title.slice(ed.index! + ed[0].length)).trim();
  }
  for (let m; (m = title.match(/\s*\(([^()]*)\)\s*$/)); ) {
    dropped.push(m[0].trim());
    title = title.slice(0, m.index).trim();
  }
  return { title: title.replace(/[\s,;:]+$/, ''), edition, dropped };
}

/** A publisher as APA writes it: no Inc., Ltd., Co.; capitals throughout set as a name. */
export function apaPublisher(p: string): string {
  const s = isCaps(p) ? p.split(' ').map(titleWord).join(' ') : p;
  return s.replace(/,?\s+(inc|ltd|limited|co|corp|llc|gmbh|plc)\.?$/i, '').replace(/,\s*$/, '').trim();
}

/** The surnames the filename carries (its words less session marks, `opt`, numbers and years). */
function filenameNames(path: string): Person[] | null {
  const w = words(path).filter((x) => !/^(s\d+|session|opt|ch|chapter|part|pp|reading|readings|\d+)$/.test(x) && /^\p{L}{3,}$/u.test(x));
  return w.length ? w.map((x) => ({ family: x[0].toUpperCase() + x.slice(1), given: [] })) : null;
}

/** "Hobson Lane and Maria Dyshel - Title (2024, Manning Publications Co.)", the shape of a shadow-library title. */
const SHELF = /^(.+?)\s+-\s+(.+?)\s+\((\d{4})(?:,\s*([^)]+))?\)$/;

/** The book a file is from: its PDF details, else the filename for authors and year. `text` is the pages read, for a year. */
function bookOf(f: PdfFacts, path: string, text: string): Book {
  const notes: Note[] = [];
  const b: Book = { authors: null, year: null, title: null, edition: null, publisher: null, notes };
  const { title: raw, cut } = cleanTitle(f.title);
  const shelf = raw.match(SHELF);
  if (shelf) {
    b.authors = parseNames(shelf[1]);
    if (b.authors && bare(b.authors).length) notes.push(authorsNote(b.authors, 'found', `From the PDF's details: "${shelf[1].trim()}"`));
    b.year = shelf[3];
    b.publisher = shelf[4] ? apaPublisher(shelf[4]) : null;
    setTitle(b, shelf[2].replace(/_ /g, ': '));
    notes.unshift({ part: 'Book', level: 'found', text: `From the PDF's details: "${raw}"` });
  } else if (raw && !junkTitle(raw, path)) {
    setTitle(b, raw);
    notes.unshift({ part: 'Book', level: 'found', text: `Title from the PDF's details: "${raw}"` });
  } else {
    notes.unshift({ part: 'Book', level: '?', text: raw ? `The PDF's details give "${raw}", which is no title` : "No title in the PDF's details" });
  }
  if (cut) notes.splice(1, 0, { part: 'Book', level: 'check', text: `Took "${cut}" off the end of the title` });
  if (!b.authors && !shelf && f.author.trim()) {
    // The details' author is often whoever made the PDF: trusted when the filename agrees.
    const people = parseNames(f.author);
    const fn = words(path);
    if (people && people.some((p) => fn.includes(p.family.toLowerCase().split(' ').pop()!))) {
      b.authors = people;
      notes.push(authorsNote(people, 'check', `From the PDF's details: "${f.author.trim()}"`));
    } else if (people && !filenameNames(path)) {
      b.authors = people;
      notes.push(authorsNote(people, 'check', `From the PDF's details: "${f.author.trim()}" (may be whoever made the PDF)`));
    }
  }
  if (!b.authors) {
    b.authors = filenameNames(path);
    notes.push(b.authors
      ? authorsNote(b.authors, 'guess', `Surnames from the filename${shelf ? ` (the PDF's details name "${shelf[1].trim()}", which does not split into people)` : f.author.trim() ? ` (the PDF's details name "${f.author.trim()}", not in the filename)` : ''}`)
      : { part: 'Authors', level: '?', text: 'No authors in the PDF\'s details or the filename' });
  }
  if (!b.year) {
    const y = yearOf(text, path);
    b.year = y.year;
    notes.push(y.note);
  }
  if (!b.publisher) notes.push({ part: 'Publisher', level: '?', text: "No publisher in the PDF's details" });
  return b;
}

function setTitle(b: Book, t: string) {
  const s = splitTitle(t);
  const trunc = s.title.match(/^(.+?):\s.*(\.\.\.|…)/);
  const main = (trunc ? trunc[1] : s.title).trim();
  b.edition = s.edition;
  if (!main) {
    b.title = null;
    b.notes.push({ part: 'Book', level: '?', text: `No title left once "${t.trim()}" loses its edition and brackets` });
    return;
  }
  b.title = sentenceCase(main);
  const left = [...(trunc ? ['a cut-off subtitle'] : []), ...s.dropped];
  if (left.length) b.notes.push({ part: 'Book', level: 'check', text: `Left out of the title: ${left.join(', ')}` });
  b.notes.push({ part: 'Book', level: 'check', text: 'Title set in sentence case: check names and acronyms' });
}

/**
 * A year the pages print (a © line, an arXiv stamp). A year in the filename alone is not taken
 * (the trial found 7 of 24 wrong): the line keeps a `?`, and the note says what the filename says.
 */
function yearOf(text: string, path: string): { year: string | null; note: Note } {
  const c = text.match(/(?:©|\(c\)|copyright)\s*(?:©\s*)?((?:19|20)\d{2})\b/i) ?? text.match(/arXiv:\S+\s+\[[^\]]+\]\s+\d{1,2}\s+\w{3}\s+((?:19|20)\d{2})/);
  if (c) return { year: c[1], note: { part: 'Year', level: 'check', text: `From the pages read: "${c[0]}"` } };
  const y = words(path).find((w) => /^(19|20)\d{2}$/.test(w));
  return {
    year: null,
    note: { part: 'Year', level: '?', text: y ? `No year in the PDF's details or pages; the filename says ${y}, which is often the file's year, not the edition's` : "No year in the PDF's details, the pages read or the filename" },
  };
}

/** A page's text as lines: a new line where pdf.js marks one or where the baseline moves. */
export function textLines(items: readonly { str: string; hasEOL?: boolean; transform: number[] }[]): string[] {
  const out: string[] = [];
  let cur = '';
  let y: number | null = null;
  const flush = () => {
    if (cur.trim()) out.push(cur.trim().replace(/\s+/g, ' '));
    cur = '';
  };
  for (const it of items) {
    const iy = it.transform[5];
    if (y !== null && Math.abs(iy - y) > 2) flush();
    cur += it.str;
    y = iy;
    if (it.hasEOL) {
      flush();
      y = null;
    }
  }
  flush();
  return out;
}

/** A page's first and last three lines (a short page's lines once each): where running heads and feet are. */
const ends = (lines: string[]) => (lines.length <= 6 ? lines : [...lines.slice(0, 3), ...lines.slice(-3)]);

/** Standalone numbers at the start or end of a page's first and last three lines (running heads and feet); a section number like 2.1 is not one. */
export function folios(lines: string[]): number[] {
  const out: number[] = [];
  for (const l of ends(lines)) {
    for (const m of l.matchAll(/(?<![\d.])(\d{1,4})(?![\d.])/g)) if (l.startsWith(m[1]) || l.endsWith(m[1])) out.push(Number(m[1]));
  }
  return out;
}

/**
 * The printed page range: the last page's number `e` and a start `e - pages + 1` that page 1
 * prints, or that page 2 prints plus one (a chapter opener often has no number). A pair that does
 * not fit the page count is `?` with the reason; no numbers at all leaves pages out.
 */
export function pageRange(f: PdfFacts): { range: [number, number] | null; unknown: boolean; note: Note } {
  const n = f.pages;
  const part = 'Pages';
  if (f.labels && /^\d+$/.test(f.labels[0]) && /^\d+$/.test(f.labels[1]) && f.labels[0] !== '1') {
    const [s, e] = f.labels.map(Number);
    if (e - s + 1 === n) return { range: [s, e], unknown: false, note: { part, level: 'found', text: "From the PDF's page labels" } };
  }
  const c1 = folios(f.first), c2 = n > 1 ? folios(f.second) : [], cl = folios(f.last);
  let best: { s: number; e: number; on1: boolean; on2: boolean } | null = null;
  for (const e of cl) {
    const s = e - n + 1;
    const on1 = c1.includes(s), on2 = n > 1 && c2.includes(s + 1);
    if (s > 0 && (on1 || on2) && (!best || +on1 + +on2 > +best.on1 + +best.on2)) best = { s, e, on1, on2 };
  }
  if (best) {
    const text = best.on1
      ? `${best.s} printed on page 1 and ${best.e} on page ${n}: ${n} pages`
      : `${best.e} printed on page ${n}; page 1 has no number, ${best.s} counted back from ${best.s + 1} on page 2`;
    return { range: [best.s, best.e], unknown: false, note: { part, level: best.on1 ? 'found' : 'check', text } };
  }
  const start = c1[0] ?? (c2.length ? c2[0] - 1 : undefined);
  if (start !== undefined && cl.length) {
    const e = cl[cl.length - 1];
    return { range: null, unknown: true, note: { part, level: '?', text: `Read ${start} at the start and ${e} on page ${n}, which does not fit the file's ${n} pages` } };
  }
  return { range: null, unknown: false, note: { part, level: '?', text: `No printed page numbers on pages 1, 2 or ${n}: none given` } };
}

/**
 * The chapter: a "CHAPTER N" opener on page 1, "Chapter N" running heads (`4 | Chapter 1: …`,
 * `Chapter 12 373`), or an opener that starts with its number ("1 Machines that read…" above
 * "This chapter covers", or a large "14" alone on the first line). The number most of them give wins.
 */
export function chapterOf(f: PdfFacts): { n: number | null; note: Note } {
  const part = 'Chapter';
  const first = f.first.find((l) => l.trim()) ?? '';
  const opener = first.match(/^chapter\s+(\d+)\s*$/i);
  if (opener) return { n: Number(opener[1]), note: { part, level: 'found', text: `"${first.trim()}" opens page 1` } };
  const seen: number[] = [];
  for (const l of [...ends(f.first), ...ends(f.second), ...ends(f.last)]) {
    const m = l.match(/^(?:\d+\s*\|?\s*)?chapter\s+(\d+)\b/i);
    if (m) seen.push(Number(m[1]));
  }
  const lead = first.match(/^(\d{1,2})(?:\s+\S.*)?$/);
  const leadIsChapter = lead && (lead[0] === lead[1] || f.first.slice(0, 5).some((l) => /^this chapter covers/i.test(l.trim())));
  if (leadIsChapter) seen.push(Number(lead[1]));
  if (!seen.length) return { n: null, note: { part, level: '?', text: 'No "Chapter N" on the pages read: add one if this is part of a chapter' } };
  const count = new Map<number, number>();
  for (const x of seen) count.set(x, (count.get(x) ?? 0) + 1);
  const [n, times] = [...count].sort((a, b) => b[1] - a[1])[0];
  const text = leadIsChapter && Number(lead[1]) === n && times === 1
    ? `Page 1 opens with "${lead[1]}", likely the chapter number`
    : `"Chapter ${n}" in the running heads${count.size > 1 ? ` (other numbers seen: ${[...count.keys()].filter((k) => k !== n).join(', ')})` : ''}`;
  return { n, note: { part, level: times > 1 && count.size === 1 ? 'found' : 'check', text } };
}

/** The APA 7 line for a book part: `Authors (Year). *Title* (2nd ed., Ch. 15, pp. 499–538). Publisher.` */
export function apaLine(b: Pick<Book, 'authors' | 'year' | 'title' | 'edition' | 'publisher'>, chapter: number | null, pages: { range: [number, number] | null; unknown: boolean }): string {
  const paren = [
    b.edition ? `${b.edition} ed.` : '',
    chapter !== null ? `Ch. ${chapter}` : '',
    pages.range ? (pages.range[0] === pages.range[1] ? `p. ${pages.range[0]}` : `pp. ${pages.range[0]}–${pages.range[1]}`) : pages.unknown ? 'pp. ?' : '',
  ].filter(Boolean);
  const title = b.title ? `*${b.title}*` : '*?*';
  const end = b.title && /[?!]$/.test(b.title) && !paren.length ? '' : '.';
  return `${apaAuthors(b.authors)} (${b.year ?? '?'}). ${title}${paren.length ? ` (${paren.join(', ')})` : ''}${end} ${b.publisher ?? '?'}.`;
}

/** The filename less its session mark, `opt` and a trailing part number: files of one book share it (`s1-hobson-dyshel-2024-opt` ~ `s2-hobson-dyshel-2024-2`). */
const workKey = (path: string) => words(path).filter((w, i, all) => !(i === 0 && /^s\d+$/.test(w)) && w !== 'opt' && !(i === all.length - 1 && /^\d$/.test(w))).join('-');

const RANK: Record<Level, number> = { '?': 0, guess: 1, check: 2, found: 3 };
/** Notes least sure first (`?`, `guess`, `check`, `found`), in their order within a level. */
export const byLevel = (notes: Note[]) => [...notes].sort((a, b) => RANK[a.level] - RANK[b.level]);

/** Books drafted this session, by their metadata title: the same book across files (and dialogs) is drafted once. */
const books = new Map<string, Book>();
export const forgetBooks = () => books.clear();

/**
 * One line per file. A file whose details name no book takes the book of a file with the same
 * filename stem (`check`, saying which).
 */
export function draftReadings(files: { path: string; facts: PdfFacts }[]): Draft[] {
  const own = files.map(({ path, facts }) => {
    const key = cleanTitle(facts.title).title;
    const text = [...facts.first, ...facts.second, ...facts.last].join('\n');
    let b = key ? books.get(key) : undefined;
    if (!b) {
      b = bookOf(facts, path, text);
      if (key && b.title) books.set(key, b);
    }
    return { path, facts, book: b };
  });
  return own.map(({ path, facts, book }) => {
    let b = book;
    if (!b.title) {
      const twin = own.find((o) => o.path !== path && o.book.title && workKey(o.path) === workKey(path));
      if (twin) {
        // The twin's authors, year and publisher come with the notes that say how sure they are.
        const lent = twin.book.notes.filter((n) => n.part !== 'Book').map((n) => ({ ...n, text: `As for ${baseName(twin.path)}: ${n.text}` }));
        b = { ...twin.book, notes: [{ part: 'Book', level: 'check', text: `No book in this PDF's details: the same as ${baseName(twin.path)}, by its filename` }, ...lent] };
      }
    }
    const pages = pageRange(facts);
    const ch = chapterOf(facts);
    const sec = sectionOf(path);
    return { path, section: sec.section, line: apaLine(b, ch.n, pages), notes: byLevel([...b.notes, ch.note, pages.note, sec.note]) };
  });
}

export const SECTION_HEADING: Record<Section, string> = { required: 'Required readings', additional: 'Additional readings' };

/** `to` relative to the folder of `from` (both repo paths), for a link in the list. */
export function relPath(from: string, to: string): string {
  const a = from.split('/').slice(0, -1), b = to.split('/');
  let i = 0;
  while (i < a.length && i < b.length - 1 && a[i] === b[i]) i++;
  return [...a.slice(i).map(() => '..'), ...b.slice(i)].join('/');
}

/**
 * The list with `- <line> [PDF](<file>)` added under `## Required readings` / `## Additional
 * readings`: after the section's last line when the heading is there, else under a new heading
 * (Required before an existing Additional, otherwise at the end). `listPath` places the links; a
 * file in another repo than the list (`listRepo`) gets no link, a relative one would not reach it.
 */
export function insertLines(text: string, listPath: string, adds: { section: Section; line: string; path: string; repo?: string }[], listRepo?: string): string {
  const lines = text.replace(/\s+$/, '').split('\n');
  if (lines.length === 1 && lines[0] === '') lines.pop();
  const heading = (s: Section) => lines.findIndex((l) => new RegExp(`^##\\s+${SECTION_HEADING[s]}\\s*$`, 'i').test(l.trim()));
  for (const s of ['required', 'additional'] as Section[]) {
    const items = adds.filter((a) => a.section === s).map((a) => {
      if (listRepo && a.repo && a.repo !== listRepo) return `- ${a.line}`;
      const rel = relPath(listPath, a.path);
      return `- ${a.line} [PDF](${/[\s()<>]/.test(rel) ? `<${rel}>` : rel})`;
    });
    if (!items.length) continue;
    let h = heading(s);
    if (h < 0) {
      // Required goes above an existing Additional; otherwise a new heading goes at the end.
      const at = s === 'required' && heading('additional') >= 0 ? heading('additional') : lines.length;
      const add = at === lines.length ? [...(lines.length ? [''] : []), `## ${SECTION_HEADING[s]}`] : [`## ${SECTION_HEADING[s]}`, ''];
      lines.splice(at, 0, ...add);
      h = at === lines.length - add.length ? lines.length - 1 : at;
    }
    let next = h + 1;
    while (next < lines.length && !/^#{1,2}\s/.test(lines[next])) next++;
    let end = next;
    while (end > h + 1 && !lines[end - 1].trim()) end--;
    if (end === h + 1) lines.splice(h + 1, next - h - 1, '', ...items, ...(next < lines.length ? [''] : []));
    else lines.splice(end, 0, ...items);
  }
  return `${lines.join('\n')}\n`;
}
