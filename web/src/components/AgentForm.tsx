import { useState, type FormEvent } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import type { Agent, AgentInput, RuleKind } from '../api'
import { Check, PressButton, Spinner } from './PressButton'
import { motionSet } from './motion'

const KINDS: { value: RuleKind; label: string }[] = [
  { value: 'per_unit_cap', label: 'Max per item' },
  { value: 'per_order_cap', label: 'Max per order' },
  { value: 'approval_threshold', label: 'Ask above' },
]

const dollars = (cents: number | null | undefined): string =>
  cents === null || cents === undefined ? '' : (cents / 100).toFixed(2)

const cents = (value: string): number | null => {
  const clean = value.replace(/[^0-9.]/g, '')
  if (!clean) return null
  const n = Number(clean)
  return Number.isFinite(n) ? Math.round(n * 100) : null
}

const list = (value: string): string[] =>
  Array.from(new Set(value.split(/[,\n]+/).map((v) => v.trim().toLowerCase()).filter(Boolean)))

const domains = (value: string): string[] =>
  list(value).map((v) => v.replace(/^https?:\/\//, '').replace(/\/.*$/, '').replace(/^www\./, ''))

interface AgentFormProps {
  initial?: Agent
  submitLabel: string
  busy: boolean
  saved: boolean
  error: string | null
  onSubmit: (input: AgentInput) => void
  onCancel?: () => void
}

/**
 * Guided rule setup (option A): six fields with template defaults plus one optional
 * category exception. Everything a finance person needs to trust an agent, nothing
 * more — the full rule rows stay visible read-only on the agent screen.
 */
export function AgentForm({
  initial,
  submitLabel,
  busy,
  saved,
  error,
  onSubmit,
  onCancel,
}: AgentFormProps) {
  const firstOverride = initial?.overrides?.[0]
  const [name, setName] = useState(initial?.name ?? '')
  const [tag, setTag] = useState(initial?.tag ?? 'general')
  const [channel, setChannel] = useState(initial?.channel ?? '')
  const [monthly, setMonthly] = useState(dollars(initial?.monthly_cap_cents))
  const [perOrder, setPerOrder] = useState(dollars(initial?.per_order_cap_cents))
  const [perUnit, setPerUnit] = useState(dollars(initial?.per_unit_cap_cents))
  const [threshold, setThreshold] = useState(dollars(initial?.approval_threshold_cents))
  const [vendors, setVendors] = useState((initial?.vendors ?? []).join(', '))
  const [categories, setCategories] = useState((initial?.categories ?? []).join(', '))
  const [overrideCategory, setOverrideCategory] = useState(firstOverride?.category ?? '')
  const [overrideKind, setOverrideKind] = useState<RuleKind>(
    (firstOverride?.kind as RuleKind) ?? 'per_unit_cap',
  )
  const [overrideValue, setOverrideValue] = useState(dollars(firstOverride?.value_cents))
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    const vendorList = domains(vendors)
    const categoryList = list(categories)
    const overrides: AgentInput['overrides'] = []
    const ovValue = cents(overrideValue)
    if (overrideCategory.trim() && ovValue !== null) {
      overrides.push({
        category: overrideCategory.trim().toLowerCase(),
        kind: overrideKind,
        value_cents: ovValue,
      })
    }
    onSubmit({
      name: name.trim(),
      tag: tag.trim() || 'general',
      channel: channel.trim() || null,
      monthly_cap_cents: cents(monthly),
      per_order_cap_cents: cents(perOrder),
      per_unit_cap_cents: cents(perUnit),
      approval_threshold_cents: cents(threshold),
      vendors: vendorList,
      categories: categoryList,
      rules: [
        'monthly_cap',
        'per_order_cap',
        'per_unit_cap',
        'approval_threshold',
        'vendor_allowlist',
        'category_allowlist',
      ],
      overrides,
    })
  }

  return (
    <motion.form
      className="agent-form"
      variants={m.stagger}
      initial="hidden"
      animate="show"
      onSubmit={submit}
      noValidate
    >
      <motion.div className="form-row" variants={m.riseIn}>
        <label className="field">
          <span className="field-label">Agent name</span>
          <input
            className="input"
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Pantry runner"
            required
            disabled={busy}
          />
        </label>
        <label className="field">
          <span className="field-label">
            Tag <span className="field-hint mono">for analytics</span>
          </span>
          <input
            className="input"
            value={tag}
            onChange={(e) => setTag(e.target.value)}
            placeholder="pantry"
            disabled={busy}
          />
        </label>
        <label className="field">
          <span className="field-label">
            Channel <span className="field-hint mono">optional</span>
          </span>
          <input
            className="input mono"
            value={channel}
            onChange={(e) => setChannel(e.target.value)}
            placeholder="#pantry"
            disabled={busy}
          />
        </label>
      </motion.div>

      <motion.div className="form-row" variants={m.riseIn}>
        <label className="field">
          <span className="field-label">Monthly budget</span>
          <input
            className="input mono"
            inputMode="decimal"
            value={monthly}
            onChange={(e) => setMonthly(e.target.value)}
            placeholder="800.00"
            disabled={busy}
          />
        </label>
        <label className="field">
          <span className="field-label">Max per order</span>
          <input
            className="input mono"
            inputMode="decimal"
            value={perOrder}
            onChange={(e) => setPerOrder(e.target.value)}
            placeholder="300.00"
            disabled={busy}
          />
        </label>
        <label className="field">
          <span className="field-label">Max per item</span>
          <input
            className="input mono"
            inputMode="decimal"
            value={perUnit}
            onChange={(e) => setPerUnit(e.target.value)}
            placeholder="15.00"
            disabled={busy}
          />
        </label>
        <label className="field">
          <span className="field-label">Ask me above</span>
          <input
            className="input mono"
            inputMode="decimal"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            placeholder="50.00"
            disabled={busy}
          />
        </label>
      </motion.div>

      <motion.div className="form-row" variants={m.riseIn}>
        <label className="field">
          <span className="field-label">
            Approved merchants <span className="field-hint mono">domains, comma separated</span>
          </span>
          <textarea
            className="input textarea"
            value={vendors}
            onChange={(e) => setVendors(e.target.value)}
            placeholder="grove.co, staples.com, amazon.com"
            rows={2}
            disabled={busy}
          />
        </label>
        <label className="field">
          <span className="field-label">
            Can buy <span className="field-hint mono">categories</span>
          </span>
          <input
            className="input"
            value={categories}
            onChange={(e) => setCategories(e.target.value)}
            placeholder="pantry, stationery"
            disabled={busy}
          />
        </label>
      </motion.div>

      <motion.div className="override-block" variants={m.riseIn}>
        <p className="override-title">
          Category exception <span className="mono">optional</span>
        </p>
        <p className="override-note">
          One category may break the agent&rsquo;s default. It is the only knob the guided form
          hides — everything else stays as above.
        </p>
        <div className="form-row form-row-tight">
          <label className="field">
            <span className="field-label">Category</span>
            <input
              className="input"
              value={overrideCategory}
              onChange={(e) => setOverrideCategory(e.target.value)}
              placeholder="stationery"
              disabled={busy}
            />
          </label>
          <label className="field">
            <span className="field-label">Rule</span>
            <select
              className="input select"
              value={overrideKind}
              onChange={(e) => setOverrideKind(e.target.value as RuleKind)}
              disabled={busy}
            >
              {KINDS.map((kind) => (
                <option key={kind.value} value={kind.value}>
                  {kind.label}
                </option>
              ))}
            </select>
          </label>
          <label className="field">
            <span className="field-label">New limit</span>
            <input
              className="input mono"
              inputMode="decimal"
              value={overrideValue}
              onChange={(e) => setOverrideValue(e.target.value)}
              placeholder="5.00"
              disabled={busy}
            />
          </label>
        </div>
      </motion.div>

      {error ? (
        <p className="form-error" role="alert">
          {error}
        </p>
      ) : null}

      <motion.div className="form-actions" variants={m.riseIn}>
        <PressButton type="submit" loading={busy}>
          {busy ? (
            <>
              <Spinner label="saving" /> Saving…
            </>
          ) : saved ? (
            <>
              <Check /> Saved
            </>
          ) : (
            submitLabel
          )}
        </PressButton>
        {onCancel ? (
          <PressButton variant="quiet" onClick={onCancel}>
            Cancel
          </PressButton>
        ) : null}
      </motion.div>
    </motion.form>
  )
}
