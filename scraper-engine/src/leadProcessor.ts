import type { Lead } from './types'
import { normalizePhone, normalizeName } from './utils'

export function normalizeLead(raw: Partial<Lead>): Lead {
  return {
    business_name: (raw.business_name ?? '').trim(),
    phone:         raw.phone   ? normalizePhone(raw.phone)  : undefined,
    email:         raw.email   ? raw.email.trim().toLowerCase() : undefined,
    website:       raw.website ? raw.website.trim()         : undefined,
    niche:         raw.niche   ? raw.niche.trim()           : undefined,
    city:          raw.city    ? raw.city.trim()            : undefined,
    address:       raw.address ? raw.address.trim()         : undefined,
    source:        raw.source  ?? 'GOOGLE_MAPS',
    rating:        raw.rating,
    review_count:  raw.review_count,
  }
}

export function isValidLead(lead: Lead): boolean {
  if (!lead.business_name || lead.business_name.length < 2) return false
  // must have at least one contact vector
  if (!lead.phone && !lead.email && !lead.website) return false
  return true
}

export class DuplicateTracker {
  private seenPhones = new Set<string>()
  private seenNames  = new Set<string>()

  isDuplicate(lead: Lead): boolean {
    if (lead.phone && this.seenPhones.has(lead.phone)) return true
    const name = normalizeName(lead.business_name)
    if (this.seenNames.has(name)) return true
    return false
  }

  track(lead: Lead): void {
    if (lead.phone) this.seenPhones.add(lead.phone)
    this.seenNames.add(normalizeName(lead.business_name))
  }

  reset(): void {
    this.seenPhones.clear()
    this.seenNames.clear()
  }
}
