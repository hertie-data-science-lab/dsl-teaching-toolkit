// Generate draft's extractors (decision 0044 point 6) on fixture text: the PDF details, the
// printed page numbers and the chapter as pdf.js reads them from NLP's reading PDFs (trimmed to
// the lines the heuristics look at), and the lines' insertion into a reading list.

import { beforeEach, describe, expect, it } from 'vitest';
import {
  apaAuthors, apaLine, chapterOf, cleanTitle, draftReadings, folios, forgetBooks, hasUnknown, insertLines, pageRange, parseNames, relPath, sectionOf, sentenceCase, textLines, type PdfFacts,
} from '../src/model/readingDraft';

const facts = (over: Partial<PdfFacts>): PdfFacts => ({ pages: 1, title: '', author: '', first: [], second: [], last: [], labels: null, ...over });

const RASCHKA = ' SEBASTIAN LIU YUXI (HAYDEN) MIRJALILI VAHID RASCHKA - MACHINE LEARNING WITH PYTORCH AND SCIKIT-LEARN _ develop machine learning and deep learning... models with scikit-learn and pyto (2022, PACKT PUBLISHING LIMITED) - libgen.li';
const VAJJALA = " Sowmya Vajjala, Bodhisattwa Majumder, Anuj Gupta, Harshit Surana - Practical Natural Language Processing_ A Comprehensive Guide to Building Real-World NLP Systems (2020, O'Reilly Media, Inc.) - libgen.li";
const HOBSON = ' Hobson Lane and Maria Dyshel - Natural Language Processing in Action, Second Edition (MEAP V12) (2024, Manning Publications Co.) - libgen.li';

/** An O'Reilly chapter opener: "CHAPTER 1" on top, the folio at the foot of page 1, "N | Chapter 1: …" feet after. */
const vajjala = facts({
  pages: 34, title: VAJJALA, author: 'Sascha Göbel ',
  first: ['CHAPTER 1', 'NLP: A Primer', 'A language is not just words.', 'can represent language data in binary, how do we make machines', '3'],
  second: ['language? This is where natural language processing (NLP) comes in.', 'have some form of NLP as a major component.', '4 | Chapter 1: NLP: A Primer'],
  last: ['[33] Wang, Yaqing, Quanming Yao.', 'ing Scholarship”, (2018).', '36 | Chapter 1: NLP: A Primer'],
});
/** A Packt chapter opener: a large "15" (the chapter, not a page) and no folio; heads glue the title to the number. */
const raschka = facts({
  pages: 40, title: RASCHKA, author: 'Sascha Göbel ',
  first: ['15', 'Modeling Sequential Data Using', 'Recurrent Neural Networks', 'of a model.'],
  second: ['Modeling Sequential Data Using Recurrent Neural Networks500', 'Modeling sequential data – order matters'],
  last: ['Modeling Sequential Data Using Recurrent Neural Networks538', 'Join our book’s Discord space', 'https://packt.link/MLwPyTorch'],
});
/** A Manning MEAP pre-print: the chapter number leads its title, no printed page numbers, a stray year at the foot of the last page. */
const hobson = facts({
  pages: 57, title: HOBSON, author: 'Sascha Göbel ',
  first: ['3 Math with words (TF-IDF', 'vectors)', 'This chapter covers'],
  second: ['representation with more exciting math.', '[1]'],
  last: ['[17] For a complete list, see http://icame.uib.no', 'the Best 2017', 'https://opensearch.org/blog/'],
});
/** A textbook draft: section numbers lead the running heads ("2.1 • … 5"); its details carry a URL, not a title. */
const jurafsky = facts({
  pages: 9, title: 'https/web.stanford.edu/~jurafsky/slp3/ed3bookaug20_2024', author: 'Sascha Göbel ',
  first: ['2.1 • REGULAR EXPRESSIONS 5', 'Some languages, like Japanese, don’t have spaces'],
  second: ['6 CHAPTER 2 • REGULAR EXPRESSIONS, TOKENIZATION, EDIT DISTANCE', 'Regex Example Patterns Matched'],
  last: ['2.2 • WORDS 13', 'ELIZA works by having a series'],
});
/** A paper: no details, an arXiv stamp, footnote lines that start with a number. */
const bertopic = facts({
  pages: 10,
  first: ['BERTopic: Neural topic modeling with a class-based TF-IDF procedure', 'Maarten Grootendorst', 'arXiv:2203.05794v1 [cs.CL] 11 Mar 2022'],
  second: ['model that leverages clustering techniques and a'],
  last: ['Suzanna Sia, Ayush Dalmia, and Sabrina J Mielke.', 'arXiv:2103.00498.'],
});

beforeEach(() => forgetBooks());

describe('the PDF details', () => {
  it('lose a trailing " - <domain>" or " | <site>"', () => {
    expect(cleanTitle(HOBSON)).toEqual({ title: 'Hobson Lane and Maria Dyshel - Natural Language Processing in Action, Second Edition (MEAP V12) (2024, Manning Publications Co.)', cut: '- libgen.li' });
    expect(cleanTitle('Deep Learning | MIT Press')).toEqual({ title: 'Deep Learning', cut: '| MIT Press' });
    expect(cleanTitle('Speech and Language Processing - Draft')).toEqual({ title: 'Speech and Language Processing - Draft', cut: null });
  });

  it('give APA authors, "Given Family" and "Family, Given" alike; a string with no separators is no list', () => {
    expect(apaAuthors(parseNames('Hobson Lane and Maria Dyshel'))).toBe('Lane, H., & Dyshel, M.');
    expect(apaAuthors(parseNames('Lewis Tunstall, Leandro von Werra, Thomas Wolf'))).toBe('Tunstall, L., von Werra, L., & Wolf, T.');
    expect(apaAuthors(parseNames('Raschka, Sebastian; Liu, Yuxi (Hayden); Mirjalili, Vahid'))).toBe('Raschka, S., Liu, Y. H., & Mirjalili, V.');
    expect(apaAuthors(parseNames('Raschka, Sebastian'))).toBe('Raschka, S.');
    expect(apaAuthors(parseNames('Jean-Paul Sartre'))).toBe('Sartre, J.-P.');
    expect(parseNames('SEBASTIAN LIU YUXI (HAYDEN) MIRJALILI VAHID RASCHKA')).toBeNull();
    expect(apaAuthors(null)).toBe('?');
  });

  it('give a sentence-case title that keeps acronyms and names with inner capitals', () => {
    expect(sentenceCase('Machine Learning with PyTorch and Scikit-Learn')).toBe('Machine learning with PyTorch and Scikit-Learn');
    expect(sentenceCase('Practical Natural Language Processing: A Comprehensive Guide to NLP')).toBe('Practical natural language processing: A comprehensive guide to NLP');
    expect(sentenceCase('MACHINE LEARNING WITH PYTORCH')).toBe('Machine learning with pytorch');
  });

  it('read a shadow-library title as authors, title, edition, year and publisher', () => {
    const [d] = draftReadings([{ path: 'readings/s3-hobson-dyshel-2024-opt.pdf', facts: hobson }]);
    expect(d.line).toBe('Lane, H., & Dyshel, M. (2024). *Natural language processing in action* (2nd ed., Ch. 3, pp. ?). Manning Publications.');
    expect(d.notes.filter((n) => n.part === 'Book').map((n) => [n.level, n.text])).toEqual([
      ['found', 'From the PDF\'s details: "Hobson Lane and Maria Dyshel - Natural Language Processing in Action, Second Edition (MEAP V12) (2024, Manning Publications Co.)"'],
      ['check', 'Took "- libgen.li" off the end of the title'],
      ['check', 'Left out of the title: (MEAP V12)'],
      ['check', 'Title set in sentence case: check names and acronyms'],
    ]);
  });

  it('fall back to the filename for authors and year when the details have no title, each part tagged', () => {
    const [d] = draftReadings([{ path: 'readings/02_session-2__s2-jurafsky-martin-2024.pdf', facts: jurafsky }]);
    expect(d.line).toBe('Jurafsky, ?, & Martin, ? (2024). *?* (Ch. 2, pp. 5–13). ?.');
    expect(d.notes.map((n) => [n.part, n.level])).toEqual([['Book', '?'], ['Authors', 'guess'], ['Year', 'guess'], ['Publisher', '?'], ['Chapter', 'check'], ['Pages', 'found'], ['Section', 'found']]);
    expect(d.notes[1].text).toBe('Surnames from the filename (the PDF\'s details name "Sascha Göbel", not in the filename)');
  });

  it('take a year from an arXiv stamp on the pages', () => {
    const [d] = draftReadings([{ path: 'readings/s6-bertopic-opt.pdf', facts: bertopic }]);
    expect(d.line).toBe('Bertopic, ? (2022). *?*. ?.');
    expect(d.notes.find((n) => n.part === 'Year')).toEqual({ part: 'Year', level: 'check', text: 'From the pages read: "arXiv:2203.05794v1 [cs.CL] 11 Mar 2022"' });
  });
});

describe('the printed page range', () => {
  it('reads folios at the ends of head and foot lines, not section numbers', () => {
    expect(folios(jurafsky.first)).toEqual([5]);
    expect(folios(raschka.second)).toEqual([500]);
    expect(folios(['4 | Chapter 1: NLP: A Primer'])).toEqual([4]);
  });

  it('is the first page’s number to the last page’s when they fit the page count', () => {
    expect(pageRange(vajjala)).toMatchObject({ range: [3, 36], note: { level: 'found', text: '3 printed on page 1 and 36 on page 34: 34 pages' } });
  });

  it('counts back from page 2 when a chapter opener has no number (`check`)', () => {
    expect(pageRange(raschka)).toMatchObject({ range: [499, 538], note: { level: 'check', text: '538 printed on page 40; page 1 has no number, 499 counted back from 500 on page 2' } });
  });

  it('is `?` with the reason when the numbers do not fit, and left out when there are none', () => {
    expect(pageRange(hobson)).toMatchObject({ range: null, unknown: true, note: { level: '?', text: 'Read 3 at the start and 2017 on page 57, which does not fit the file\'s 57 pages' } });
    expect(pageRange(bertopic)).toMatchObject({ range: null, unknown: false, note: { level: '?', text: 'No printed page numbers on pages 1, 2 or 10: none given' } });
    expect(apaLine({ authors: null, year: null, title: 'T', edition: null, publisher: 'P' }, null, { range: null, unknown: true })).toBe('? (?). *T* (pp. ?). P.');
  });

  it('takes numeric page labels that fit the page count', () => {
    expect(pageRange(facts({ pages: 12, labels: ['81', '92'] }))).toMatchObject({ range: [81, 92], note: { level: 'found' } });
  });
});

describe('the chapter', () => {
  it('reads a CHAPTER N opener, running heads, and a number that opens page 1', () => {
    expect(chapterOf(vajjala)).toMatchObject({ n: 1, note: { level: 'found', text: '"CHAPTER 1" opens page 1' } });
    expect(chapterOf(jurafsky)).toMatchObject({ n: 2, note: { level: 'check', text: '"Chapter 2" in the running heads' } });
    expect(chapterOf(raschka)).toMatchObject({ n: 15, note: { level: 'check', text: 'Page 1 opens with "15", likely the chapter number' } });
    expect(chapterOf(hobson)).toMatchObject({ n: 3, note: { level: 'check' } });
    expect(chapterOf(facts({ first: ['In Chapter 2, we saw what it takes'] }))).toMatchObject({ n: null, note: { level: '?' } });
  });
});

describe('the section', () => {
  it('is Additional with "opt" in the filename, else Required', () => {
    expect(sectionOf('r/07_session-7__s7-raschka-2022-opt-1.pdf').section).toBe('additional');
    expect(sectionOf('r/s7-raschka-2022-1.pdf').section).toBe('required');
    expect(sectionOf('r/options.pdf').section).toBe('required');
  });
});

describe('the drafted line', () => {
  it('reuses a book across files by its title, and lends it to a file with the same filename stem', () => {
    const noTitle = facts({ ...hobson, title: 's2- hobson-dyshel-2024', first: ['positive feedback loop'] });
    const out = draftReadings([
      { path: 'r/01_session-1__s1-hobson-dyshel-2024-opt.pdf', facts: hobson },
      { path: 'r/02_session-2__s2-hobson-dyshel-2024-opt.pdf', facts: noTitle },
    ]);
    expect(out[1].line).toBe('Lane, H., & Dyshel, M. (2024). *Natural language processing in action* (2nd ed.). Manning Publications.');
    expect(out[1].notes[0]).toEqual({ part: 'Book', level: 'check', text: 'No book in this PDF\'s details: the same as 01_session-1__s1-hobson-dyshel-2024-opt.pdf, by its filename' });
  });

  it('is the brief’s APA 7 shape', () => {
    const [d] = draftReadings([{ path: 'r/s8-raschka-2024.pdf', facts: raschka }]);
    expect(d.line).toBe('Raschka, ? (2022). *Machine learning with pytorch and scikit-learn* (Ch. 15, pp. 499–538). Packt Publishing.');
    expect(d.notes.find((n) => n.part === 'Authors')!.text).toBe('Surnames from the filename (the PDF\'s details name "SEBASTIAN LIU YUXI (HAYDEN) MIRJALILI VAHID RASCHKA", which does not split into people)');
  });

  it('marks a free-standing ? as unknown, never one attached to a word', () => {
    expect(hasUnknown('Raschka, ? (2022).')).toBe(true);
    expect(hasUnknown('*?*')).toBe(true);
    expect(hasUnknown('pp. ??–12')).toBe(true);
    expect(hasUnknown('*Tired of topic models?* (2020).')).toBe(false);
    expect(hasUnknown('What?! (2020).')).toBe(false);
  });
});

describe('pdf.js text as lines', () => {
  it('breaks at an end-of-line mark and where the baseline moves', () => {
    const at = (y: number) => [1, 0, 0, 1, 0, y];
    expect(textLines([
      { str: 'Chapter 12', transform: at(700) }, { str: ' 373', transform: at(700), hasEOL: true },
      { str: 'Saving and', transform: at(680) }, { str: ' reloading', transform: at(681) },
      { str: '15', transform: at(40) },
    ])).toEqual(['Chapter 12 373', 'Saving and reloading', '15']);
  });
});

describe('adding lines to the list', () => {
  const add = (section: 'required' | 'additional', name: string) => ({ section, line: `L ${name}`, path: `readings/04/${name}` });

  it('creates the headings when absent, Required first', () => {
    expect(insertLines('', 'readings/04/READINGS.md', [add('additional', 'b.pdf'), add('required', 'a.pdf')]))
      .toBe('## Required readings\n\n- L a.pdf [PDF](a.pdf)\n\n## Additional readings\n\n- L b.pdf [PDF](b.pdf)\n');
    expect(insertLines('Read these before class.\n', 'readings/04/READINGS.md', [add('required', 'a.pdf')]))
      .toBe('Read these before class.\n\n## Required readings\n\n- L a.pdf [PDF](a.pdf)\n');
  });

  it('appends under existing headings, and puts a new Required above an existing Additional', () => {
    const text = '# Week 4\n\n## Required readings\n\n- Bishop.\n\n## Additional readings\n\n- Murphy.\n';
    expect(insertLines(text, 'readings/04/READINGS.md', [add('required', 'a.pdf'), add('additional', 'b.pdf')]))
      .toBe('# Week 4\n\n## Required readings\n\n- Bishop.\n- L a.pdf [PDF](a.pdf)\n\n## Additional readings\n\n- Murphy.\n- L b.pdf [PDF](b.pdf)\n');
    expect(insertLines('## Additional readings\n\n- Murphy.\n', 'readings/04/READINGS.md', [add('required', 'a.pdf')]))
      .toBe('## Required readings\n\n- L a.pdf [PDF](a.pdf)\n\n## Additional readings\n\n- Murphy.\n');
    expect(insertLines('## Required readings\n\n## Additional readings\n- Murphy.\n', 'readings/04/READINGS.md', [add('required', 'a.pdf')]))
      .toBe('## Required readings\n\n- L a.pdf [PDF](a.pdf)\n\n## Additional readings\n- Murphy.\n');
  });

  it('links each file relative to the list, in angle brackets when the name has a space', () => {
    expect(relPath('lectures/05/READINGS.md', 'readings/05/a.pdf')).toBe('../../readings/05/a.pdf');
    expect(relPath('READINGS.md', 'readings/05/a.pdf')).toBe('readings/05/a.pdf');
    expect(insertLines('', 'readings/04/READINGS.md', [{ section: 'required', line: 'L', path: 'readings/04/week 4.pdf' }])).toBe('## Required readings\n\n- L [PDF](<week 4.pdf>)\n');
  });
});
