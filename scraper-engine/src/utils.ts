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
  return raw.replace(/\D/g, '').slice(-10)
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
