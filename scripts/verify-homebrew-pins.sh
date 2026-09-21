#!/bin/bash
# SPDX-License-Identifier: BSD-2-Clause
# Run only on an ephemeral GitHub-hosted macOS Intel runner after preparation.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted \
   && "${RUNNER_OS:-}" == macOS && "${RUNNER_ARCH:-}" == X64 \
   && "$(/usr/bin/sw_vers -productVersion)" == 15.* ]] || {
  echo 'Refusing Homebrew pin verification outside GitHub-hosted macOS Intel Sequoia.' >&2
  exit 1
}

project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
read -r configured_brew_commit configured_core_commit < <(/usr/bin/python3 -c 'import json,sys;config=json.load(open(sys.argv[1]));print(config["brew_commit"],config["core_commit"])' "$project_dir/policy/config.json")
[[ "${INTELBREW_CORE_COMMIT:-}" == "$configured_core_commit" ]] || {
  echo "INTELBREW_CORE_COMMIT does not match policy/config.json" >&2
  exit 1
}

brew_dir="$(brew --repository)"
core_dir="$(brew --repository homebrew/core)"
[[ "$(/usr/bin/git -C "$brew_dir" rev-parse HEAD)" == "$configured_brew_commit" ]]
[[ "$(/usr/bin/git -C "$core_dir" rev-parse HEAD)" == "$configured_core_commit" ]]

# Load every reviewed target through the same native bridge as production.
# This catches Formula DSL/API incompatibilities before allocating build jobs.
(cd "$project_dir" && /usr/bin/python3 - <<'PYTHON'
from intelbrew.core import ROOT, native, read_json
names = read_json(ROOT / "policy/targets.json")["formulae"]
for offset in range(0, len(names), 50):
    batch = names[offset:offset + 50]
    result = native({"mode": "inspect", "names": batch})
    if set(result) != set(batch):
        raise SystemExit("Native bridge did not inspect every requested formula")
print(f"Loaded {len(names)} reviewed formulae from the pinned core")
PYTHON
)

# Pick a current core formula whose recipe exercises Homebrew's real Cargo
# fetch hook. The preferred formula keeps this gate small; the fallback makes
# the check continue to cover the API when that recipe is renamed or removed.
cargo_formula="$(CORE_FORMULA_DIR="$core_dir/Formula" TARGETS_FILE="$project_dir/policy/targets.json" /usr/bin/python3 - <<'PY'
import json
import os
from pathlib import Path

root = Path(os.environ["CORE_FORMULA_DIR"])
files = sorted(root.glob("**/*.rb"))
preferred = ["just", "dust", "rtk"]
targets = set(json.loads(Path(os.environ["TARGETS_FILE"]).read_text())["formulae"])
matches = [path for path in files if path.stem in targets and
           "std_cargo_fetch_args" in path.read_text(encoding="utf-8")]
for name in preferred:
    if any(path.stem == name for path in matches):
        print(name)
        break
else:
    if not matches:
        raise SystemExit("No reviewed core formula exercises std_cargo_fetch_args")
    print(matches[0].stem)
PY
)"

HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_FROM_API=1 brew ruby -e '
  require "formula"
  require "formulary"
  formula = Formulary.factory("homebrew/core/#{ARGV.fetch(0)}")
  abort "Formula did not load from the pinned core" unless formula.is_a?(Formula)
  abort "Formula has no Cargo fetch hook" unless formula.fetch_defined?
  puts formula.full_name
' "$cargo_formula"

# A fresh cache ensures the test executes the fetch hook even if the runner
# image already cached a source archive or Cargo dependencies.
export HOMEBREW_CACHE="$(mktemp -d "${RUNNER_TEMP:?}/intelbrew-compatibility.XXXXXXXX")"

# The workflow resolves the real toolchain before preparation quarantines the
# Homebrew-installed rustup shim. The toolchain itself lives outside the kegs.
# Real binaries also survive Formula#brew changing HOME/CARGO_HOME.
toolchain_bin="${INTELBREW_CARGO_BIN:?Missing runner Cargo toolchain path}"
[[ -x "$toolchain_bin/cargo" && -x "$toolchain_bin/rustc" ]] || exit 1
export PATH="$toolchain_bin:$PATH"
cargo --version
rustc --version

# brew fetch may silently SKIP a formula's fetch hook when brew dependencies
# are absent. Invoke the actual hook in Homebrew's real staged source instead;
# the runner Cargo toolchain suffices for this non-compiling compatibility test.
HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_FROM_API=1 brew ruby -e '
  require "formula"
  require "formulary"
  require "extend/ENV"
  formula = Formulary.factory("homebrew/core/#{ARGV.fetch(0)}")
  abort "Formula has no Cargo fetch hook" unless formula.fetch_defined?
  ENV.activate_extensions!(env: "std")
  ENV.setup_build_environment(formula: formula)
  ENV.prepend_path "PATH", ENV.fetch("INTELBREW_CARGO_BIN")
  formula.stable.resource.fetch(verify_download_integrity: true)
  formula.brew do
    formula.fetch
  end
  puts "Cargo fetch hook completed for #{formula.full_name}"
' "$cargo_formula"
