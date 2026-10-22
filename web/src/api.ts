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

export interface SimulateResult {
  agent: Agent
  items: { name: string; qty: number }[]
  lines: PricedLine[]
  total_cents: number
  currency: string
  decision: Decision
  events: { kind: string; label: string; detail?: string | null }[]
  near_misses?: NearMiss[]
  source: string
  elapsed_ms: number
  dry_run: boolean
}

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
}

export class ApiError extends Error {
  code: string
  status: number
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
  error?: { code?: string; message?: string }
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
    throw new ApiError(code, message, res.status)
  }
  return body.data as T
}

function post<T>(path: string, payload?: unknown): Promise<T> {
  return request<T>(path, {
    method: 'POST',
    body: payload === undefined ? '{}' : JSON.stringify(payload),
  })
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
  agents: () => request<{ agents: Agent[] }>('/api/v1/agents').then((d) => d.agents),
  agent: (id: string) => request<{ agent: Agent }>(`/api/v1/agents/${id}`).then((d) => d.agent),
  createAgent: (input: AgentInput) =>
    post<{ agent: Agent }>('/api/v1/agents', input).then((d) => d.agent),
  updateAgent: (id: string, input: Partial<AgentInput>) =>
    request<{ agent: Agent }>(`/api/v1/agents/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(input),
    }).then((d) => d.agent),
  searchAgent: (id: string, q: string, qty = 1) =>
    request<ScopedSearch>(
      `/api/v1/agents/${id}/search?q=${encodeURIComponent(q)}&qty=${qty}`,
    ),
  simulate: (text: string, agentId?: string) =>
    post<SimulateResult>('/api/v1/simulate', { text, agent_id: agentId ?? null }),
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

export function initials(name: string): string {
  return name
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase() ?? '')
    .join('')
}
