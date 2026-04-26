/** Validated, normalized data for a single WhatsApp send operation. */
export interface LeadData {
  /** E.164 format: +[country_code][subscriber_number]. Never a raw or partial number. */
  readonly phone_number: string
  /** The WhatsApp message body to send. */
  readonly message: string
  /** Human-readable name for logging. Never used as a search query or message content. */
  readonly business_name: string
}

export interface AgentConfig {
  readonly debug: boolean
  /** Launch browser headless (true) or visible (false). Visible required for WhatsApp QR auth. */
  readonly headless: boolean
  /** Per-action Playwright timeout in ms. */
  readonly timeoutMs: number
  /**
   * Directory for Playwright's persistent browser context (keeps WhatsApp logged in).
   * If omitted, a fresh context is used each run — user must scan QR every time.
   */
  readonly userDataDir?: string
}

export interface SendResult {
  readonly success: boolean
  readonly phone_number: string
  readonly business_name: string
  readonly error?: string
  readonly sentAt?: string
}
