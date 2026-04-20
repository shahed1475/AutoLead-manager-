import type { Browser, Page, ElementHandle } from 'puppeteer'
import type { Lead, ScraperConfig, ScraperSource } from './types'
import { launchBrowser, newPage } from './browserController'
import { enrichEmailFromWebsite } from './emailEnricher'
import { normalizeLead, isValidLead, DuplicateTracker } from './leadProcessor'
import { humanDelay, randomInt, sleep } from './utils'
import { logger } from './logger'

// ── Helpers ───────────────────────────────────────────────────────────────────

async function safeText(el: ElementHandle | null, selector?: string): Promise<string> {
  try {
    const target = selector ? await el!.$(selector) : el
    if (!target) return ''
    return (await target.evaluate((n) => (n as HTMLElement).textContent?.trim() ?? '')) ?? ''
  } catch { return '' }
}

async function safeAttr(el: ElementHandle | null, selector: string, attr: string): Promise<string> {
  try {
    const target = await el!.$(selector)
    if (!target) return ''
    return (await target.evaluate((n, a) => n.getAttribute(a), attr)) ?? ''
  } catch { return '' }
}

// ── Google Maps ───────────────────────────────────────────────────────────────

async function scrollResultsPanel(page: Page): Promise<void> {
  await page.evaluate(() => {
    const panel = document.querySelector('[role="feed"]')
    if (panel) panel.scrollTop += 700
  })
  await sleep(randomInt(700, 1400))
}

async function extractGoogleMapsListing(page: Page, niche: string, city: string): Promise<Partial<Lead>> {
  await page.waitForFunction(() => document.readyState === 'complete', { timeout: 10000 }).catch(() => {})
  await sleep(randomInt(1200, 2200))

  const name = await page.$eval('h1', (el) => el.textContent?.trim() ?? '').catch(() => '')

  const phone = await page.$$eval('a[href^="tel:"]', (links) =>
    (links[0]?.getAttribute('href') ?? '').replace('tel:', '').trim(),
  ).catch(() => '')

  const website = await page.$$eval(
    'a[data-item-id="authority"]',
    (links) => (links[0] as HTMLAnchorElement)?.href ?? '',
  ).catch(() => '')

  const address = await page.$$eval(
    'button[data-item-id="address"] .fontBodyMedium',
    (els) => els[0]?.textContent?.trim() ?? '',
  ).catch(() => '')

  const rating = await page.$$eval(
    'div[jsaction*="pane.rating"] span[aria-label]',
    (els) => els[0]?.getAttribute('aria-label')?.split(' ')[0] ?? '',
  ).catch(() => '')

  const review_count = await page.$$eval(
    'span[aria-label*="review"]',
    (els) => els[0]?.getAttribute('aria-label')?.match(/[\d,]+/)?.[0]?.replace(',', '') ?? '',
  ).catch(() => '')

  return { business_name: name, phone, website, address, niche, city, rating, review_count }
}

// ── Exported: used by AI pipeline to run planner-generated queries ───────────

export async function scrapeGoogleMapsWithQuery(
  page: Page,
  searchQuery: string,
  niche: string,
  city: string,
  maxResults: number,
  cfg: ScraperConfig,
  stopFn: () => boolean,
): Promise<Lead[]> {
  return scrapeGoogleMaps(page, niche, city, maxResults, cfg, stopFn, searchQuery)
}

async function scrapeGoogleMaps(
  page: Page,
  niche: string,
  city: string,
  maxResults: number,
  cfg: ScraperConfig,
  stopFn: () => boolean,
  customQuery?: string,
): Promise<Lead[]> {
  const searchTerm = customQuery ?? `${niche} in ${city}`
  const query = encodeURIComponent(searchTerm)
  const url   = `https://www.google.com/maps/search/${query}/`

  logger.info(`Google Maps → "${searchTerm}"`, 'maps')
  await page.goto(url, { waitUntil: 'networkidle2', timeout: 30000 })
  await humanDelay(2000, 3500)

  const results: Lead[]     = []
  const seenUrls = new Set<string>()

  let scrollRounds = 0
  const maxScrolls = Math.ceil(maxResults / 4) + 6

  while (results.length < maxResults && scrollRounds < maxScrolls && !stopFn()) {
    await scrollResultsPanel(page)
    scrollRounds++

    const listingAnchors = await page.$$('[role="feed"] > div > div > a')

    for (const anchor of listingAnchors) {
      if (results.length >= maxResults || stopFn()) break

      const href = await anchor.evaluate((a) => a.getAttribute('href') ?? '').catch(() => '')
      if (!href || seenUrls.has(href)) continue
      seenUrls.add(href)

      const quickName = await safeText(anchor, '.fontHeadlineSmall')
      logger.info(`Visiting: ${quickName || href.slice(0, 60)}…`, 'maps')

      try {
        await Promise.all([
          anchor.click(),
          page.waitForNavigation({ waitUntil: 'domcontentloaded', timeout: 15000 }),
        ])
        await humanDelay(cfg.pageDelayMin, cfg.pageDelayMax)

        // Wait until URL contains /maps/place/
        await page.waitForFunction(
          () => window.location.href.includes('/maps/place/'),
          { timeout: 10000 },
        ).catch(() => {})

        const detail  = await extractGoogleMapsListing(page, niche, city)
        detail.source = 'GOOGLE_MAPS'

        const lead = normalizeLead(detail)
        if (isValidLead(lead)) {
          if (lead.website && !lead.email) {
            logger.debug(`Enriching email for ${lead.business_name}…`, 'enricher')
            lead.email = await enrichEmailFromWebsite(lead.website, cfg)
          }
          results.push(lead)
          logger.success(`🗺 ${lead.business_name} | ${lead.phone ?? '—'} | ${lead.email ?? '—'}`, 'maps')
        }

        await page.goBack({ waitUntil: 'domcontentloaded', timeout: 15000 })
        await humanDelay(1200, 2200)
      } catch (err) {
        logger.warn(`Error on listing: ${err}`, 'maps')
        try { await page.goBack({ waitUntil: 'domcontentloaded', timeout: 8000 }) } catch {
          await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 20000 })
        }
        await humanDelay(1000, 2000)
      }
    }
  }

  return results
}

// ── Yelp ──────────────────────────────────────────────────────────────────────

async function scrapeYelp(
  page: Page,
  niche: string,
  city: string,
  maxResults: number,
  cfg: ScraperConfig,
  stopFn: () => boolean,
): Promise<Lead[]> {
  const url = `https://www.yelp.com/search?find_desc=${encodeURIComponent(niche)}&find_loc=${encodeURIComponent(city)}`
  logger.info(`Yelp → "${niche}" in "${city}"`, 'yelp')

  await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 })
  await humanDelay(2500, 4000)

  if (page.url().includes('captcha') || page.url().includes('accessdenied')) {
    logger.warn('Yelp CAPTCHA — skipping', 'yelp')
    return []
  }

  // Collect /biz/ links from search results
  const rawLinks = await page.$$eval('a[href*="/biz/"]', (as) =>
    as.map((a) => a.getAttribute('href') ?? '').filter((h) => h.includes('/biz/')),
  ).catch(() => [] as string[])

  const bizUrls = [...new Set(
    rawLinks.map((h) => h.startsWith('http') ? h : `https://www.yelp.com${h}`),
  )].slice(0, maxResults)

  logger.info(`Found ${bizUrls.length} Yelp listings`, 'yelp')

  const results: Lead[] = []

  for (const bizUrl of bizUrls) {
    if (results.length >= maxResults || stopFn()) break
    try {
      await page.goto(bizUrl, { waitUntil: 'domcontentloaded', timeout: 20000 })
      await humanDelay(cfg.pageDelayMin, cfg.pageDelayMax)

      if (!page.url().includes('/biz/')) {
        logger.warn('Yelp CAPTCHA on detail page — stopping Yelp', 'yelp')
        break
      }

      const name = await page.$eval('h1', (el) => el.textContent?.trim() ?? '').catch(() => '')

      const phone = await page.$$eval('a[href^="tel:"]', (as) =>
        (as[0]?.getAttribute('href') ?? '').replace('tel:', '').trim(),
      ).catch(() => '')

      let website = ''
      try {
        const rawHref = await page.$$eval('a[href*="biz_redir"]', (as) =>
          as[0]?.getAttribute('href') ?? '',
        )
        if (rawHref) {
          const u = new URL(rawHref, 'https://www.yelp.com')
          website = u.searchParams.get('url') ?? ''
        }
      } catch { /* no website */ }

      const address = await page.$$eval('address', (els) =>
        els[0]?.textContent?.replace(/\s+/g, ' ').trim() ?? '',
      ).catch(async () =>
        page.$$eval('p[itemprop="address"]', (els) => els[0]?.textContent?.trim() ?? '').catch(() => ''),
      )

      const lead = normalizeLead({ business_name: name, phone, website, address: await address, niche, city, source: 'YELP' })
      if (isValidLead(lead)) {
        if (lead.website && !lead.email) lead.email = await enrichEmailFromWebsite(lead.website, cfg)
        results.push(lead)
        logger.success(`⭐ ${lead.business_name} | ${lead.phone ?? '—'}`, 'yelp')
      }
    } catch (err) {
      logger.warn(`Yelp detail error: ${err}`, 'yelp')
    }
  }

  return results
}

// ── Yellow Pages ──────────────────────────────────────────────────────────────

async function scrapeYellowPages(
  page: Page,
  niche: string,
  city: string,
  maxResults: number,
  cfg: ScraperConfig,
  stopFn: () => boolean,
): Promise<Lead[]> {
  let url = `https://www.yellowpages.com/search?search_terms=${encodeURIComponent(niche)}&geo_location_terms=${encodeURIComponent(city)}`
  logger.info(`Yellow Pages → "${niche}" in "${city}"`, 'yp')

  const results: Lead[] = []
  let   pageNum  = 0

  while (results.length < maxResults && !stopFn()) {
    pageNum++
    await page.goto(url, { waitUntil: 'domcontentloaded', timeout: 30000 })
    await humanDelay(1500, 3000)

    const cards = await page.$$('.organic .result').catch(() => [] as ElementHandle[])
    if (!cards.length) { logger.warn(`YP page ${pageNum}: no results`, 'yp'); break }

    for (const card of cards) {
      if (results.length >= maxResults || stopFn()) break
      try {
        const name = await card.$eval('.business-name span', (el) => el.textContent?.trim() ?? '')
          .catch(async () => card.$eval('h2.n span', (el) => el.textContent?.trim() ?? '').catch(() => ''))

        const phone = await card.$eval('.phones.phone.primary', (el) => el.textContent?.trim() ?? '')
          .catch(async () => card.$eval('.phone', (el) => el.textContent?.trim() ?? '').catch(() => ''))

        const website = await card.$eval('a.track-visit-website', (a) => (a as HTMLAnchorElement).href)
          .catch(() => '')

        const address = await card.$eval('.adr', (el) => el.textContent?.replace(/\s+/g, ' ').trim() ?? '')
          .catch(() => '')

        const lead = normalizeLead({ business_name: name, phone, website, address, niche, city, source: 'YELLOW_PAGES' })
        if (isValidLead(lead)) {
          if (lead.website && !lead.email) {
            await humanDelay(300, 600)
            lead.email = await enrichEmailFromWebsite(lead.website, cfg)
          }
          results.push(lead)
          logger.success(`📒 ${lead.business_name} | ${lead.phone ?? '—'}`, 'yp')
        }
      } catch (err) {
        logger.debug(`YP card error: ${err}`, 'yp')
      }
    }

    // Pagination
    const nextUrl = await page.$$eval('a.next.ajax-page', (as) => (as[0] as HTMLAnchorElement)?.href ?? '')
      .catch(async () => page.$$eval('a[rel="next"]', (as) => (as[0] as HTMLAnchorElement)?.href ?? '').catch(() => ''))
    if (!nextUrl) break
    url = nextUrl
  }

  return results
}

// ── Multi-source orchestrator ─────────────────────────────────────────────────

export type ProgressCallback = (lead: Lead) => Promise<void> | void

export async function scrapeMultiSource(
  sources:     ScraperSource[],
  niche:       string,
  city:        string,
  maxResults:  number,
  cfg:         ScraperConfig,
  stopFn:      () => boolean,
  onProgress?: ProgressCallback,
): Promise<Lead[]> {
  const n       = sources.length
  const base    = Math.floor(maxResults / n)
  const budgets = Object.fromEntries(
    sources.map((s, i) => [s, base + (i === 0 ? maxResults % n : 0)]),
  )

  const browser = await launchBrowser(cfg)
  const allLeads: Lead[]     = []
  const dedup  = new DuplicateTracker()

  for (const source of sources) {
    if (stopFn()) break
    const budget = budgets[source]
    logger.info(`Starting ${source} (budget: ${budget})`, 'orchestrator')

    const page = await newPage(browser)
    try {
      let sourceLeads: Lead[] = []

      if (source === 'GOOGLE_MAPS')  sourceLeads = await scrapeGoogleMaps(page, niche, city, budget, cfg, stopFn)
      else if (source === 'YELP')    sourceLeads = await scrapeYelp(page, niche, city, budget, cfg, stopFn)
      else if (source === 'YELLOW_PAGES') sourceLeads = await scrapeYellowPages(page, niche, city, budget, cfg, stopFn)

      for (const lead of sourceLeads) {
        if (dedup.isDuplicate(lead)) { logger.debug(`Duplicate: ${lead.business_name}`, 'dedup'); continue }
        dedup.track(lead)
        allLeads.push(lead)
        await onProgress?.(lead)
      }
    } finally {
      await page.close().catch(() => {})
    }
  }

  logger.success(`Total unique leads: ${allLeads.length}`, 'orchestrator')
  return allLeads
}
