// Shared wording for Find leads runs (backend/lead_runs).
export function stepsLabel(steps = []) {
  const parts = ['Find']
  if (steps.includes('research')) parts.push('research')
  if (steps.includes('outreach')) parts.push('write outreach')
  return parts.length === 1 ? 'Find leads' : `${parts.slice(0, -1).join(', ')} & ${parts.at(-1)}`
}

export const RUN_ACTIVE = new Set(['QUEUED', 'RUNNING', 'CANCEL_REQUESTED'])
