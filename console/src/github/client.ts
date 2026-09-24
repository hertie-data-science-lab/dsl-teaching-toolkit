// A small fetch-based GitHub REST client. Every call goes out with the signed-in user's
// token; GET responses are cached in memory by URL and revalidated with If-None-Match, so a
// 304 costs no rate limit and returns the cached body. The browser's own HTTP cache is kept
// out of it (`no-store`): GitHub sends `max-age=60`, so after a write dropped our entry the
// browser would otherwise answer the next read with the body from before the write.

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

export interface OrgMembership {
  state: string; // active | pending
  role: string; // admin | member
  organization: { login: string };
}

export interface GhRepo {
  name: string;
  full_name: string;
  private: boolean;
  archived?: boolean;
  default_branch: string;
  topics?: string[];
  pushed_at?: string | null;
  permissions?: { admin?: boolean; maintain?: boolean; push?: boolean; triage?: boolean; pull?: boolean };
  html_url: string;
  /** On a single-repo read: whether it is a fork, and of what. */
  fork?: boolean;
  parent?: { full_name: string };
}

export interface GhTeam {
  slug: string;
  name: string;
  organization: { login: string };
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
  /** Bytes, for a blob. */
  size?: number;
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

export interface GhIssue {
  number: number;
  title: string;
  state: string; // open | closed
  state_reason?: string | null;
  html_url: string;
  body?: string | null;
  created_at: string;
  comments: number;
  labels: { name: string }[];
  user?: { login: string } | null;
  /** Present when the "issue" is a pull request (the issues listing returns both). */
  pull_request?: object;
}

export interface GhComment {
  id: number;
  body: string;
  created_at: string;
  html_url: string;
  user?: { login: string } | null;
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

/** Bytes of base64 (whitespace allowed). */
export function decodeBytes(b64: string): Uint8Array {
  const bin = atob(b64.replace(/\s/g, ''));
  return Uint8Array.from(bin, (c) => c.charCodeAt(0));
}

/** The contents API's base64 ceiling: above it a file's bytes come from the blob API. */
export const ONE_MB = 1024 * 1024;
/** How many bytes of file content the client keeps by blob sha. */
export const BYTES_KEPT = 64 * ONE_MB;

/** Resolve after `ms` milliseconds: the pause between polls. */
export const wait = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));

function enc(path: string): string {
  return path.split('/').map(encodeURIComponent).join('/');
}

export class GitHubClient {
  private cache = new Map<string, CacheEntry>();
  private bytes = new Map<string, Uint8Array>();
  private bytesHeld = 0;
  private readonly fetchFn: Fetch;
  private readonly base: string;
  rateLimit: RateLimit | null = null;

  constructor(private readonly opts: ClientOptions) {
    this.fetchFn = opts.fetch ?? ((i, init) => globalThis.fetch(i, init));
    this.base = opts.baseUrl ?? API;
  }

  clearCache(): void {
    this.cache.clear();
    this.bytes.clear();
    this.bytesHeld = 0;
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
    const res = await this.fetchFn(url, { method: 'GET', headers: this.headers(extra), cache: 'no-store' });
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
      cache: 'no-store',
    });
    this.noteRateLimit(res);
    if (!res.ok) return this.fail(res, url);
    // A write changes what the next read returns: drop every cached GET under this repo,
    // however its owner and name were cased (GitHub reads them case-insensitively).
    const m = /^\/repos\/[^/]+\/[^/]+/.exec(path);
    const under = m ? `${this.base}${m[0]}/`.toLowerCase() : null;
    if (under) for (const k of [...this.cache.keys()]) if (k.toLowerCase().startsWith(under)) this.cache.delete(k);
    if (res.status === 204) return null;
    const text = await res.text();
    return text ? (JSON.parse(text) as T) : null;
  }

  // ---------------------------------------------------------------- typed helpers

  getUser(): Promise<GhUser> {
    return this.get<GhUser>('/user');
  }

  /** Every page of a listing (up to 1,900 items: 19 pages of 100, a runaway guard); `pick` takes the items out of a page. */
  async listPages<T>(path: string, pick: (page: unknown) => T[] = (p) => p as T[]): Promise<T[]> {
    const out: T[] = [];
    const sep = path.includes('?') ? '&' : '?';
    for (let page = 1; page < 20; page++) {
      const batch = pick(await this.get<unknown>(`${path}${sep}per_page=100&page=${page}`));
      out.push(...batch);
      if (batch.length < 100) break;
    }
    return out;
  }

  /** The signed-in user's orgs, every page. Empty for a fine-grained token (GitHub's rule). */
  listUserOrgs(): Promise<GhOrg[]> {
    return this.listPages<GhOrg>('/user/orgs');
  }

  /** The signed-in user's active org memberships, every page (classic and GitHub App tokens). */
  listOrgMemberships(): Promise<OrgMembership[]> {
    return this.listPages<OrgMembership>('/user/memberships/orgs?state=active');
  }

  /** The org invitations the signed-in user has not accepted yet, every page (classic and GitHub App tokens). */
  listPendingMemberships(): Promise<OrgMembership[]> {
    return this.listPages<OrgMembership>('/user/memberships/orgs?state=pending');
  }

  /** The accounts of the App installations the user's App token can see, every page (GitHub App tokens only). */
  async listInstallationAccounts(): Promise<{ login: string; type: string }[]> {
    const list = await this.listPages<{ account?: { login?: string; type?: string } | null }>('/user/installations', (p) => (p as { installations: [] }).installations ?? []);
    return list.flatMap((i) => (i.account?.login ? [{ login: i.account.login, type: i.account.type ?? '' }] : []));
  }

  /** The owners of the repos the token can reach, every page: the one listing a fine-grained token answers. */
  async listRepoOwners(): Promise<{ login: string; type: string }[]> {
    const repos = await this.listPages<{ owner: { login: string; type: string } }>('/user/repos');
    return repos.map((r) => r.owner);
  }

  /** `login`'s public org memberships, every page. */
  listPublicOrgs(login: string): Promise<GhOrg[]> {
    return this.listPages<GhOrg>(`/users/${encodeURIComponent(login)}/orgs`);
  }

  /** The signed-in user's own membership of `org`, or null when the token cannot tell (not a member, or out of its reach). */
  async getMyMembership(org: string): Promise<OrgMembership | null> {
    try {
      return await this.get<OrgMembership>(`/user/memberships/orgs/${encodeURIComponent(org)}`);
    } catch (e) {
      if (e instanceof GitHubError) return null;
      throw e;
    }
  }

  /** Every repo of an org the caller can see, every page. */
  listOrgRepos(org: string): Promise<GhRepo[]> {
    return this.listPages<GhRepo>(`/orgs/${encodeURIComponent(org)}/repos`);
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

  /** When `path` last changed on the default branch (its newest commit's date), or null for a path with no history. */
  async lastCommitDate(owner: string, repo: string, path: string): Promise<string | null> {
    const r = await this.getOrNull<{ commit?: { committer?: { date?: string } } }[]>(`/repos/${owner}/${repo}/commits?path=${encodeURIComponent(path)}&per_page=1`);
    return r?.[0]?.commit?.committer?.date ?? null;
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

  /** Delete a file as the signed-in user, sha-conditionally. */
  async deleteContents(args: { owner: string; repo: string; path: string; sha: string; message: string; author: Author; branch?: string }): Promise<{ commit: string }> {
    const body: Record<string, unknown> = { message: args.message, sha: args.sha, author: args.author, committer: args.author };
    if (args.branch) body.branch = args.branch;
    const r = await this.send<{ commit: { sha: string } }>('DELETE', `/repos/${args.owner}/${args.repo}/contents/${enc(args.path)}`, body);
    return { commit: r!.commit.sha };
  }

  /** Whether a GitHub account exists (false on 404). */
  async userExists(login: string): Promise<boolean> {
    return (await this.getOrNull<GhUser>(`/users/${encodeURIComponent(login)}`)) !== null;
  }

  /** Ask GitHub to stop a run. */
  async cancelRun(owner: string, repo: string, runId: number): Promise<void> {
    await this.send('POST', `/repos/${owner}/${repo}/actions/runs/${runId}/cancel`);
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

  /** An org, or null when there is none of that name (or it is not visible). */
  getOrg(org: string): Promise<GhOrg | null> {
    return this.getOrNull<GhOrg>(`/orgs/${encodeURIComponent(org)}`);
  }

  /**
   * `user`'s membership of `org` (state and role), null when they are neither a member nor
   * invited. Only an owner of the org may read another member's role; anyone else gets a
   * GitHubError, which the caller reports as "could not tell".
   */
  getOrgMembership(org: string, user: string): Promise<{ state: string; role: string } | null> {
    return this.getOrNull<{ state: string; role: string }>(`/orgs/${encodeURIComponent(org)}/memberships/${encodeURIComponent(user)}`);
  }

  /** A branch, or null when the repo has no branch of that name. */
  getBranch(owner: string, repo: string, branch: string): Promise<{ name: string } | null> {
    return this.getOrNull<{ name: string }>(`/repos/${owner}/${repo}/branches/${encodeURIComponent(branch)}`);
  }

  // ---------------------------------------------------------------- student screens

  /**
   * A file's bytes, not kept in the ETag cache (a notebook or a PDF can run to megabytes):
   * the contents API up to 1 MB, the blob API by `sha` above it (both stop at 100 MB). A blob
   * is immutable, so its bytes are kept by sha (up to BYTES_KEPT, oldest dropped first):
   * reopening a file or a deck's bundle costs no call.
   */
  async getBytes(owner: string, repo: string, path: string, sha: string, size: number): Promise<Uint8Array> {
    const hit = this.bytes.get(sha);
    if (hit) {
      this.bytes.delete(sha); // most recently used last
      this.bytes.set(sha, hit);
      return hit;
    }
    const got = await this.fetchBytes(owner, repo, path, sha, size);
    this.bytes.set(sha, got);
    this.bytesHeld += got.length;
    for (const [k, v] of this.bytes) {
      if (this.bytesHeld <= BYTES_KEPT || k === sha) break;
      this.bytes.delete(k);
      this.bytesHeld -= v.length;
    }
    return got;
  }

  private async fetchBytes(owner: string, repo: string, path: string, sha: string, size: number): Promise<Uint8Array> {
    const url = this.url(size > ONE_MB ? `/repos/${owner}/${repo}/git/blobs/${sha}` : `/repos/${owner}/${repo}/contents/${enc(path)}`);
    const res = await this.fetchFn(url, { method: 'GET', headers: this.headers(), cache: 'no-store' });
    this.noteRateLimit(res);
    if (!res.ok) return this.fail(res, url);
    const body = (await res.json()) as { content?: string; encoding?: string };
    return decodeBytes(body.content ?? '');
  }

  /** GitHub's own rendering of markdown (sanitised by GitHub), with `context` (`owner/repo`) for its links. */
  async renderMarkdown(text: string, context?: string): Promise<string> {
    const url = this.url('/markdown');
    const res = await this.fetchFn(url, {
      method: 'POST',
      headers: this.headers({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({ text, mode: 'gfm', ...(context ? { context } : {}) }),
      cache: 'no-store',
    });
    this.noteRateLimit(res);
    if (!res.ok) return this.fail(res, url);
    return res.text();
  }

  /** A repo's issues matching `query` (`labels=...&state=all&creator=...`), newest first, one page. */
  async listIssues(owner: string, repo: string, query: string): Promise<GhIssue[]> {
    return (await this.getOrNull<GhIssue[]>(`/repos/${owner}/${repo}/issues?${query}&per_page=30`)) ?? [];
  }

  /** An issue's comments, oldest first (up to 100: a receipts thread stays well under). */
  async listIssueComments(owner: string, repo: string, issue: number): Promise<GhComment[]> {
    return (await this.getOrNull<GhComment[]>(`/repos/${owner}/${repo}/issues/${issue}/comments?per_page=100`)) ?? [];
  }

  /** The logins of a team the caller can see, or null when it cannot (not a member, or no such team). */
  async listTeamMembers(org: string, team: string): Promise<string[] | null> {
    try {
      const r = await this.getOrNull<{ login: string }[]>(`/orgs/${encodeURIComponent(org)}/teams/${encodeURIComponent(team)}/members?per_page=100`);
      return r ? r.map((m) => m.login) : null;
    } catch (e) {
      if (e instanceof GitHubError) return null;
      throw e;
    }
  }

  /** The teams the signed-in person is in, across every org their token reaches (one listing, every page). */
  listMyTeams(): Promise<GhTeam[]> {
    return this.listPages<GhTeam>('/user/teams');
  }

  /** `user`'s membership state of team `team` in `org` (`active` | `pending`), or null when not a member or it cannot tell. A member may read their own membership of a secret team. */
  async getTeamMembership(org: string, team: string, user: string): Promise<string | null> {
    try {
      const r = await this.getOrNull<{ state: string }>(`/orgs/${encodeURIComponent(org)}/teams/${encodeURIComponent(team)}/memberships/${encodeURIComponent(user)}`);
      return r?.state ?? null;
    } catch (e) {
      if (e instanceof GitHubError) return null;
      throw e;
    }
  }

  /** A small file's bytes through the contents API (up to 1 MB), or null when it is absent. */
  async getSmallBytes(owner: string, repo: string, path: string): Promise<Uint8Array | null> {
    const r = await this.getOrNull<{ type?: string; content?: string }>(`/repos/${owner}/${repo}/contents/${enc(path)}`);
    return r && r.type === 'file' && r.content ? decodeBytes(r.content) : null;
  }

  /** Whether `user` is a member of `org` (as far as the caller may see). */
  async isOrgMember(org: string, user: string): Promise<boolean | null> {
    const url = this.url(`/orgs/${org}/members/${user}`);
    const res = await this.fetchFn(url, { method: 'GET', headers: this.headers(), cache: 'no-store' });
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
