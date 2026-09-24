// Markdown an instructor wrote at length (an assignment brief, the home text), rendered by
// GitHub's own `/markdown` (sanitised by GitHub, headings and tables included), and the
// console's small renderer (`Md`) while that is on its way, when it fails, or with no
// session. Rendered once per text: the result is kept for the page's life.

import { useEffect, useState } from 'preact/hooks';
import { useEnv } from '../env';
import { Md } from './bits';

const rendered = new Map<string, Promise<string>>();

export function GhMd({ src, context, class: cls }: { src: string; context?: string; class?: string }) {
  const env = useEnv();
  const key = `${context ?? ''}\n${src}`;
  const [html, setHtml] = useState<string | null>(null);
  useEffect(() => {
    if (!env || !src.trim()) return;
    let live = true;
    let p = rendered.get(key);
    if (!p) {
      p = env.client.renderMarkdown(src, context);
      rendered.set(key, p);
      p.catch(() => rendered.delete(key));
    }
    p.then((h) => live && setHtml(h), () => {});
    return () => {
      live = false;
    };
  }, [key, !!env]);
  if (html === null) return <Md class={cls} src={src} />;
  return <div class={`md-body${cls ? ` ${cls}` : ''}`} dangerouslySetInnerHTML={{ __html: html }} />;
}

/** A fold whose body is built only once it is first opened (so a closed brief costs no call). */
export function LazyFold({ summary, children, open: start = false }: { summary: preact.ComponentChildren; children: () => preact.ComponentChildren; open?: boolean }) {
  const [seen, setSeen] = useState(start);
  return (
    <details class="fold" open={start} onToggle={(e) => (e.currentTarget as HTMLDetailsElement).open && setSeen(true)}>
      <summary>{summary}</summary>
      <div class="fold-body">{seen ? children() : null}</div>
    </details>
  );
}
