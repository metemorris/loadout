#!/usr/bin/env ruby

# One-time, deterministic migration from quantity-based clothes.yaml records to
# shared definitions and individually trackable physical instances.

require "yaml"
require "optparse"
require "tempfile"

ROOT = File.expand_path("..", __dir__)
options = {path: File.join(ROOT, "data", "clothes.yaml"), dry_run: false, confirm: false}
OptionParser.new do |parser|
  parser.banner = "Usage: #{File.basename($PROGRAM_NAME)} [options]"
  parser.on("--path PATH", "version-1 clothes.yaml to migrate") { |value| options[:path] = File.expand_path(value) }
  parser.on("--dry-run", "validate and report without writing") { options[:dry_run] = true }
  parser.on("--confirm", "confirm the migration write") { options[:confirm] = true }
end.parse!
abort "--confirm is required unless --dry-run is used" unless options[:dry_run] || options[:confirm]

path = options.fetch(:path)

raw = YAML.safe_load(
  File.read(path),
  permitted_classes: [],
  permitted_symbols: [],
  aliases: false
)

abort "data/clothes.yaml is not a version-1 list" unless raw.is_a?(Array)

def definition_key(record)
  attributes = record.fetch("attributes").reject { |key, _value| key == "condition" }
  [record.fetch("name"), record.fetch("type"), attributes, record.fetch("use").sort]
end

groups = {}
raw.each do |record|
  key = definition_key(record)
  groups[key] ||= []
  groups[key] << record
end

definitions = groups.values.map do |records|
  canonical = records.first
  noted_sources = records.map do |record|
    [record.fetch("id"), record["notes"]] if record["notes"]
  end.compact
  notes = if noted_sources.empty?
            nil
          elsif noted_sources.length == 1
            noted_sources.first.last
          else
            noted_sources.map { |id, note| "[#{id}] #{note}" }.join("\n")
          end

  {
    "id" => canonical.fetch("id"),
    "name" => canonical.fetch("name"),
    "type" => canonical.fetch("type"),
    "attributes" => canonical.fetch("attributes").reject { |key, _value| key == "condition" },
    "use" => canonical.fetch("use"),
    "notes" => notes,
    "sources" => records.map do |record|
      {
        "id" => record.fetch("id"),
        "original_location" => record.fetch("location"),
        "original_quantity" => record.fetch("quantity"),
        "original_condition" => record.fetch("attributes")["condition"],
        "notes" => record["notes"]
      }
    end
  }
end

instances = groups.values.flat_map do |records|
  definition_id = records.first.fetch("id")
  records.flat_map do |record|
    quantity = record.fetch("quantity")
    abort "cannot migrate unknown quantity for #{record.fetch('id')}" unless quantity.is_a?(Integer) && quantity.positive?

    (1..quantity).map do |copy_number|
      instance_id = if quantity == 1
                      record.fetch("id")
                    else
                      format("%s-%02d", record.fetch("id"), copy_number)
                    end
      {
        "id" => instance_id,
        "definition" => definition_id,
        "source" => record.fetch("id"),
        "current_location" => record.fetch("location"),
        "preferred_location" => record.fetch("location"),
        "condition" => record.fetch("attributes")["condition"],
        "status" => nil,
        "notes" => nil,
        "movements" => []
      }
    end
  end
end

duplicate_instance_ids = instances.group_by { |instance| instance.fetch("id") }.select { |_id, copies| copies.length > 1 }
abort "generated duplicate instance IDs: #{duplicate_instance_ids.keys.join(', ')}" unless duplicate_instance_ids.empty?

output = YAML.dump({"definitions" => definitions, "instances" => instances}).sub(/\A---\s*\n/, "")
output = output.gsub(/^(\s+(?:notes|original_condition|condition|status|source):)\s*$/, '\\1 null')

unless options[:dry_run]
  backup = "#{path}.v1.bak"
  abort "backup already exists: #{backup}" if File.exist?(backup)
  File.write(backup, File.binread(path))
  Tempfile.create([File.basename(path), ".tmp"], File.dirname(path)) do |temporary|
    temporary.write(output)
    temporary.flush
    temporary.fsync
    File.rename(temporary.path, path)
  end
end

verb = options[:dry_run] ? "Would migrate" : "Migrated"
puts "#{verb} #{raw.length} source records into #{definitions.length} definitions and #{instances.length} instances."
