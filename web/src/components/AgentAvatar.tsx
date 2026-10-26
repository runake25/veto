/**
 * The instance mark.
 *
 * A Veto agent is a running thing with a serial, a state and a charter — not a
 * folder. Its avatar is therefore a machine plate, not a photograph: a dark tile,
 * a two-letter mono monogram, and a small dot grid derived from the serial so no
 * two agents share a mark. The state dot is the same four tones as `State`.
 */
import type { Agent } from '../api'
import { agentState } from './state'

/** Two letters that say which instance this is. */
export function monogram(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean)
  if (!words.length) return 'AG'
  if (words.length === 1) return words[0]!.slice(0, 2).toUpperCase()
  return (words[0]![0]! + words[words.length - 1]![0]!).toUpperCase()
}

/** FNV-1a: stable across renders and sessions, so a mark never moves. */
function serialHash(value: string): number {
  let hash = 2166136261
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index)
    hash = Math.imul(hash, 16777619)
  }
  return hash >>> 0
}

const GRID = 4

/** Which cells of the dot grid are lit, from the serial. */
function dotPlan(agent: Agent): boolean[] {
  let state = serialHash(`${agent.id}:${agent.name}`)
  const cells: boolean[] = []
  for (let index = 0; index < GRID * GRID; index += 1) {
    state = (state * 1664525 + 1013904223) >>> 0
    cells.push((state >>> 28) % 3 !== 0)
  }
  return cells
}

export function AgentAvatar({
  agent,
  size = 28,
  showState = true,
  className,
}: {
  agent: Pick<Agent, 'id' | 'name' | 'status' | 'rules' | 'categories' | 'vendors'>
  size?: number
  showState?: boolean
  className?: string
}) {
  const state = agentState(agent)
  const cells = dotPlan(agent as Agent)
  const step = 24 / GRID

  return (
    <span
      className={`agent-avatar${className ? ` ${className}` : ''}`}
      style={{ width: size, height: size, borderRadius: Math.round(size * 0.22) }}
      aria-hidden="true"
    >
      <span className="agent-avatar-tile">
        <svg className="agent-avatar-code" viewBox="0 0 24 24" focusable="false">
          {cells.map((lit, index) =>
            lit ? (
              <rect
                key={index}
                x={(index % GRID) * step + step / 2 - 0.7}
                y={Math.floor(index / GRID) * step + step / 2 - 0.7}
                width="1.4"
                height="1.4"
                rx="0.7"
              />
            ) : null,
          )}
        </svg>
        <span className="agent-avatar-mono" style={{ fontSize: Math.round(size * 0.36) }}>
          {monogram(agent.name)}
        </span>
      </span>
      {showState ? (
        <span className={`agent-avatar-state is-${state.tone}`} title={state.label} />
      ) : null}
    </span>
  )
}
