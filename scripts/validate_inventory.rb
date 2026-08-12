#!/usr/bin/env ruby

# Compatibility entry point. The Python validator is the single source of
# truth; keeping a second schema implementation here caused silent drift.

ROOT = File.expand_path("..", __dir__)
argument = ARGV.shift
DATA_DIR = argument ? File.expand_path(argument, Dir.pwd) : File.join(ROOT, "data")
abort "Usage: #{File.basename($PROGRAM_NAME)} [DATA_DIR]" unless ARGV.empty?

venv_python = File.join(ROOT, ".venv", "bin", "python")
python = File.executable?(venv_python) ? venv_python : (ENV["PYTHON"] || "python3")

Dir.chdir(ROOT) do
  exec python, "-m", "inventory_toolkit", "--data-dir", DATA_DIR, "validate"
end
