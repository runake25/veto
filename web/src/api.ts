/**
 * Typed client for the Veto runner. Same-origin cookies carry the session, so every
 * call runs with `credentials: "same-origin"`. Responses use the envelope
 * `{ ok: true, data } | { ok: false, error: { code, message } }`.
 */

export type Role = 'admin' | 'approver' | 'viewer'

export interface User {
  id: string
  name: string
  email: string
  role: Role
}

export interface Workspace {
  id: string
  name: string
  currency: string
  monthly_budget_cents: number
}

export interface Wallet {
  status: 'connected' | 'disconnected' | 'pending'
  vault_id?: string | null
}

export interface Me {
  user: User
  workspace: Workspace
  agent_count: number
  wallet: Wallet
}

/** One request as the list endpoints return it. Full records add lines/events. */
export interface RequestSummary {
  id: string
  seq: number
  title: string
  status: 'blocked' | 'awaiting' | 'approved' | 'captured' | 'vetoed' | 'voided'
  decision: 'auto' | 'review' | 'block' | null
  total_cents: number
  currency: string
  channel: string | null
  requester: string | null
  origin: string | null
  line_count: number
  created_at: string
  decided_at: string | null
  /** The person who signed or vetoed it, for receipt rows. */
  approver?: { id: string; name: string } | null
  intent?: string | null
  consent_expires_at?: string | null
  consent_expired?: boolean
  /** Set when a standing job raised this request. */
  routine_name?: string | null
  agent: { id: string; name: string; tag: string }
}

export interface OverviewCounts {
  needs_you: number
  expired_consents: number
  committed_cents: number
  settled_month_cents: number
  agent_count: number
  request_count: number
}

export interface OverviewData {
  counts: OverviewCounts
  pending: RequestSummary[]
  expired: RequestSummary[]
  wallet: { status: string; mode: string }
  budget_cents: number
  remaining_cents: number
  month: string
}

export type RequestStatus = 'blocked' | 'awaiting' | 'approved' | 'captured' | 'vetoed' | 'voided'

export interface RequestLine {
  sku: string | null
  title: string
  qty: number
  unit_cents: number
  line_cents: number
  vendor: string | null
  category: string | null
  brand: string | null
  url: string | null
  image: string | null
  source: string | null
}

export interface Consent {
  id: string
  cart_hash: string
  issued_at: string | null
  expires_at: string | null
  used_at: string | null
  approver_user_id: string | null
}

export interface Payment {
  id: string
  mode: string
  status: string
  amount_cents: number
  order_id: string | null
  authorization_id: string | null
  capture_id: string | null
  created_at: string | null
  updated_at: string | null
}

export interface RequestEvent {
  seq: number
  kind: string
  label: string
  detail: string | null
  at: string
}

/** A request with its cart, consent, payment and trail — the Record screen. */
export interface RequestDetail extends Omit<RequestSummary, 'status'> {
  status: RequestStatus
  text: string
  agent: { id: string; name: string; tag: string; channel: string | null; status: string }
  lines: RequestLine[]
  reasons: Reason[]
  consent: Consent | null
  payment: Payment | null
  events: RequestEvent[]
  approver_user_id: string | null
  /** The standing job that raised this request, when one did. */
  routine?: { id: string; name: string; cadence: string; run_id: string } | null
}

export interface ApiKey {
  id: string
  agent_id: string
  prefix: string
  created_at: string | null
  last_used_at: string | null
  revoked_at: string | null
}

export interface WorkspaceKey extends ApiKey {
  agent_name: string
}

export interface AnalyticsBucket {
  key: string
  label: string
  tag?: string
  requests: number
  captured: number
  captured_cents: number
  pending?: number
  pending_cents?: number
  blocked: number
  vetoed: number
}

export interface AnalyticsSpendRow {
  key: string
  label: string
  requests: number
  captured_cents: number
  lines: number
  blocked: number
  vetoed: number
}

export interface AnalyticsRuleRow {
  key: string
  severity: 'block' | 'review'
  count: number
  requests: number
  blocked: number
  review: number
  example: string | null
}

export interface Analytics {
  range: { from: string; to: string }
  totals: {
    requests: number
    captured: number
    awaiting: number
    approved: number
    blocked: number
    vetoed: number
    voided: number
    captured_cents: number
    pending_cents: number
    blocked_cents: number
    auto: number
    review: number
    block: number
  }
  by_agent: AnalyticsBucket[]
  by_tag: AnalyticsBucket[]
  by_vendor: AnalyticsSpendRow[]
  by_category: AnalyticsSpendRow[]
  by_rule: AnalyticsRuleRow[]
  series: { date: string; captured_cents: number; requests: number }[]
}

export interface IntegrationsData {
  workspace: { id: string; name: string; currency: string; monthly_budget_cents: number }
  wallet: {
    status: string
    mode: string
    vault_id: string | null
    payer_email: string | null
    connected_at: string | null
    connect_url: string
  }
  slack: {
    signing_secret: boolean
    bot_token: boolean
    app_ready: boolean
    slash_command: string
    slash_url: string
    interact_url: string
    public_url: string
    commands: {
      command: string
      description: string
      usage_hint: string
      example: string
    }[]
    default_agent: { id: string; name: string } | null
    routing: 'model'
  }
  agents: {
    id: string
    name: string
    tag: string
    status: string
    channel: string | null
    categories: string[]
    merchants: string[]
  }[]
  api: { requests_path: string; base_url: string }
  keys: WorkspaceKey[]
}


export type RuleKind =
  | 'monthly_cap'
  | 'per_order_cap'
  | 'per_unit_cap'
  | 'approval_threshold'
  | 'vendor_allowlist'
  | 'category_allowlist'

export interface Reason {
  rule: string
  severity: 'block' | 'review'
  message: string
  observed_cents?: number | null
  limit_cents?: number | null
  unblock?: string | null
}

export interface Decision {
  outcome: 'auto' | 'review' | 'block'
  reasons: Reason[]
}

export interface PricedLine {
  sku?: string | null
  title: string
  qty: number
  unit_cents: number
  line_cents: number
  vendor?: string | null
  category?: string | null
  brand?: string | null
  image?: string | null
  url?: string | null
  source?: string | null
}

/** Offers that matched the words but were not the requested product. */
export interface NearMissOffer {
  title: string
  category: string
  vendor: string | null
  unit_cents: number
}

export interface NearMiss {
  item: string
  offers: NearMissOffer[]
}

export interface SimulateItem {
  name: string
  qty: number
  /** Catalogue query the extractor derived for the item. */
  query?: string
  /** Bare product noun used for the category lookup. */
  product_type?: string
  brand?: string | null
}

export interface SimulateResult {
  agent: Agent
  items: SimulateItem[]
  lines: PricedLine[]
  total_cents: number
  currency: string
  decision: Decision
  events: { kind: string; label: string; detail?: string | null }[]
  near_misses?: NearMiss[]
  source: string
  elapsed_ms: number
  /** How many items were resolved at once (1 when the cart was serial). */
  parallel_items?: number
  dry_run: boolean
}

/** One server-sent frame from the streamed dry run. */
export type SimulateStreamFrame =
  | { type: 'start' }
  | { type: 'event'; event: { kind: string; label: string; detail?: string | null } }
  | {
      type: 'item'
      index: number
      name: string
      ok: boolean
      title?: string | null
      source?: string | null
    }
  | { type: 'result'; result: SimulateResult }
  | { type: 'error'; error: { code?: string; message?: string } }

export interface SearchFit {
  category: boolean
  per_unit: boolean
  per_order: boolean
  threshold: boolean
}

export interface SearchResult extends PricedLine {
  fits: SearchFit
  verdict: 'auto' | 'review' | 'block'
  reasons: Reason[]
}

export interface ScopedSearch {
  agent: Agent
  query: string
  qty: number
  source: string
  scoped_by: { vendors: string[]; max_price_cents: number | null }
  results: SearchResult[]
  filtered: { irrelevant: number; category: number }
}

export interface AgentInput {
  name: string
  status?: 'active' | 'paused'
  tag: string
  channel: string | null
  monthly_cap_cents: number | null
  per_order_cap_cents: number | null
  per_unit_cap_cents: number | null
  approval_threshold_cents: number | null
  vendors: string[]
  categories: string[]
  rules: string[]
  overrides: { category: string; kind: RuleKind; value_cents: number }[]
}

export interface RuleOverride {
  category: string
  kind: string
  value_cents: number
}

export interface Agent {
  id: string
  name: string
  tag: string
  channel: string | null
  status: 'active' | 'paused'
  monthly_cap_cents: number | null
  per_order_cap_cents: number | null
  per_unit_cap_cents: number | null
  approval_threshold_cents: number | null
  vendors: string[]
  categories: string[]
  rules: string[]
  overrides: RuleOverride[]
  /** This month, captured. Present on the agent endpoints. */
  spent_month_cents?: number
  /** This month, awaiting a signature or approved with capture owed. */
  committed_month_cents?: number
  /** The standing job, summarised on the agent list. Full view on the agent. */
  routine?: RoutineSummary | null
}

/** The routine as the agent list carries it: enough for one line, no month maths. */
export interface RoutineSummary {
  id: string
  name: string
  cadence: 'daily' | 'weekly' | 'monthly'
  cadence_word: string
  status: 'active' | 'paused'
  next_run_at: string | null
}

export interface Routine extends RoutineSummary {
  agent_id: string
  instruction: string
  envelope_cents: number
  /** Captured by runs of this routine this month. */
  spent_cents: number
  /** Awaiting a signature or approved with capture owed, this month. */
  committed_cents: number
  /** Envelope minus spent and committed, or null when there is no envelope. */
  remaining_cents: number | null
  dedupe_days: number
  last_run_at: string | null
  created_at: string | null
}

export type RoutineOutcome =
  | 'captured'
  | 'awaiting'
  | 'approved'
  | 'blocked'
  | 'skipped'
  | 'failed'

/** One recorded run: what the schedule did, and the request it made. */
export interface RoutineRun {
  id: string
  routine_id: string
  due_at: string | null
  started_at: string | null
  finished_at: string | null
  outcome: RoutineOutcome
  request_id: string | null
  amount_cents: number
  note: string | null
}

export interface AgentDetailData {
  agent: Agent
  routine: Routine | null
  runs: RoutineRun[]
}

export interface RoutineInput {
  name?: string
  instruction: string
  cadence: 'daily' | 'weekly' | 'monthly'
  envelope_cents: number
  dedupe_days: number
}

/** Everything a run emits before the run row: the same frames as a dry run. */
export type RoutineStreamFrame =
  | SimulateStreamFrame
  | { type: 'run'; run: RoutineRun; request: RequestSummary | null }

export interface RoutineRunResult {
  run: RoutineRun
  request: RequestSummary | null
}

export class ApiError extends Error {
  code: string
  status: number
  /** Set when the workspace has no PayPal wallet yet (HTTP 428 wallet_required). */
  approveUrl?: string
  connect?: string
  constructor(code: string, message: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

interface Envelope<T> {
  ok: boolean
  data?: T
  error?: { code?: string; message?: string; approve_url?: string; connect?: string }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(path, {
      credentials: 'same-origin',
      headers: init?.body ? { 'Content-Type': 'application/json' } : undefined,
      ...init,
    })
  } catch {
    throw new ApiError('offline', 'The engine is not answering. Start it with python3 server.py.', 0)
  }

  let body: Envelope<T> | null = null
  try {
    body = (await res.json()) as Envelope<T>
  } catch {
    body = null
  }

  if (!res.ok || !body?.ok) {
    const code = body?.error?.code ?? `http_${res.status}`
    const message = body?.error?.message ?? `Request failed (${res.status})`
    const err = new ApiError(code, message, res.status)
    if (body?.error?.approve_url) err.approveUrl = body.error.approve_url
    if (body?.error?.connect) err.connect = body.error.connect
    throw err
  }
  return body.data as T
}

function post<T>(path: string, payload?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: payload === undefined ? '{}' : JSON.stringify(payload),
  })
}

/**
 * Read one server-sent stream to the end. Every `data:` frame is handed to
 * `onFrame`; an `error` frame becomes the thrown ApiError. Returns every frame
 * in order, so a caller can pick the final one it expects.
 */
async function readStream<T extends { type: string }>(
  path: string,
  body: unknown,
  onFrame: (frame: T) => void,
  signal: AbortSignal | undefined,
  failure: string,
): Promise<T[]> {
  let res: Response
  try {
    res = await fetch(path, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal,
    })
  } catch (err) {
    if (signal?.aborted) throw err
    throw new ApiError(
      'offline',
      'The engine is not answering. Start it with python3 server.py.',
      0,
    )
  }

  if (!res.ok || !res.body) {
    let payload: Envelope<unknown> | null = null
    try {
      payload = (await res.json()) as Envelope<unknown>
    } catch {
      payload = null
    }
    throw new ApiError(
      payload?.error?.code ?? `http_${res.status}`,
      payload?.error?.message ?? `${failure} (${res.status}).`,
      res.status,
    )
  }

  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  const frames: T[] = []
  let buffer = ''

  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buffer += decoder.decode(value, { stream: true })
    let cut = buffer.indexOf('\n\n')
    while (cut >= 0) {
      const chunk = buffer.slice(0, cut)
      buffer = buffer.slice(cut + 2)
      const line = chunk.split('\n').find((row) => row.startsWith('data:'))
      if (line) {
        let frame: T | null = null
        try {
          frame = JSON.parse(line.slice(5).trim()) as T
        } catch {
          frame = null
        }
        if (frame) {
          if (frame.type === 'error') {
            const fault = frame as unknown as {
              error?: { code?: string; message?: string }
            }
            throw new ApiError(
              fault.error?.code ?? 'stream_error',
              fault.error?.message ?? failure,
              0,
            )
          }
          frames.push(frame)
          onFrame(frame)
        }
      }
      cut = buffer.indexOf('\n\n')
    }
  }

  return frames
}

/**
 * The same dry run as `simulate`, read as it happens: the server sends one
 * `data:` frame per pipeline stage, plus one per cart item as it resolves.
 * Resolves with the final result; `onFrame` sees every step on the way.
 */
export async function streamSimulate(
  text: string,
  agentId: string | undefined,
  onFrame: (frame: SimulateStreamFrame) => void,
  signal?: AbortSignal,
): Promise<SimulateResult> {
  const frames = await readStream<SimulateStreamFrame>(
    '/api/v1/simulate/stream',
    { text, agent_id: agentId ?? null },
    onFrame,
    signal,
    'The dry run failed',
  )
  const final = frames.find((frame) => frame.type === 'result')
  if (!final || final.type !== 'result') {
    throw new ApiError('stream_error', 'The dry run ended without a result.', 0)
  }
  return final.result
}

/**
 * Run a routine now, streaming the pipeline while it works. The last frame is
 * the recorded run (and the request it created, if any).
 */
export async function streamRoutineRun(
  agentId: string,
  onFrame: (frame: RoutineStreamFrame) => void,
  signal?: AbortSignal,
): Promise<RoutineRunResult> {
  const frames = await readStream<RoutineStreamFrame>(
    `/api/v1/agents/${encodeURIComponent(agentId)}/routine/run/stream`,
    {},
    onFrame,
    signal,
    'The run failed',
  )
  const final = frames.find((frame) => frame.type === 'run')
  if (!final || final.type !== 'run') {
    throw new ApiError('stream_error', 'The run ended without a result.', 0)
  }
  return { run: final.run, request: final.request }
}

export interface SignupPayload {
  workspace_name: string
  name: string
  email: string
  password: string
}

export interface SessionData {
  user: User
  workspace: Workspace
}

export const api = {
  me: () => request<Me>('/api/v1/me'),
  overview: () => request<OverviewData>('/api/v1/overview'),
  requests: (query = '') => request<{
    requests: RequestSummary[]
    total: number
    sum_cents?: number
  }>(
    `/api/v1/requests${query ? `?${query}` : ''}`,
  ),
  request: (id: string) =>
    request<{ request: RequestDetail }>(`/api/v1/requests/${encodeURIComponent(id)}`).then(
      (d) => d.request,
    ),
  decide: (id: string, action: 'approve' | 'veto') =>
    post<{ request: RequestDetail }>(`/api/v1/requests/${encodeURIComponent(id)}/decision`, {
      action,
    }).then((d) => d.request),
  voidRequest: (id: string) =>
    post<{ request: RequestDetail }>(`/api/v1/requests/${encodeURIComponent(id)}/void`).then(
      (d) => d.request,
    ),
  analytics: (from: string, to: string) =>
    request<Analytics>(
      `/api/v1/analytics?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,
    ),
  integrations: () => request<IntegrationsData>('/api/v1/integrations'),
  updateMe: (name: string) =>
    request<{ user: User }>('/api/v1/me', {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }).then((d) => d.user),
  updateWorkspace: (name: string) =>
    request<{ workspace: Workspace }>('/api/v1/workspace', {
      method: 'PATCH',
      body: JSON.stringify({ name }),
    }).then((d) => d.workspace),
  changePassword: (currentPassword: string, newPassword: string) =>
    post<Record<string, never>>('/api/v1/me/password', {
      current_password: currentPassword,
      new_password: newPassword,
    }),
  mintKey: (agentId: string) =>
    post<{ key: ApiKey; secret: string; note: string }>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/keys`,
    ),
  revokeKey: (agentId: string, keyId: string) =>
    request<{ key: ApiKey }>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/keys/${encodeURIComponent(keyId)}`,
      { method: 'DELETE' },
    ),
  deleteAgent: (id: string) =>
    request<{ deleted: boolean }>(`/api/v1/agents/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    }),
  deleteRoutine: (agentId: string) =>
    request<{ deleted: boolean }>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/routine`,
      { method: 'DELETE' },
    ),
  agents: () => request<{ agents: Agent[] }>('/api/v1/agents').then((d) => d.agents),
  agent: (id: string) => request<{ agent: Agent }>(`/api/v1/agents/${id}`).then((d) => d.agent),
  agentDetail: (id: string) => request<AgentDetailData>(`/api/v1/agents/${id}`),
  createAgent: (input: AgentInput) =>
    post<{ agent: Agent }>('/api/v1/agents', input).then((d) => d.agent),
  updateAgent: (id: string, input: Partial<AgentInput>) =>
    request<{ agent: Agent }>(`/api/v1/agents/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(input),
    }).then((d) => d.agent),
  saveRoutine: (agentId: string, input: RoutineInput) =>
    post<{ routine: Routine }>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/routine`,
      input,
    ).then((d) => d.routine),
  setRoutineStatus: (agentId: string, action: 'pause' | 'resume') =>
    post<{ routine: Routine }>(
      `/api/v1/agents/${encodeURIComponent(agentId)}/routine/${action}`,
    ).then((d) => d.routine),
  runRoutineStream: streamRoutineRun,
  searchAgent: (id: string, q: string, qty = 1) =>
    request<ScopedSearch>(
      `/api/v1/agents/${id}/search?q=${encodeURIComponent(q)}&qty=${qty}`,
    ),
  simulate: (text: string, agentId?: string) =>
    post<SimulateResult>('/api/v1/simulate', { text, agent_id: agentId ?? null }),
  simulateStream: streamSimulate,
  createRequest: (text: string, agentId?: string | null) =>
    post<{ request: RequestDetail }>('/api/v1/requests', {
      text,
      agent_id: agentId ?? null,
    }).then((d) => d.request),
  /** Order exactly the offer a person picked from the catalogue (no re-pricing). */
  createPinnedRequest: (agentId: string, line: PricedLine) =>
    post<{ request: RequestDetail }>('/api/v1/requests', {
      text: line.title,
      agent_id: agentId,
      pinned: line,
    }).then((d) => d.request),
  login: (email: string, password: string) =>
    post<SessionData>('/api/v1/login', { email, password }),
  signup: (payload: SignupPayload) => post<SessionData>('/api/v1/signup', payload),
  demoSession: () => post<SessionData>('/api/v1/demo-session'),
  logout: () => post<Record<string, never>>('/api/v1/logout'),
}

export function money(cents: number | null | undefined, currency = 'USD'): string {
  if (cents === null || cents === undefined) return '—'
  return new Intl.NumberFormat('en-US', {
    style: 'currency',
    currency,
    maximumFractionDigits: 2,
  }).format(cents / 100)
}

/**
 * Money for caps, thresholds and limits: cents only when they carry information
 * ($300, $1,200, $145.99). Records and totals always use `money`.
 */
export function capMoney(cents: number | null | undefined, currency = 'USD'): string {
  if (cents === null || cents === undefined) return '—'
  return money(cents, currency).replace(/\.00$/, '')
}

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
}
