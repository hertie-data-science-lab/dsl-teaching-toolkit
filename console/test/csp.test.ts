import { describe, expect, it } from 'vitest';
import { cspPolicy, relayOrigin, withCsp } from '../src/csp';

describe('Content-Security-Policy', () => {
  it('allows GitHub and the relay origin only, with no frames', () => {
    const p = cspPolicy('https://dsl-console-auth.example.workers.dev/exchange');
    expect(p).toContain("connect-src 'self' https://api.github.com https://github.com https://dsl-console-auth.example.workers.dev;");
    expect(p).toContain("default-src 'self'");
    expect(p).toContain("img-src 'self' https://avatars.githubusercontent.com https://github.com data:");
    expect(p).toContain("style-src 'self' 'unsafe-inline' https://fonts.googleapis.com");
    expect(p).toContain('font-src https://fonts.gstatic.com');
    expect(p).toContain("frame-src 'none'");
    expect(cspPolicy('')).toContain("connect-src 'self' https://api.github.com https://github.com;");
    expect(relayOrigin('not a url')).toBe('');
    expect(relayOrigin('javascript:alert(1)')).toBe('');
  });

  it('is filled into index.html on a build and taken out for the dev server', () => {
    const html = '<head>\n<meta charset="utf-8">\n<meta http-equiv="Content-Security-Policy" content="%CONSOLE_CSP%">\n<title>x</title>';
    expect(withCsp(html, true, 'https://r.example')).toContain('content="default-src');
    expect(withCsp(html, true, 'https://r.example')).toContain('https://r.example;');
    expect(withCsp(html, false)).not.toContain('Content-Security-Policy');
    expect(withCsp(html, false)).toContain('<title>x</title>');
  });
});
