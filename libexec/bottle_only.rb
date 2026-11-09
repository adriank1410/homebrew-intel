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

  # Homebrew/brew ec249da5f removed path loading from Formulary.factory and the
  # internal opt-out above; explicitly selected local bottles now load through
  # Formulary.from_bottle. Older pins keep the factory path inside the scope.
  def self.local_bottle_loading_allowed?
    Formulary.respond_to?(:from_bottle) || !Homebrew::EnvConfig.forbid_packages_from_paths?
  end

  def self.load_local_bottle(path)
    if Formulary.respond_to?(:from_bottle)
      Formulary.from_bottle(path, force_bottle: true)
    else
      Formulary.factory(path, force_bottle: true)
    end
  end

  def build(*)
    raise SourceBuildRefused,
          "intelbrew: a Homebrew source build was requested. Refusing instead of compiling locally."
  end
end
