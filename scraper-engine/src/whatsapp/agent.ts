import { chromium, type Browser, type BrowserContext, type Page } from 'playwright'
import { debugLog } from './state'
import type { AgentState } from './state'
import type { SendResult } from './types'
import { humanDelay } from '../utils'

const WHATSAPP_WEB_BASE = 'https://web.whatsapp.com'

/**
 * WhatsApp Web element selectors.
 * Using data-testid where available — more stable than class names.
 */
const SEL = {
  sendButton: '[data-testid="send"]',
  qrCanvas:   'canvas[aria-label="Scan this QR code to link a device"]',
  // Fallback: the SVG send arrow inside the compose area
  sendFallback: 'span[data-icon="send"]',
  // Loading spinner present while the app initialises
  appLoader: '#app .landing-wrapper',
} as const

/**
 * Build the WhatsApp Web deep-link URL.
 *
 * CRITICAL ISOLATION: this function only ever reads `state.phone_number` and
 * `state.message`. Neither can be confused with the other because:
 *  1. They come from different, named properties of the frozen AgentState.
 *  2. phone_number is passed as the `phone` query param (digits only, no +).
 *  3. message is passed as the `text` query param (URL-encoded).
 *
 * The old bug ("09Initial message") occurred because a mutable local variable
 * holding the phone number was re-used or shadowed by the message variable in
 * scope. Frozen state + named destructuring makes that impossible here.
 */
function buildSendUrl(state: AgentState): string {
  const { phone_number, message } = state            // explicit named destructure
  const digitsOnly = phone_number.replace(/^\+/, '') // strip leading +  (E.164 → plain digits)
  const encodedMsg = encodeURIComponent(message)     // encode message separately
  return `${WHATSAPP_WEB_BASE}/send?phone=${digitsOnly}&text=${encodedMsg}`
}

/**
 * Navigate to the WhatsApp Web deep-link for this lead.
 * The phone number in the URL comes exclusively from state.phone_number.
 */
export async function searchWhatsApp(state: AgentState, page: Page): Promise<void> {
  const url = buildSendUrl(state)
  debugLog(state, 'searchWhatsApp', {
    url,
    phone_digits: state.phone_number.replace(/^\+/, '').length,
  })
  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: state.config.timeoutMs })
}

/**
 * Wait until WhatsApp Web is ready to send (chat loaded) or fail fast with
 * a meaningful error (not logged in, invalid phone, etc.).
 */
export async function waitForChatReady(state: AgentState, page: Page): Promise<void> {
  debugLog(state, 'waitForChatReady')

  // Wait for send button OR the QR code (not-logged-in state), whichever comes first
  await page.waitForSelector(
    `${SEL.sendButton}, ${SEL.sendFallback}, ${SEL.qrCanvas}`,
    { timeout: state.config.timeoutMs },
  )

  const isLoggedOut = await page.$(SEL.qrCanvas)
  if (isLoggedOut) {
    throw new Error(
      'WhatsApp Web is not logged in. ' +
      'Start the agent with headless:false, scan the QR code, then re-run.',
    )
  }

  debugLog(state, 'waitForChatReady:ready')
}

/**
 * Click the send button to dispatch the pre-filled message.
 * The message text was injected via the URL `text=` param — we never retype it
 * here, so there is no opportunity for a variable to be substituted into the
 * wrong field.
 */
export async function sendMessage(state: AgentState, page: Page): Promise<void> {
  debugLog(state, 'sendMessage', { message_length: state.message.length })

  // Try primary selector, fall back to the SVG icon variant
  let sendBtn = await page.$(SEL.sendButton)
  if (!sendBtn) {
    sendBtn = await page.$(SEL.sendFallback)
  }

  if (!sendBtn) {
    throw new Error(
      'Send button not found after chat loaded. ' +
      'WhatsApp Web layout may have changed — check SEL.sendButton selector.',
    )
  }

  await humanDelay(600, 1400)    // human-like pause before clicking
  await sendBtn.click()
  await humanDelay(800, 1600)    // wait for message delivery confirmation

  debugLog(state, 'sendMessage:done')
}

/**
 * Full lifecycle: open browser → navigate → send → close.
 *
 * Uses a persistent browser context when `config.userDataDir` is set,
 * keeping the WhatsApp session alive between runs (no QR re-scan needed).
 * Without userDataDir, every run starts a fresh ephemeral context and will
 * reach the QR screen — only useful for testing with headless:false.
 */
export async function runWhatsAppAgent(state: AgentState): Promise<SendResult> {
  const { phone_number, business_name, config } = state

  let browser: Browser | undefined
  let context: BrowserContext | undefined

  try {
    debugLog(state, 'runWhatsAppAgent:start')

    if (config.userDataDir) {
      // Persistent context — WhatsApp session survives across runs
      context = await chromium.launchPersistentContext(config.userDataDir, {
        headless: config.headless,
        args:     ['--no-sandbox', '--disable-setuid-sandbox'],
      })
    } else {
      // Ephemeral — for one-off runs or first-time QR setup
      browser = await chromium.launch({
        headless: config.headless,
        args:     ['--no-sandbox', '--disable-setuid-sandbox'],
      })
      context = await browser.newContext()
    }

    const page = await context.newPage()

    await searchWhatsApp(state, page)
    await waitForChatReady(state, page)
    await sendMessage(state, page)

    debugLog(state, 'runWhatsAppAgent:success')

    return {
      success:       true,
      phone_number,
      business_name,
      sentAt:        new Date().toISOString(),
    }

  } catch (err) {
    const error = err instanceof Error ? err.message : String(err)
    debugLog(state, 'runWhatsAppAgent:error', { error })

    return {
      success:       false,
      phone_number,
      business_name,
      error,
    }

  } finally {
    // Always clean up — even if context was launched as persistent
    await context?.close().catch(() => undefined)
    await browser?.close().catch(() => undefined)
  }
}
