import puppeteer, { Browser, Page } from 'puppeteer'
import puppeteerExtra from 'puppeteer-extra'
import StealthPlugin from 'puppeteer-extra-plugin-stealth'
import * as path from 'path'
import * as fs from 'fs'
import type { ScraperConfig } from './types'
import { logger } from './logger'

puppeteerExtra.use(StealthPlugin())

let _browser: Browser | null = null

export async function launchBrowser(cfg: ScraperConfig): Promise<Browser> {
  if (_browser) return _browser

  const userDataDir = path.resolve(cfg.userDataDir)
  if (!fs.existsSync(userDataDir)) fs.mkdirSync(userDataDir, { recursive: true })

  logger.info(`Launching Chrome (headless=${cfg.headless})…`, 'browser')

  _browser = await (puppeteerExtra as unknown as typeof puppeteer).launch({
    headless: cfg.headless,
    userDataDir,
    defaultViewport: { width: 1280, height: 900 },
    args: [
      '--no-sandbox',
      '--disable-setuid-sandbox',
      '--disable-blink-features=AutomationControlled',
      '--disable-infobars',
      '--window-size=1280,900',
      '--start-maximized',
      '--disable-web-security',
      '--disable-features=IsolateOrigins,site-per-process',
    ],
    ignoreDefaultArgs: ['--enable-automation'],
  })

  logger.success('Chrome launched', 'browser')
  return _browser
}

export async function newPage(browser: Browser): Promise<Page> {
  const page = await browser.newPage()

  await page.setUserAgent(
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
  )
  await page.setViewport({ width: 1280, height: 900 })

  await page.evaluateOnNewDocument(() => {
    Object.defineProperty(navigator, 'webdriver', { get: () => undefined })
    Object.defineProperty(navigator, 'plugins',   { get: () => [1, 2, 3, 4, 5] })
    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] })
    ;(window as unknown as Record<string, unknown>).chrome = { runtime: {} }
  })

  page.on('dialog', async (dialog) => { await dialog.dismiss().catch(() => {}) })

  return page
}

export async function closeBrowser(): Promise<void> {
  if (_browser) {
    await _browser.close()
    _browser = null
    logger.info('Chrome closed', 'browser')
  }
}

export function getBrowser(): Browser | null {
  return _browser
}

export async function withPage<T>(
  cfg: ScraperConfig,
  fn: (page: Page) => Promise<T>,
): Promise<T> {
  const browser = await launchBrowser(cfg)
  const page    = await newPage(browser)
  try {
    return await fn(page)
  } finally {
    await page.close().catch(() => {})
  }
}
