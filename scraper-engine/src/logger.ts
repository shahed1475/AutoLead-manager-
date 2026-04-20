import type WebSocket from 'ws'
import type { LogEntry, WsMessage } from './types'

const COLORS: Record<string, string> = {
  info:    '\x1b[36m',  // cyan
  success: '\x1b[32m',  // green
  warn:    '\x1b[33m',  // yellow
  error:   '\x1b[31m',  // red
  debug:   '\x1b[90m',  // gray
  reset:   '\x1b[0m',
}

const EMOJI: Record<string, string> = {
  info:    'ℹ️ ',
  success: '✅',
  warn:    '⚠️ ',
  error:   '❌',
  debug:   '🔍',
}

let wsClients: Set<WebSocket> = new Set()

export function registerWsClient(ws: WebSocket): void {
  wsClients.add(ws)
  ws.on('close', () => wsClients.delete(ws))
}

function broadcast(msg: WsMessage): void {
  const raw = JSON.stringify(msg)
  for (const client of wsClients) {
    if (client.readyState === 1 /* OPEN */) {
      client.send(raw)
    }
  }
}

export function log(
  level: LogEntry['level'],
  message: string,
  source?: string,
  data?: Record<string, unknown>,
): void {
  const entry: LogEntry = {
    level,
    message,
    source,
    data,
    timestamp: new Date().toISOString(),
  }

  const color  = COLORS[level] ?? ''
  const prefix = EMOJI[level] ?? ''
  const tag    = source ? ` [${source}]` : ''
  console.log(`${color}${prefix}${tag} ${message}${COLORS.reset}`)

  broadcast({ type: 'log', payload: entry })
}

export const logger = {
  info:    (msg: string, src?: string, data?: Record<string, unknown>) => log('info',    msg, src, data),
  success: (msg: string, src?: string, data?: Record<string, unknown>) => log('success', msg, src, data),
  warn:    (msg: string, src?: string, data?: Record<string, unknown>) => log('warn',    msg, src, data),
  error:   (msg: string, src?: string, data?: Record<string, unknown>) => log('error',   msg, src, data),
  debug:   (msg: string, src?: string, data?: Record<string, unknown>) => log('debug',   msg, src, data),
}

export function broadcastState(state: unknown): void {
  broadcast({ type: 'state', payload: state as WsMessage['payload'] })
}

export function broadcastLead(lead: unknown): void {
  broadcast({ type: 'lead', payload: lead as WsMessage['payload'] })
}

export function broadcastDone(message: string): void {
  broadcast({ type: 'done', payload: { message } })
}
