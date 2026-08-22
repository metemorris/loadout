import { useCallback, useEffect, useMemo, useRef, useState, type FormEvent } from 'react'
import {
  DndContext,
  KeyboardSensor,
  PointerSensor,
  useDraggable,
  useDroppable,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core'
import { motion, AnimatePresence } from 'motion/react'
import {
  BaseballCapIcon,
  BeanieIcon,
  BeltIcon,
  BootIcon,
  BoxingGloveIcon,
  CoatHangerIcon,
  DressIcon,
  HoodieIcon,
  PantsIcon,
  PersonSimpleSwimIcon,
  ShirtFoldedIcon,
  SneakerIcon,
  SockIcon,
  TowelIcon,
  TShirtIcon,
  type IconProps,
} from '@phosphor-icons/react'
import {
  Archive,
  ArrowLeftRight,
  Backpack,
  BriefcaseBusiness,
  Building2,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronUp,
  CloudOff,
  Home,
  House,
  LoaderCircle,
  ListChecks,
  MapPinHouse,
  Menu,
  MoreHorizontal,
  PackageCheck,
  Plane,
  Plus,
  Search,
  ShoppingBag,
  Luggage,
  Shirt,
  Sparkles,
  Star,
  Trash2,
  Warehouse,
  X,
} from 'lucide-react'
import { api } from './api'
import type {
  CategoryGroup,
  InventoryCreatePayload,
  InventoryItem,
  InventoryOptions,
  LocationDetail,
  LocationSummary,
  Mode,
  MovementPlan,
  Overview,
  PackingPlan,
  PackingPlanEntry,
  PackingSection,
  TripDetailResponse,
  TripSummary,
} from './types'

const COLOR_MAP: Record<string, string> = {
  black: '#1a1a1a', white: '#e8e6df', gray: '#747474', grey: '#747474',
  blue: '#264b73', navy: '#14253f', red: '#7d2d2b', green: '#35563a',
  brown: '#634936', beige: '#b5a287', cream: '#d8cfb9', tan: '#a8845c',
  purple: '#5c426d', orange: '#a75a26', yellow: '#b59a3d', pink: '#9d6878',
  silver: '#9b9b9b', gold: '#9c7b38', maroon: '#5d2733', turquoise: '#2e6f70',
}

function colorValue(value: string | null, index: number) {
  if (!value) return ['#292929', '#494949', '#716d65'][index % 3]
  const normalized = value.toLowerCase().split(/[,/+&]/)[0].trim()
  return COLOR_MAP[normalized] || '#4a4a48'
}

function useServerData() {
  const [overview, setOverview] = useState<Overview | null>(null)
  const [trips, setTrips] = useState<TripSummary[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const refreshRequest = useRef(0)

  const refresh = useCallback(async () => {
    const request = ++refreshRequest.current
    try {
      setError(null)
      const [overviewData, tripData] = await Promise.all([api.overview(), api.trips()])
      if (request !== refreshRequest.current) return
      setOverview(overviewData)
      setTrips(tripData.trips)
    } catch (reason) {
      if (request !== refreshRequest.current) return
      setError(reason instanceof Error ? reason.message : 'LoadOut could not reach its local data service.')
    } finally {
      if (request === refreshRequest.current) setLoading(false)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])
  return { overview, trips, loading, error, refresh }
}

function Brand({ onHome }: { onHome?: () => void }) {
  const content = (
    <>
      <img className="brand-mark" src="/logo.svg" alt="" />
      <div className="brand-copy">
        <div className="brand-name">LoadOut</div>
        <div className="brand-subtitle">Wardrobe logistics</div>
      </div>
    </>
  )
  return onHome
    ? <button type="button" className="brand brand-home" onClick={onHome} aria-label="Go to home wardrobe">{content}</button>
    : <div className="brand">{content}</div>
}

function TopBar({ mode, onModeChange, onHome, onAddItem, query, onQueryChange }: {
  mode: Mode
  onModeChange: (mode: Mode) => void
  onHome: () => void
  onAddItem: () => void
  query: string
  onQueryChange: (query: string) => void
}) {
  return (
    <header className="topbar">
      <Brand onHome={onHome} />
      <div className="topbar-divider" />
      <nav className="mode-switcher" aria-label="Primary navigation">
        <button className={mode === 'wardrobe' ? 'mode-button active' : 'mode-button'} onClick={() => onModeChange('wardrobe')}>
          <Shirt size={19} strokeWidth={1.7} /> Wardrobe
        </button>
        <button className={mode === 'trips' ? 'mode-button active' : 'mode-button'} onClick={() => onModeChange('trips')}>
          <Plane size={19} strokeWidth={1.8} /> Trips
        </button>
      </nav>
      {mode === 'wardrobe' && <button type="button" className="topbar-add-item" onClick={onAddItem} aria-label="Add item"><Plus size={18} /><span>Add item</span></button>}
      <label className="global-search">
        <Search size={18} strokeWidth={1.6} />
        <input
          value={query}
          onChange={event => onQueryChange(event.target.value)}
          placeholder={mode === 'wardrobe' ? 'Search this wardrobe' : 'Search trips and containers'}
          aria-label={mode === 'wardrobe' ? 'Search this wardrobe' : 'Search trips and containers'}
        />
        {query && <button type="button" onClick={() => onQueryChange('')} aria-label="Clear search"><X size={15} /></button>}
      </label>
    </header>
  )
}

const LOCATION_ICON_OPTIONS = {
  house: { label: 'House', icon: House },
  building: { label: 'Apartment', icon: Building2 },
  address: { label: 'Address', icon: MapPinHouse },
  warehouse: { label: 'Storage', icon: Warehouse },
  suitcase: { label: 'Suitcase', icon: Luggage },
  carry_on: { label: 'Carry-on', icon: BriefcaseBusiness },
  duffel: { label: 'Duffel', icon: ShoppingBag },
  backpack: { label: 'Backpack', icon: Backpack },
} as const

type LocationIconName = keyof typeof LOCATION_ICON_OPTIONS
type LocationIconPreferences = Record<string, LocationIconName>

function defaultLocationIcon(location: LocationSummary, index: number): LocationIconName {
  const identity = `${location.id} ${location.name}`.toLowerCase()
  if (location.kind === 'travel_container') {
    if (identity.includes('backpack')) return 'backpack'
    if (identity.includes('duffel')) return 'duffel'
    if (identity.includes('carry')) return 'carry_on'
    return 'suitcase'
  }
  return (['house', 'building', 'address'] as const)[index % 3]
}

function LocationIcon({ name, size = 19 }: { name: LocationIconName; size?: number }) {
  const Icon = LOCATION_ICON_OPTIONS[name].icon
  return <Icon size={size} strokeWidth={1.55} />
}

function containerCapacityLabel(location: LocationSummary) {
  const capacity = location.capacity
  if (!capacity || capacity.capacityLiters === null || capacity.maxLoadKg === null) return 'Capacity unavailable'
  return `${capacity.estimatedUsedSpaceLiters} / ${capacity.capacityLiters} L · ${capacity.estimatedLoadKg} / ${capacity.maxLoadKg} kg`
}

function DroppableLocation({ location, active, onSelect, dragging, icon, onIconChange }: {
  location: LocationSummary
  active: boolean
  onSelect: () => void
  dragging: boolean
  icon: LocationIconName
  onIconChange: (icon: LocationIconName) => void
}) {
  const { isOver, setNodeRef } = useDroppable({ id: `location:${location.id}` })
  const [choosingIcon, setChoosingIcon] = useState(false)
  const iconOptions = Object.entries(LOCATION_ICON_OPTIONS) as Array<[LocationIconName, typeof LOCATION_ICON_OPTIONS[LocationIconName]]>
  return (
    <div
      ref={setNodeRef}
      className={`location-row ${active ? 'active' : ''} ${isOver ? 'drop-over' : ''} ${dragging ? 'drop-ready' : ''}`}
    >
      <button className="location-select" type="button" onClick={onSelect}>
        <LocationIcon name={icon} />
        <span>
          <strong>{location.name}</strong>
          <small>{location.kind === 'home' ? `${location.itemCount} items · Home` : `${location.itemCount} items · ${containerCapacityLabel(location)}`}</small>
        </span>
      </button>
      <button className="location-icon-button" type="button" onClick={() => setChoosingIcon(value => !value)} aria-label={`Change icon for ${location.name}`} aria-expanded={choosingIcon}>
        <MoreHorizontal size={16} />
      </button>
      {isOver && <span className="drop-label">Move here</span>}
      {choosingIcon && <div className="location-icon-picker" role="group" aria-label={`Icons for ${location.name}`}>
        {iconOptions.map(([name, option]) => <button key={name} type="button" className={icon === name ? 'active' : ''} onClick={() => { onIconChange(name); setChoosingIcon(false) }} aria-label={option.label} title={option.label}><LocationIcon name={name} size={18} /></button>)}
      </div>}
    </div>
  )
}

function Sidebar({ overview, selectedId, onSelect, dragging, iconPreferences, onIconChange }: {
  overview: Overview
  selectedId: string
  onSelect: (id: string) => void
  dragging: boolean
  iconPreferences: LocationIconPreferences
  onIconChange: (id: string, icon: LocationIconName) => void
}) {
  const [showUnusedContainers, setShowUnusedContainers] = useState(false)
  const homes = overview.locations.filter(location => location.kind === 'home')
  const containers = overview.locations.filter(location => location.kind === 'travel_container')
  const usedContainers = containers.filter(location => location.itemCount > 0)
  const unusedContainers = containers.filter(location => location.itemCount === 0)
  const iconFor = (location: LocationSummary) => iconPreferences[location.id] || defaultLocationIcon(location, overview.locations.indexOf(location))
  return (
    <aside className="sidebar">
      <div className="sidebar-label">Homes</div>
      <div className="location-list">
        {homes.map(location => (
          <DroppableLocation key={location.id} location={location} active={selectedId === location.id} onSelect={() => onSelect(location.id)} dragging={dragging} icon={iconFor(location)} onIconChange={icon => onIconChange(location.id, icon)} />
        ))}
      </div>
      <div className="sidebar-label">Travel containers</div>
      {usedContainers.length > 0 && <div className="location-list">
        {usedContainers.map(location => (
          <DroppableLocation key={location.id} location={location} active={selectedId === location.id} onSelect={() => onSelect(location.id)} dragging={dragging} icon={iconFor(location)} onIconChange={icon => onIconChange(location.id, icon)} />
        ))}
      </div>}
      {unusedContainers.length > 0 && <>
        <button className="unused-containers-toggle" type="button" onClick={() => setShowUnusedContainers(value => !value)} aria-expanded={showUnusedContainers}>
          <span>Unused containers</span><small>{unusedContainers.length}</small>{showUnusedContainers ? <ChevronUp size={15} /> : <ChevronDown size={15} />}
        </button>
        {showUnusedContainers && <div className="location-list unused-container-list">
          {unusedContainers.map(location => (
            <DroppableLocation key={location.id} location={location} active={selectedId === location.id} onSelect={() => onSelect(location.id)} dragging={dragging} icon={iconFor(location)} onIconChange={icon => onIconChange(location.id, icon)} />
          ))}
        </div>}
      </>}
      <div className="sidebar-spacer" />
    </aside>
  )
}

function StatRing({ value, label }: { value: number; label: string }) {
  return (
    <div className="stat-ring" style={{ '--ring-value': `${Math.max(4, Math.min(100, value)) * 3.6}deg` } as React.CSSProperties}>
      <div><strong>{value}%</strong><small>{label}</small></div>
    </div>
  )
}

function CategoryArtwork({ group, onOpen }: { group: CategoryGroup; onOpen: () => void }) {
  const itemPreview = group.items.slice(0, 3)
  const [focusedItem, setFocusedItem] = useState<InventoryItem | null>(null)
  const focusLabel = focusedItem ? focusedItem.name : 'Explore items'
  return (
    <div className={`artwork zone-art zone-art-${group.id}`}>
      <img src={group.artwork} alt={`${group.name} inventory`} />
      <div className="photo-vignette" />
      <div className="asset-interaction" aria-label={`${group.count} items in ${group.name}`}>
        <span className="asset-hint">{focusLabel}</span>
        <div className="zone-preview">
          {itemPreview.map((item, index) => (
            <button key={item.id} type="button" aria-label={`View ${item.name}`} onClick={event => { event.stopPropagation(); onOpen() }} onMouseEnter={() => setFocusedItem(item)} onMouseLeave={() => setFocusedItem(null)} onFocus={() => setFocusedItem(item)} onBlur={() => setFocusedItem(null)}>
              <GarmentGlyph item={item} index={index} size={20} />
            </button>
          ))}
        </div>
      </div>
    </div>
  )
}

function GarmentGlyph({ item, index, size = 28 }: { item: InventoryItem; index: number; size?: number }) {
  const props: IconProps = { size, color: colorValue(item.color, index), weight: 'duotone' }
  if (['jeans', 'shorts', 'sports_shorts', 'sweatpants', 'thermal_pants', 'underwear'].includes(item.type)) return <PantsIcon {...props} />
  if (['hoodie', 'zip_up', 'sweater', 'jacket'].includes(item.type)) return <HoodieIcon {...props} />
  if (['boots'].includes(item.type)) return <BootIcon {...props} />
  if (['shoes', 'dress_shoes', 'athletic_shoes', 'soccer_shoes', 'flip_flops'].includes(item.type)) return <SneakerIcon {...props} />
  if (item.type === 'hat') return <BaseballCapIcon {...props} />
  if (item.type === 'beanie') return <BeanieIcon {...props} />
  if (item.type === 'belt' || item.type === 'tie') return <BeltIcon {...props} />
  if (item.type === 'gloves') return <BoxingGloveIcon {...props} />
  if (item.type === 'socks') return <SockIcon {...props} />
  if (['towel', 'beach_towel'].includes(item.type)) return <TowelIcon {...props} />
  if (['swim_trunks', 'swimwear'].includes(item.type)) return <PersonSimpleSwimIcon {...props} />
  if (['suit', 'tuxedo', 'suit_jacket', 'vest'].includes(item.type)) return index % 2 ? <DressIcon {...props} /> : <CoatHangerIcon {...props} />
  if (item.type === 'shirt' || item.type === 'long_sleeve_shirt') return <ShirtFoldedIcon {...props} />
  return <TShirtIcon {...props} />
}

function CategoryCard({ group, onOpen }: { group: CategoryGroup; onOpen: () => void }) {
  return (
    <div
      role="button"
      tabIndex={0}
      className={`category-card category-${group.id}`}
      onClick={onOpen}
      onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onOpen() } }}
    >
      <div className="card-title"><span><strong>{group.name}</strong><small>{group.count} items</small></span><MoreHorizontal size={20} /></div>
      <CategoryArtwork group={group} onOpen={onOpen} />
    </div>
  )
}

function DraggableItem({ item, locationName, onView }: { item: InventoryItem; locationName: string; onView: () => void }) {
  const { attributes, listeners, setNodeRef, transform, isDragging } = useDraggable({
    id: `item:${item.id}`,
    data: { item },
  })
  const style = transform ? { transform: `translate3d(${transform.x}px, ${transform.y}px, 0)` } : undefined
  return (
    <div ref={setNodeRef} style={style} className={`item-row ${isDragging ? 'dragging' : ''}`} onClick={onView} onKeyDown={event => { if (event.key === 'Enter') onView() }} {...listeners} {...attributes}>
      <span className="item-icon-box"><GarmentGlyph item={item} index={0} size={29} /></span>
      <span className="item-copy"><strong>{item.name}</strong><small>{item.type.replaceAll('_', ' ')} · {locationName}</small></span>
      <span className="drag-handle"><Menu size={17} /></span>
    </div>
  )
}

function CategoryDrawer({ group, location, onClose }: {
  group: CategoryGroup | null
  location: LocationSummary
  onClose: () => void
}) {
  const [query, setQuery] = useState('')
  const [selectedItem, setSelectedItem] = useState<InventoryItem | null>(null)
  const items = useMemo(() => {
    if (!group) return []
    const needle = query.toLowerCase().trim()
    return needle ? group.items.filter(item => `${item.name} ${item.type} ${item.color || ''}`.toLowerCase().includes(needle)) : group.items
  }, [group, query])
  return (
    <AnimatePresence>
      {group && (
        <motion.aside className="drawer" initial={{ x: '100%' }} animate={{ x: 0 }} exit={{ x: '100%' }} transition={{ type: 'spring', stiffness: 330, damping: 34 }}>
          {selectedItem ? <ItemDetail item={selectedItem} locationName={location.name} onBack={() => setSelectedItem(null)} onClose={onClose} /> : <>
          <div className="drawer-header">
            <div><small>{location.name}</small><h2>{group.name}</h2><p>{group.count} physical items. Drag any item onto a location.</p></div>
            <button className="icon-button" onClick={onClose} aria-label="Close category"><X size={20} /></button>
          </div>
          <label className="search-field"><Search size={17} /><input value={query} onChange={event => setQuery(event.target.value)} placeholder={`Search ${group.name.toLowerCase()}`} /></label>
          <div className="drawer-list">
            {items.map(item => <DraggableItem key={item.id} item={item} locationName={location.name} onView={() => setSelectedItem(item)} />)}
          </div>
          <div className="drawer-tip"><ArrowLeftRight size={17} /> Drag onto a home or container in the sidebar to preview a move.</div>
          </>}
        </motion.aside>
      )}
    </AnimatePresence>
  )
}

function ItemDetail({ item, locationName, onBack, onClose }: { item: InventoryItem; locationName: string; onBack: () => void; onClose: () => void }) {
  const attributes = Object.entries(item.attributes).filter(([, value]) => value !== null && value !== '')
  return <div className="item-detail">
    <div className="item-detail-nav"><button onClick={onBack}><ArrowLeftRight size={16} /> All items</button><button className="icon-button" onClick={onClose} aria-label="Close item"><X size={20} /></button></div>
    <div className="item-detail-hero"><span className="item-icon-box"><GarmentGlyph item={item} index={0} size={42} /></span><small>Item details · View only</small><h2>{item.name}</h2><p>{item.type.replaceAll('_', ' ')}</p></div>
    <dl className="item-metadata">
      <div><dt>Current location</dt><dd>{locationName}</dd></div><div><dt>Preferred location</dt><dd>{item.preferredLocation}</dd></div>
      <div><dt>Condition</dt><dd>{item.condition || 'Not recorded'}</dd></div><div><dt>Status</dt><dd>{item.status || 'Available'}</dd></div>
      {attributes.map(([key, value]) => <div key={key}><dt>{key.replaceAll('_', ' ')}</dt><dd>{Array.isArray(value) ? value.join(', ') : String(value)}</dd></div>)}
    </dl>
    <section className="item-notes"><h3>Notes</h3><p>{item.notes || 'No notes have been added.'}</p></section>
    <section className="item-history"><h3>Movement history</h3><p>{item.movements.length ? `${item.movements.length} recorded movement${item.movements.length === 1 ? '' : 's'}` : 'No recorded movements.'}</p></section>
  </div>
}

function WardrobeView({ detail, category, onCategory, query }: {
  detail: LocationDetail
  category: CategoryGroup | null
  onCategory: (category: CategoryGroup | null) => void
  query: string
}) {
  const displayCategories = detail.categories
  const searchedGroups = useMemo(() => {
    const needle = query.toLowerCase().trim()
    if (!needle) return displayCategories
    return displayCategories.map(group => {
      const items = group.items.filter(item => {
        const attributes = Object.values(item.attributes).flat().filter(Boolean).join(' ')
        return `${item.name} ${item.type} ${item.color || ''} ${item.uses.join(' ')} ${attributes}`.toLowerCase().includes(needle)
      })
      return { ...group, items, count: items.length }
    }).filter(group => group.count > 0)
  }, [displayCategories, query])
  return (
    <main className="main-panel">
      <div className="view-header">
        <div className="view-title"><House size={29} strokeWidth={1.4} /><div><h1>{detail.location.name}</h1><p>{detail.location.kind === 'home' ? 'Your wardrobe. Organized by real inventory category.' : 'A physical travel container.'}</p></div><Star size={21} strokeWidth={1.4} /></div>
        <div className="view-stats">
          {detail.location.kind === 'travel_container' && <div className="capacity-stat"><strong>—</strong><small>Capacity not set</small></div>}
          <div><strong>{displayCategories.length}</strong><small>Categories</small></div>
          <div><strong>{detail.location.itemCount}</strong><small>Items</small></div>
        </div>
      </div>
      {searchedGroups.length ? (
        <div className="category-grid">
          {searchedGroups.map(group => <CategoryCard key={group.id} group={group} onOpen={() => onCategory(group)} />)}
        </div>
      ) : query ? (
        <div className="empty-state search-empty"><Search size={38} /><h2>No wardrobe matches</h2><p>Try an item name, type, color, use, or attribute.</p></div>
      ) : (
        <div className="empty-state"><Archive size={42} /><h2>This location is empty</h2><p>Move physical items here when you are ready. Nothing planned is treated as packed.</p></div>
      )}
      <CategoryDrawer key={`${detail.location.id}:${category?.id || 'closed'}`} group={category} location={detail.location} onClose={() => onCategory(null)} />
    </main>
  )
}

function ContainerVisual({ detail }: { detail?: LocationDetail }) {
  const itemCount = detail?.categories.flatMap(group => group.items).length || 0
  return (
    <div className={`container-visual ${itemCount ? 'has-contents' : ''}`}>
      <img src="/assets/travel-editorial.png" alt={itemCount ? 'Packed travel container' : 'Travel container ready to pack'} />
      <div className="container-photo-shade" />
      {!itemCount && <div className="empty-container-mark"><Luggage size={42} strokeWidth={1.2} /><small>Ready to pack</small></div>}
      {itemCount > 0 && <div className="container-photo-caption"><Check size={15} /> Physical items inside</div>}
    </div>
  )
}

const PACKING_SECTION_LABELS: Record<PackingSection, string> = {
  pack: 'Pack',
  wear_in_transit: 'Wear in transit',
  already_there: 'Already there',
  optional: 'Optional',
  do_not_pack: 'Do not pack',
  missing: 'Missing',
  leave_at_destination: 'Leave there',
  bring_back: 'Bring back',
  move_between_legs: 'Between legs',
}

type PendingPackingAction = {
  action: 'pack' | 'swap' | 'remove'
  section: PackingSection
  entryIndex: number
  entry: PackingPlanEntry
  item: InventoryItem
  plan: PackingPlan
  candidates: InventoryItem[]
  batch?: PendingPackingAction[]
  continuesExecution?: boolean
}

type PendingPackingEdit =
  | { mode: 'add'; plan: PackingPlan; section: 'pack' | 'wear_in_transit'; candidates: InventoryItem[]; containers: LocationSummary[]; categoryLabels: Record<string, string>; locationLabels: Record<string, string> }
  | { mode: 'container'; plan: PackingPlan; section: PackingSection; entryIndex: number; entry: PackingPlanEntry; item: InventoryItem; containers: LocationSummary[]; assignedContainerIds: string[]; physicallyPacked: boolean }
  | { mode: 'unpack'; plan: PackingPlan; section: PackingSection; entryIndex: number; entry: PackingPlanEntry; item: InventoryItem; containers: LocationSummary[]; returnTo: string }

const ACTIVE_EXECUTION_STATUSES = new Set(['preparing', 'in_progress', 'reconciling'])

function activeTripExecution(detail: TripDetailResponse) {
  return [...detail.executions]
    .reverse()
    .find(value => ACTIVE_EXECUTION_STATUSES.has(value.status))
}

function visiblePackingPlan(detail: TripDetailResponse): PackingPlan | undefined {
  const latestDraft = [...detail.plans].reverse().find(value => value.status === 'draft')
  if (latestDraft) return latestDraft
  const activeExecution = activeTripExecution(detail)
  if (activeExecution) {
    const executionPlan = detail.plans.find(value => value.id === activeExecution.packing_plan)
    if (executionPlan) return executionPlan
  }
  return detail.plans.at(-1)
}

function PackingListSurface({ detail, availableContainers, locationNames, query, busy, view = 'pack', onAction, onAddItem, onChangeContainer, onUnpack }: {
  detail: TripDetailResponse
  availableContainers: LocationSummary[]
  locationNames: Record<string, string>
  query: string
  busy: boolean
  view?: 'pack' | 'unpack'
  onAction: (pending: PendingPackingAction) => void
  onAddItem: (plan: PackingPlan, section: 'pack' | 'wear_in_transit') => void
  onChangeContainer: (pending: Extract<PendingPackingEdit, { mode: 'container' }>) => void
  onUnpack: (pending: Extract<PendingPackingEdit, { mode: 'unpack' }>) => void
}) {
  const plan = visiblePackingPlan(detail)
  const [section, setSection] = useState<PackingSection>('pack')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [selectedDecisions, setSelectedDecisions] = useState<Set<string>>(new Set())
  const execution = activeTripExecution(detail) || (plan ? detail.executions.find(value => value.packing_plan === plan.id) : undefined)
  const planOwnsExecution = execution?.packing_plan === plan?.id
  useEffect(() => { setSection('pack'); setCategoryFilter('all'); setSelectedDecisions(new Set()) }, [plan?.id, view])
  useEffect(() => { setCategoryFilter('all') }, [section])
  useEffect(() => {
    setSelectedDecisions(current => new Set(
      [...current].filter(decision => {
        const [decisionSection, rawIndex] = decision.split(':') as [PackingSection, string]
        const entry = plan?.sections[decisionSection]?.[Number(rawIndex) - 1]
        return !execution?.actions.some(action => (
          action.states.at(-1)?.status === 'applied'
          && (action.decision === decision || (entry?.item && action.item === entry.item && action.kind === 'packed'))
        ))
      })
    ))
  }, [execution?.actions.length, plan?.id])
  if (!plan) return <div className="empty-state pack-empty"><ListChecks size={42} /><h2>No packing plan yet</h2><p>Create a proposed packing plan for this trip and it will appear here.</p></div>
  const itemMap = new Map(detail.items.map(item => [item.id, item]))
  const entries = plan.sections[section] || []
  const luggageIds = new Set(detail.containers.map(container => container.id))
  const appliedPackedItems = new Set(execution?.actions.filter(action => action.item && action.kind === 'packed' && action.states.at(-1)?.status === 'applied' && luggageIds.has(itemMap.get(action.item)?.currentLocation || '')).map(action => action.item) || [])
  const categoryOptions = Array.from(new Set(entries.map(entry => entry.item ? itemMap.get(entry.item)?.type : null).filter((value): value is string => Boolean(value)))).sort()
  const needle = query.toLowerCase().trim()
  const visibleEntries = entries.map((entry, index) => ({ entry, index })).filter(({ entry }) => {
    const item = entry.item ? itemMap.get(entry.item) : null
    if (view === 'unpack' && (!item || !appliedPackedItems.has(item.id))) return false
    const categoryMatches = categoryFilter === 'all' || item?.type === categoryFilter
    const searchMatches = !needle || `${item?.name || ''} ${item?.type || ''} ${entry.requirement || ''} ${entry.reason} ${entry.container || ''}`.toLowerCase().includes(needle)
    return categoryMatches && searchMatches
  })
  const packEntries = plan.sections.pack
  const packedCount = packEntries.filter(entry => entry.item && appliedPackedItems.has(entry.item)).length
  const completedCount = packEntries.filter((entry, index) => entry.item && (appliedPackedItems.has(entry.item) || (planOwnsExecution && execution?.actions.some(action => action.decision === `pack:${index + 1}` && action.kind === 'rejected' && action.states.at(-1)?.status === 'applied')))).length
  const visibleSelectable = section === 'pack' ? visibleEntries.filter(({ entry }) => !entry.item || !appliedPackedItems.has(entry.item)) : []
  const allVisibleSelected = visibleSelectable.length > 0 && visibleSelectable.every(({ index }) => selectedDecisions.has(`pack:${index + 1}`))

  const toggleVisible = () => {
    setSelectedDecisions(current => {
      const next = new Set(current)
      visibleSelectable.forEach(({ index }) => {
        const decision = `pack:${index + 1}`
        if (allVisibleSelected) next.delete(decision)
        else next.add(decision)
      })
      return next
    })
  }

  const packSelected = () => {
    const selections = packEntries.flatMap((entry, index) => {
      const item = entry.item ? itemMap.get(entry.item) : null
      if (!item || appliedPackedItems.has(item.id) || !selectedDecisions.has(`pack:${index + 1}`)) return []
      return [{ action: 'pack' as const, section: 'pack' as const, entryIndex: index + 1, entry, item, plan, candidates: [], continuesExecution: Boolean(execution) }]
    })
    if (!selections.length) return
    onAction({ ...selections[0], batch: selections })
  }

  return (
    <section className="packing-surface">
      <div className="packing-summary">
        <div><span className={`plan-status ${plan.status}`}>{view === 'unpack' ? 'Physical contents' : plan.status === 'draft' ? 'Updated selections' : 'Confirmed plan'}</span><h2>{view === 'unpack' ? 'Unpack your trip' : 'Your packing list'}</h2><p>{view === 'unpack' ? 'Only physically packed items appear here. Every unpack requires confirmation and records a returned outcome.' : plan.status === 'draft' && execution ? 'Your updated selections are shown against factual progress from the active execution.' : plan.status === 'draft' ? 'Review the proposal. Your first confirmed Pack locks the plan and begins factual execution.' : 'Each completed action is recorded in the trip execution ledger.'}</p>{view === 'pack' && (section === 'pack' || section === 'wear_in_transit') && <button className="add-packing-item" disabled={busy} onClick={() => onAddItem(plan, section)}><Plus size={14} /> Add to {PACKING_SECTION_LABELS[section]}</button>}</div>
        <div className="packing-progress"><strong>{packedCount}<span> / {packEntries.length}</span></strong><small>physically packed</small><div><span style={{ width: `${packEntries.length ? completedCount / packEntries.length * 100 : 0}%` }} /></div></div>
      </div>
      {view === 'pack' && <nav className="packing-section-tabs" aria-label="Packing plan sections">
        {(Object.keys(PACKING_SECTION_LABELS) as PackingSection[]).map(value => (
          <button key={value} className={section === value ? 'active' : ''} onClick={() => setSection(value)}><span>{PACKING_SECTION_LABELS[value]}</span><strong>{plan.sections[value].length}</strong></button>
        ))}
      </nav>}
      {categoryOptions.length > 1 && <nav className="packing-category-filters" aria-label="Filter packing items by category">
        <button className={categoryFilter === 'all' ? 'active' : ''} onClick={() => setCategoryFilter('all')}>All <strong>{entries.length}</strong></button>
        {categoryOptions.map(type => <button key={type} className={categoryFilter === type ? 'active' : ''} onClick={() => setCategoryFilter(type)}>{type.replaceAll('_', ' ')} <strong>{entries.filter(entry => entry.item && itemMap.get(entry.item)?.type === type).length}</strong></button>)}
      </nav>}
      <div className="packing-list">
        <div className="packing-list-head"><span>{PACKING_SECTION_LABELS[section]}</span>{section === 'pack' ? <div className="packing-batch-controls"><label><input type="checkbox" checked={allVisibleSelected} onChange={toggleVisible} disabled={!visibleSelectable.length} /> Select visible</label><span>{selectedDecisions.size} selected</span><button disabled={!selectedDecisions.size || busy} onClick={packSelected}><PackageCheck size={13} /> Pack selected</button></div> : <span>{visibleEntries.length} decisions</span>}</div>
        {visibleEntries.map(({ entry, index }) => {
          const item = entry.item ? itemMap.get(entry.item) : null
          const decision = `${section}:${index + 1}`
          const itemOutcome = entry.item ? execution?.actions.find(action => action.item === entry.item && action.kind === 'packed' && action.states.at(-1)?.status === 'applied') : undefined
          const outcome = itemOutcome || (planOwnsExecution ? execution?.actions.find(action => action.decision === decision && action.states.at(-1)?.status === 'applied') : undefined)
          const replacement = planOwnsExecution ? execution?.actions.find(action => action.description === `Replacement for ${decision}` && action.kind === 'packed' && action.states.at(-1)?.status === 'applied') : undefined
          const replacementItem = replacement?.item ? itemMap.get(replacement.item) : null
          const status = replacement ? 'swapped' : outcome?.kind === 'packed' ? 'packed' : outcome?.kind === 'rejected' ? 'removed' : 'proposed'
          return (
            <article className={`packing-item ${status}`} key={`${decision}:${entry.item || entry.requirement}`}>
              {section === 'pack' && status === 'proposed' && <label className="packing-item-select"><input type="checkbox" checked={selectedDecisions.has(decision)} onChange={() => setSelectedDecisions(current => { const next = new Set(current); if (next.has(decision)) next.delete(decision); else next.add(decision); return next })} aria-label={`Select ${item?.name || entry.requirement || 'packing item'}`} /></label>}
              <span className="packing-item-icon">{item ? <GarmentGlyph item={item} index={index} size={27} /> : <Sparkles size={22} />}</span>
              <div className="packing-item-copy">
                <div><strong>{item?.name || entry.requirement?.replaceAll('_', ' ') || 'Unspecified item'}</strong><span className={`packing-state ${status}`}>{status}</span></div>
                <small>{entry.container ? `To ${locationNames[entry.container] || entry.container}` : PACKING_SECTION_LABELS[section]}{entry.leg ? ` · ${entry.leg.replaceAll('-', ' → ')}` : ''}</small>
                <p>{replacementItem ? `Swapped for ${replacementItem.name}. ` : ''}{entry.reason}</p>
              </div>
              {section === 'pack' && item && status === 'proposed' && <div className="packing-item-actions">
                <button className="pack-now" disabled={busy} onClick={() => onAction({ action: 'pack', section, entryIndex: index + 1, entry, item, plan, candidates: [], continuesExecution: Boolean(execution) })}><PackageCheck size={16} /> Pack</button>
                <button disabled={busy} onClick={() => onAction({ action: 'swap', section, entryIndex: index + 1, entry, item, plan, candidates: [] })}><ArrowLeftRight size={15} /> Swap item</button>
                <button disabled={busy} onClick={() => onChangeContainer({ mode: 'container', plan, section, entryIndex: index + 1, entry, item, containers: availableContainers, assignedContainerIds: detail.containers.map(container => container.id), physicallyPacked: false })}><Luggage size={15} /> Change bag</button>
                <button className="remove-item" disabled={busy} onClick={() => onAction({ action: 'remove', section, entryIndex: index + 1, entry, item, plan, candidates: [] })} aria-label={`Remove ${item.name}`}><Trash2 size={15} /></button>
              </div>}
              {section === 'wear_in_transit' && item && status === 'proposed' && <div className="packing-item-actions">
                <button disabled={busy} onClick={() => onAction({ action: 'swap', section, entryIndex: index + 1, entry, item, plan, candidates: [] })}><ArrowLeftRight size={15} /> Change item</button>
                <button className="remove-item" disabled={busy} onClick={() => onAction({ action: 'remove', section, entryIndex: index + 1, entry, item, plan, candidates: [] })} aria-label={`Remove ${item.name}`}><Trash2 size={15} /></button>
              </div>}
              {section === 'pack' && item && status === 'packed' && <div className="packing-item-actions"><button disabled={busy} onClick={() => onChangeContainer({ mode: 'container', plan, section, entryIndex: index + 1, entry, item, containers: availableContainers, assignedContainerIds: detail.containers.map(container => container.id), physicallyPacked: true })}><Luggage size={15} /> Change bag</button><button className="unpack-item" disabled={busy} onClick={() => { const packedAction = execution?.actions.find(action => action.item === item.id && action.kind === 'packed' && action.states.at(-1)?.status === 'applied' && action.source && !luggageIds.has(action.source)); if (packedAction?.source) onUnpack({ mode: 'unpack', plan, section, entryIndex: index + 1, entry, item, containers: detail.containers, returnTo: packedAction.source }) }}><Archive size={15} /> Unpack</button></div>}
              {status !== 'proposed' && <span className={`outcome-mark ${status}`}>{status === 'packed' ? <Check size={18} /> : status === 'swapped' ? <ArrowLeftRight size={18} /> : <X size={18} />}</span>}
            </article>
          )
        })}
        {!visibleEntries.length && <div className="packing-list-empty"><Search size={24} /><span>{query || categoryFilter !== 'all' ? 'No decisions match these filters.' : 'No decisions in this section.'}</span></div>}
      </div>
    </section>
  )
}

function PackingActionDialog({ pending, busy, error, onClose, onConfirm }: {
  pending: PendingPackingAction | null
  busy: boolean
  error: string | null
  onClose: () => void
  onConfirm: (replacementItemId?: string, notes?: string) => void
}) {
  const [replacementId, setReplacementId] = useState('')
  const [swapNotes, setSwapNotes] = useState('')
  useEffect(() => { setReplacementId(''); setSwapNotes('') }, [pending])
  if (!pending) return null
  const verb = pending.action === 'pack' ? 'Pack' : pending.action === 'swap' ? 'Swap' : 'Remove'
  const startsExecution = pending.plan.status === 'draft' && pending.action === 'pack' && !pending.continuesExecution
  const batchCount = pending.batch?.length || 0
  const targetLabel = batchCount > 1 ? `${batchCount} selected items` : pending.item.name
  const replacementType = pending.item.type.replaceAll('_', ' ')
  const sameTypeCandidates = pending.candidates.filter(candidate => candidate.type === pending.item.type)
  const editsWearSelection = pending.section === 'wear_in_transit' && (pending.action === 'swap' || pending.action === 'remove')
  return (
    <div className="modal-backdrop" role="presentation">
      <motion.div className="move-dialog packing-dialog" role="dialog" aria-modal="true" aria-labelledby="packing-dialog-title" initial={{ opacity: 0, scale: .97 }} animate={{ opacity: 1, scale: 1 }}>
        <div className="dialog-icon">{pending.action === 'pack' ? <PackageCheck /> : pending.action === 'swap' ? <ArrowLeftRight /> : <Trash2 />}</div>
        <div><small>Confirm packing action</small><h2 id="packing-dialog-title">{verb} {targetLabel}?</h2></div>
        {pending.action === 'swap' && <div className="swap-fields"><label className="dialog-field">Replacement · {replacementType}<select value={replacementId} onChange={event => setReplacementId(event.target.value)}><option value="">Choose a {replacementType}</option>{sameTypeCandidates.map(candidate => <option key={candidate.id} value={candidate.id}>{candidate.name} · {candidate.currentLocation}</option>)}</select><small>Only items of the same type are shown.</small></label><label className="dialog-field">Why are you swapping?<textarea value={swapNotes} onChange={event => setSwapNotes(event.target.value)} placeholder="Fit, weather, color, comfort, condition…" rows={3} /><small>This note is saved with the decision as evidence for future recommendations.</small></label></div>}
        <p>{startsExecution ? `This confirms the proposed plan, creates its execution ledger, and moves ${batchCount > 1 ? 'these exact items' : 'this exact item'} into the assigned container${batchCount > 1 ? 's' : ''}. ` : pending.action === 'pack' && pending.continuesExecution && pending.plan.status === 'draft' ? 'This records the updated selection in the active execution without replacing its confirmed plan. ' : ''}{pending.action === 'pack' ? `${batchCount > 1 ? 'Every packed outcome and physical location change' : 'The packed outcome and physical location change'} will be recorded.` : pending.action === 'swap' ? editsWearSelection ? 'This updates the wear-in-transit recommendation only; no item moves.' : pending.plan.status === 'draft' ? 'This updates only the draft recommendation; no item moves yet.' : 'The original decision will be rejected and the replacement will be physically moved and recorded.' : editsWearSelection ? 'This removes the item from the wear-in-transit recommendation; no inventory location changes.' : pending.plan.status === 'draft' ? 'This removes the recommendation from the draft; no physical inventory changes.' : 'This records that you rejected this confirmed packing decision.'}</p>
        {error && <div className="dialog-error">{error}</div>}
        <div className="dialog-actions"><button onClick={onClose} disabled={busy}>Cancel</button><button className="confirm" onClick={() => onConfirm(replacementId || undefined, swapNotes.trim() || undefined)} disabled={busy || (pending.action === 'swap' && (!replacementId || !swapNotes.trim()))}>{busy ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />} Confirm {verb.toLowerCase()}</button></div>
      </motion.div>
    </div>
  )
}

function PackingEditDialog({ pending, busy, error, onClose, onConfirm }: {
  pending: PendingPackingEdit | null
  busy: boolean
  error: string | null
  onClose: () => void
  onConfirm: (itemId: string | undefined, container: string, reason: string | undefined) => void
}) {
  const [itemId, setItemId] = useState('')
  const [container, setContainer] = useState('')
  const [reason, setReason] = useState('')
  const [filter, setFilter] = useState('')
  const [categoryFilter, setCategoryFilter] = useState('all')
  const [locationFilter, setLocationFilter] = useState('all')
  const addCandidates = pending?.mode === 'add' ? pending.candidates : []
  const categoryOptions = Array.from(new Set(addCandidates.map(item => item.category))).sort((left, right) => (pending?.mode === 'add' ? pending.categoryLabels[left] || left : left).localeCompare(pending?.mode === 'add' ? pending.categoryLabels[right] || right : right))
  const locationOptions = Array.from(new Set(addCandidates.map(item => item.currentLocation))).sort((left, right) => (pending?.mode === 'add' ? pending.locationLabels[left] || left : left).localeCompare(pending?.mode === 'add' ? pending.locationLabels[right] || right : right))
  const needle = filter.toLowerCase().trim()
  const filteredCandidates = addCandidates.filter(item => (
    (categoryFilter === 'all' || item.category === categoryFilter)
    && (locationFilter === 'all' || item.currentLocation === locationFilter)
    && (!needle || `${item.name} ${item.type} ${item.color || ''}`.toLowerCase().includes(needle))
  ))
  useEffect(() => {
    setItemId(pending?.mode === 'add' ? pending.candidates[0]?.id || '' : pending?.item.id || '')
    setContainer(pending?.mode === 'unpack' ? pending.returnTo : pending?.mode === 'container' ? pending.entry.container || pending.containers[0]?.id || '' : pending?.containers[0]?.id || '')
    setReason('')
    setFilter('')
    setCategoryFilter('all')
    setLocationFilter('all')
  }, [pending])
  useEffect(() => {
    if (pending?.mode !== 'add' || filteredCandidates.some(item => item.id === itemId)) return
    setItemId(filteredCandidates[0]?.id || '')
  }, [pending, filteredCandidates, itemId])
  if (!pending) return null
  const selectedItem = pending.mode === 'add' ? pending.candidates.find(item => item.id === itemId) : pending.item
  const currentContainer = pending.mode === 'container' ? pending.entry.container : null
  const addingToWear = pending.mode === 'add' && pending.section === 'wear_in_transit'
  const assigningNewTripBag = pending.mode === 'container' && Boolean(container) && !pending.assignedContainerIds.includes(container)
  return (
    <div className="modal-backdrop" role="presentation">
      <motion.div className="move-dialog packing-dialog packing-edit-dialog" role="dialog" aria-modal="true" aria-labelledby="packing-edit-title" initial={{ opacity: 0, scale: .97 }} animate={{ opacity: 1, scale: 1 }}>
        <div className="dialog-icon">{pending.mode === 'add' ? <Plus /> : pending.mode === 'unpack' ? <Archive /> : <Luggage />}</div>
        <div><small>{pending.mode === 'add' ? 'Edit packing list' : pending.mode === 'unpack' ? 'Confirm unpacking' : 'Container assignment'}</small><h2 id="packing-edit-title">{pending.mode === 'add' ? `Add an item to ${PACKING_SECTION_LABELS[pending.section]}` : pending.mode === 'unpack' ? `Unpack ${pending.item.name}?` : `Move ${pending.item.name}?`}</h2></div>
        <div className="swap-fields">
          {pending.mode === 'add' && <><div className="add-item-filters"><label className="dialog-field">Category<select value={categoryFilter} onChange={event => setCategoryFilter(event.target.value)}><option value="all">All categories · {pending.candidates.length}</option>{categoryOptions.map(category => <option key={category} value={category}>{pending.categoryLabels[category] || category.replaceAll('_', ' ')} · {pending.candidates.filter(item => item.category === category).length}</option>)}</select></label><label className="dialog-field">Current location<select value={locationFilter} onChange={event => setLocationFilter(event.target.value)}><option value="all">All locations · {pending.candidates.length}</option>{locationOptions.map(location => <option key={location} value={location}>{pending.locationLabels[location] || location} · {pending.candidates.filter(item => item.currentLocation === location).length}</option>)}</select></label></div><label className="dialog-field">Find within selection<input value={filter} onChange={event => setFilter(event.target.value)} placeholder="Name, type, or color" /></label><label className="dialog-field">Item <span className="optional-label">{filteredCandidates.length} matches</span><select value={itemId} onChange={event => setItemId(event.target.value)}><option value="">{filteredCandidates.length ? 'Choose an item' : 'No items match these filters'}</option>{filteredCandidates.map(item => <option key={item.id} value={item.id}>{item.name} · {item.type.replaceAll('_', ' ')} · {pending.locationLabels[item.currentLocation] || item.currentLocation}</option>)}</select></label></>}
          {pending.mode !== 'unpack' && !addingToWear && <label className="dialog-field">Bag<select value={container} onChange={event => setContainer(event.target.value)}><option value="">Choose a bag</option>{pending.containers.map(value => <option key={value.id} value={value.id} disabled={value.id === currentContainer}>{value.name}{value.id === currentContainer ? ' · current' : pending.mode === 'container' && !pending.assignedContainerIds.includes(value.id) ? ' · add to trip' : ''}</option>)}</select></label>}
          {pending.mode === 'add' && <label className="dialog-field">Reason <span className="optional-label">optional</span><textarea value={reason} onChange={event => setReason(event.target.value)} placeholder="Why this belongs on the trip" rows={2} /></label>}
        </div>
        <p>{pending.mode === 'add' ? addingToWear ? `${selectedItem?.name || 'The selected item'} will be added to the wear-in-transit recommendation. No inventory location changes.` : `${selectedItem?.name || 'The selected item'} will be added to the editable packing revision. Nothing moves until you pack it.` : pending.mode === 'unpack' ? `This moves the item from its current bag back to ${pending.returnTo}, records a returned outcome in the execution ledger, and leaves the packing recommendation available to pack again.` : `${assigningNewTripBag ? 'This bag will also be added to the trip’s available luggage. ' : ''}${pending.physicallyPacked ? 'This item is already packed. Confirming will update the packing revision, physically transfer it to the selected bag, and record the transfer in the execution ledger.' : 'This only changes the planned bag. The item will not move until you pack it.'}`}</p>
        {error && <div className="dialog-error">{error}</div>}
        <div className="dialog-actions"><button onClick={onClose} disabled={busy}>Cancel</button><button className="confirm" disabled={busy || (!addingToWear && !container) || (pending.mode === 'add' && !itemId) || (pending.mode === 'container' && container === currentContainer)} onClick={() => onConfirm(pending.mode === 'add' ? itemId : undefined, container, reason.trim() || undefined)}>{busy ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />} Confirm {pending.mode === 'unpack' ? 'unpack' : ''}</button></div>
      </motion.div>
    </div>
  )
}

type TripTimeFilter = 'all' | 'current' | 'upcoming' | 'past'
type TripSort = 'nearest' | 'chronological'

const TRIP_FILTER_STORAGE_KEY = 'loadout.trip-filter'
const TRIP_SORT_STORAGE_KEY = 'loadout.trip-sort'
const TRIP_SELECTION_STORAGE_KEY = 'loadout.selected-trip'

function localDateKey(date = new Date()) {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function tripTimeBucket(trip: TripSummary, today: string): Exclude<TripTimeFilter, 'all'> {
  if (trip.endDate < today) return 'past'
  if (trip.startDate > today) return 'upcoming'
  return 'current'
}

function compareTrips(left: TripSummary, right: TripSummary, today: string, sort: TripSort) {
  if (sort === 'chronological') return left.startDate.localeCompare(right.startDate) || left.name.localeCompare(right.name)
  const order: Record<Exclude<TripTimeFilter, 'all'>, number> = { current: 0, upcoming: 1, past: 2 }
  const leftBucket = tripTimeBucket(left, today)
  const rightBucket = tripTimeBucket(right, today)
  if (leftBucket !== rightBucket) return order[leftBucket] - order[rightBucket]
  if (leftBucket === 'past') return right.endDate.localeCompare(left.endDate) || left.name.localeCompare(right.name)
  if (leftBucket === 'current') return left.endDate.localeCompare(right.endDate) || right.startDate.localeCompare(left.startDate) || left.name.localeCompare(right.name)
  return left.startDate.localeCompare(right.startDate) || left.endDate.localeCompare(right.endDate) || left.name.localeCompare(right.name)
}

function smartTripSelection(trips: TripSummary[], today: string) {
  const selectable = trips.filter(trip => trip.status !== 'cancelled')
  return [...(selectable.length ? selectable : trips)].sort((left, right) => compareTrips(left, right, today, 'nearest'))[0]?.id || null
}

function tripSearchText(trip: TripSummary, overview: Overview) {
  const locationNames = new Map(overview.locations.map(location => [location.id, location.name]))
  return [
    trip.name,
    trip.status,
    ...trip.places.flatMap(place => [place.name, place.notes || '']),
    ...trip.legs.flatMap(leg => [leg.origin, leg.destination, leg.transport_notes || '']),
    ...trip.luggage.flatMap(id => [id, locationNames.get(id) || '']),
  ].join(' ').toLowerCase()
}

function TripsView({ trips, overview, containerDetails, query, onDataChanged, onLoadContainers }: {
  trips: TripSummary[]
  overview: Overview
  containerDetails: Record<string, LocationDetail>
  query: string
  onDataChanged: () => Promise<void>
  onLoadContainers: () => Promise<void>
}) {
  const today = localDateKey()
  const [selectedTripId, setSelectedTripId] = useState<string | null>(null)
  const [tripFilter, setTripFilter] = useState<TripTimeFilter>(() => {
    const stored = typeof window === 'undefined' ? null : window.sessionStorage.getItem(TRIP_FILTER_STORAGE_KEY)
    return stored === 'current' || stored === 'upcoming' || stored === 'past' ? stored : 'all'
  })
  const [tripSort, setTripSort] = useState<TripSort>(() => (
    typeof window !== 'undefined' && window.sessionStorage.getItem(TRIP_SORT_STORAGE_KEY) === 'chronological' ? 'chronological' : 'nearest'
  ))
  const [selectedContainerId, setSelectedContainerId] = useState<string | null>(null)
  const [surface, setSurface] = useState<'packing' | 'unpacking' | 'containers'>('packing')
  const [tripDetail, setTripDetail] = useState<TripDetailResponse | null>(null)
  const [pendingAction, setPendingAction] = useState<PendingPackingAction | null>(null)
  const [pendingEdit, setPendingEdit] = useState<PendingPackingEdit | null>(null)
  const [actionBusy, setActionBusy] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const inventoryItems = useRef<InventoryItem[] | null>(null)
  const inventoryRequest = useRef<Promise<InventoryItem[]> | null>(null)
  const tripListRef = useRef<HTMLDivElement>(null)
  const trip = trips.find(value => value.id === selectedTripId) || null
  const needle = query.toLowerCase().trim()
  const searchedTrips = trips.filter(value => !needle || tripSearchText(value, overview).includes(needle))
  const tripCounts = searchedTrips.reduce<Record<TripTimeFilter, number>>((counts, value) => {
    counts.all += 1
    counts[tripTimeBucket(value, today)] += 1
    return counts
  }, { all: 0, current: 0, upcoming: 0, past: 0 })
  const visibleTrips = searchedTrips
    .filter(value => tripFilter === 'all' || tripTimeBucket(value, today) === tripFilter)
    .sort((left, right) => compareTrips(left, right, today, tripSort))
  const availableContainers = overview.locations.filter(location => location.kind === 'travel_container')
  const containers = availableContainers.filter(location => !trip || trip.luggage.includes(location.id))
  const visibleContainers = containers.filter(location => {
    if (!needle) return true
    const contents = containerDetails[location.id]?.categories.flatMap(group => group.items).map(item => `${item.name} ${item.type} ${item.color || ''}`).join(' ') || ''
    return `${location.name} ${contents}`.toLowerCase().includes(needle)
  })
  const selectedContainer = containers.find(container => container.id === selectedContainerId) || containers[0]
  const locationNames = Object.fromEntries(overview.locations.map(location => [location.id, location.name]))

  const loadInventoryItems = useCallback(() => {
    if (inventoryItems.current) return Promise.resolve(inventoryItems.current)
    if (!inventoryRequest.current) {
      inventoryRequest.current = api.inventory()
        .then(response => {
          inventoryItems.current = response.items
          return response.items
        })
        .finally(() => { inventoryRequest.current = null })
    }
    return inventoryRequest.current
  }, [])

  useEffect(() => {
    void loadInventoryItems().catch(() => {
      // A visible error is shown if the user opens a chooser and retry also fails.
    })
  }, [loadInventoryItems])

  useEffect(() => {
    if (!trips.length) {
      setSelectedTripId(null)
      return
    }
    const stored = typeof window === 'undefined' ? null : window.sessionStorage.getItem(TRIP_SELECTION_STORAGE_KEY)
    if (!selectedTripId || !trips.some(value => value.id === selectedTripId)) {
      setSelectedTripId(trips.some(value => value.id === stored) ? stored : smartTripSelection(trips, today))
    }
  }, [trips, selectedTripId, today])

  useEffect(() => {
    if (!selectedTripId || !visibleTrips.length || visibleTrips.some(value => value.id === selectedTripId)) return
    setSelectedTripId(visibleTrips[0].id)
  }, [visibleTrips, selectedTripId])

  useEffect(() => {
    if (typeof window === 'undefined') return
    window.sessionStorage.setItem(TRIP_FILTER_STORAGE_KEY, tripFilter)
    window.sessionStorage.setItem(TRIP_SORT_STORAGE_KEY, tripSort)
    if (selectedTripId) window.sessionStorage.setItem(TRIP_SELECTION_STORAGE_KEY, selectedTripId)
  }, [tripFilter, tripSort, selectedTripId])

  useEffect(() => {
    const selected = tripListRef.current?.querySelector<HTMLElement>('[aria-current="true"]')
    selected?.scrollIntoView({ block: 'nearest' })
  }, [selectedTripId, tripFilter, query])

  useEffect(() => {
    let cancelled = false
    if (!selectedTripId) { setTripDetail(null); return () => { cancelled = true } }
    setTripDetail(null)
    setSelectedContainerId(null)
    void api.trip(selectedTripId).then(nextDetail => {
      if (cancelled) return
      setTripDetail(nextDetail)
      setSurface(nextDetail.plans.length ? 'packing' : 'containers')
      if (!nextDetail.plans.length) void onLoadContainers()
    }).catch(reason => {
      if (!cancelled) setActionError(reason instanceof Error ? reason.message : 'The trip could not be loaded.')
    })
    return () => { cancelled = true }
  }, [selectedTripId, onLoadContainers])

  useEffect(() => { if (selectedContainer && !selectedContainerId) setSelectedContainerId(selectedContainer.id) }, [selectedContainer, selectedContainerId])

  const prepareAction = async (pending: PendingPackingAction) => {
    setActionError(null)
    if (pending.action !== 'swap' || !trip) { setPendingAction(pending); return }
    setActionBusy(true)
    try {
      const items = await loadInventoryItems()
      const plannedIds = new Set(Object.values(pending.plan.sections).flat().map(entry => entry.item).filter(Boolean))
      const unavailable = new Set(['missing', 'lost', 'loaned', 'repair', 'discarded'])
      const candidates = items
        .filter(item => !plannedIds.has(item.id) && item.type === pending.item.type && !unavailable.has(item.status || ''))
        .sort((left, right) => Number(left.currentLocation !== pending.item.currentLocation) - Number(right.currentLocation !== pending.item.currentLocation) || left.name.localeCompare(right.name) || left.id.localeCompare(right.id))
      setPendingAction({ ...pending, candidates })
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Swap options could not be loaded.')
    } finally {
      setActionBusy(false)
    }
  }

  const prepareAddItem = async (plan: PackingPlan, section: 'pack' | 'wear_in_transit') => {
    setActionBusy(true)
    setActionError(null)
    try {
      const items = await loadInventoryItems()
      const plannedIds = new Set(Object.values(plan.sections).flat().map(entry => entry.item).filter(Boolean))
      const unavailable = new Set(['missing', 'lost', 'loaned', 'repair', 'discarded'])
      const candidates = items.filter(item => !plannedIds.has(item.id) && !unavailable.has(item.status || ''))
      setPendingEdit({
        mode: 'add',
        plan,
        section,
        candidates,
        containers: tripDetail?.containers || [],
        categoryLabels: Object.fromEntries(overview.categories.map(category => [category.id, category.name])),
        locationLabels: Object.fromEntries(overview.locations.map(location => [location.id, location.name])),
      })
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'Inventory could not be loaded.')
    } finally {
      setActionBusy(false)
    }
  }

  const confirmEdit = async (itemId: string | undefined, container: string, reason: string | undefined) => {
    if (!pendingEdit || !trip) return
    setActionBusy(true)
    setActionError(null)
    try {
      const nextDetail = pendingEdit.mode === 'add'
        ? await api.addPackingItem(trip.id, {
          plan_id: pendingEdit.plan.id,
          item_id: itemId || '',
          section: pendingEdit.section,
          ...(pendingEdit.section === 'pack' ? { container } : {}),
          reason,
        })
        : pendingEdit.mode === 'unpack'
          ? await api.unpackPackingItem(trip.id, { plan_id: pendingEdit.plan.id, section: pendingEdit.section, entry_index: pendingEdit.entryIndex })
          : await api.changePackingContainer(trip.id, { plan_id: pendingEdit.plan.id, section: pendingEdit.section, entry_index: pendingEdit.entryIndex, container })
      setTripDetail(nextDetail)
      setPendingEdit(null)
      if (pendingEdit.mode === 'unpack' || (pendingEdit.mode === 'container' && pendingEdit.physicallyPacked)) {
        inventoryItems.current = null
        void loadInventoryItems().catch(() => {})
      }
      void onDataChanged()
      if (pendingEdit.mode === 'unpack' || (pendingEdit.mode === 'container' && pendingEdit.physicallyPacked)) await onLoadContainers()
    } catch (reasonValue) {
      setActionError(reasonValue instanceof Error ? reasonValue.message : 'The packing-list edit failed.')
    } finally {
      setActionBusy(false)
    }
  }

  const confirmAction = async (replacementItemId?: string, notes?: string) => {
    if (!pendingAction || !trip) return
    setActionBusy(true)
    setActionError(null)
    try {
      if (pendingAction.action === 'pack') {
        const selections = pendingAction.batch || [pendingAction]
        const result = await api.packBatch(trip.id, {
          plan_id: pendingAction.plan.id,
          decisions: selections.map(value => ({ section: value.section, entry_index: value.entryIndex })),
        })
        setTripDetail(current => {
          if (!current) return current
          const itemMap = new Map(current.items.map(item => [item.id, item]))
          result.items.forEach(item => itemMap.set(item.id, item))
          const executions = current.executions.some(value => value.id === result.execution.id)
            ? current.executions.map(value => value.id === result.execution.id ? result.execution : value)
            : [...current.executions, result.execution]
          return {
            ...current,
            plans: current.plans.map(value => value.id === pendingAction.plan.id && !pendingAction.continuesExecution ? { ...value, status: 'confirmed' } : value),
            executions,
            items: [...itemMap.values()],
          }
        })
        if (result.failedDecisions.length) {
          const failed = new Set(result.failedDecisions)
          const failedSelections = selections.filter(value => failed.has(`${value.section}:${value.entryIndex}`))
          setPendingAction({ ...failedSelections[0], batch: failedSelections })
          setActionError(`${result.failedDecisions.length} item${result.failedDecisions.length === 1 ? '' : 's'} could not be packed. Review and retry.`)
        } else {
          setPendingAction(null)
        }
        inventoryItems.current = null
        void loadInventoryItems().catch(() => {})
        void onDataChanged()
        return
      }
      const nextDetail = await api.packingAction(trip.id, {
        plan_id: pendingAction.plan.id,
        section: pendingAction.section,
        entry_index: pendingAction.entryIndex,
        action: pendingAction.action,
        replacement_item_id: replacementItemId,
        notes,
      })
      setTripDetail(nextDetail)
      setPendingAction(null)
      if (pendingAction.action === 'swap' && pendingAction.plan.status !== 'draft' && pendingAction.section !== 'wear_in_transit') {
        inventoryItems.current = null
        void loadInventoryItems().catch(() => {})
      }
      void onDataChanged()
    } catch (reason) {
      setActionError(reason instanceof Error ? reason.message : 'The packing action failed.')
    } finally {
      setActionBusy(false)
    }
  }

  const plan = tripDetail ? visiblePackingPlan(tripDetail) : undefined
  const execution = tripDetail ? activeTripExecution(tripDetail) || (plan ? tripDetail.executions.find(value => value.packing_plan === plan.id) : null) : null
  const tripLuggage = new Set(tripDetail?.containers.map(container => container.id) || [])
  const detailItems = new Map(tripDetail?.items.map(item => [item.id, item]) || [])
  const packedItems = new Set(execution?.actions.filter(action => action.item && action.kind === 'packed' && action.states.at(-1)?.status === 'applied' && tripLuggage.has(detailItems.get(action.item)?.currentLocation || '')).map(action => action.item) || [])
  const packedCount = plan?.sections.pack.filter(entry => entry.item && packedItems.has(entry.item)).length || 0
  return (
    <div className="trips-layout">
      <aside className="trip-sidebar">
        <div className="trip-sidebar-title"><Plane size={17} /> Trips overview</div>
        <div className="trip-sidebar-controls">
          <div className="trip-time-filters" role="group" aria-label="Filter trips by date">
            {(['all', 'current', 'upcoming', 'past'] as TripTimeFilter[]).map(filter => (
              <button key={filter} type="button" className={tripFilter === filter ? 'active' : ''} aria-pressed={tripFilter === filter} onClick={() => setTripFilter(filter)}>
                <span>{filter}</span><strong>{tripCounts[filter]}</strong>
              </button>
            ))}
          </div>
          <div className="trip-list-meta">
            <span>Showing {visibleTrips.length} {visibleTrips.length === 1 ? 'trip' : 'trips'}</span>
            <label><span className="sr-only">Sort trips</span><select value={tripSort} onChange={event => setTripSort(event.target.value as TripSort)}><option value="nearest">Nearest first</option><option value="chronological">Chronological</option></select></label>
          </div>
        </div>
        <div className={`trip-list-scroll ${visibleTrips.length <= 2 ? 'compact' : ''}`} ref={tripListRef} aria-label="Trips">
          {trips.length ? visibleTrips.map(value => {
            const bucket = tripTimeBucket(value, today)
            const badge = value.status === 'cancelled' ? 'cancelled' : bucket
            return (
              <button key={value.id} className={value.id === selectedTripId ? 'trip-row active' : 'trip-row'} aria-current={value.id === selectedTripId ? 'true' : undefined} onClick={() => setSelectedTripId(value.id)}>
                <span className="trip-row-icon"><CalendarDays size={17} /></span>
                <span className="trip-row-copy"><span className={`trip-date-badge ${badge}`}>{badge}</span><strong>{value.name}</strong><small>{formatDate(value.startDate)} – {formatDate(value.endDate)}</small><span>{value.durationDays} days · {value.legs.length} legs · {value.status.replaceAll('_', ' ')}</span></span>
                {value.planCount > 0 && <span className="trip-plan-count" title={`${value.planCount} packing plan`}><ListChecks size={13} />{value.planCount}</span>}
              </button>
            )
          }) : <div className="trip-empty-copy"><CalendarDays size={22} /><strong>No trips saved</strong><small>Containers remain physical inventory locations until a trip is created.</small></div>}
          {trips.length > 0 && visibleTrips.length === 0 && <div className="trip-empty-copy"><Search size={22} /><strong>No trips match</strong><small>{query ? 'Try another search or date filter.' : `There are no ${tripFilter} trips.`}</small></div>}
        </div>
        {trip && <TripTimeline key={trip.id} trip={trip} today={today} />}
      </aside>
      <main className="main-panel trip-main">
        <div className="view-header">
          <div className="view-title">{surface === 'containers' ? <Luggage size={29} strokeWidth={1.4} /> : surface === 'unpacking' ? <Archive size={29} strokeWidth={1.4} /> : <ListChecks size={29} strokeWidth={1.4} />}<div><h1>{trip?.name || 'Travel Containers'}</h1><p>{surface === 'packing' ? 'Pack · Review recommendations and confirm physical packing.' : surface === 'unpacking' ? 'Unpack · Return physically packed items to their recorded homes.' : 'Containers · Physical luggage contents. Confirm every movement.'}</p></div></div>
          <div className="view-stats">{surface !== 'containers' ? <><div><strong>{plan?.sections.pack.length || 0}</strong><small>Proposed</small></div><div><strong>{packedCount}</strong><small>Packed</small></div><StatRing value={plan?.sections.pack.length ? Math.round(packedCount / plan.sections.pack.length * 100) : 0} label="Done" /></> : <><div><strong>{containers.length}</strong><small>Containers</small></div><div><strong>{containers.reduce((sum, value) => sum + value.itemCount, 0)}</strong><small>Items</small></div><div className="capacity-stat"><strong>—</strong><small>Capacity not set</small></div></>}</div>
        </div>
        {surface !== 'containers' ? tripDetail ? <PackingListSurface view={surface === 'unpacking' ? 'unpack' : 'pack'} detail={tripDetail} availableContainers={availableContainers} locationNames={locationNames} query={query} busy={actionBusy} onAction={pending => void prepareAction(pending)} onAddItem={(planValue, sectionValue) => void prepareAddItem(planValue, sectionValue)} onChangeContainer={setPendingEdit} onUnpack={setPendingEdit} /> : <div className="panel-loading"><LoaderCircle className="spin" /></div> : (
          <div className="container-stage">
            <div className="container-grid">
              {visibleContainers.map(container => (
                <button key={container.id} className={selectedContainer?.id === container.id ? 'container-card active' : 'container-card'} onClick={() => setSelectedContainerId(container.id)}>
                  <div className="container-card-title"><div><strong>{container.name}</strong><small>{container.itemCount} physical items</small><small className="container-card-capacity">{containerCapacityLabel(container)}</small></div>{selectedContainer?.id === container.id && <span className="selected-check"><Check size={14} /></span>}</div>
                  <ContainerVisual detail={containerDetails[container.id]} />
                  <div className={container.itemCount ? 'container-status active' : 'container-status'}><Check size={15} /> {container.itemCount ? 'Contains items' : 'Empty'}</div>
                </button>
              ))}
              {!visibleContainers.length && query && <div className="empty-state search-empty"><Search size={38} /><h2>No container matches</h2><p>Try a location, container, or packed item name.</p></div>}
            </div>
            {selectedContainer && <ContainerInspector container={selectedContainer} detail={containerDetails[selectedContainer.id]} />}
          </div>
        )}
        <div className="trip-actions">
          <button className={surface === 'packing' ? 'active' : ''} onClick={() => setSurface('packing')}><PackageCheck /><span><strong>Pack list</strong><small>Review items one by one</small></span></button>
          <button className={surface === 'unpacking' ? 'active' : ''} onClick={() => setSurface('unpacking')}><Archive /><span><strong>Unpack</strong><small>Return packed items home</small></span></button>
          <button className={surface === 'containers' ? 'active' : ''} onClick={() => { setSurface('containers'); void onLoadContainers() }}><Archive /><span><strong>View contents</strong><small>Inspect packed items</small></span></button>
        </div>
      </main>
      <PackingActionDialog pending={pendingAction} busy={actionBusy} error={actionError} onClose={() => { setPendingAction(null); setActionError(null) }} onConfirm={(replacementId, notes) => void confirmAction(replacementId, notes)} />
      <PackingEditDialog pending={pendingEdit} busy={actionBusy} error={actionError} onClose={() => { setPendingEdit(null); setActionError(null) }} onConfirm={(itemId, container, reason) => void confirmEdit(itemId, container, reason)} />
    </div>
  )
}

type TimelinePhase = {
  id: string
  kind: 'leg' | 'stay'
  label: string
  startDate: string
  endDate: string
  notes: string | null
}

function tripTimelinePhases(trip: TripSummary): TimelinePhase[] {
  return trip.legs.flatMap((leg, index) => {
    const phases: TimelinePhase[] = [{
      id: leg.id,
      kind: 'leg',
      label: `${formatPlace(leg.origin)} → ${formatPlace(leg.destination)}`,
      startDate: leg.departure,
      endDate: leg.arrival,
      notes: leg.transport_notes,
    }]
    const nextDate = trip.legs[index + 1]?.departure || trip.endDate
    if (leg.arrival < nextDate) phases.push({
      id: `${leg.id}:stay`,
      kind: 'stay',
      label: `In ${formatPlace(leg.destination)}`,
      startDate: leg.arrival,
      endDate: nextDate,
      notes: null,
    })
    return phases
  })
}

function isCurrentTimelinePhase(phase: TimelinePhase, today: string) {
  return phase.kind === 'leg'
    ? phase.startDate <= today && today <= phase.endDate
    : phase.startDate < today && today < phase.endDate
}

function isCompletedTimelinePhase(phase: TimelinePhase, today: string) {
  return phase.kind === 'stay' ? phase.endDate <= today : phase.endDate < today
}

function TripTimeline({ trip, today }: { trip: TripSummary; today: string }) {
  const [collapsed, setCollapsed] = useState(false)
  const [showCompleted, setShowCompleted] = useState(false)
  const timelineScrollRef = useRef<HTMLDivElement>(null)
  const phases = useMemo(() => tripTimelinePhases(trip), [trip])
  const currentPhase = phases.find(phase => isCurrentTimelinePhase(phase, today))
  const completedPhases = phases.filter(phase => phase.id !== currentPhase?.id && isCompletedTimelinePhase(phase, today))
  const activeAndUpcomingPhases = phases.filter(phase => !completedPhases.includes(phase))
  const firstOrigin = trip.legs[0]?.origin
  const finalDestination = trip.legs[trip.legs.length - 1]?.destination
  const routeSummary = !trip.legs.length
    ? `${trip.places.length} ${trip.places.length === 1 ? 'place' : 'places'} · No travel legs`
    : firstOrigin === finalDestination
    ? `${formatPlace(firstOrigin)} round trip · ${trip.places.length} places`
    : `${formatPlace(firstOrigin)} → ${formatPlace(finalDestination)}`

  const jumpToCurrent = () => {
    setCollapsed(false)
    window.requestAnimationFrame(() => {
      timelineScrollRef.current?.querySelector<HTMLElement>('[data-current-phase="true"]')?.scrollIntoView({ block: 'center', behavior: 'smooth' })
    })
  }

  useEffect(() => {
    if (collapsed || !currentPhase) return
    const frame = window.requestAnimationFrame(() => {
      timelineScrollRef.current?.querySelector<HTMLElement>('[data-current-phase="true"]')?.scrollIntoView({ block: 'nearest' })
    })
    return () => window.cancelAnimationFrame(frame)
  }, [trip.id, collapsed, currentPhase])

  const renderPhase = (phase: TimelinePhase) => {
    const current = phase.id === currentPhase?.id
    const completed = completedPhases.includes(phase)
    const firstLeg = phase.id === trip.legs[0]?.id
    return <div className={`timeline-leg ${phase.kind} ${current ? 'current' : ''} ${completed ? 'completed' : ''}`} key={phase.id} data-current-phase={current || undefined}>
      <span className="timeline-node">{completed ? <Check size={14} /> : phase.kind === 'stay' ? <MapPinHouse size={14} /> : firstLeg ? <Home size={14} /> : <Plane size={14} />}</span>
      <div><span className="timeline-phase-kind">{current ? 'Current · ' : completed ? 'Completed · ' : ''}{phase.kind === 'stay' ? 'Stay' : 'Travel'}</span><strong>{phase.label}</strong><small>{phase.startDate === phase.endDate ? formatDate(phase.startDate) : `${formatDate(phase.startDate)} – ${formatDate(phase.endDate)}`}</small>{phase.notes && <p>{phase.notes}</p>}</div>
    </div>
  }

  return (
    <section className={`trip-timeline ${collapsed ? 'collapsed' : ''}`} aria-label={`Itinerary for ${trip.name}`}>
      <div className="itinerary-heading">
        <button type="button" className="itinerary-collapse" onClick={() => setCollapsed(value => !value)} aria-expanded={!collapsed}><span>Itinerary</span>{collapsed ? <ChevronDown size={14} /> : <ChevronUp size={14} />}</button>
        <strong>{trip.legs.length} legs</strong>
      </div>
      {!collapsed && <>
        <div className="itinerary-tools"><span>{currentPhase ? 'Current phase found' : tripTimeBucket(trip, today) === 'current' ? 'Between dated phases' : 'No active phase'}</span><button type="button" onClick={jumpToCurrent} disabled={!currentPhase}>Jump to current</button></div>
        <div className="trip-timeline-scroll" ref={timelineScrollRef}>
          <div className="trip-route-summary"><Plane size={14} /><span>{routeSummary}</span></div>
          {completedPhases.length > 0 && <div className="completed-phases">
            <button type="button" className="completed-phases-toggle" onClick={() => setShowCompleted(value => !value)} aria-expanded={showCompleted}>
              <span className="completed-phases-check"><Check size={14} /></span>
              <span><strong>{completedPhases.length} completed {completedPhases.length === 1 ? 'phase' : 'phases'}</strong><small>{formatDate(completedPhases[0].startDate)} – {formatDate(completedPhases.at(-1)?.endDate || completedPhases[0].endDate)}</small></span>
              {showCompleted ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
            </button>
            {showCompleted && <div className="completed-phases-list">{completedPhases.map(renderPhase)}</div>}
          </div>}
          {activeAndUpcomingPhases.map(renderPhase)}
        </div>
      </>}
    </section>
  )
}

function ContainerInspector({ container, detail }: { container: LocationSummary; detail?: LocationDetail }) {
  const capacity = container.capacity
  const fill = Math.min(100, Math.max(0, capacity?.spaceUtilizationPercent || 0))
  return (
    <aside className="container-inspector">
      <div className="inspector-title"><Luggage size={21} /><div><h2>{container.name}</h2><p>Physical container location</p></div><Star size={17} /></div>
      <section><h3>Rough capacity</h3><div className="inspector-stat"><strong>{capacity ? `${capacity.estimatedUsedSpaceLiters} L` : '—'}</strong><span>{capacity?.capacityLiters ? `of ${capacity.capacityLiters} L estimated` : 'capacity not configured'}</span></div><div className="simple-meter"><span style={{ width: `${fill}%` }} /></div>{capacity && <><div className="breakdown-row"><span>Space remaining</span><strong>{capacity.remainingSpaceLiters} L</strong></div><div className="breakdown-row"><span>Estimated load</span><strong>{capacity.estimatedLoadKg} / {capacity.maxLoadKg} kg</strong></div></>}<small>Uses compressed-volume and weight defaults by item type; non-inventory contents and the bag itself are excluded.</small></section>
      <section><h3>Contents breakdown</h3>{detail?.categories.length ? detail.categories.map(group => <div className="breakdown-row" key={group.id}><span><Shirt size={15} /> {group.name}</span><strong>{group.count}</strong></div>) : <p className="muted">No physical items in this container.</p>}</section>
      <section><h3>Status</h3><div className={container.itemCount ? 'truth-status active' : 'truth-status'}><Check size={16} /> {container.itemCount ? 'Items physically present' : 'Empty location'}</div><small>Recommendations and accepted plans do not affect this count.</small></section>
    </aside>
  )
}

function AddInventoryItemDialog({ options, locations, defaultLocationId, busy, error, onClose, onConfirm }: {
  options: InventoryOptions | null
  locations: LocationSummary[]
  defaultLocationId: string
  busy: boolean
  error: string | null
  onClose: () => void
  onConfirm: (payload: InventoryCreatePayload) => void
}) {
  const defaultCurrent = locations.some(location => location.id === defaultLocationId) ? defaultLocationId : locations[0]?.id || ''
  const defaultPreferred = locations.find(location => location.id === defaultCurrent && location.kind === 'home')?.id
    || locations.find(location => location.kind === 'home')?.id
    || defaultCurrent
  const [name, setName] = useState('')
  const [itemType, setItemType] = useState('t_shirt')
  const [color, setColor] = useState('')
  const [currentLocation, setCurrentLocation] = useState(defaultCurrent)
  const [preferredLocation, setPreferredLocation] = useState(defaultPreferred)
  const [condition, setCondition] = useState('')
  const [uses, setUses] = useState<string[]>([])
  const [notes, setNotes] = useState('')
  const toggleUse = (value: string) => setUses(current => current.includes(value) ? current.filter(item => item !== value) : [...current, value])
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault()
    const attributes: Record<string, string> = {}
    if (color.trim()) attributes.color = color.trim()
    onConfirm({
      name: name.trim(),
      type: itemType,
      current_location: currentLocation,
      preferred_location: preferredLocation,
      attributes,
      uses,
      condition: condition || undefined,
      notes: notes.trim() || undefined,
    })
  }
  return (
    <div className="modal-backdrop" role="presentation">
      <motion.form className="move-dialog add-inventory-dialog" role="dialog" aria-modal="true" aria-labelledby="add-item-dialog-title" onSubmit={submit} initial={{ opacity: 0, scale: .97 }} animate={{ opacity: 1, scale: 1 }}>
        <div className="dialog-icon"><Plus /></div>
        <div><small>Add physical inventory</small><h2 id="add-item-dialog-title">Add an item</h2></div>
        <p>Record one possession where it physically is now and where it normally belongs.</p>
        <div className="inventory-form-grid">
          <label className="dialog-field field-wide">Item name<input autoFocus value={name} onChange={event => setName(event.target.value)} placeholder="e.g. Benfica White Jersey" /></label>
          <label className="dialog-field">Type<select value={itemType} onChange={event => setItemType(event.target.value)} disabled={!options}><option value="">Choose a type</option>{options?.itemTypes.map(value => <option key={value} value={value}>{value.replaceAll('_', ' ')}</option>)}</select></label>
          <label className="dialog-field">Color <span className="optional-label">optional</span><input value={color} onChange={event => setColor(event.target.value)} placeholder="e.g. white" /></label>
          <label className="dialog-field">Current location<select value={currentLocation} onChange={event => setCurrentLocation(event.target.value)}>{locations.map(location => <option key={location.id} value={location.id}>{location.name}</option>)}</select></label>
          <label className="dialog-field">Preferred location<select value={preferredLocation} onChange={event => setPreferredLocation(event.target.value)}>{locations.map(location => <option key={location.id} value={location.id}>{location.name}</option>)}</select></label>
          <label className="dialog-field">Condition <span className="optional-label">optional</span><select value={condition} onChange={event => setCondition(event.target.value)}><option value="">Not recorded</option><option value="new">New</option><option value="good">Good</option><option value="worn">Worn</option><option value="damaged">Damaged</option></select></label>
          <label className="dialog-field field-wide">Notes <span className="optional-label">optional</span><textarea value={notes} onChange={event => setNotes(event.target.value)} placeholder="Brand, material, graphic, fit, or anything memorable" rows={2} /></label>
        </div>
        <fieldset className="use-picker"><legend>Uses <span>optional</span></legend><div>{options?.uses.map(value => <button key={value} type="button" className={uses.includes(value) ? 'active' : ''} onClick={() => toggleUse(value)}>{value.replaceAll('_', ' ')}</button>)}</div></fieldset>
        <div className="inventory-confirmation"><Check size={16} /> This adds one physical item. It does not infer a movement, packing action, or trip outcome.</div>
        {error && <div className="dialog-error">{error}</div>}
        <div className="dialog-actions"><button type="button" onClick={onClose} disabled={busy}>Cancel</button><button type="submit" className="confirm" disabled={busy || !options || !name.trim() || !itemType || !currentLocation || !preferredLocation}>{busy ? <LoaderCircle className="spin" size={18} /> : <Plus size={18} />} Confirm add</button></div>
      </motion.form>
    </div>
  )
}

function MoveDialog({ plan, item, locationNames, busy, error, onClose, onConfirm }: {
  plan: MovementPlan | null
  item: InventoryItem | null
  locationNames: Record<string, string>
  busy: boolean
  error: string | null
  onClose: () => void
  onConfirm: (reason: string, updatePreferred: boolean) => void
}) {
  const [reason, setReason] = useState('Moved through the LoadOut wardrobe UI')
  const [updatePreferred, setUpdatePreferred] = useState(false)
  if (!plan || !item) return null
  return (
    <div className="modal-backdrop" role="presentation">
      <motion.div className="move-dialog" role="dialog" aria-modal="true" aria-labelledby="move-dialog-title" initial={{ opacity: 0, scale: .97 }} animate={{ opacity: 1, scale: 1 }}>
        <div className="dialog-icon"><ArrowLeftRight /></div>
        <div><small>Confirm physical movement</small><h2 id="move-dialog-title">Move {item.name}?</h2></div>
        <div className="movement-route"><span>{locationNames[plan.source] || plan.source}</span><ArrowLeftRight size={18} /><span>{locationNames[plan.destination] || plan.destination}</span></div>
        <p>This changes the item’s current physical location and appends a movement-history entry. It is separate from any packing recommendation.</p>
        <label className="dialog-field">Reason<input value={reason} onChange={event => setReason(event.target.value)} /></label>
        <label className="check-field"><input type="checkbox" checked={updatePreferred} onChange={event => setUpdatePreferred(event.target.checked)} /><span><strong>Also change preferred home</strong><small>Leave off for temporary packing or travel.</small></span></label>
        {error && <div className="dialog-error">{error}</div>}
        <div className="dialog-actions"><button onClick={onClose} disabled={busy}>Cancel</button><button className="confirm" onClick={() => onConfirm(reason, updatePreferred)} disabled={busy || !reason.trim()}>{busy ? <LoaderCircle className="spin" size={18} /> : <Check size={18} />} Confirm move</button></div>
      </motion.div>
    </div>
  )
}

function formatDate(value: string) {
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(new Date(`${value}T00:00:00Z`))
}

function formatPlace(value?: string) {
  if (!value) return '—'
  return value.replaceAll('-', ' ').replace(/\b\w/g, letter => letter.toUpperCase())
}

export default function App() {
  const { overview, trips, loading, error, refresh } = useServerData()
  const [mode, setMode] = useState<Mode>('wardrobe')
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedLocationId, setSelectedLocationId] = useState('')
  const [detail, setDetail] = useState<LocationDetail | null>(null)
  const [category, setCategory] = useState<CategoryGroup | null>(null)
  const [containerDetails, setContainerDetails] = useState<Record<string, LocationDetail>>({})
  const [dragging, setDragging] = useState(false)
  const [pendingPlan, setPendingPlan] = useState<MovementPlan | null>(null)
  const [pendingItem, setPendingItem] = useState<InventoryItem | null>(null)
  const [moveBusy, setMoveBusy] = useState(false)
  const [moveError, setMoveError] = useState<string | null>(null)
  const [addItemOpen, setAddItemOpen] = useState(false)
  const [inventoryOptions, setInventoryOptions] = useState<InventoryOptions | null>(null)
  const [addItemBusy, setAddItemBusy] = useState(false)
  const [addItemError, setAddItemError] = useState<string | null>(null)
  const [iconPreferences, setIconPreferences] = useState<LocationIconPreferences>(() => {
    try { return JSON.parse(localStorage.getItem('loadout:location-icons') || '{}') as LocationIconPreferences } catch { return {} }
  })
  const locationRequest = useRef(0)
  const containerRequest = useRef(0)
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }), useSensor(KeyboardSensor))

  useEffect(() => {
    if (!overview || selectedLocationId) return
    const homes = overview.locations.filter(location => location.kind === 'home')
    const lastHomeId = localStorage.getItem('loadout:last-home')
    const lastHome = homes.find(location => location.id === lastHomeId)
    setSelectedLocationId(lastHome?.id || homes[0]?.id || overview.locations[0]?.id || '')
  }, [overview, selectedLocationId])

  const selectLocation = (id: string) => {
    setSelectedLocationId(id)
    if (overview?.locations.find(location => location.id === id)?.kind === 'home') localStorage.setItem('loadout:last-home', id)
  }

  const goHome = () => {
    const homes = overview?.locations.filter(location => location.kind === 'home') || []
    const rememberedHome = homes.find(location => location.id === localStorage.getItem('loadout:last-home'))
    const home = rememberedHome || homes[0]
    if (home) selectLocation(home.id)
    setMode('wardrobe')
    setCategory(null)
    setSearchQuery('')
  }

  const changeLocationIcon = (id: string, icon: LocationIconName) => {
    setIconPreferences(current => {
      const next = { ...current, [id]: icon }
      localStorage.setItem('loadout:location-icons', JSON.stringify(next))
      return next
    })
  }

  const loadLocation = useCallback(async (id: string) => {
    const request = ++locationRequest.current
    try {
      const value = await api.location(id)
      if (request !== locationRequest.current) return
      setDetail(value)
      setCategory(null)
    } catch (reason) {
      if (request !== locationRequest.current) return
      setDetail(null)
      console.error(reason)
    }
  }, [])

  useEffect(() => { if (selectedLocationId) void loadLocation(selectedLocationId) }, [selectedLocationId, loadLocation])

  const loadContainerDetails = useCallback(async () => {
    if (!overview) return
    const request = ++containerRequest.current
    const containers = overview.locations.filter(location => location.kind === 'travel_container')
    try {
      const entries = await Promise.all(containers.map(async location => [location.id, await api.location(location.id)] as const))
      if (request !== containerRequest.current) return
      setContainerDetails(Object.fromEntries(entries))
    } catch (reason) {
      if (request !== containerRequest.current) return
      console.error(reason)
    }
  }, [overview])

  const handleDragEnd = async (event: DragEndEvent) => {
    setDragging(false)
    const item = event.active.data.current?.item as InventoryItem | undefined
    const destination = String(event.over?.id || '').replace('location:', '')
    if (!item || !destination || destination === item.currentLocation) return
    try {
      setMoveError(null)
      const response = await api.previewMove({ item_ids: [item.id], source: item.currentLocation, destination })
      setPendingItem(item)
      setPendingPlan(response.movement)
    } catch (reason) {
      setMoveError(reason instanceof Error ? reason.message : 'The movement could not be previewed.')
    }
  }

  const confirmMove = async (reason: string, updatePreferred: boolean) => {
    if (!pendingPlan) return
    setMoveBusy(true)
    try {
      await api.confirmMove({ item_ids: pendingPlan.item_ids, source: pendingPlan.source, destination: pendingPlan.destination, reason, update_preferred: updatePreferred })
      setPendingPlan(null)
      setPendingItem(null)
      await Promise.all([refresh(), loadLocation(selectedLocationId)])
    } catch (failure) {
      setMoveError(failure instanceof Error ? failure.message : 'The physical movement failed.')
    } finally {
      setMoveBusy(false)
    }
  }

  const openAddItem = () => {
    setAddItemOpen(true)
    setAddItemError(null)
    if (inventoryOptions) return
    void api.inventoryOptions().then(setInventoryOptions).catch(reason => {
      setAddItemError(reason instanceof Error ? reason.message : 'Inventory choices could not be loaded.')
    })
  }

  const confirmAddItem = async (payload: InventoryCreatePayload) => {
    setAddItemBusy(true)
    setAddItemError(null)
    try {
      await api.createInventoryItem(payload)
      setAddItemOpen(false)
      selectLocation(payload.current_location)
      await Promise.all([refresh(), loadLocation(payload.current_location)])
    } catch (failure) {
      setAddItemError(failure instanceof Error ? failure.message : 'The inventory item could not be added.')
    } finally {
      setAddItemBusy(false)
    }
  }

  if (loading) return <div className="boot-screen"><Brand /><LoaderCircle className="spin" /><span>Opening your local wardrobe…</span></div>
  if (!overview || error) return <div className="boot-screen error"><CloudOff /><h1>LoadOut is offline</h1><p>{error || 'The local inventory could not be loaded.'}</p><button onClick={() => void refresh()}>Try again</button></div>

  const locationNames = Object.fromEntries(overview.locations.map(location => [location.id, location.name]))
  return (
    <DndContext sensors={sensors} onDragStart={() => setDragging(true)} onDragCancel={() => setDragging(false)} onDragEnd={event => void handleDragEnd(event)}>
      <div className="app-shell">
        <TopBar mode={mode} query={searchQuery} onQueryChange={setSearchQuery} onHome={goHome} onAddItem={openAddItem} onModeChange={value => { setMode(value); setCategory(null); setSearchQuery('') }} />
        {mode === 'wardrobe' ? (
          <div className="workspace">
            <Sidebar overview={overview} selectedId={selectedLocationId} onSelect={selectLocation} dragging={dragging} iconPreferences={iconPreferences} onIconChange={changeLocationIcon} />
            {detail ? <WardrobeView detail={detail} category={category} onCategory={setCategory} query={searchQuery} /> : <div className="panel-loading"><LoaderCircle className="spin" /></div>}
          </div>
        ) : <TripsView trips={trips} overview={overview} containerDetails={containerDetails} query={searchQuery} onDataChanged={refresh} onLoadContainers={loadContainerDetails} />}
        {addItemOpen && <AddInventoryItemDialog options={inventoryOptions} locations={overview.locations} defaultLocationId={selectedLocationId} busy={addItemBusy} error={addItemError} onClose={() => { setAddItemOpen(false); setAddItemError(null) }} onConfirm={payload => void confirmAddItem(payload)} />}
        <MoveDialog plan={pendingPlan} item={pendingItem} locationNames={locationNames} busy={moveBusy} error={moveError} onClose={() => { setPendingPlan(null); setPendingItem(null); setMoveError(null) }} onConfirm={(reason, updatePreferred) => void confirmMove(reason, updatePreferred)} />
      </div>
    </DndContext>
  )
}
