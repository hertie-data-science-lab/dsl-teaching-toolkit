// What Generate draft reads from a PDF (decision 0044 point 6): its details, its page count and
// labels, and the text lines of pages 1, 2 and the last page. pdf.js runs in the browser; this
// module is only ever imported dynamically (`import('./pdfText')`), so pdf.js and its worker are
// a chunk of their own, fetched on the first Generate draft. The worker is bundled by Vite from
// the console's own origin (the CSP allows no other script source).

import { GlobalWorkerOptions, getDocument } from 'pdfjs-dist';
import type { TextItem } from 'pdfjs-dist/types/src/display/api';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
import { textLines, type PdfFacts } from './readingDraft';

GlobalWorkerOptions.workerSrc = workerUrl;

const str = (v: unknown) => (typeof v === 'string' ? v : '');

/** `bytes` are handed to pdf.js's worker (transferred, so emptied here): pass bytes no one keeps. */
export async function readPdf(bytes: Uint8Array): Promise<PdfFacts> {
  // Fonts are not drawn, only text read. (pdf.js 6 evaluates no code: the CSP has no 'unsafe-eval'.)
  const task = getDocument({ data: bytes, disableFontFace: true, verbosity: 0 });
  try {
    const doc = await task.promise;
    const n = doc.numPages;
    const page = async (i: number) => textLines((await (await doc.getPage(i)).getTextContent()).items.filter((x): x is TextItem => 'str' in x));
    const [meta, labels, first, second, end] = await Promise.all([
      doc.getMetadata(), doc.getPageLabels().catch(() => null), page(1), n > 1 ? page(2) : [], n > 2 ? page(n) : null,
    ]);
    const info = (meta.info ?? {}) as Record<string, unknown>;
    return {
      pages: n,
      title: str(info.Title),
      author: str(info.Author),
      first,
      second,
      last: end ?? (n > 1 ? second : first),
      labels: labels && labels.length === n ? [labels[0], labels[n - 1]] : null,
    };
  } finally {
    void task.destroy();
  }
}
