import type { InventoryResponse, LocationDetail, MovementPlan, Overview, PackingBatchResponse, PackingSection, TripDetailResponse, TripListResponse } from './types'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
  })
  if (!response.ok) {
    const body = await response.json().catch(() => ({}))
    throw new Error(body.detail || body.errors?.join(' · ') || `Request failed (${response.status})`)
  }
  return response.json() as Promise<T>
}

export const api = {
  overview: () => request<Overview>('/api/overview'),
  location: (id: string) => request<LocationDetail>(`/api/locations/${encodeURIComponent(id)}`),
  inventory: () => request<InventoryResponse>('/api/inventory'),
  trips: () => request<TripListResponse>('/api/trips'),
  trip: (id: string) => request<TripDetailResponse>(`/api/trips/${encodeURIComponent(id)}`),
  swapCandidates: (tripId: string, planId: string, itemId: string) => request<InventoryResponse>(`/api/trips/${encodeURIComponent(tripId)}/packing-plans/${encodeURIComponent(planId)}/swap-candidates?item_id=${encodeURIComponent(itemId)}`),
  packBatch: (tripId: string, payload: {
    plan_id: string
    decisions: Array<{ section: PackingSection; entry_index: number }>
  }) => request<PackingBatchResponse>(`/api/trips/${encodeURIComponent(tripId)}/packing-batch`, {
    method: 'POST',
    body: JSON.stringify({ ...payload, confirmed: true }),
  }),
  packingAction: (tripId: string, payload: {
    plan_id: string
    section: PackingSection
    entry_index: number
    action: 'pack' | 'swap' | 'remove'
    replacement_item_id?: string
    notes?: string
  }) => request<TripDetailResponse>(`/api/trips/${encodeURIComponent(tripId)}/packing-actions`, {
    method: 'POST',
    body: JSON.stringify({ ...payload, confirmed: true }),
  }),
  addPackingItem: (tripId: string, payload: {
    plan_id: string
    item_id: string
    container: string
    reason?: string
  }) => request<TripDetailResponse>(`/api/trips/${encodeURIComponent(tripId)}/packing-plan-items`, {
    method: 'POST',
    body: JSON.stringify({ ...payload, confirmed: true }),
  }),
  changePackingContainer: (tripId: string, payload: {
    plan_id: string
    section: PackingSection
    entry_index: number
    container: string
  }) => request<TripDetailResponse>(`/api/trips/${encodeURIComponent(tripId)}/packing-plan-containers`, {
    method: 'POST',
    body: JSON.stringify({ ...payload, confirmed: true }),
  }),
  unpackPackingItem: (tripId: string, payload: {
    plan_id: string
    section: PackingSection
    entry_index: number
  }) => request<TripDetailResponse>(`/api/trips/${encodeURIComponent(tripId)}/packing-unpack`, {
    method: 'POST',
    body: JSON.stringify({ ...payload, confirmed: true }),
  }),
  previewMove: (payload: {
    item_ids: string[]
    source: string
    destination: string
    reason?: string
    update_preferred?: boolean
  }) => request<{ movement: MovementPlan; requiresConfirmation: boolean }>('/api/movements/preview', {
    method: 'POST',
    body: JSON.stringify(payload),
  }),
  confirmMove: (payload: {
    item_ids: string[]
    source: string
    destination: string
    reason?: string
    update_preferred?: boolean
  }) => request<{ movement: MovementPlan; applied: boolean }>('/api/movements/confirm', {
    method: 'POST',
    body: JSON.stringify({ ...payload, confirmed: true }),
  }),
}
