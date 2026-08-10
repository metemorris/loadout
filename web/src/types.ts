export type Mode = 'wardrobe' | 'trips'

export interface LocationSummary {
  id: string
  name: string
  kind: 'home' | 'travel_container'
  city: string | null
  region: string | null
  country: string | null
  notes: string | null
  itemCount: number
  definitionCount: number
  categoryCounts: Record<string, number>
}

export interface InventoryItem {
  id: string
  definitionId: string
  name: string
  type: string
  category: string
  color: string | null
  attributes: Record<string, string | number | boolean | null | Array<string | number | boolean | null>>
  uses: string[]
  currentLocation: string
  preferredLocation: string
  condition: string | null
  status: string | null
  notes: string | null
  movements: Array<{ timestamp: string; source: string; destination: string; reason: string | null }>
}

export interface CategoryGroup {
  id: string
  name: string
  description: string
  count: number
  items: InventoryItem[]
}

export interface LocationDetail {
  location: LocationSummary
  categories: CategoryGroup[]
}

export interface Overview {
  locations: LocationSummary[]
  totals: { items: number; definitions: number; homes: number; containers: number }
  categories: Array<{ id: string; name: string; description: string }>
}

export interface TripLeg {
  id: string
  origin: string
  destination: string
  departure: string
  arrival: string
  transport_notes: string | null
}

export interface TripSummary {
  id: string
  name: string
  status: 'planned' | 'in_progress' | 'completed' | 'cancelled'
  startDate: string
  endDate: string
  durationDays: number
  places: Array<{ id: string; name: string; notes: string | null }>
  legs: TripLeg[]
  luggage: string[]
  attachments: Array<Record<string, unknown>>
  planCount: number
  execution: { id: string; status: string } | null
}

export interface TripListResponse { trips: TripSummary[] }

export type PackingSection =
  | 'pack'
  | 'wear_in_transit'
  | 'already_there'
  | 'optional'
  | 'do_not_pack'
  | 'missing'
  | 'leave_at_destination'
  | 'bring_back'
  | 'move_between_legs'

export interface PackingPlanEntry {
  item: string | null
  requirement: string | null
  leg: string | null
  container: string | null
  source: string | null
  destination: string | null
  quantity: number
  reason: string
}

export interface PackingPlan {
  id: string
  trip: string
  status: 'draft' | 'confirmed' | 'superseded' | 'cancelled'
  created_at: string
  sections: Record<PackingSection, PackingPlanEntry[]>
  notes: string | null
}

export interface ExecutionAction {
  id: string
  timestamp: string
  kind: string
  item: string | null
  description: string | null
  decision: string | null
  leg: string | null
  source: string | null
  destination: string | null
  reason: string
  states: Array<{ timestamp: string; status: string; notes: string | null }>
}

export interface TripExecution {
  id: string
  trip: string
  packing_plan: string
  status: string
  created_at: string
  actions: ExecutionAction[]
  reconciliation: Record<string, boolean | string | null>
  notes: string | null
}

export interface TripDetailResponse {
  trip: Omit<TripSummary, 'planCount' | 'execution'>
  containers: LocationSummary[]
  plans: PackingPlan[]
  executions: TripExecution[]
  items: InventoryItem[]
}

export interface InventoryResponse { items: InventoryItem[]; count: number }

export interface MovementPlan {
  item_ids: string[]
  source: string
  destination: string
  timestamp: string
  reason: string | null
  update_preferred: boolean
}
