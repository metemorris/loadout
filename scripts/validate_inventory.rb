#!/usr/bin/env ruby

require "yaml"
require "time"
require "date"

ROOT = File.expand_path("..", __dir__)
DATA_DIR = File.join(ROOT, "data")
ID_PATTERN = /\A[a-z0-9]+(?:-[a-z0-9]+)*\z/
KEY_PATTERN = /\A[a-z][a-z0-9_]*\z/
CATEGORY_PATTERN = /\A[a-z][a-z0-9_]*\z/
LOCATION_FIELDS = %w[id name kind city region country aliases notes].freeze
DEFINITION_FIELDS = %w[id name type attributes use notes sources].freeze
SOURCE_FIELDS = %w[id original_location original_quantity original_condition notes].freeze
INSTANCE_FIELDS = %w[id definition source current_location preferred_location condition status notes movements].freeze
MOVEMENT_FIELDS = %w[timestamp source destination reason].freeze
TRIP_FIELDS = %w[id name status start_date end_date places legs luggage attachments planning notes].freeze
PLACE_FIELDS = %w[id name notes].freeze
LEG_FIELDS = %w[id origin destination departure arrival transport_notes].freeze
ATTACHMENT_FIELDS = %w[id kind name type leg location date frequency requirements notes].freeze
TEMPLATE_FIELDS = %w[id name required optional notes].freeze
OVERRIDE_FIELDS = %w[add add_optional remove remove_optional].freeze
PLANNING_FIELDS = %w[legs notes].freeze
LEG_PLANNING_FIELDS = %w[leg activities_complete laundry_available weather context notes].freeze
WEATHER_FIELDS = %w[summary low_c high_c precipitation source checked_at].freeze
MATCHER_FIELDS = %w[category any].freeze
MATCHER_CLAUSE_FIELDS = %w[types tags attributes].freeze
PACKING_PLAN_FIELDS = %w[id trip status created_at sections notes].freeze
PACKING_ENTRY_FIELDS = %w[item requirement leg container source destination quantity reason].freeze
PACKING_SECTIONS = %w[pack wear_in_transit already_there optional do_not_pack missing leave_at_destination bring_back move_between_legs].freeze
EXECUTION_FIELDS = %w[id trip packing_plan status created_at actions reconciliation notes].freeze
EXECUTION_ACTION_FIELDS = %w[id timestamp kind item description decision leg source destination reason states].freeze
EXECUTION_STATE_FIELDS = %w[timestamp status notes].freeze
RECONCILIATION_FIELDS = %w[what_came_back stayed_at_destination never_used purchased incidents rejected_or_changed notes completed_at].freeze
RECONCILIATION_TOPICS = %w[what_came_back stayed_at_destination never_used purchased incidents rejected_or_changed].freeze

def load_yaml(name)
  path = File.join(DATA_DIR, name)
  YAML.safe_load(File.read(path), permitted_classes: [Date], permitted_symbols: [], aliases: false)
rescue Psych::Exception => e
  abort "#{name}: invalid YAML: #{e.message}"
end

def scalar?(value)
  value.nil? || value.is_a?(String) || value.is_a?(Integer) ||
    value.is_a?(Float) || value == true || value == false
end

def optional_string?(value)
  value.nil? || (value.is_a?(String) && !value.strip.empty?)
end

def check_fields(value, required, allowed, label, errors)
  missing = required - value.keys
  extra = value.keys - allowed
  errors << "#{label}: missing #{missing.join(', ')}" unless missing.empty?
  errors << "#{label}: unknown fields #{extra.join(', ')}" unless extra.empty?
end

def date_value(value)
  return value if value.is_a?(Date)
  return nil unless value.is_a?(String)
  parsed = Date.iso8601(value)
  parsed.iso8601 == value ? parsed : nil
rescue Date::Error
  nil
end

def requirement_categories(values, label, errors)
  unless values.is_a?(Array)
    errors << "#{label}: must be a list"
    return []
  end
  categories = []
  values.each_with_index do |value, index|
    entry_label = "#{label} entry #{index + 1}"
    if value.is_a?(String)
      category = value
      count = 1
    elsif value.is_a?(Hash) && value.keys.sort == %w[category count]
      category = value["category"]
      count = value["count"]
    else
      errors << "#{entry_label}: must be a category string or category/count mapping"
      next
    end
    errors << "#{entry_label}: invalid category #{category.inspect}" unless category.is_a?(String) && category.match?(CATEGORY_PATTERN)
    errors << "#{entry_label}: count must be a positive integer" unless count.is_a?(Integer) && count.positive?
    errors << "#{label}: duplicate category #{category.inspect}" if categories.include?(category)
    categories << category if category.is_a?(String)
  end
  categories
end

def category_list(values, label, errors)
  unless values.is_a?(Array)
    errors << "#{label}: must be a list of categories"
    return []
  end
  values.each do |value|
    errors << "#{label}: invalid category #{value.inspect}" unless value.is_a?(String) && value.match?(CATEGORY_PATTERN)
  end
  errors << "#{label}: duplicate categories" unless values.uniq.length == values.length
  values
end

errors = []
schema = load_yaml("schema.yaml")
locations = load_yaml("locations.yaml")
inventory = load_yaml("clothes.yaml")
trip_store = load_yaml("trips.yaml")
template_store = load_yaml("activity_templates.yaml")
matcher_store = load_yaml("requirement_matchers.yaml")
packing_plan_store = load_yaml("packing_plans.yaml")
execution_store = load_yaml("trip_executions.yaml")

unless schema.is_a?(Hash) && schema["version"] == 8
  errors << "schema.yaml: version must be 8"
  schema = {} unless schema.is_a?(Hash)
end

unless inventory.is_a?(Hash)
  errors << "clothes.yaml: root must be a mapping"
  inventory = {}
end

required_sections = schema.dig("inventory", "required_sections") || []
missing_sections = required_sections - inventory.keys
extra_sections = inventory.keys - %w[definitions instances]
errors << "clothes.yaml: missing sections #{missing_sections.join(', ')}" unless missing_sections.empty?
errors << "clothes.yaml: unknown sections #{extra_sections.join(', ')}" unless extra_sections.empty?

unless locations.is_a?(Array)
  errors << "locations.yaml: root must be a list"
  locations = []
end

location_required = schema.dig("locations", "required_fields") || []
allowed_kinds = schema.dig("locations", "allowed_kinds") || []
required_by_kind = schema.dig("locations", "required_by_kind") || {}
location_ids = []
locations_by_id = {}

locations.each_with_index do |location, index|
  label = "locations.yaml entry #{index + 1}"
  unless location.is_a?(Hash)
    errors << "#{label}: must be a mapping"
    next
  end
  check_fields(location, location_required, LOCATION_FIELDS, label, errors)
  id = location["id"]
  errors << "#{label}: invalid id #{id.inspect}" unless id.is_a?(String) && id.match?(ID_PATTERN)
  location_ids << id if id.is_a?(String)
  locations_by_id[id] = location if id.is_a?(String)
  errors << "#{label}: name must be a non-empty string" unless location["name"].is_a?(String) && !location["name"].strip.empty?
  kind = location["kind"]
  errors << "#{label}: unknown kind #{kind.inspect}" unless allowed_kinds.include?(kind)
  aliases = location["aliases"]
  errors << "#{label}: aliases must be a list of non-empty strings" unless aliases.is_a?(Array) && aliases.all? { |value| value.is_a?(String) && !value.strip.empty? }
  errors << "#{label}: aliases contain duplicates" if aliases.is_a?(Array) && aliases.uniq.length != aliases.length
  %w[city region country notes].each do |field|
    errors << "#{label}: #{field} must be a non-empty string or null" unless optional_string?(location[field])
  end
  (required_by_kind[kind] || []).each do |field|
    errors << "#{label}: kind #{kind.inspect} requires #{field}" unless location[field].is_a?(String) && !location[field].strip.empty?
  end
end

location_ids.group_by(&:itself).each do |id, copies|
  errors << "locations.yaml: duplicate id #{id}" if copies.length > 1
end

definitions = inventory["definitions"]
instances = inventory["instances"]
unless definitions.is_a?(Array)
  errors << "clothes.yaml: definitions must be a list"
  definitions = []
end
unless instances.is_a?(Array)
  errors << "clothes.yaml: instances must be a list"
  instances = []
end

definition_required = schema.dig("definitions", "required_fields") || []
source_required = schema.dig("sources", "required_fields") || []
instance_required = schema.dig("instances", "required_fields") || []
movement_required = schema.dig("movements", "required_fields") || []
allowed_types = schema.dig("definitions", "allowed_types") || []
allowed_uses = schema.dig("definitions", "allowed_uses") || []
definition_ids = []
source_index = {}

definitions.each_with_index do |definition, index|
  label = "clothes.yaml definition #{index + 1}"
  unless definition.is_a?(Hash)
    errors << "#{label}: must be a mapping"
    next
  end
  check_fields(definition, definition_required, DEFINITION_FIELDS, label, errors)
  id = definition["id"]
  errors << "#{label}: invalid id #{id.inspect}" unless id.is_a?(String) && id.match?(ID_PATTERN)
  definition_ids << id if id.is_a?(String)
  errors << "#{label}: name must be a non-empty string" unless definition["name"].is_a?(String) && !definition["name"].strip.empty?
  errors << "#{label}: unknown type #{definition['type'].inspect}" unless allowed_types.include?(definition["type"])
  errors << "#{label}: notes must be a non-empty string or null" unless optional_string?(definition["notes"])

  attributes = definition["attributes"]
  if !attributes.is_a?(Hash)
    errors << "#{label}: attributes must be a mapping"
  else
    attributes.each do |key, value|
      errors << "#{label}: invalid attribute key #{key.inspect}" unless key.is_a?(String) && key.match?(KEY_PATTERN)
      errors << "#{label}: condition belongs on physical instances" if key == "condition"
      valid_value = scalar?(value) || (value.is_a?(Array) && value.all? { |entry| scalar?(entry) })
      errors << "#{label}: attribute #{key} must be a scalar or list of scalars" unless valid_value
    end
  end

  uses = definition["use"]
  if !uses.is_a?(Array) || !uses.all? { |use| use.is_a?(String) && !use.strip.empty? }
    errors << "#{label}: use must be a list of non-empty strings"
  else
    unknown_uses = uses - allowed_uses
    errors << "#{label}: unknown use values #{unknown_uses.join(', ')}" unless unknown_uses.empty?
    errors << "#{label}: duplicate use values" unless uses.uniq.length == uses.length
  end

  sources = definition["sources"]
  unless sources.is_a?(Array)
    errors << "#{label}: sources must be a list"
    next
  end
  sources.each_with_index do |source, source_index_number|
    source_label = "#{label} source #{source_index_number + 1}"
    unless source.is_a?(Hash)
      errors << "#{source_label}: must be a mapping"
      next
    end
    check_fields(source, source_required, SOURCE_FIELDS, source_label, errors)
    source_id = source["id"]
    errors << "#{source_label}: invalid id #{source_id.inspect}" unless source_id.is_a?(String) && source_id.match?(ID_PATTERN)
    errors << "clothes.yaml: duplicate source id #{source_id}" if source_index.key?(source_id)
    errors << "#{source_label}: unknown original location #{source['original_location'].inspect}" unless location_ids.include?(source["original_location"])
    quantity = source["original_quantity"]
    errors << "#{source_label}: original_quantity must be a positive integer" unless quantity.is_a?(Integer) && quantity.positive?
    errors << "#{source_label}: original_condition must be a non-empty string or null" unless optional_string?(source["original_condition"])
    errors << "#{source_label}: notes must be a non-empty string or null" unless optional_string?(source["notes"])
    source_index[source_id] = [id, quantity, source["original_location"]] if source_id.is_a?(String)
  end
end

definition_ids.group_by(&:itself).each do |id, copies|
  errors << "clothes.yaml: duplicate definition id #{id}" if copies.length > 1
end

instance_ids = []
instances_by_id = {}
source_counts = Hash.new(0)
instances.each_with_index do |instance, index|
  label = "clothes.yaml instance #{index + 1}"
  unless instance.is_a?(Hash)
    errors << "#{label}: must be a mapping"
    next
  end
  check_fields(instance, instance_required, INSTANCE_FIELDS, label, errors)
  id = instance["id"]
  errors << "#{label}: invalid id #{id.inspect}" unless id.is_a?(String) && id.match?(ID_PATTERN)
  instance_ids << id if id.is_a?(String)
  instances_by_id[id] = instance if id.is_a?(String)
  definition_id = instance["definition"]
  errors << "#{label}: unknown definition #{definition_id.inspect}" unless definition_ids.include?(definition_id)
  source_id = instance["source"]
  if source_id
    source = source_index[source_id]
    errors << "#{label}: unknown source #{source_id.inspect}" unless source
    errors << "#{label}: source #{source_id.inspect} belongs to #{source[0].inspect}" if source && source[0] != definition_id
    source_counts[source_id] += 1
  end
  %w[current_location preferred_location].each do |field|
    errors << "#{label}: unknown #{field} #{instance[field].inspect}" unless location_ids.include?(instance[field])
  end
  %w[condition status notes].each do |field|
    errors << "#{label}: #{field} must be a non-empty string or null" unless optional_string?(instance[field])
  end
  movements = instance["movements"]
  unless movements.is_a?(Array)
    errors << "#{label}: movements must be a list"
    next
  end
  previous_destination = source_index[source_id]&.fetch(2, nil)
  previous_timestamp = nil
  movements.each_with_index do |movement, movement_index|
    movement_label = "#{label} movement #{movement_index + 1}"
    unless movement.is_a?(Hash)
      errors << "#{movement_label}: must be a mapping"
      next
    end
    check_fields(movement, movement_required, MOVEMENT_FIELDS, movement_label, errors)
    timestamp = begin
      raw_timestamp = movement["timestamp"]
      raise ArgumentError unless raw_timestamp.is_a?(String) && raw_timestamp.match?(/(?:Z|[+-]\d{2}:\d{2})\z/)
      Time.iso8601(raw_timestamp)
    rescue ArgumentError, TypeError
      errors << "#{movement_label}: timestamp must be ISO 8601 with a timezone"
      nil
    end
    movement_source = movement["source"]
    destination = movement["destination"]
    errors << "#{movement_label}: unknown source #{movement_source.inspect}" unless location_ids.include?(movement_source)
    errors << "#{movement_label}: unknown destination #{destination.inspect}" unless location_ids.include?(destination)
    errors << "#{movement_label}: source and destination must differ" if movement_source == destination && location_ids.include?(movement_source)
    if previous_destination && movement_source != previous_destination
      errors << "#{movement_label}: source #{movement_source.inspect} does not continue from #{previous_destination.inspect}"
    end
    errors << "#{movement_label}: timestamp is earlier than the preceding movement" if timestamp && previous_timestamp && timestamp < previous_timestamp
    errors << "#{movement_label}: reason must be a non-empty string or null" unless optional_string?(movement["reason"])
    previous_destination = destination if location_ids.include?(destination)
    previous_timestamp = timestamp if timestamp
  end
  if !movements.empty? && previous_destination != instance["current_location"]
    errors << "#{label}: current location does not match movement history"
  elsif movements.empty? && source_index[source_id] && instance["current_location"] != source_index[source_id][2]
    errors << "#{label}: current location differs from original location without movement history"
  end
end

instance_ids.group_by(&:itself).each do |id, copies|
  errors << "clothes.yaml: duplicate physical item id #{id}" if copies.length > 1
end

source_index.each do |source_id, (_definition_id, quantity, _original_location)|
  errors << "source #{source_id.inspect}: expected #{quantity} physical instances, found #{source_counts[source_id]}" if quantity != source_counts[source_id]
end

unless template_store.is_a?(Hash) && template_store.keys.sort == ["templates"] && template_store["templates"].is_a?(Array)
  errors << "activity_templates.yaml: root must contain only a templates list"
  template_store = {"templates" => []}
end

template_required = schema.dig("requirement_templates", "required_fields") || []
template_ids = []
template_categories = []
template_store["templates"].each_with_index do |template, template_index|
  label = "activity_templates.yaml template #{template_index + 1}"
  unless template.is_a?(Hash)
    errors << "#{label}: must be a mapping"
    next
  end
  check_fields(template, template_required, TEMPLATE_FIELDS, label, errors)
  template_id = template["id"]
  errors << "#{label}: invalid id #{template_id.inspect}" unless template_id.is_a?(String) && template_id.match?(ID_PATTERN)
  template_ids << template_id if template_id.is_a?(String)
  errors << "#{label}: name must be a non-empty string" unless template["name"].is_a?(String) && !template["name"].strip.empty?
  required_categories = requirement_categories(template["required"], "#{label}.required", errors)
  optional_categories = requirement_categories(template["optional"], "#{label}.optional", errors)
  template_categories.concat(required_categories + optional_categories)
  overlap = required_categories & optional_categories
  errors << "#{label}: categories cannot be both required and optional: #{overlap.join(', ')}" unless overlap.empty?
  errors << "#{label}: notes must be a non-empty string or null" unless optional_string?(template["notes"])
end

template_ids.group_by(&:itself).each do |id, copies|
  errors << "activity_templates.yaml: duplicate template id #{id}" if copies.length > 1
end

unless matcher_store.is_a?(Hash) && matcher_store.keys.sort == ["matchers"] && matcher_store["matchers"].is_a?(Array)
  errors << "requirement_matchers.yaml: root must contain only a matchers list"
  matcher_store = {"matchers" => []}
end
matcher_required = schema.dig("requirement_matchers", "required_fields") || []
matcher_clause_required = schema.dig("requirement_matcher_clauses", "required_fields") || []
matcher_categories = []
matcher_store["matchers"].each_with_index do |matcher, matcher_index|
  label = "requirement_matchers.yaml entry #{matcher_index + 1}"
  unless matcher.is_a?(Hash)
    errors << "#{label}: must be a mapping"
    next
  end
  check_fields(matcher, matcher_required, MATCHER_FIELDS, label, errors)
  category = matcher["category"]
  errors << "#{label}: invalid category #{category.inspect}" unless category.is_a?(String) && category.match?(CATEGORY_PATTERN)
  matcher_categories << category if category.is_a?(String)
  clauses = matcher["any"]
  unless clauses.is_a?(Array)
    errors << "#{label}: any must be a list"
    clauses = []
  end
  clauses.each_with_index do |clause, clause_index|
    clause_label = "#{label}.any entry #{clause_index + 1}"
    unless clause.is_a?(Hash)
      errors << "#{clause_label}: must be a mapping"
      next
    end
    check_fields(clause, matcher_clause_required, MATCHER_CLAUSE_FIELDS, clause_label, errors)
    %w[types tags].each do |field|
      values = clause[field]
      errors << "#{clause_label}: #{field} must be a list of non-empty strings" unless values.is_a?(Array) && values.all? { |value| value.is_a?(String) && !value.empty? }
      errors << "#{clause_label}: #{field} contains duplicates" if values.is_a?(Array) && values.uniq.length != values.length
    end
    attributes = clause["attributes"]
    unless attributes.is_a?(Hash) && attributes.all? { |key, value| key.is_a?(String) && key.match?(KEY_PATTERN) && scalar?(value) }
      errors << "#{clause_label}: attributes must map valid keys to scalar values"
    end
  end
end
matcher_categories.group_by(&:itself).each do |category, copies|
  errors << "requirement_matchers.yaml: duplicate category #{category}" if copies.length > 1
end
missing_matchers = template_categories.uniq - matcher_categories
errors << "requirement_matchers.yaml: missing template categories #{missing_matchers.join(', ')}" unless missing_matchers.empty?

unless trip_store.is_a?(Hash) && trip_store.keys.sort == ["trips"] && trip_store["trips"].is_a?(Array)
  errors << "trips.yaml: root must contain only a trips list"
  trip_store = {"trips" => []}
end

trip_required = schema.dig("trips", "required_fields") || []
place_required = schema.dig("trip_places", "required_fields") || []
leg_required = schema.dig("trip_legs", "required_fields") || []
attachment_required = schema.dig("trip_attachments", "required_fields") || []
override_required = schema.dig("requirement_overrides", "required_fields") || []
planning_required = schema.dig("trip_planning", "required_fields") || []
leg_planning_required = schema.dig("trip_leg_planning", "required_fields") || []
weather_required = schema.dig("trip_weather", "required_fields") || []
allowed_statuses = schema.dig("trips", "allowed_statuses") || []
allowed_attachment_kinds = schema.dig("trip_attachments", "allowed_kinds") || []
trip_ids = []
trip_references = {}

trip_store["trips"].each_with_index do |trip, trip_index|
  label = "trips.yaml trip #{trip_index + 1}"
  unless trip.is_a?(Hash)
    errors << "#{label}: must be a mapping"
    next
  end
  check_fields(trip, trip_required, TRIP_FIELDS, label, errors)
  trip_id = trip["id"]
  errors << "#{label}: invalid id #{trip_id.inspect}" unless trip_id.is_a?(String) && trip_id.match?(ID_PATTERN)
  trip_ids << trip_id if trip_id.is_a?(String)
  errors << "#{label}: name must be a non-empty string" unless trip["name"].is_a?(String) && !trip["name"].strip.empty?
  errors << "#{label}: unknown status #{trip['status'].inspect}" unless allowed_statuses.include?(trip["status"])
  errors << "#{label}: notes must be a non-empty string or null" unless optional_string?(trip["notes"])
  start_date = date_value(trip["start_date"])
  end_date = date_value(trip["end_date"])
  errors << "#{label}: start_date must use YYYY-MM-DD" unless start_date
  errors << "#{label}: end_date must use YYYY-MM-DD" unless end_date
  errors << "#{label}: start_date must not be after end_date" if start_date && end_date && start_date > end_date

  places = trip["places"]
  unless places.is_a?(Array)
    errors << "#{label}: places must be a list"
    places = []
  end
  place_ids = []
  places.each_with_index do |place, place_index|
    place_label = "#{label} place #{place_index + 1}"
    unless place.is_a?(Hash)
      errors << "#{place_label}: must be a mapping"
      next
    end
    check_fields(place, place_required, PLACE_FIELDS, place_label, errors)
    place_id = place["id"]
    errors << "#{place_label}: invalid id #{place_id.inspect}" unless place_id.is_a?(String) && place_id.match?(ID_PATTERN)
    errors << "#{place_label}: id conflicts with an inventory location" if location_ids.include?(place_id)
    place_ids << place_id if place_id.is_a?(String)
    errors << "#{place_label}: name must be a non-empty string" unless place["name"].is_a?(String) && !place["name"].strip.empty?
    errors << "#{place_label}: notes must be a non-empty string or null" unless optional_string?(place["notes"])
  end
  place_ids.group_by(&:itself).each { |id, copies| errors << "#{label}: duplicate place id #{id}" if copies.length > 1 }
  known_locations = location_ids + place_ids

  legs = trip["legs"]
  unless legs.is_a?(Array) && !legs.empty?
    errors << "#{label}: legs must be a non-empty list"
    legs = []
  end
  leg_ids = []
  parsed_legs = []
  legs.each_with_index do |leg, leg_index|
    leg_label = "#{label} leg #{leg_index + 1}"
    unless leg.is_a?(Hash)
      errors << "#{leg_label}: must be a mapping"
      next
    end
    check_fields(leg, leg_required, LEG_FIELDS, leg_label, errors)
    leg_id = leg["id"]
    errors << "#{leg_label}: invalid id #{leg_id.inspect}" unless leg_id.is_a?(String) && leg_id.match?(ID_PATTERN)
    leg_ids << leg_id if leg_id.is_a?(String)
    origin = leg["origin"]
    destination = leg["destination"]
    errors << "#{leg_label}: unknown origin #{origin.inspect}" unless known_locations.include?(origin)
    errors << "#{leg_label}: unknown destination #{destination.inspect}" unless known_locations.include?(destination)
    errors << "#{leg_label}: origin and destination must differ" if origin == destination && known_locations.include?(origin)
    departure = date_value(leg["departure"])
    arrival = date_value(leg["arrival"])
    errors << "#{leg_label}: departure must use YYYY-MM-DD" unless departure
    errors << "#{leg_label}: arrival must use YYYY-MM-DD" unless arrival
    errors << "#{leg_label}: departure must not be after arrival" if departure && arrival && departure > arrival
    errors << "#{leg_label}: departure is before trip start_date" if start_date && departure && departure < start_date
    errors << "#{leg_label}: arrival is after trip end_date" if end_date && arrival && arrival > end_date
    errors << "#{leg_label}: transport_notes must be a non-empty string or null" unless optional_string?(leg["transport_notes"])
    parsed_legs << [leg_id, origin, destination, departure, arrival] if departure && arrival
  end
  leg_ids.group_by(&:itself).each { |id, copies| errors << "#{label}: duplicate leg id #{id}" if copies.length > 1 }
  parsed_legs.each_cons(2) do |previous, following|
    errors << "#{label}: leg #{following[0]} departs before #{previous[0]} arrives" if following[3] < previous[4]
    errors << "#{label}: leg #{following[0]} does not continue from #{previous[2]}" if following[1] != previous[2]
  end

  luggage = trip["luggage"]
  if !luggage.is_a?(Array) || !luggage.all? { |id| id.is_a?(String) }
    errors << "#{label}: luggage must be a list of location IDs"
    luggage = []
  end
  errors << "#{label}: luggage contains duplicates" if luggage.uniq.length != luggage.length
  luggage.each do |luggage_id|
    location = locations_by_id[luggage_id]
    errors << "#{label}: luggage #{luggage_id.inspect} is not a travel container" unless location && location["kind"] == "travel_container"
  end

  attachments = trip["attachments"]
  unless attachments.is_a?(Array)
    errors << "#{label}: attachments must be a list"
    attachments = []
  end
  attachment_ids = []
  leg_dates = {}
  parsed_legs.each { |leg| leg_dates[leg[0]] = [leg[3], leg[4]] }
  attachments.each_with_index do |attachment, attachment_index|
    attachment_label = "#{label} attachment #{attachment_index + 1}"
    unless attachment.is_a?(Hash)
      errors << "#{attachment_label}: must be a mapping"
      next
    end
    check_fields(attachment, attachment_required, ATTACHMENT_FIELDS, attachment_label, errors)
    attachment_id = attachment["id"]
    errors << "#{attachment_label}: invalid id #{attachment_id.inspect}" unless attachment_id.is_a?(String) && attachment_id.match?(ID_PATTERN)
    attachment_ids << attachment_id if attachment_id.is_a?(String)
    errors << "#{attachment_label}: unknown kind #{attachment['kind'].inspect}" unless allowed_attachment_kinds.include?(attachment["kind"])
    errors << "#{attachment_label}: unknown type #{attachment['type'].inspect}" unless template_ids.include?(attachment["type"])
    errors << "#{attachment_label}: name must be a non-empty string" unless attachment["name"].is_a?(String) && !attachment["name"].strip.empty?
    leg_id = attachment["leg"]
    errors << "#{attachment_label}: unknown leg #{leg_id.inspect}" if leg_id && !leg_ids.include?(leg_id)
    location_id = attachment["location"]
    errors << "#{attachment_label}: unknown location #{location_id.inspect}" if location_id && !known_locations.include?(location_id)
    attachment_date = attachment["date"].nil? ? nil : date_value(attachment["date"])
    errors << "#{attachment_label}: date must use YYYY-MM-DD or null" if !attachment["date"].nil? && !attachment_date
    errors << "#{attachment_label}: must attach to a leg, location, date, or a combination" if leg_id.nil? && location_id.nil? && attachment_date.nil?
    errors << "#{attachment_label}: date is outside trip dates" if attachment_date && start_date && end_date && !(start_date..end_date).cover?(attachment_date)
    if attachment_date && leg_dates[leg_id] && !(leg_dates[leg_id][0]..leg_dates[leg_id][1]).cover?(attachment_date)
      errors << "#{attachment_label}: date is outside its attached leg dates"
    end
    frequency = attachment["frequency"]
    errors << "#{attachment_label}: frequency must be a positive integer" unless frequency.is_a?(Integer) && frequency.positive?
    overrides = attachment["requirements"]
    if !overrides.is_a?(Hash)
      errors << "#{attachment_label}: requirements must be a mapping"
    else
      check_fields(overrides, override_required, OVERRIDE_FIELDS, "#{attachment_label}.requirements", errors)
      requirement_categories(overrides["add"], "#{attachment_label}.requirements.add", errors)
      requirement_categories(overrides["add_optional"], "#{attachment_label}.requirements.add_optional", errors)
      category_list(overrides["remove"], "#{attachment_label}.requirements.remove", errors)
      category_list(overrides["remove_optional"], "#{attachment_label}.requirements.remove_optional", errors)
    end
    errors << "#{attachment_label}: notes must be a non-empty string or null" unless optional_string?(attachment["notes"])
  end
  attachment_ids.group_by(&:itself).each { |id, copies| errors << "#{label}: duplicate attachment id #{id}" if copies.length > 1 }

  planning = trip["planning"]
  if !planning.is_a?(Hash)
    errors << "#{label}: planning must be a mapping"
    planning = {}
  else
    check_fields(planning, planning_required, PLANNING_FIELDS, "#{label}.planning", errors)
    errors << "#{label}.planning: notes must be a non-empty string or null" unless optional_string?(planning["notes"])
  end
  planning_legs = planning["legs"]
  unless planning_legs.is_a?(Array)
    errors << "#{label}.planning: legs must be a list"
    planning_legs = []
  end
  planned_leg_ids = []
  planning_legs.each_with_index do |entry, planning_index|
    planning_label = "#{label}.planning leg #{planning_index + 1}"
    unless entry.is_a?(Hash)
      errors << "#{planning_label}: must be a mapping"
      next
    end
    check_fields(entry, leg_planning_required, LEG_PLANNING_FIELDS, planning_label, errors)
    planned_leg_id = entry["leg"]
    errors << "#{planning_label}: unknown leg #{planned_leg_id.inspect}" unless leg_ids.include?(planned_leg_id)
    planned_leg_ids << planned_leg_id if planned_leg_id.is_a?(String)
    %w[activities_complete laundry_available].each do |field|
      answer = entry[field]
      errors << "#{planning_label}: #{field} must be true, false, or null" unless answer.nil? || answer == true || answer == false
    end
    weather = entry["weather"]
    if !weather.is_a?(Hash)
      errors << "#{planning_label}: weather must be a mapping"
    else
      check_fields(weather, weather_required, WEATHER_FIELDS, "#{planning_label}.weather", errors)
      %w[summary precipitation source].each do |field|
        errors << "#{planning_label}.weather: #{field} must be a non-empty string or null" unless optional_string?(weather[field])
      end
      %w[low_c high_c].each do |field|
        value = weather[field]
        errors << "#{planning_label}.weather: #{field} must be a number or null" unless value.nil? || value.is_a?(Numeric)
      end
      if weather["low_c"].is_a?(Numeric) && weather["high_c"].is_a?(Numeric) && weather["low_c"] > weather["high_c"]
        errors << "#{planning_label}.weather: low_c must not exceed high_c"
      end
      checked_at = weather["checked_at"]
      if checked_at
        begin
          parsed = Time.iso8601(checked_at)
          errors << "#{planning_label}.weather: checked_at must include a timezone" unless parsed.utc_offset
        rescue ArgumentError, TypeError
          errors << "#{planning_label}.weather: checked_at must be a timezone-aware ISO 8601 timestamp or null"
        end
      end
    end
    context = entry["context"]
    unless context.is_a?(Array) && context.all? { |note| note.is_a?(String) && !note.strip.empty? }
      errors << "#{planning_label}: context must be a list of non-empty strings"
      context = []
    end
    errors << "#{planning_label}: context contains duplicates" unless context.uniq.length == context.length
    errors << "#{planning_label}: notes must be a non-empty string or null" unless optional_string?(entry["notes"])
  end
  planned_leg_ids.group_by(&:itself).each do |id, copies|
    errors << "#{label}.planning: duplicate leg #{id}" if copies.length > 1
  end
  missing_planning_legs = leg_ids - planned_leg_ids
  errors << "#{label}.planning: missing legs #{missing_planning_legs.join(', ')}" unless missing_planning_legs.empty?
  trip_references[trip_id] = {
    "legs" => leg_ids,
    "locations" => known_locations,
    "luggage" => luggage,
  } if trip_id.is_a?(String)
end

trip_ids.group_by(&:itself).each do |id, copies|
  errors << "trips.yaml: duplicate trip id #{id}" if copies.length > 1
end

unless packing_plan_store.is_a?(Hash) && packing_plan_store.keys.sort == ["plans"] && packing_plan_store["plans"].is_a?(Array)
  errors << "packing_plans.yaml: root must contain only a plans list"
  packing_plan_store = {"plans" => []}
end
packing_plan_required = schema.dig("packing_plans", "required_fields") || []
packing_entry_required = schema.dig("packing_plan_entries", "required_fields") || []
packing_statuses = schema.dig("packing_plans", "allowed_statuses") || []
packing_plan_ids = []
packing_plan_references = {}
packing_plan_store["plans"].each_with_index do |plan, plan_index|
  label = "packing_plans.yaml plan #{plan_index + 1}"
  unless plan.is_a?(Hash)
    errors << "#{label}: must be a mapping"
    next
  end
  check_fields(plan, packing_plan_required, PACKING_PLAN_FIELDS, label, errors)
  plan_id = plan["id"]
  errors << "#{label}: invalid id #{plan_id.inspect}" unless plan_id.is_a?(String) && plan_id.match?(ID_PATTERN)
  packing_plan_ids << plan_id if plan_id.is_a?(String)
  trip_id = plan["trip"]
  trip_reference = trip_references[trip_id]
  errors << "#{label}: unknown trip #{trip_id.inspect}" unless trip_reference
  errors << "#{label}: unknown status #{plan['status'].inspect}" unless packing_statuses.include?(plan["status"])
  created_at = plan["created_at"]
  begin
    Time.iso8601(created_at)
    errors << "#{label}: created_at must include a timezone" unless created_at.match?(/(?:Z|[+-]\d{2}:\d{2})\z/)
  rescue ArgumentError, TypeError
    errors << "#{label}: created_at must be a timezone-aware ISO 8601 timestamp"
  end
  errors << "#{label}: notes must be a non-empty string or null" unless optional_string?(plan["notes"])
  sections = plan["sections"]
  if !sections.is_a?(Hash)
    errors << "#{label}: sections must be a mapping"
    sections = {}
  else
    missing_sections = PACKING_SECTIONS - sections.keys
    extra_sections = sections.keys - PACKING_SECTIONS
    errors << "#{label}: sections missing #{missing_sections.join(', ')}" unless missing_sections.empty?
    errors << "#{label}: sections unknown #{extra_sections.join(', ')}" unless extra_sections.empty?
  end
  PACKING_SECTIONS.each do |section|
    entries = sections[section]
    unless entries.is_a?(Array)
      errors << "#{label}.#{section}: must be a list"
      entries = []
    end
    entries.each_with_index do |entry, entry_index|
      entry_label = "#{label}.#{section} entry #{entry_index + 1}"
      unless entry.is_a?(Hash)
        errors << "#{entry_label}: must be a mapping"
        next
      end
      check_fields(entry, packing_entry_required, PACKING_ENTRY_FIELDS, entry_label, errors)
      item = entry["item"]
      requirement = entry["requirement"]
      errors << "#{entry_label}: unknown item #{item.inspect}" if item && !instance_ids.include?(item)
      errors << "#{entry_label}: invalid requirement #{requirement.inspect}" if requirement && !(requirement.is_a?(String) && requirement.match?(CATEGORY_PATTERN))
      errors << "#{entry_label}: must identify an item or requirement" if item.nil? && requirement.nil?
      if section == "missing"
        errors << "#{entry_label}: missing entries require a category and no item" unless item.nil? && requirement
      elsif item.nil?
        errors << "#{entry_label}: requires a physical item"
      end
      leg_id = entry["leg"]
      errors << "#{entry_label}: unknown leg #{leg_id.inspect}" if leg_id && (!trip_reference || !trip_reference["legs"].include?(leg_id))
      container = entry["container"]
      if container
        location = locations_by_id[container]
        errors << "#{entry_label}: invalid container #{container.inspect}" unless location && location["kind"] == "travel_container"
        errors << "#{entry_label}: container is not available for trip" if trip_reference && !trip_reference["luggage"].include?(container)
      end
      %w[source destination].each do |field|
        value = entry[field]
        errors << "#{entry_label}: unknown #{field} #{value.inspect}" if value && (!trip_reference || !trip_reference["locations"].include?(value))
      end
      if section == "move_between_legs" && (entry["source"].nil? || entry["destination"].nil?)
        errors << "#{entry_label}: move_between_legs requires source and destination"
      end
      errors << "#{entry_label}: leave_at_destination requires destination" if section == "leave_at_destination" && entry["destination"].nil?
      errors << "#{entry_label}: pack requires container" if section == "pack" && entry["container"].nil?
      errors << "#{entry_label}: wear_in_transit must not specify container" if section == "wear_in_transit" && entry["container"]
      if section == "already_there"
        errors << "#{entry_label}: already_there requires destination" if entry["destination"].nil?
        current = instances_by_id.dig(item, "current_location")
        if current && entry["destination"] && current != entry["destination"]
          errors << "#{entry_label}: item is currently at #{current.inspect}, not #{entry['destination'].inspect}"
        end
      end
      quantity = entry["quantity"]
      errors << "#{entry_label}: quantity must be a positive integer" unless quantity.is_a?(Integer) && quantity.positive?
      errors << "#{entry_label}: reason must be a non-empty string" unless entry["reason"].is_a?(String) && !entry["reason"].strip.empty?
    end
  end
  section_items = {}
  PACKING_SECTIONS.each do |section|
    section_items[section] = (sections[section].is_a?(Array) ? sections[section] : []).each_with_object([]) do |entry, values|
      values << entry["item"] if entry.is_a?(Hash) && entry["item"]
    end
  end
  conflicts = (section_items["pack"] + section_items["wear_in_transit"]) & section_items["do_not_pack"]
  errors << "#{label}: items cannot be both brought and do_not_pack: #{conflicts.join(', ')}" unless conflicts.empty?
  conflicts = section_items["pack"] & section_items["already_there"]
  errors << "#{label}: items cannot be both pack and already_there: #{conflicts.join(', ')}" unless conflicts.empty?
  packing_plan_references[plan_id] = {
    "trip" => trip_id,
    "status" => plan["status"],
    "sections" => sections,
  } if plan_id.is_a?(String)
end
packing_plan_ids.group_by(&:itself).each do |id, copies|
  errors << "packing_plans.yaml: duplicate plan id #{id}" if copies.length > 1
end

unless execution_store.is_a?(Hash) && execution_store.keys.sort == ["executions"] && execution_store["executions"].is_a?(Array)
  errors << "trip_executions.yaml: root must contain only an executions list"
  execution_store = {"executions" => []}
end
execution_required = schema.dig("trip_executions", "required_fields") || []
execution_action_required = schema.dig("trip_execution_actions", "required_fields") || []
execution_state_required = schema.dig("trip_execution_action_states", "required_fields") || []
reconciliation_required = schema.dig("trip_reconciliation", "required_fields") || []
execution_statuses = schema.dig("trip_executions", "allowed_statuses") || []
execution_kinds = schema.dig("trip_execution_actions", "allowed_kinds") || []
execution_state_statuses = schema.dig("trip_execution_action_states", "allowed_statuses") || []
execution_ids = []
executed_trips = []
execution_store["executions"].each_with_index do |execution, execution_index|
  label = "trip_executions.yaml execution #{execution_index + 1}"
  unless execution.is_a?(Hash)
    errors << "#{label}: must be a mapping"
    next
  end
  check_fields(execution, execution_required, EXECUTION_FIELDS, label, errors)
  execution_id = execution["id"]
  errors << "#{label}: invalid id #{execution_id.inspect}" unless execution_id.is_a?(String) && execution_id.match?(ID_PATTERN)
  execution_ids << execution_id if execution_id.is_a?(String)
  trip_id = execution["trip"]
  trip_reference = trip_references[trip_id]
  errors << "#{label}: unknown trip #{trip_id.inspect}" unless trip_reference
  executed_trips << trip_id if trip_id.is_a?(String)
  plan_reference = packing_plan_references[execution["packing_plan"]]
  unless plan_reference && plan_reference["trip"] == trip_id && plan_reference["status"] == "confirmed"
    errors << "#{label}: packing plan must be confirmed and belong to its trip"
  end
  status = execution["status"]
  errors << "#{label}: unknown status #{status.inspect}" unless execution_statuses.include?(status)
  created_at = execution["created_at"]
  begin
    Time.iso8601(created_at)
    errors << "#{label}: created_at must be normalized UTC" unless created_at.match?(/Z\z/)
  rescue ArgumentError, TypeError
    errors << "#{label}: created_at must be a timezone-aware ISO 8601 timestamp"
  end
  errors << "#{label}: notes must be a non-empty string or null" unless optional_string?(execution["notes"])
  actions = execution["actions"]
  unless actions.is_a?(Array)
    errors << "#{label}: actions must be a list"
    actions = []
  end
  action_ids = []
  actions.each_with_index do |action, action_index|
    action_label = "#{label} action #{action_index + 1}"
    unless action.is_a?(Hash)
      errors << "#{action_label}: must be a mapping"
      next
    end
    check_fields(action, execution_action_required, EXECUTION_ACTION_FIELDS, action_label, errors)
    action_id = action["id"]
    errors << "#{action_label}: invalid id #{action_id.inspect}" unless action_id.is_a?(String) && action_id.match?(ID_PATTERN)
    action_ids << action_id if action_id.is_a?(String)
    kind = action["kind"]
    errors << "#{action_label}: unknown kind #{kind.inspect}" unless execution_kinds.include?(kind)
    item = action["item"]
    errors << "#{action_label}: unknown item #{item.inspect}" if item && !instance_ids.include?(item) && kind != "purchased"
    errors << "#{action_label}: description must be a non-empty string or null" unless optional_string?(action["description"])
    errors << "#{action_label}: must identify an item or description" if item.nil? && action["description"].nil?
    decision = action["decision"]
    if decision
      match = /\A([a-z_]+):([1-9][0-9]*)\z/.match(decision)
      if !match || !PACKING_SECTIONS.include?(match[1]) || !plan_reference || !plan_reference["sections"][match[1]].is_a?(Array) || match[2].to_i > plan_reference["sections"][match[1]].length
        errors << "#{action_label}: invalid packing decision #{decision.inspect}"
      elsif plan_reference["sections"][match[1]][match[2].to_i - 1]["item"] != item
        errors << "#{action_label}: item does not match packing decision"
      end
    end
    leg = action["leg"]
    errors << "#{action_label}: unknown leg #{leg.inspect}" if leg && (!trip_reference || !trip_reference["legs"].include?(leg))
    source = action["source"]
    destination = action["destination"]
    errors << "#{action_label}: source and destination must both be set or null" if source.nil? != destination.nil?
    errors << "#{action_label}: unknown source #{source.inspect}" if source && !location_ids.include?(source)
    errors << "#{action_label}: unknown destination #{destination.inspect}" if destination && !location_ids.include?(destination)
    errors << "#{action_label}: movement is not valid for kind #{kind.inspect}" if source && !%w[packed transferred returned stayed].include?(kind)
    errors << "#{action_label}: #{kind} requires a movement" if %w[transferred returned].include?(kind) && source.nil?
    errors << "#{action_label}: reason must be a non-empty string" unless action["reason"].is_a?(String) && !action["reason"].strip.empty?
    states = action["states"]
    unless states.is_a?(Array) && !states.empty?
      errors << "#{action_label}: states must be a non-empty list"
      states = []
    end
    state_status_values = []
    states.each_with_index do |state, state_index|
      state_label = "#{action_label} state #{state_index + 1}"
      unless state.is_a?(Hash)
        errors << "#{state_label}: must be a mapping"
        next
      end
      check_fields(state, execution_state_required, EXECUTION_STATE_FIELDS, state_label, errors)
      state_status_values << state["status"]
      errors << "#{state_label}: unknown status #{state['status'].inspect}" unless execution_state_statuses.include?(state["status"])
      begin
        Time.iso8601(state["timestamp"])
        errors << "#{state_label}: timestamp must be normalized UTC" unless state["timestamp"].match?(/Z\z/)
      rescue ArgumentError, TypeError
        errors << "#{state_label}: invalid timestamp"
      end
      errors << "#{state_label}: notes must be a non-empty string or null" unless optional_string?(state["notes"])
    end
    errors << "#{action_label}: states must begin with confirmed" unless state_status_values.first == "confirmed"
    unless state_status_values.length <= 2 && (state_status_values.length == 1 || %w[applied failed].include?(state_status_values[1]))
      errors << "#{action_label}: states must be confirmed then applied or failed"
    end
  end
  action_ids.group_by(&:itself).each { |id, copies| errors << "#{label}: duplicate action id #{id}" if copies.length > 1 }
  reconciliation = execution["reconciliation"]
  if !reconciliation.is_a?(Hash)
    errors << "#{label}: reconciliation must be a mapping"
    reconciliation = {}
  else
    check_fields(reconciliation, reconciliation_required, RECONCILIATION_FIELDS, "#{label}.reconciliation", errors)
  end
  RECONCILIATION_TOPICS.each do |topic|
    errors << "#{label}.reconciliation: #{topic} must be boolean" unless reconciliation[topic] == true || reconciliation[topic] == false
  end
  errors << "#{label}.reconciliation: notes must be a non-empty string or null" unless optional_string?(reconciliation["notes"])
  if reconciliation["completed_at"]
    begin
      Time.iso8601(reconciliation["completed_at"])
      errors << "#{label}.reconciliation: completed_at must be normalized UTC" unless reconciliation["completed_at"].match?(/Z\z/)
    rescue ArgumentError, TypeError
      errors << "#{label}.reconciliation: invalid completed_at"
    end
  end
  if status == "completed" && (!RECONCILIATION_TOPICS.all? { |topic| reconciliation[topic] } || reconciliation["completed_at"].nil?)
    errors << "#{label}: completed execution requires finished reconciliation"
  end
end
execution_ids.group_by(&:itself).each { |id, copies| errors << "trip_executions.yaml: duplicate id #{id}" if copies.length > 1 }
executed_trips.group_by(&:itself).each { |id, copies| errors << "trip #{id}: more than one execution" if copies.length > 1 }

if errors.empty?
  puts "Valid inventory: #{definitions.length} definitions, #{instances.length} physical items, #{locations.length} locations, #{trip_store['trips'].length} trips, #{template_store['templates'].length} activity templates, #{matcher_store['matchers'].length} requirement matchers, #{packing_plan_store['plans'].length} packing plans, #{execution_store['executions'].length} trip executions."
else
  warn errors.join("\n")
  exit 1
end
