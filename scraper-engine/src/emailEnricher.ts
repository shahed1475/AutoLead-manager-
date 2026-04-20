import type { Page } from 'puppeteer'
import type { ScraperConfig } from './types'
import { withPage } from './browserController'
import { extractEmailsFromText, humanDelay } from './utils'
import { logger } from './logger'

const CONTACT_PATHS = ['/contact', '/contact-us', '/about', '/about-us', '/get-in-touch']

async function extractEmailsFromPage(page: Page): Promise<string[]> {
  const [text, html] = await Promise.all([
    page.evaluate(() => document.body?.innerText ?? ''),
    page.content(),
  ])

  const fromText = extractEmailsFromText(text)
  const fromHtml = extractEmailsFromText(html)

  const mailtoLinks = await page.$$eval(
    'a[href^="mailto:"]',
    (anchors) =>
      anchors.map((a) => (a.getAttribute('href') ?? '').replace('mailto:', '').split('?')[0]),
  ).catch(() => [] as string[])

  return [...new Set([...fromText, ...fromHtml, ...mailtoLinks])].filter(Boolean)
}

export async function enrichEmailFromWebsite(
  website: string,
  cfg: ScraperConfig,
): Promise<string | undefined> {
  try {
    const email = await withPage(cfg, async (page) => {
      await page.goto(website, { waitUntil: 'domcontentloaded', timeout: 15000 })
      await humanDelay(800, 1500)

      let emails = await extractEmailsFromPage(page)
      if (emails.length) return emails[0]

      const base = new URL(website).origin
      for (const subPath of CONTACT_PATHS) {
        try {
          await page.goto(base + subPath, { waitUntil: 'domcontentloaded', timeout: 10000 })
          await humanDelay(500, 1200)
          emails = await extractEmailsFromPage(page)
          if (emails.length) return emails[0]
        } catch {
          // 404 or timeout — try next
        }
      }
      return undefined
    })
    return email
  } catch (err) {
    logger.debug(`Email enrichment failed for ${website}: ${err}`, 'enricher')
    return undefined
  }
}
