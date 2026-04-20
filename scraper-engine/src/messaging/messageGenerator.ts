/**
 * AI Message Generator — produces 4 personalized outreach messages per lead.
 * Mirrors the backend ai_brain.py logic but runs directly in the Node.js engine.
 * Retries with a JSON repair prompt on malformed output.
 */

import axios from 'axios'
import type { EnrichedLead, OutreachMessages, OllamaConfig } from '../types'
import { logger } from '../logger'

// ── Style variety pool ────────────────────────────────────────────────────────

const OPENING_STYLES = [
  'Open with a genuine observation about a challenge businesses in their niche commonly face.',
  'Start with a compelling question that highlights a pain point they experience daily.',
  'Begin with a surprising insight about a trend currently affecting their industry.',
  'Open by referencing the competitive pressure in their specific city and how to stand out.',
  'Start with a result-angle — something a similar business achieved recently.',
  'Begin with a direct, confident value statement tailored to their exact industry.',
  'Open by painting a vivid scenario of the specific problem they face right now.',
  'Start by naming something businesses in their niche always struggle with but rarely fix.',
  'Open with a counterintuitive insight about what actually drives growth in their market.',
  'Begin with empathy — acknowledge how the market has shifted, then pivot to clear value.',
]

function randomStyle(): string {
  return OPENING_STYLES[Math.floor(Math.random() * OPENING_STYLES.length)]
}

// ── Prompt builder ────────────────────────────────────────────────────────────

function buildPrompt(lead: EnrichedLead, companyContext: string): string {
  const wq       = lead.websiteQuality
  const analysis = lead.analysis
  const websiteNote = !lead.website
    ? 'They have NO website — no online presence at all.'
    : wq?.quality === 'poor'
      ? `Their website is poor quality (score: ${wq.qualityScore}/100). Issues: ${wq.issues.join(', ')}.`
      : `They have a ${wq?.quality ?? 'basic'} website.`
  const problemNote = analysis?.problems.slice(0, 2).join('; ') ?? websiteNote
  const pitchAngle  = analysis?.pitch_angle?.replace(/_/g, ' ') ?? 'digital marketing'

  return `You are a world-class outreach copywriter. Generate four personalized marketing messages for a business lead.

=== YOUR COMPANY CONTEXT ===
${companyContext}

=== TARGET BUSINESS ===
- Name:    ${lead.business_name}
- Niche:   ${lead.niche ?? 'Unknown'}
- City:    ${lead.city ?? 'Unknown'}
- Website: ${lead.website ?? 'NONE'}
- Rating:  ${lead.rating ?? 'Unknown'} stars, ${lead.review_count ?? '?'} reviews
- Key issues: ${problemNote}
- Best pitch angle: ${pitchAngle}

=== OPENING STYLE DIRECTIVE ===
${randomStyle()}

=== OUTPUT FORMAT ===
Return a single raw JSON object. No markdown. No code fences. Start with { and end with }.

{
  "whatsapp_message": "...",
  "email_subject": "...",
  "email_body": "...",
  "followup_message": "..."
}

=== MESSAGE RULES ===
whatsapp_message:
  - Casual, warm, conversational — like a text from a trusted contact
  - Under 150 words — plain text only, NO asterisks or markdown
  - Reference their specific niche and a real issue from the data above
  - End with a soft, natural CTA

email_subject:
  - 40-60 characters, personalized to their business name or niche
  - Curiosity-driven — forbidden: "Quick question", "Following up", "Checking in"

email_body:
  - 150-250 words, professional but human
  - Reference their niche, city, and the core problem by name
  - One clear value proposition — soft CTA at the end
  - Plain text only

followup_message:
  - Under 80 words — acknowledge prior outreach, introduce a fresh angle
  - Confident and friendly — not desperate — plain text only

=== BANNED PHRASES ===
"I hope this message finds you well" | "I wanted to reach out" | "Touch base" |
"Circle back" | "Synergy" | "Please don't hesitate" | "As a leading provider"

Output ONLY the JSON. Start with {.`
}

const REPAIR_PROMPT = (bad: string) => `Your output was not valid JSON or was missing required keys.

Your output was:
---
${bad.slice(0, 1500)}
---

Return ONLY this JSON structure with all 4 keys. Start with {, end with }. No other text:
{
  "whatsapp_message": "<casual WhatsApp message, under 150 words, plain text>",
  "email_subject": "<subject line, 40-60 chars, personalized>",
  "email_body": "<professional cold email, 150-250 words, plain text>",
  "followup_message": "<friendly follow-up, under 80 words, plain text>"
}`

// ── JSON extraction ───────────────────────────────────────────────────────────

const REQUIRED_KEYS = ['whatsapp_message', 'email_subject', 'email_body', 'followup_message'] as const

function extractMessages(raw: string): OutreachMessages | null {
  const candidates = [
    raw.trim(),
    raw.match(/\{[\s\S]+\}/)?.[0],
    raw.match(/```json\s*([\s\S]+?)\s*```/)?.[1],
    raw.match(/```\s*([\s\S]+?)\s*```/)?.[1],
    raw.replace(/,\s*([}\]])/g, '$1'),
  ]

  for (const candidate of candidates) {
    if (!candidate) continue
    try {
      const d = JSON.parse(candidate)
      if (!REQUIRED_KEYS.every((k) => typeof d[k] === 'string' && d[k].length > 5)) continue
      return {
        whatsapp:      String(d.whatsapp_message).trim(),
        email_subject: String(d.email_subject).trim(),
        email_body:    String(d.email_body).trim(),
        followup:      String(d.followup_message).trim(),
      }
    } catch { /* try next */ }
  }
  return null
}

// ── Ollama call ───────────────────────────────────────────────────────────────

function stripThinking(text: string): string {
  return text.replace(/<think>[\s\S]*?<\/think>/g, '').trim()
}

async function callOllama(
  prompt: string,
  cfg: OllamaConfig,
  temperature: number,
): Promise<string> {
  const res = await axios.post(
    `${cfg.baseUrl}/api/generate`,
    {
      model:   cfg.model,
      prompt,
      stream:  false,
      think:   false,
      options: { temperature, top_p: 0.92, num_predict: 1100 },
    },
    { timeout: cfg.timeout * 1000 },
  )
  return stripThinking((res.data?.response ?? '') as string)
}

// ── Fallback messages ─────────────────────────────────────────────────────────

function fallbackMessages(lead: EnrichedLead): OutreachMessages {
  const biz    = lead.business_name
  const niche  = lead.niche ?? 'business'
  const city   = lead.city ?? ''
  const issue  = !lead.website ? 'you currently don\'t have a website' : 'there\'s room to grow your online presence'

  return {
    whatsapp:
      `Hi! I noticed ${issue} for ${biz}. We help ${niche} businesses in ${city} get more customers online. Would you be open to a quick chat?`,
    email_subject:
      `Growing ${biz}'s online presence in ${city}`,
    email_body:
      `Hi,\n\nI was looking at ${niche} businesses in ${city} and noticed that ${biz} could benefit from a stronger online presence.\n\nMany ${niche} businesses in ${city} are missing out on local customers simply because they're hard to find online. We specialize in helping businesses like yours get discovered and convert visitors into paying customers.\n\nWould you be open to a 15-minute call to explore what's possible?\n\nBest,`,
    followup:
      `Hi, just circling back on my previous message about ${biz}'s online visibility in ${city}. I have a specific idea I think could work well for your ${niche} business. Worth a quick chat?`,
  }
}

// ── Public API ────────────────────────────────────────────────────────────────

const COMPANY_DEFAULT =
  'We are a digital marketing agency helping local businesses grow their online presence through websites, SEO, social media, and lead generation.'

export async function generateMessages(
  lead: EnrichedLead,
  cfg: OllamaConfig,
  companyContext: string = COMPANY_DEFAULT,
): Promise<OutreachMessages> {
  const prompt   = buildPrompt(lead, companyContext)
  let lastOutput = ''

  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const activePrompt = attempt === 1 ? prompt : REPAIR_PROMPT(lastOutput)
      const temp         = attempt === 1 ? 0.7 + Math.random() * 0.15 : 0.25

      lastOutput      = await callOllama(activePrompt, cfg, temp)
      const messages  = extractMessages(lastOutput)

      if (messages) {
        logger.debug(`Messages generated for ${lead.business_name} (attempt ${attempt})`, 'messaging')
        return messages
      }

      logger.warn(`Message parse failed for ${lead.business_name}, attempt ${attempt}`, 'messaging')
    } catch (err) {
      logger.warn(`Ollama error for ${lead.business_name} attempt ${attempt}: ${err}`, 'messaging')
      if (attempt === 3) break
      await new Promise((r) => setTimeout(r, 1000 * attempt))
    }
  }

  logger.warn(`Using fallback messages for ${lead.business_name}`, 'messaging')
  return fallbackMessages(lead)
}

// ── Batch helper ──────────────────────────────────────────────────────────────

export async function generateMessagesInBatch(
  leads: EnrichedLead[],
  cfg: OllamaConfig,
  companyContext?: string,
  onProgress?: (lead: EnrichedLead) => void,
): Promise<void> {
  for (const lead of leads) {
    lead.messages = await generateMessages(lead, cfg, companyContext)
    onProgress?.(lead)
  }
}
