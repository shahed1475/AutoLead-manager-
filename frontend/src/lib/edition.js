// Which HOM this is: 'owner' (your dashboard) or 'client' (a client's private
// workspace — see backend/edition.py). Set once by AuthGate from
// /api/auth/status before anything else renders.
let current = 'owner'

export function setEdition(value) {
  current = value === 'client' ? 'client' : 'owner'
}

export const isClientEdition = () => current === 'client'

// Where a client workspace sends people to sign in (the client link's front door).
export const CLIENT_SIGN_IN = '/login'
export const CLIENT_SIGN_OUT = '/login?signout=1'
