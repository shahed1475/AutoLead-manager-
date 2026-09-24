import { useQuery } from '@tanstack/react-query'
import { BRAND } from '../components/Logo'
import { portalApi, getToken } from './api'

// Public site info set by the owner (name, welcome, contact, sign-up state).
export function usePortalInfo() {
  const { data } = useQuery({ queryKey: ['portal-status'], queryFn: portalApi.status, staleTime: 60_000, refetchInterval: 120_000 })
  return { name: BRAND.name, ...data, name: data?.name || BRAND.name }
}

// The signed-in client (if any).
export function useMe() {
  return useQuery({ queryKey: ['portal-me'], queryFn: portalApi.me, enabled: !!getToken(), retry: false, staleTime: 30_000 })
}
