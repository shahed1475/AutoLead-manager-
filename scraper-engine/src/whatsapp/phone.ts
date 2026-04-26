export class PhoneValidationError extends Error {
  constructor(
    public readonly raw: string,
    public readonly reason: string,
  ) {
    super(`Invalid phone "${raw}": ${reason}`)
    this.name = 'PhoneValidationError'
  }
}

/**
 * Converts any phone string into E.164 format: +[country_code][number]
 * Never strips the country code. Throws PhoneValidationError if normalization
 * would produce an invalid result.
 */
export function normalizePhoneNumber(raw: string): string {
  const s = raw.trim()

  if (!s) {
    throw new PhoneValidationError(raw, 'empty string')
  }

  // Already clean E.164
  if (/^\+\d{7,15}$/.test(s)) {
    return s
  }

  // Has leading + but contains formatting chars (spaces, dashes, parens)
  if (s.startsWith('+')) {
    const digits = s.slice(1).replace(/\D/g, '')
    if (digits.length < 7) {
      throw new PhoneValidationError(raw, `only ${digits.length} digit(s) after +, need at least 7`)
    }
    if (digits.length > 15) {
      throw new PhoneValidationError(raw, `${digits.length} digits exceeds E.164 max of 15`)
    }
    return `+${digits}`
  }

  const digits = s.replace(/\D/g, '')

  if (digits.length === 0) {
    throw new PhoneValidationError(raw, 'no digits found')
  }

  // International prefix 00 → +
  if (digits.startsWith('00') && digits.length >= 9) {
    const e164digits = digits.slice(2)
    if (e164digits.length < 7 || e164digits.length > 15) {
      throw new PhoneValidationError(raw, `after stripping 00-prefix: ${e164digits.length} digits is out of range`)
    }
    return `+${e164digits}`
  }

  // Bare digits — must include country code, so total length 10–15
  if (digits.length < 10) {
    throw new PhoneValidationError(
      raw,
      `${digits.length} digits is too short — country code required (e.g. +1 for US, +44 for UK)`,
    )
  }
  if (digits.length > 15) {
    throw new PhoneValidationError(raw, `${digits.length} digits exceeds E.164 max of 15`)
  }

  return `+${digits}`
}

/**
 * Asserts that `normalized` is valid E.164. Call after normalizePhoneNumber.
 */
export function validatePhoneNumber(normalized: string): void {
  if (!/^\+\d{7,15}$/.test(normalized)) {
    throw new PhoneValidationError(normalized, 'must be E.164 format: +[country_code][number]')
  }
}
