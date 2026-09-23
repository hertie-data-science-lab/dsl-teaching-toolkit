// A fake `fetch` for the GitHub client: a table of routes, and a log of every request.

import { encodeBase64 } from '../src/github/client';

export interface Seen {
  url: string;
  method: string;
  headers: Record<string, string>;
  body: unknown;
}

type Handler = (req: Seen) => Response | { status?: number; body?: unknown; headers?: Record<string, string> };

export function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(status === 204 || status === 304 ? null : JSON.stringify(body), {
    status,
    headers: { 'content-type': 'application/json', ...headers },
  });
}

export function fileBody(path: string, text: string, sha = 'blob-sha') {
  return { type: 'file', path, sha, encoding: 'base64', content: encodeBase64(text) };
}

export class FakeGitHub {
  seen: Seen[] = [];
  private routes: [string, RegExp | string, Handler][] = [];

  on(method: string, path: RegExp | string, h: Handler | object): this {
    this.routes.push([method, path, typeof h === 'function' ? (h as Handler) : () => json(h)]);
    return this;
  }

  fetch = async (input: string, init: RequestInit = {}): Promise<Response> => {
    const method = (init.method ?? 'GET').toUpperCase();
    const headers = (init.headers ?? {}) as Record<string, string>;
    const req: Seen = { url: input, method, headers, body: init.body ? JSON.parse(String(init.body)) : undefined };
    this.seen.push(req);
    const path = input.replace('https://api.github.com', '');
    for (const [m, p, h] of this.routes) {
      if (m !== method) continue;
      if (typeof p === 'string' ? p === path : p.test(path)) {
        const r = h(req);
        return r instanceof Response ? r : json(r.body ?? {}, r.status ?? 200, r.headers ?? {});
      }
    }
    return json({ message: 'Not Found' }, 404);
  };
}
