/**
 * Public API for the WhatsApp automation agent.
 *
 * Usage:
 *   import { sendWhatsApp } from './whatsapp'
 *
 *   const result = await sendWhatsApp(
 *     {
 *       phone_number:  '+60123456789',   // any format — normalized automatically
 *       message:       'Hello! ...',
 *       business_name: 'Acme Corp',
 *     },
 *     { debug: true, userDataDir: './wa-session' },
 *   )
 */

import { LeadDataSchema, type LeadDataInput } from './schemas'
import { createAgentState } from './state'
import { runWhatsAppAgent } from './agent'
import type { AgentConfig, SendResult } from './types'

// Re-export types and utilities for consumers that need them directly
export type { LeadData, AgentConfig, SendResult } from './types'
export type { LeadDataInput, LeadDataParsed } from './schemas'
export { PhoneValidationError, normalizePhoneNumber, validatePhoneNumber } from './phone'

/**
 * Send a WhatsApp message to one lead.
 *
 * @param input   Raw lead data — phone_number is normalized automatically.
 * @param config  Optional agent configuration (debug, headless, userDataDir, …).
 * @returns       SendResult with success flag, sentAt timestamp, or error string.
 * @throws        ZodError if input fails validation (invalid phone, empty message, etc.)
 */
export async function sendWhatsApp(
  input: LeadDataInput,
  config?: Partial<AgentConfig>,
): Promise<SendResult> {
  // Step 1 — Validate + normalize. Throws ZodError on invalid input.
  //          After this, `parsed.phone_number` is guaranteed E.164.
  const parsed = LeadDataSchema.parse(input)

  // Step 2 — Create immutable, frozen agent state.
  //          From here on, phone_number and message can never be confused
  //          because they live in separate, named, readonly properties.
  const state = createAgentState(parsed, config)

  // Step 3 — Run the Playwright automation with the frozen state.
  return runWhatsAppAgent(state)
}
