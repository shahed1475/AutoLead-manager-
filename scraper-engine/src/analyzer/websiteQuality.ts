/**
 * Website Quality Checker — fast HTTP-based analysis (no browser).
 * Checks SSL, mobile viewport, analytics, contact forms, technology stack.
 * Returns a structured WebsiteQuality report + 0-100 quality score.
 */

import axios from 'axios'
import { load } from 'cheerio'
import type { WebsiteQuality } from '../types'
import { logger } from '../logger'

const UA =
  'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

// ── No-website sentinel ───────────────────────────────────────────────────────

function noWebsite(): WebsiteQuality {
  return {
    hasWebsite:       false,
    isReachable:      false,
    hasSSL:           false,
    hasMobileViewport:false,
    hasContactInfo:   false,
    hasAnalytics:     false,
    technologies:     [],
    issues:           ['No website found'],
    quality:          'none',
    qualityScore:     0,
  }
}

function unreachable(url: string): WebsiteQuality {
  return {
    hasWebsite:       true,
    isReachable:      false,
    hasSSL:           url.startsWith('https'),
    hasMobileViewport:false,
    hasContactInfo:   false,
    hasAnalytics:     false,
    technologies:     [],
    issues:           ['Website unreachable or timed out'],
    quality:          'poor',
    qualityScore:     8,
  }
}

// ── Technology fingerprinting ─────────────────────────────────────────────────

const TECH_FINGERPRINTS: Record<string, string[]> = {
  'WordPress':     ['wp-content', 'wp-includes', '/wp-json/', 'wordpress'],
  'Wix':           ['wix.com', 'wixsite.com', 'wixstatic.com'],
  'Squarespace':   ['squarespace.com', 'squarespace-cdn.com'],
  'Shopify':       ['shopify.com', 'cdn.shopify.com', 'myshopify.com'],
  'Webflow':       ['webflow.io', 'webflow.com'],
  'GoDaddy':       ['godaddy', 'godaddysites.com'],
  'Weebly':        ['weebly.com', 'weeblysite.com'],
  'Blogger':       ['blogspot.com', 'blogger.com'],
  'React':         ['react.development.js', 'react.min.js', '__REACT'],
  'Next.js':       ['_next/static', '__NEXT_DATA__'],
  'Vue.js':        ['vue.min.js', '__vue__'],
  'Bootstrap':     ['bootstrap.min.css', 'bootstrap.css'],
}

function detectTechnologies(html: string, url: string): string[] {
  const combined = html + url
  return Object.entries(TECH_FINGERPRINTS)
    .filter(([, signatures]) => signatures.some((sig) => combined.includes(sig)))
    .map(([tech]) => tech)
}

// ── Low-quality builder detection ─────────────────────────────────────────────

const LOW_QUALITY_BUILDERS = new Set(['Wix', 'Squarespace', 'GoDaddy', 'Weebly', 'Blogger'])

// ── Main analyser ─────────────────────────────────────────────────────────────

export async function checkWebsiteQuality(url: string): Promise<WebsiteQuality> {
  if (!url || !url.startsWith('http')) return noWebsite()

  try {
    const t0  = Date.now()
    const res = await axios.get(url, {
      timeout:        10_000,
      maxRedirects:   5,
      headers:        { 'User-Agent': UA, 'Accept': 'text/html' },
      validateStatus: (s) => s < 500,
    })
    const loadMs = Date.now() - t0

    const html = typeof res.data === 'string' ? res.data : JSON.stringify(res.data)
    const $    = load(html)

    // ── Feature checks ──────────────────────────────────────────────────────────
    const finalUrl         = res.request?.res?.responseUrl ?? url
    const hasSSL           = finalUrl.startsWith('https')
    const hasMobileViewport= $('meta[name="viewport"]').length > 0
    const hasMetaDesc      = ($('meta[name="description"]').attr('content') ?? '').length > 10
    const hasGTagAnalytics = html.includes('gtag(') || html.includes('googletagmanager')
    const hasGALegacy      = html.includes("ga('create") || html.includes("ga('send")
    const hasFBPixel       = html.includes('fbq(') || html.includes('facebook.com/tr')
    const hasAnalytics     = hasGTagAnalytics || hasGALegacy || hasFBPixel
    const hasEmailOnPage   = /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/.test(html)
    const hasPhoneOnPage   = /\+?[\d\s\-().]{7,}/.test($('body').text())
    const hasContactForm   = $('form').length > 0 || $('input[type="email"]').length > 0
    const hasContactInfo   = hasEmailOnPage || hasPhoneOnPage || hasContactForm
    const isSlowLoad       = loadMs > 5000

    // ── Technology stack ────────────────────────────────────────────────────────
    const technologies = detectTechnologies(html, finalUrl)

    // ── Score calculation ───────────────────────────────────────────────────────
    const issues: string[] = []
    let score = 60

    if (!hasSSL)            { issues.push('No SSL certificate — insecure site');     score -= 15 }
    if (!hasMobileViewport) { issues.push('Not mobile-friendly — no viewport meta'); score -= 15 }
    if (!hasMetaDesc)       { issues.push('Missing meta description — poor SEO');    score -= 8  }
    if (!hasAnalytics)      { issues.push('No analytics tracking installed');        score -= 5  }
    if (!hasContactInfo)    { issues.push('No visible contact information');         score -= 8  }
    if (isSlowLoad)         { issues.push(`Slow load time (${loadMs}ms)`);           score -= 8  }

    const usingLowQualityBuilder = technologies.some((t) => LOW_QUALITY_BUILDERS.has(t))
    if (usingLowQualityBuilder)  { issues.push(`Built on ${technologies.find((t) => LOW_QUALITY_BUILDERS.has(t))} — limited capability`); score -= 10 }

    // Bonuses
    if (hasAnalytics) score += 5
    if (hasContactForm) score += 5

    score = Math.max(10, Math.min(100, score))
    const quality: WebsiteQuality['quality'] =
      score >= 70 ? 'good' : score >= 45 ? 'average' : 'poor'

    return {
      hasWebsite:       true,
      isReachable:      true,
      hasSSL,
      hasMobileViewport,
      hasContactInfo,
      hasAnalytics,
      technologies,
      issues,
      quality,
      qualityScore:     score,
    }
  } catch (err) {
    logger.debug(`Website quality check failed for ${url}: ${err}`, 'quality')
    return unreachable(url)
  }
}

// ── Batch helper ──────────────────────────────────────────────────────────────

export async function checkWebsitesInBatch(
  leads: Array<{ website?: string; business_name: string }>,
  onProgress?: (name: string, q: WebsiteQuality) => void,
): Promise<Map<string, WebsiteQuality>> {
  const results = new Map<string, WebsiteQuality>()

  for (const lead of leads) {
    const q = lead.website
      ? await checkWebsiteQuality(lead.website)
      : noWebsite()

    results.set(lead.business_name, q)
    onProgress?.(lead.business_name, q)
    // short delay to avoid hammering servers
    await new Promise((r) => setTimeout(r, 300 + Math.random() * 400))
  }

  return results
}
