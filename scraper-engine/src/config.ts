import * as dotenv from 'dotenv'
import * as path from 'path'
import type { ScraperConfig, OllamaConfig } from './types'

dotenv.config({ path: path.join(__dirname, '..', '.env') })

function int(val: string | undefined, def: number): number {
  const n = parseInt(val ?? '', 10)
  return isNaN(n) ? def : n
}

export const config: ScraperConfig = {
  headless:       process.env.HEADLESS === 'true',
  userDataDir:    path.resolve(process.env.USER_DATA_DIR ?? './browser-data'),
  maxResults:     int(process.env.MAX_RESULTS, 20),
  pageDelayMin:   int(process.env.PAGE_DELAY_MIN, 2000),
  pageDelayMax:   int(process.env.PAGE_DELAY_MAX, 5000),
  scrollDelayMin: int(process.env.SCROLL_DELAY_MIN, 800),
  scrollDelayMax: int(process.env.SCROLL_DELAY_MAX, 2000),
  fastapiUrl:     process.env.FASTAPI_URL ?? 'http://localhost:8000',
}

export const ollamaConfig: OllamaConfig = {
  baseUrl: process.env.OLLAMA_URL ?? 'http://localhost:11434',
  model:   process.env.OLLAMA_MODEL ?? 'llama3',
  timeout: int(process.env.OLLAMA_TIMEOUT, 120),
}

export const PORT = int(process.env.PORT, 3001)
