export function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

export function randomInt(min: number, max: number): number {
  return Math.floor(Math.random() * (max - min + 1)) + min
}

export async function humanDelay(minMs: number, maxMs: number): Promise<void> {
  await sleep(randomInt(minMs, maxMs))
}

export async function humanDelaySeconds(minSec: number, maxSec: number): Promise<void> {
  await humanDelay(minSec * 1000, maxSec * 1000)
}

export function now(): string {
  return new Date().toISOString()
}

export function normalizePhone(raw: string): string {
  const s = raw.trim()
  // Preserve E.164 (+ followed by digits)
  if (s.startsWith('+')) {
    const digits = s.slice(1).replace(/\D/g, '')
    return `+${digits}`
  }
  // International prefix 00… → +…
  const digits = s.replace(/\D/g, '')
  if (digits.startsWith('00') && digits.length > 4) {
    return `+${digits.slice(2)}`
  }
  // Return all digits with + — preserve country code
  return `+${digits}`
}

export function normalizeName(raw: string): string {
  return raw.toLowerCase().trim().replace(/\s+/g, ' ')
}

export function extractEmailsFromText(text: string): string[] {
  const re = /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g
  return [...new Set(text.match(re) ?? [])]
}

export function cleanUrl(raw: string): string {
  try {
    const u = new URL(raw)
    return u.origin + u.pathname
  } catch {
    return raw
  }
}
