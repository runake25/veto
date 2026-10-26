import {
  cloneElement,
  useEffect,
  useId,
  useRef,
  useState,
  type FormEvent,
  type ReactElement,
  type ReactNode,
} from 'react'
import { motion, useReducedMotion } from 'motion/react'
import type { Agent, AgentInput, RuleKind } from '../api'
import { Check, PressButton, Spinner } from './PressButton'
import { motionSet } from './motion'

const KINDS: { value: RuleKind; label: string }[] = [
  { value: 'per_unit_cap', label: 'Max per item' },
  { value: 'per_order_cap', label: 'Max per order' },
  { value: 'approval_threshold', label: 'Ask above' },
]

const MAX_NAME = 80
const MAX_TAG = 40
const MAX_CHANNEL = 60
const SLUG = /^[a-z0-9][a-z0-9_-]*$/

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

/** Money typed by a person: blank, or dollars with at most two decimals. */
function moneyError(raw: string, label: string): string | null {
  const clean = raw.replace(/[$,\s]/g, '')
  if (!clean) return null
  if (!/^\d+(\.\d{1,2})?$/.test(clean)) {
    return `${label}: dollars and cents, like 300 or 145.99.`
  }
  return null
}

type FieldKey =
  | 'name'
  | 'tag'
  | 'channel'
  | 'monthly'
  | 'perOrder'
  | 'perUnit'
  | 'threshold'
  | 'vendors'
  | 'categories'
  | 'overrideCategory'
  | 'overrideValue'

type Errors = Partial<Record<FieldKey, string>>

/** Map the API's field-first message onto the field it names. */
function mapServerError(message: string): { field: FieldKey; message: string } | null {
  const lower = message.toLowerCase()
  if (lower.startsWith('name ')) return { field: 'name', message }
  if (lower.startsWith('tag ')) return { field: 'tag', message }
  if (lower.startsWith('channel ')) return { field: 'channel', message }
  if (lower.startsWith('vendors:')) return { field: 'vendors', message }
  if (lower.startsWith('categories:')) return { field: 'categories', message }
  if (lower.startsWith('overrides:')) return { field: 'overrideCategory', message }
  return null
}

/**
 * One field: the label, the control and the error in the same block, so a
 * message about the monthly cap cannot appear at the bottom of the form.
 */
function Field({
  label,
  hint,
  error,
  children,
}: {
  label: string
  hint?: ReactNode
  error?: string | null
  children: ReactElement<{ 'aria-invalid'?: boolean; 'aria-describedby'?: string }>
}) {
  const id = useId()
  const control = cloneElement(children, {
    'aria-invalid': error ? true : undefined,
    'aria-describedby': error ? `${id}-error` : undefined,
  })
  return (
    <label className="field">
      <span className="field-label">
        {label}
        {hint ? <span className="field-hint mono">{hint}</span> : null}
      </span>
      {control}
      {error ? (
        <span className="field-error" id={`${id}-error`}>
          {error}
        </span>
      ) : null}
    </label>
  )
}

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
  const [errors, setErrors] = useState<Errors>({})
  const [dirty, setDirty] = useState(false)
  const formRef = useRef<HTMLFormElement>(null)
  const reduce = useReducedMotion()
  const m = motionSet(reduce)

  // A message from the server belongs beside its field, not at the bottom of
  // the form. Editing anything clears the placement — the next submit re-maps.
  const serverMap = error ? mapServerError(error) : null
  const mapped = dirty ? null : serverMap
  const generalError = error && !serverMap ? error : null

  useEffect(() => {
    if (!error) return
    setDirty(false)
  }, [error])

  const touch = () => {
    setDirty(true)
  }

  const clear = (key: FieldKey) => {
    setErrors((prev) => {
      if (!prev[key]) return prev
      const next = { ...prev }
      delete next[key]
      return next
    })
  }

  const fieldError = (key: FieldKey): string | null =>
    errors[key] ?? (mapped?.field === key ? mapped.message : null)

  const validate = (): Errors => {
    const next: Errors = {}
    if (!name.trim()) next.name = 'Give the agent a name.'
    else if (name.trim().length > MAX_NAME) next.name = `Keep the name under ${MAX_NAME} characters.`
    if (tag.trim().length > MAX_TAG) next.tag = `Keep the tag under ${MAX_TAG} characters.`
    if (channel.trim().length > MAX_CHANNEL) {
      next.channel = `Keep the channel under ${MAX_CHANNEL} characters.`
    }
    for (const [key, raw, label] of [
      ['monthly', monthly, 'Monthly budget'],
      ['perOrder', perOrder, 'Max per order'],
      ['perUnit', perUnit, 'Max per item'],
      ['threshold', threshold, 'Ask-me-above amount'],
    ] as const) {
      const message = moneyError(raw, label)
      if (message) next[key] = message
    }
    if (domains(vendors).some((domain) => /\s/.test(domain) || !domain)) {
      next.vendors = 'One merchant per item, comma separated — domains like grove.co.'
    }
    if (list(categories).some((category) => !SLUG.test(category))) {
      next.categories = 'Categories are single words: pantry, stationery.'
    }
    const overrideCategoryClean = overrideCategory.trim().toLowerCase()
    if (overrideCategoryClean && !SLUG.test(overrideCategoryClean)) {
      next.overrideCategory = 'The category is one word, like stationery.'
    }
    if (overrideCategoryClean && !overrideValue.trim()) {
      next.overrideValue = 'Set the exception limit, or clear the category.'
    }
    if (!overrideCategoryClean && overrideValue.trim()) {
      next.overrideCategory = 'Name the category this exception applies to.'
    }
    const overrideMoney = moneyError(overrideValue, 'Exception limit')
    if (overrideMoney) next.overrideValue = overrideMoney
    return next
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setDirty(false)
    const next = validate()
    setErrors(next)
    if (Object.keys(next).length) {
      window.setTimeout(() => {
        formRef.current?.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus()
      }, 0)
      return
    }
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
      ref={formRef}
      className="agent-form"
      variants={m.stagger}
      initial="hidden"
      animate="show"
      onSubmit={submit}
      noValidate
    >
      <motion.div className="form-row" variants={m.riseIn}>
        <Field label="Agent name" error={fieldError('name')}>
          <input
            className="input"
            value={name}
            onChange={(e) => {
              touch()
              setName(e.target.value)
              clear('name')
            }}
            placeholder="Pantry runner"
            maxLength={MAX_NAME}
            required
            disabled={busy}
          />
        </Field>
        <Field
          label="Tag"
          hint="for analytics"
          error={fieldError('tag')}
        >
          <input
            className="input"
            value={tag}
            onChange={(e) => {
              touch()
              setTag(e.target.value)
              clear('tag')
            }}
            placeholder="pantry"
            maxLength={MAX_TAG}
            disabled={busy}
          />
        </Field>
        <Field label="Home channel" hint="a label, not routing" error={fieldError('channel')}>
          <input
            className="input mono"
            value={channel}
            onChange={(e) => {
              touch()
              setChannel(e.target.value)
              clear('channel')
            }}
            placeholder="#pantry"
            maxLength={MAX_CHANNEL}
            disabled={busy}
          />
        </Field>
      </motion.div>

      <motion.div className="form-row" variants={m.riseIn}>
        <Field label="Monthly budget" error={fieldError('monthly')}>
          <input
            className="input mono"
            inputMode="decimal"
            value={monthly}
            onChange={(e) => {
              touch()
              setMonthly(e.target.value)
              clear('monthly')
            }}
            placeholder="800.00"
            disabled={busy}
          />
        </Field>
        <Field label="Max per order" error={fieldError('perOrder')}>
          <input
            className="input mono"
            inputMode="decimal"
            value={perOrder}
            onChange={(e) => {
              touch()
              setPerOrder(e.target.value)
              clear('perOrder')
            }}
            placeholder="300.00"
            disabled={busy}
          />
        </Field>
        <Field label="Max per item" error={fieldError('perUnit')}>
          <input
            className="input mono"
            inputMode="decimal"
            value={perUnit}
            onChange={(e) => {
              touch()
              setPerUnit(e.target.value)
              clear('perUnit')
            }}
            placeholder="15.00"
            disabled={busy}
          />
        </Field>
        <Field label="Ask me above" error={fieldError('threshold')}>
          <input
            className="input mono"
            inputMode="decimal"
            value={threshold}
            onChange={(e) => {
              touch()
              setThreshold(e.target.value)
              clear('threshold')
            }}
            placeholder="50.00"
            disabled={busy}
          />
        </Field>
      </motion.div>

      <motion.div className="form-row" variants={m.riseIn}>
        <Field
          label="Approved merchants"
          hint="domains, comma separated"
          error={fieldError('vendors')}
        >
          <textarea
            className="input textarea"
            value={vendors}
            onChange={(e) => {
              touch()
              setVendors(e.target.value)
              clear('vendors')
            }}
            placeholder="grove.co, staples.com, amazon.com"
            rows={2}
            disabled={busy}
          />
        </Field>
        <Field label="Can buy" hint="categories" error={fieldError('categories')}>
          <input
            className="input"
            value={categories}
            onChange={(e) => {
              touch()
              setCategories(e.target.value)
              clear('categories')
            }}
            placeholder="pantry, stationery"
            disabled={busy}
          />
        </Field>
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
          <Field label="Category" error={fieldError('overrideCategory')}>
            <input
              className="input"
              value={overrideCategory}
              onChange={(e) => {
                touch()
                setOverrideCategory(e.target.value)
                clear('overrideCategory')
              }}
              placeholder="stationery"
              disabled={busy}
            />
          </Field>
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
          <Field label="New limit" error={fieldError('overrideValue')}>
            <input
              className="input mono"
              inputMode="decimal"
              value={overrideValue}
              onChange={(e) => {
                touch()
                setOverrideValue(e.target.value)
                clear('overrideValue')
              }}
              placeholder="5.00"
              disabled={busy}
            />
          </Field>
        </div>
      </motion.div>

      {generalError ? (
        <p className="form-error" role="alert">
          {generalError}
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
