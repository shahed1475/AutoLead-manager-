import type { LeadData, AgentConfig } from './types'

/**
 * Immutable, frozen agent state. Single source of truth for one send operation.
 * Once created, no field can be reassigned or mutated — TypeScript's readonly
 * catches it at compile time, Object.freeze catches any runtime bypass.
 */
export type AgentState = Readonly<{
  phone_number: string
  message: string
  business_name: string
  config: Readonly<AgentConfig>
}>

const DEFAULT_CONFIG: AgentConfig = {
  debug: false,
  headless: false,
  timeoutMs: 30_000,
  userDataDir: undefined,
}

export function createAgentState(
  lead: LeadData,
  config?: Partial<AgentConfig>,
): AgentState {
  const mergedConfig = Object.freeze<AgentConfig>({ ...DEFAULT_CONFIG, ...config })

  const state = Object.freeze<AgentState>({
    phone_number:  lead.phone_number,
    message:       lead.message,
    business_name: lead.business_name,
    config:        mergedConfig,
  })

  if (mergedConfig.debug) {
    console.debug('[AgentState] created', {
      phone_number:   state.phone_number,
      business_name:  state.business_name,
      message_length: state.message.length,
      headless:       state.config.headless,
      userDataDir:    state.config.userDataDir ?? '(ephemeral)',
    })
  }

  return state
}

export function debugLog(
  state: AgentState,
  action: string,
  data?: Record<string, unknown>,
): void {
  if (!state.config.debug) return
  console.debug(`[WhatsAppAgent:${action}]`, {
    phone_number:  state.phone_number,
    business_name: state.business_name,
    ...data,
  })
}
