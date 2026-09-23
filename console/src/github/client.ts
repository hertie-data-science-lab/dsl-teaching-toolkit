// A small fetch-based GitHub REST client. Every call goes out with the signed-in user's
// token; GET responses are cached in memory by URL and revalidated with If-None-Match, so a
// 304 costs no rate limit and returns the cached body.

export type Fetch = (input: string, init?: RequestInit) => Promise<Response>;

export interface RateLimit {
  limit: number;
  remaining: number;
  reset: number; // epoch seconds
}

export interface GhUser {
  login: string;
  id: number;
  name: string | null;
  email: string | null;
  avatar_url: string;
}

export interface GhOrg {
  login: string;
  avatar_url?: string;
  description?: string | null;
}

export interface GhRepo {
  name: string;
  full_name: string;
  private: boolean;
  archived?: boolean;
  default_branch: string;
  topics?: string[];
  permissions?: { admin?: boolean; maintain?: boolean; push?: boolean; triage?: boolean; pull?: boolean };
  html_url: string;
}

export interface FileContent {
  path: string;
  sha: string;
  text: string;
}

export interface DirEntry {
  name: string;
  path: string;
  sha: string;
  type: 'file' | 'dir' | 'symlink' | 'submodule';
}

export interface TreeEntry {
  path: string;
  mode: string;
  type: 'blob' | 'tree' | 'commit';
  sha: string;
}

export interface Tree {
  sha: string;
  tree: TreeEntry[];
  truncated: boolean;
}

export interface DispatchResult {
  workflow_run_id: number;
  run_url: string;
  html_url: string;
}

export interface WorkflowRun {
  id: number;
  event?: string;
  display_title?: string;
  status: string;
  conclusion: string | null;
  html_url: string;
  head_sha: string;
  created_at: string;
  updated_at: string;
}

export interface Job {
  id: number;
  name: string;
  status: string;
  conclusion: string | null;
  steps?: { name: string; status: string; conclusion: string | null; number: number }[];
}

export interface CheckRun {
  id: number;
  name: string;
  status: string;
  conclusion: string | null;
  html_url: string;
  output: { title: string | null; summary: string | null; annotations_count: number };
}

export interface Author {
  name: string;
  email: string;
}

export class GitHubError extends Error {
  constructor(
    readonly status: number,
    message: string,
    readonly url: string,
  ) {
    super(message);
    this.name = 'GitHubError';
  }
}

/** A sha-conditional write lost the race: the file changed since it was read. */
export class ConflictError extends GitHubError {
  constructor(url: string, message: string) {
    super(409, message, url);
    this.name = 'ConflictError';
  }
}

interface CacheEntry {
  etag: string;
  body: unknown;
  headers: Headers;
}

export interface ClientOptions {
  token: () => string | null;
  fetch?: Fetch;
  baseUrl?: string;
  onRateLimit?: (rl: RateLimit) => void;
}

export const API = 'https://api.github.com';

/** Base64 of UTF-8 text, and back. */
export function encodeBase64(text: string): string {
  const bytes = new TextEncoder().encode(text);
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin);
}

export function decodeBase64(b64: string): string {
  const bin = atob(b64.replace(/\s/g, ''));
  const bytes = Uint8Array.from(bin, (c) => c.charCodeAt(0));
  return new TextDecoder().decode(bytes);
}

function enc(path: string): string {
  return path.split('/').map(encodeURIComponent).join('/');
}

export class GitHubClient {
  private cache = new Map<string, CacheEntry>();
  private readonly fetchFn: Fetch;
  private readonly base: string;
  rateLimit: RateLimit | null = null;

  constructor(private readonly opts: ClientOptions) {
    this.fetchFn = opts.fetch ?? ((i, init) => globalThis.fetch(i, init));
    this.base = opts.baseUrl ?? API;
  }

  clearCache(): void {
    this.cache.clear();
  }

  private headers(extra?: Record<string, string>): Record<string, string> {
    const h: Record<string, string> = {
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      ...extra,
    };
    const t = this.opts.token();
    if (t) h.Authorization = `Bearer ${t}`;
    return h;
  }

  private noteRateLimit(res: Response): void {
    const limit = res.headers.get('x-ratelimit-limit');
    const remaining = res.headers.get('x-ratelimit-remaining');
    const reset = res.headers.get('x-ratelimit-reset');
    if (limit === null || remaining === null) return;
    this.rateLimit = { limit: Number(limit), remaining: Number(remaining), reset: Number(reset ?? 0) };
    this.opts.onRateLimit?.(this.rateLimit);
  }

  private url(path: string): string {
    return path.startsWith('http') ? path : `${this.base}${path}`;
  }

  private async fail(res: Response, url: string): Promise<never> {
    let message = `${res.status} ${res.statusText}`;
    try {
      const body = (await res.json()) as { message?: string };
      if (body?.message) message = body.message;
    } catch {
      /* not JSON */
    }
    if (res.status === 409) throw new ConflictError(url, message);
    throw new GitHubError(res.status, message, url);
  }

  /** GET with the ETag cache. Returns the body and the response headers. */
  async getRaw<T>(path: string): Promise<{ body: T; headers: Headers }> {
    const url = this.url(path);
    const cached = this.cache.get(url);
    const extra: Record<string, string> = cached ? { 'If-None-Match': cached.etag } : {};
    const res = await this.fetchFn(url, { method: 'GET', headers: this.headers(extra) });
    this.noteRateLimit(res);
    if (res.status === 304 && cached) return { body: cached.body as T, headers: cached.headers };
    if (!res.ok) return this.fail(res, url);
    const body = (await res.json()) as T;
    const etag = res.headers.get('etag');
    if (etag) this.cache.set(url, { etag, body, headers: res.headers });
    return { body, headers: res.headers };
  }

  async get<T>(path: string): Promise<T> {
    return (await this.getRaw<T>(path)).body;
  }

  /** GET that turns a 404 into null (a file or repo that is not there, or not visible). */
  async getOrNull<T>(path: string): Promise<T | null> {
    try {
      return await this.get<T>(path);
    } catch (e) {
      if (e instanceof GitHubError && e.status === 404) return null;
      throw e;
    }
  }

  async send<T>(method: string, path: string, body?: unknown): Promise<T | null> {
    const url = this.url(path);
    const res = await this.fetchFn(url, {
      method,
      headers: this.headers(body === undefined ? undefined : { 'Content-Type': 'application/json' }),
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    this.noteRateLimit(res);
    if (!res.ok) return this.fail(res, url);
    // A write changes what the next read returns: drop every cached GET under this repo.
    const m = /^\/repos\/[^/]+\/[^/]+/.exec(path);
    if (m) for (const k of [...this.cache.keys()]) if (k.startsWith(`${this.base}${m[0]}/`)) this.cache.delete(k);
    if (res.status === 204) return null;
    const text = await res.text();
    return text ? (JSON.parse(text) as T) : null;
  }

  // ---------------------------------------------------------------- typed helpers

  getUser(): Promise<GhUser> {
    return this.get<GhUser>('/user');
  }

  /** The signed-in user's orgs, every page. */
  async listUserOrgs(): Promise<GhOrg[]> {
    const out: GhOrg[] = [];
    for (let page = 1; page < 20; page++) {
      const batch = await this.get<GhOrg[]>(`/user/orgs?per_page=100&page=${page}`);
      out.push(...batch);
      if (batch.length < 100) break;
    }
    return out;
  }

  getRepo(owner: string, repo: string): Promise<GhRepo | null> {
    return this.getOrNull<GhRepo>(`/repos/${owner}/${repo}`);
  }

  async getRepoTopics(owner: string, repo: string): Promise<string[]> {
    const r = await this.getOrNull<{ names: string[] }>(`/repos/${owner}/${repo}/topics`);
    return r?.names ?? [];
  }

  /** A file's text and blob sha, or null when it is absent. */
  async getContents(owner: string, repo: string, path: string, ref?: string): Promise<FileContent | null> {
    const q = ref ? `?ref=${encodeURIComponent(ref)}` : '';
    const r = await this.getOrNull<{ type: string; path: string; sha: string; content?: string; encoding?: string } | DirEntry[]>(
      `/repos/${owner}/${repo}/contents/${enc(path)}${q}`,
    );
    if (!r || Array.isArray(r) || r.type !== 'file') return null;
    const text = r.encoding === 'base64' && r.content !== undefined ? decodeBase64(r.content) : (r.content ?? '');
    return { path: r.path, sha: r.sha, text };
  }

  /** A directory listing, or null when the directory is absent. */
  async listDir(owner: string, repo: string, path: string, ref?: string): Promise<DirEntry[] | null> {
    const q = ref ? `?ref=${encodeURIComponent(ref)}` : '';
    const r = await this.getOrNull<DirEntry[] | object>(`/repos/${owner}/${repo}/contents/${enc(path)}${q}`);
    return Array.isArray(r) ? r : null;
  }

  /**
   * Write a file as the signed-in user. `sha` is the blob the edit was based on (null for a
   * new file); GitHub refuses with 409 when the file has moved on, raised as ConflictError.
   */
  async putContents(args: {
    owner: string;
    repo: string;
    path: string;
    text: string;
    sha: string | null;
    message: string;
    author: Author;
    branch?: string;
  }): Promise<{ sha: string; commit: string }> {
    const body: Record<string, unknown> = {
      message: args.message,
      content: encodeBase64(args.text),
      author: args.author,
      committer: args.author,
    };
    if (args.sha) body.sha = args.sha;
    if (args.branch) body.branch = args.branch;
    const r = await this.send<{ content: { sha: string }; commit: { sha: string } }>(
      'PUT',
      `/repos/${args.owner}/${args.repo}/contents/${enc(args.path)}`,
      body,
    );
    return { sha: r!.content.sha, commit: r!.commit.sha };
  }

  /** One tree read: a ref's root tree (or a subtree by sha), optionally recursive. */
  listTree(owner: string, repo: string, ref: string, recursive = false): Promise<Tree | null> {
    return this.getOrNull<Tree>(`/repos/${owner}/${repo}/git/trees/${encodeURIComponent(ref)}${recursive ? '?recursive=1' : ''}`);
  }

  /** Dispatch a workflow and get its run back (return_run_details). */
  dispatchWorkflow(args: {
    owner: string;
    repo: string;
    workflow: string;
    ref: string;
    inputs: Record<string, string>;
  }): Promise<DispatchResult | null> {
    return this.send<DispatchResult>('POST', `/repos/${args.owner}/${args.repo}/actions/workflows/${encodeURIComponent(args.workflow)}/dispatches`, {
      ref: args.ref,
      inputs: args.inputs,
      return_run_details: true,
    });
  }

  getRun(owner: string, repo: string, runId: number): Promise<WorkflowRun> {
    return this.get<WorkflowRun>(`/repos/${owner}/${repo}/actions/runs/${runId}`);
  }

  /** A workflow's most recent runs, newest first (one ETag'd call). */
  async listWorkflowRuns(owner: string, repo: string, workflow: string, perPage = 30): Promise<WorkflowRun[]> {
    const r = await this.getOrNull<{ workflow_runs: WorkflowRun[] }>(
      `/repos/${owner}/${repo}/actions/workflows/${encodeURIComponent(workflow)}/runs?per_page=${perPage}`,
    );
    return r?.workflow_runs ?? [];
  }

  async listJobs(owner: string, repo: string, runId: number): Promise<Job[]> {
    return (await this.get<{ jobs: Job[] }>(`/repos/${owner}/${repo}/actions/runs/${runId}/jobs`)).jobs;
  }

  async listCheckRunsForRef(owner: string, repo: string, ref: string): Promise<CheckRun[]> {
    return (await this.get<{ check_runs: CheckRun[] }>(`/repos/${owner}/${repo}/commits/${encodeURIComponent(ref)}/check-runs`)).check_runs;
  }

  listCheckRunAnnotations(owner: string, repo: string, checkRunId: number): Promise<{ path: string; start_line: number; annotation_level: string; title: string | null; message: string }[]> {
    return this.get(`/repos/${owner}/${repo}/check-runs/${checkRunId}/annotations`);
  }

  async searchRepos(q: string): Promise<GhRepo[]> {
    return (await this.get<{ items: GhRepo[] }>(`/search/repositories?q=${encodeURIComponent(q)}&per_page=100`)).items;
  }

  /** Whether `user` is a member of `org` (as far as the caller may see). */
  async isOrgMember(org: string, user: string): Promise<boolean | null> {
    const url = this.url(`/orgs/${org}/members/${user}`);
    const res = await this.fetchFn(url, { method: 'GET', headers: this.headers() });
    this.noteRateLimit(res);
    if (res.status === 204) return true;
    if (res.status === 404 || res.status === 302) return false;
    return null;
  }
}

/** The commit author for a write made as `user`: their public email, or GitHub's noreply. */
export function authorOf(user: GhUser): Author {
  return { name: user.name || user.login, email: user.email || `${user.id}+${user.login}@users.noreply.github.com` };
}
