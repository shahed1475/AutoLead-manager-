import { z } from 'zod'
import { normalizePhoneNumber } from './phone'

/**
 * Validates and normalizes raw LeadData input.
 * The phone_number transform runs normalizePhoneNumber so callers never
 * need to pre-process the number — but they still get a PhoneValidationError
 * surfaced as a ZodError if the number is unworkable.
 */
export const LeadDataSchema = z.object({
  phone_number: z
    .string({ required_error: 'phone_number is required' })
    .min(1, 'phone_number cannot be empty')
    .transform((raw, ctx) => {
      try {
        return normalizePhoneNumber(raw)
      } catch (err) {
        ctx.addIssue({
          code: z.ZodIssueCode.custom,
          message: err instanceof Error ? err.message : String(err),
        })
        return z.NEVER
      }
    }),

  message: z
    .string({ required_error: 'message is required' })
    .min(1, 'message cannot be empty')
    .max(4096, 'message exceeds WhatsApp 4096-character limit'),

  business_name: z
    .string({ required_error: 'business_name is required' })
    .min(1, 'business_name cannot be empty'),
})

/** Shape of the raw input before validation (pre-transform). */
export type LeadDataInput = z.input<typeof LeadDataSchema>

/** Shape after validation and phone normalization (post-transform). */
export type LeadDataParsed = z.output<typeof LeadDataSchema>
