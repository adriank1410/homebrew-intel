# SPDX-License-Identifier: BSD-2-Clause
# Process-local guard. Never installed into Homebrew's own source tree.
module IntelbrewBottleOnly
  class SourceBuildRefused < StandardError; end

  def self.with_local_bottle
    unless ENV.fetch("HOMEBREW_FORBID_PACKAGES_FROM_PATHS", "").empty?
      raise "Explicit HOMEBREW_FORBID_PACKAGES_FROM_PATHS forbids local bottle installation"
    end
    # Homebrew uses this scoped opt-out for verified API source files too.
    # brew.sh clears it at startup, so it must be set inside this Ruby process.
    with_env(HOMEBREW_INTERNAL_ALLOW_PACKAGES_FROM_PATHS: "1") { yield }
  end

  def build(*)
    raise SourceBuildRefused,
          "intelbrew: a Homebrew source build was requested. Refusing instead of compiling locally."
  end
end
