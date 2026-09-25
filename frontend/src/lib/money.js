// Money shown in the workspace's revenue currency (a label; amounts are never
// converted). Falls back to a plain number if the browser can't format it.
export const CURRENCIES = ['USD', 'BDT', 'AED', 'EUR', 'GBP', 'INR', 'SAR', 'CAD', 'AUD']

export function fmtMoney(n, currency = 'USD', { compact = false } = {}) {
  const v = Number(n) || 0
  try {
    return new Intl.NumberFormat(undefined, {
      style: 'currency', currency, maximumFractionDigits: v % 1 ? 2 : 0,
      ...(compact && v >= 10_000 ? { notation: 'compact', maximumFractionDigits: 1 } : {}),
    }).format(v)
  } catch {
    return `${currency} ${v.toLocaleString()}`
  }
}
