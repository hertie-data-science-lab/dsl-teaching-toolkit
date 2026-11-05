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

export async function readPdf(bytes: Uint8Array): Promise<PdfFacts> {
  // A copy: pdf.js hands its data to the worker, which would empty the client's kept bytes.
  // Fonts are not drawn, only text read. (pdf.js 6 evaluates no code: the CSP has no 'unsafe-eval'.)
  const task = getDocument({ data: bytes.slice(), disableFontFace: true, verbosity: 0 });
  try {
    const doc = await task.promise;
    const n = doc.numPages;
    const info = ((await doc.getMetadata()).info ?? {}) as Record<string, unknown>;
    const labels = await doc.getPageLabels().catch(() => null);
    const page = async (i: number) => textLines((await (await doc.getPage(i)).getTextContent()).items.filter((x): x is TextItem => 'str' in x));
    return {
      pages: n,
      title: str(info.Title),
      author: str(info.Author),
      first: await page(1),
      second: n > 1 ? await page(2) : [],
      last: n > 2 ? await page(n) : n > 1 ? await page(2) : await page(1),
      labels: labels && labels.length === n ? [labels[0], labels[n - 1]] : null,
    };
  } finally {
    void task.destroy();
  }
}
