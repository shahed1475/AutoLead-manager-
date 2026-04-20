import axios from 'axios'
import type { Lead, SaveLeadResult } from './types'
import { logger } from './logger'

let _fastapiUrl = 'http://localhost:8000'

export function setFastapiUrl(url: string): void {
  _fastapiUrl = url
}

const http = () =>
  axios.create({
    baseURL: _fastapiUrl,
    timeout: 15000,
    headers: { 'Content-Type': 'application/json' },
  })

export async function checkFastapiHealth(): Promise<boolean> {
  try {
    await http().get('/api/health')
    return true
  } catch {
    return false
  }
}

export async function saveLead(lead: Lead): Promise<SaveLeadResult> {
  try {
    const res = await http().post('/api/leads/', lead)
    return { id: res.data.id, is_new: true, success: true }
  } catch (err: unknown) {
    if (axios.isAxiosError(err) && err.response?.status === 409) {
      return { id: err.response.data?.id ?? 0, is_new: false, success: true }
    }
    const msg = axios.isAxiosError(err)
      ? (err.response?.data?.detail ?? err.message)
      : String(err)
    logger.error(`Failed to save lead "${lead.business_name}": ${msg}`, 'api')
    return { id: 0, is_new: false, success: false, error: msg }
  }
}

export async function updateLeadChannel(leadId: number, channel: string): Promise<void> {
  try {
    await http().put(`/api/leads/${leadId}`, { channel })
  } catch (err) {
    logger.warn(`Could not set channel for lead ${leadId}: ${err}`, 'api')
  }
}

export async function updateLeadAI(
  leadId: number,
  data: {
    ai_whatsapp_msg?:  string
    ai_email_subject?: string
    ai_email_body?:    string
    ai_followup_msg?:  string
  },
): Promise<void> {
  try {
    await http().put(`/api/leads/${leadId}`, data)
  } catch (err) {
    logger.warn(`Could not update AI fields for lead ${leadId}: ${err}`, 'api')
  }
}

export async function getLeadsByStatus(
  status: string,
  limit: number = 50,
): Promise<Array<Record<string, unknown>>> {
  try {
    const res = await http().get('/api/leads/', { params: { status, page_size: limit } })
    return res.data?.items ?? []
  } catch (err) {
    logger.error(`Failed to fetch leads by status: ${err}`, 'api')
    return []
  }
}
