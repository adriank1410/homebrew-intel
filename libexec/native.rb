# SPDX-License-Identifier: BSD-2-Clause
# Run only via `brew ruby`. Homebrew evaluates its own recipes and decides
# which older bottle tags and uses_from_macos dependencies are compatible.
require "json"
require "digest"
require "open3"
require "formula"
require "formulary"
require "tab"
require "utils/bottles"

module IntelbrewNative
  module_function

  def check_platform!
    raise "macOS 15 on Intel is required" unless OS.mac? && Hardware::CPU.intel? && MacOS.version.to_s.split(".").first == "15"
    raise "The default /usr/local prefix is required" unless HOMEBREW_PREFIX.to_s == "/usr/local" && HOMEBREW_CELLAR.to_s == "/usr/local/Cellar"
    { "HOMEBREW_API_DOMAIN" => "https://formulae.brew.sh/api",
      "HOMEBREW_BOTTLE_DOMAIN" => "https://ghcr.io/v2/homebrew/core" }.each do |key, official|
      value = ENV[key]
      raise "Custom #{key} is unsupported; no environment setting was changed" if value && value != official
    end
    raise "Custom artifact mirror is unsupported" if ENV["HOMEBREW_ARTIFACT_DOMAIN"]
    remote = ENV["HOMEBREW_CORE_GIT_REMOTE"]
    accepted = ["https://github.com/Homebrew/homebrew-core", "https://github.com/Homebrew/homebrew-core.git",
                "git@github.com:Homebrew/homebrew-core.git"]
    raise "Custom core remote is not supported" if remote && !accepted.include?(remote)
    core = CoreTap.instance
    if (core.path/".git").exist?
      output, result = Open3.capture2e("/usr/bin/git", "-C", core.path.to_s, "remote", "get-url", "origin")
      raise "Cannot verify official core origin" unless result.success? && accepted.include?(output.strip)
    end
  end

  def core_formula(name)
    raise "Invalid canonical formula name" unless name.is_a?(String) && name.match?(/\A[a-z0-9][a-z0-9+_.-]*(?:@[0-9][a-z0-9+_.-]*)?\z/) && !name.include?("..")
    f = Formulary.factory("homebrew/core/#{name}")
    raise "Formula alias or foreign tap; use a canonical core name" unless f.name == name && f.tap&.name == "homebrew/core"
    f
  end

  def active_dependencies(f)
    f.deps.reject do |dep|
      dep.prune_from_option?(f.build) || (dep.uses_from_macos? && dep.use_macos_install?)
    end
  end

  def formula_sha(f)
    value = f.ruby_source_checksum&.hexdigest if f.respond_to?(:ruby_source_checksum)
    value ||= Digest::SHA256.file(f.path).hexdigest if f.path.file?
    raise "Missing official recipe digest" unless value&.match?(/\A[0-9a-f]{64}\z/)
    value
  end

  def metadata(name)
    f = core_formula(name)
    deps = { "runtime" => [], "build" => [], "test" => [] }
    active_dependencies(f).each do |dep|
      df = dep.to_formula
      raise "Foreign dependency is unsupported" unless df.tap&.name == "homebrew/core"
      deps[dep.build? ? "build" : (dep.test? ? "test" : "runtime")] << df.name
    end
    bottle = f.bottle_for_tag(Utils::Bottles.tag)
    official = if bottle && f.pour_bottle? && bottle.compatible_locations?
      { "tag" => bottle.tag.to_s, "sha256" => bottle.resource.checksum.hexdigest,
        "url" => bottle.url, "cellar" => bottle.cellar.to_s }
    end
    keg = f.any_installed_keg
    tab = Tab.for_keg(keg) if keg
    foreign = !!(tab && tab.source["tap"] != "homebrew/core")
    {
      "name" => f.name, "tap" => f.tap.name,
      "version" => f.version.to_s, "revision" => f.revision,
      "version_scheme" => f.version_scheme, "pkg_version" => f.pkg_version.to_s,
      "formula_sha256" => formula_sha(f), "license" => f.license,
      "official_bottle" => official, "disabled" => f.disabled?,
      "installed_current" => f.latest_version_installed? && !foreign,
      "installed_newer" => !!(keg && keg.version > f.pkg_version),
      "installed_options" => tab ? tab.used_options.to_a.map(&:to_s) : [],
      "installed_head" => !!(tab && tab.spec == :head),
      "foreign_install" => foreign, "pinned" => f.pinned?,
      "installed_versions" => f.installed_kegs.map { |k| k.version.to_s },
      "runtime" => deps["runtime"].uniq.sort,
      "build" => deps["build"].uniq.sort,
      "test" => deps["test"].uniq.sort,
    }
  end

  def receipt(name)
    f = core_formula(name)
    raise "Expected installed current version of #{name}" unless f.latest_version_installed?
    tab = Tab.for_formula(f)
    { "name" => f.name, "pkg_version" => f.pkg_version.to_s,
      "tap" => tab.source["tap"], "poured_from_bottle" => tab.poured_from_bottle,
      "built_as_bottle" => tab.built_as_bottle,
      "installed_versions" => f.installed_kegs.map { |k| k.version.to_s } }
  end

  def sources(name)
    f = core_formula(name)
    raise "Source collection requires a local pinned official recipe" unless f.path.file?
    entries = [["main", f.stable.resource]]
    f.resources.each { |resource| entries << ["resource-#{resource.name}", resource] }
    f.patchlist.each_with_index do |patch, index|
      entries << ["patch-#{index}", patch.resource] if patch.respond_to?(:resource)
    end
    result = entries.map do |label, resource|
      resource.fetch
      resource.verify_download_integrity
      cached = resource.cached_download
      raise "Non-archive/VCS resource needs manual source-distribution review: #{label}" unless cached.file?
      raise "Resource is missing a fixed checksum: #{label}" unless resource.checksum
      { "label" => label, "path" => cached.realpath.to_s, "url" => resource.url,
        "sha256" => Digest::SHA256.file(cached).hexdigest }
    end
    text = f.path.read
    {
      "formula_path" => f.path.realpath.to_s, "formula_sha256" => Digest::SHA256.hexdigest(text),
      "recipe_sha256" => Digest::SHA256.hexdigest(text.gsub(/  bottle do.+?end\n\n?/m, "")),
      "resources" => result,
    }
  end

  def bottle_only_install(request)
    require "formula_installer"
    require "cmd/install"
    require_relative "bottle_only"
    methods = FormulaInstaller.instance_methods + FormulaInstaller.private_instance_methods
    raise "Homebrew changed its installer API; refuse unsafe fallback" unless methods.include?(:build)
    raise "Unexpected installer API arity" unless FormulaInstaller.instance_method(:build).arity == 0
    FormulaInstaller.prepend(IntelbrewBottleOnly)
    target = request.fetch("target")
    name = request.fetch("name")
    before = metadata(name)
    raise "Refusing same-version reinstall/downgrade" if before["installed_current"] || before["installed_newer"]
    raise "Refusing foreign/options/HEAD/pinned installation" if before["foreign_install"] || before["installed_options"].any? || before["installed_head"] || before["pinned"]
    raise "Recipe changed after planning" unless before["formula_sha256"] == request.fetch("formula_sha256") && before["pkg_version"] == request.fetch("pkg_version")
    if target.start_with?("homebrew/core/")
      raise "Wrong target" unless target == "homebrew/core/#{name}" && before["official_bottle"]
    else
      raise "Expected an absolute local bottle path" unless target.start_with?("/") && target.end_with?(".tar.gz") && File.file?(target) && !File.symlink?(target)
      raise "Bottle changed after verification" unless Digest::SHA256.file(target).hexdigest == request.fetch("sha256")
      local = Formulary.factory(target, force_bottle: true)
      raise "Local bottle identity/version differs" unless local.name == name && local.tap&.name == "homebrew/core" && local.pkg_version.to_s == request.fetch("pkg_version")
    end
    old_versions = before["installed_versions"]
    arguments = ["--force-bottle", "--no-ask", "--formula"]
    arguments << "--as-dependency" if request["as_dependency"]
    arguments << target
    Homebrew::Cmd::Install.new(arguments).run
    raise "Homebrew installation failed" if Homebrew.respond_to?(:failed?) && Homebrew.failed?
    installed = receipt(name)
    raise "Package was not poured as a core bottle" unless installed["poured_from_bottle"] && installed["tap"] == "homebrew/core"
    raise "An old keg was unexpectedly removed" unless (old_versions - installed["installed_versions"]).empty?
  end
end

begin
  request = JSON.parse($stdin.read)
  IntelbrewNative.check_platform!
  case request.fetch("mode")
  when "inspect"
    names = request.fetch("names")
    raise "Too many requested formulae" unless names.is_a?(Array) && names.length <= 400
    puts JSON.generate(names.to_h { |name| [name, IntelbrewNative.metadata(name)] })
  when "outdated"
    names = Formula.installed.select do |f|
      f.tap&.name == "homebrew/core" && !f.pinned? && f.outdated?
    end.map(&:name).sort
    puts JSON.generate(names)
  when "receipt"
    puts JSON.generate(IntelbrewNative.receipt(request.fetch("name")))
  when "sources"
    saved_stdout = $stdout.dup
    begin
      $stdout.reopen($stderr)
      source_result = IntelbrewNative.sources(request.fetch("name"))
    ensure
      $stdout.reopen(saved_stdout)
      saved_stdout.close
    end
    puts JSON.generate(source_result)
  when "install"
    IntelbrewNative.bottle_only_install(request)
  when "guard-test"
    require "formula_installer"
    require_relative "bottle_only"
    FormulaInstaller.prepend(IntelbrewBottleOnly)
    begin
      FormulaInstaller.allocate.send(:build)
      raise "Bottle-only guard did not stop a source build"
    rescue IntelbrewBottleOnly::SourceBuildRefused
      puts JSON.generate({ "guard" => "passed" })
    end
  else
    raise "Unknown bridge operation"
  end
rescue => e
  warn "intelbrew: #{e.class}: #{e.message}"
  exit 1
end
