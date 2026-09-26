# SPDX-License-Identifier: BSD-2-Clause
# Run only via `brew ruby`. Homebrew evaluates its own recipes and decides
# which older bottle tags and uses_from_macos dependencies are compatible.
require "json";require "digest";require "fileutils";require "open3";require "formula";require "formulary";require "tab";require "utils/bottles";require "download_strategy";require "package_manager_cache";require "tmpdir";require_relative "git_sources";require_relative "svn_sources"
require_relative "source_mirrors"
require_relative "native_sources"
# A poured bottle must not replace an unmanaged prefix path, such as
# /usr/local/bin/gpg from MacGPG2 or a GitHub runner directory like
# share/gettext. Those paths stay put. The rest of the keg is still linked,
# so libraries and tools remain reachable by dependents.
module IntelbrewPreservePrefix
  def link(keg)
    return super if !link_keg || skip_link?

    conflicts = IntelbrewNative.blocking_prefix_conflicts(IntelbrewNative.prefix_link_conflicts(keg), formula)
    preserved = conflicts.select { |path| IntelbrewNative.preserve_prefix_path?(path) }
    return super if preserved.empty?

    held = []
    begin
      held = IntelbrewNative.hold_prefix_conflicts(keg, preserved)
      # Keep Homebrew's cache, relinking, overwrite-backup and error handling.
      super
    ensure
      Utils::Interrupts.ignore { IntelbrewNative.restore_held_entries(held) }
    end
    opoo "#{keg.name} kept existing prefix paths unchanged:"
    puts preserved
  end
end
module IntelbrewNative
  module_function
  def check_platform!
    raise "macOS 15 on Intel is required" unless OS.mac? && Hardware::CPU.intel? && MacOS.version.to_s.split(".").first=="15"
    raise "The default /usr/local prefix is required" unless HOMEBREW_PREFIX.to_s=="/usr/local" && HOMEBREW_CELLAR.to_s=="/usr/local/Cellar"
    {"HOMEBREW_API_DOMAIN"=>"https://formulae.brew.sh/api","HOMEBREW_BOTTLE_DOMAIN"=>"https://ghcr.io/v2/homebrew/core"}.each{|k,o|v=ENV[k];raise "Custom #{k} is unsupported" if v&&v!=o};raise "Custom artifact mirror is unsupported" if ENV["HOMEBREW_ARTIFACT_DOMAIN"]
    accepted=["https://github.com/Homebrew/homebrew-core","https://github.com/Homebrew/homebrew-core.git","git@github.com:Homebrew/homebrew-core.git"];remote=ENV["HOMEBREW_CORE_GIT_REMOTE"];raise "Custom core remote is not supported" if remote&&!accepted.include?(remote)
    core=CoreTap.instance;if (core.path/".git").exist?;out,res=Open3.capture2e("/usr/bin/git","-C",core.path.to_s,"remote","get-url","origin");raise "Cannot verify official core origin" unless res.success?&&accepted.include?(out.strip);end
  end
  def core_formula(name)
    raise "Invalid canonical formula name" unless name.is_a?(String)&&name.match?(/\A[a-z0-9][a-z0-9+_.-]*(?:@[0-9][a-z0-9+_.-]*)?\z/)&&!name.include?("..")
    f=Formulary.factory("homebrew/core/#{name}");raise "Formula alias or foreign tap" unless f.name==name&&f.tap&.name=="homebrew/core";f
  end
  def active_dependencies(f);f.deps.reject{|d|d.prune_from_option?(f.build)||(d.uses_from_macos?&&d.use_macos_install?)};end
  def formula_sha(f);v=f.ruby_source_checksum&.hexdigest if f.respond_to?(:ruby_source_checksum);v||=Digest::SHA256.file(f.path).hexdigest if f.path.file?;raise "Missing official recipe digest" unless v&.match?(/\A[0-9a-f]{64}\z/);v;end
  def installed_versions(f);f.installed_kegs.map(&:version).sort.map(&:to_s);end
  def source_entries(f);entries=[["main",f.stable.resource]];f.resources.each{|r|entries<<["resource-#{r.name}",r]};f.patchlist.each_with_index{|p,i|entries<<["patch-#{i}",p.resource] if p.respond_to?(:resource)};entries;end
  def vcs_source?(f);source_entries(f).any?{|_,r|(r.download_strategy<=VCSDownloadStrategy)==true};end
  def pinned_git_source?(f);entries=source_entries(f).select{|_,r|(r.download_strategy<=VCSDownloadStrategy)==true};!entries.empty?&&entries.all?{|_,r|GitSources.supported?(r)};end
  def pinned_svn_source?(f);entries=source_entries(f).select{|_,r|(r.download_strategy<=VCSDownloadStrategy)==true};!entries.empty?&&entries.all?{|_,r|SvnSources.supported?(r)};end
  def metadata(name)
    f=core_formula(name);deps={"runtime"=>[],"build"=>[],"test"=>[]}
    active_dependencies(f).each do |d|
      df=d.to_formula
      raise "Foreign dependency" unless df.tap&.name=="homebrew/core"
      # Local recipes retain both tags; API metadata can split them into edges.
      deps["build"] << df.name if d.build?
      deps["test"] << df.name if d.test?
      deps["runtime"] << df.name unless d.build? || d.test?
    end
    b=f.bottle_for_tag(Utils::Bottles.tag);official=b&&f.pour_bottle?&&b.compatible_locations? ? {"tag"=>b.tag.to_s,"sha256"=>b.resource.checksum.hexdigest,"url"=>b.url,"cellar"=>b.cellar.to_s}:nil;k=f.any_installed_keg;t=Tab.for_keg(k) if k;foreign=!!(t&&t.source["tap"]!="homebrew/core")
    {"name"=>f.name,"tap"=>f.tap.name,"version"=>f.version.to_s,"revision"=>f.revision,"version_scheme"=>f.version_scheme,"pkg_version"=>f.pkg_version.to_s,"formula_sha256"=>formula_sha(f),"license"=>f.license,"official_bottle"=>official,"vcs_source"=>vcs_source?(f),"pinned_git_source"=>pinned_git_source?(f),"pinned_svn_source"=>pinned_svn_source?(f),"disabled"=>f.disabled?,"installed_current"=>f.latest_version_installed?&&!foreign,"installed_newer"=>!!(k&&k.version>f.pkg_version),"installed_options"=>t ? t.used_options.to_a.map(&:to_s):[],"installed_head"=>!!(t&&t.spec==:head),"foreign_install"=>foreign,"pinned"=>f.pinned?,"installed_versions"=>installed_versions(f),"runtime"=>deps["runtime"].uniq.sort,"build"=>deps["build"].uniq.sort,"test"=>deps["test"].uniq.sort}
  end
  def receipt(name);f=core_formula(name);raise "Expected installed current version" unless f.latest_version_installed?;t=Tab.for_formula(f);{"name"=>f.name,"pkg_version"=>f.pkg_version.to_s,"tap"=>t.source["tap"],"poured_from_bottle"=>t.poured_from_bottle,"built_as_bottle"=>t.built_as_bottle,"installed_versions"=>installed_versions(f)};end
  def same_keg_link?(dst, src)
    return false unless dst.symlink?
    Utils::Path.resolved_path(dst).cleanpath == src.cleanpath
  rescue SystemCallError
    false
  end
  # Files Homebrew would refuse to replace. Directory symlinks that already
  # point at this keg are not conflicts; Homebrew removes those before linking.
  def prefix_link_conflicts(keg)
    require "find"
    conflicts = []
    root_path = Pathname(keg.to_path)
    Keg.keg_link_directories.each do |dir|
      root = keg/dir
      next unless root.exist?
      root.find do |src|
        next if src == root
        relative = src.relative_path_from(root_path)
        dst = HOMEBREW_PREFIX/relative
        if src.directory? && !src.symlink?
          if dst.symlink?
            conflicts << dst.to_s unless same_keg_link?(dst, src)
            Find.prune
          elsif dst.exist? && !dst.directory?
            conflicts << dst.to_s
            Find.prune
          end
          next
        end
        next unless src.file? || src.symlink?
        next if src.basename.to_s == ".DS_Store"
        next if %w[.pyc .pyo].include?(src.extname) && src.to_s.include?("/site-packages/")
        next if src.basename.to_s == "dir" && relative.to_s.start_with?("share/info/")
        next if relative.to_s == "lib/charset.alias"
        next unless dst.exist? || dst.symlink?
        conflicts << dst.to_s unless same_keg_link?(dst, src)
      end
    end
    conflicts
  end
  def blocking_prefix_conflicts(conflicts, formula)
    return conflicts unless formula
    conflicts.reject { |path| formula.link_overwrite?(Pathname(path)) }
  end
  # Homebrew can delete a broken symlink and can merge a directory symlink
  # that already points into another keg. Every other occupied path must stay.
  def preserve_prefix_path?(path)
    dst = Pathname(path)
    return false unless dst.exist? || dst.symlink?
    return true unless dst.symlink?
    begin
      target = Utils::Path.resolved_path(dst)
      stat = target.lstat
    rescue SystemCallError
      return false
    end
    return true unless stat.directory?
    begin
      Keg.for(target)
    rescue NotAKegError, Errno::ENOENT
      return true
    end
    false
  end
  def hold_prefix_conflicts(keg, conflicts, prefix: HOMEBREW_PREFIX)
    prefix = Pathname(prefix).cleanpath
    keg_root = Pathname(keg.to_path)
    hold_root = keg_root/".intelbrew-held-prefix"
    raise "Held prefix files are already set aside" if hold_root.exist? || hold_root.symlink?
    held = []
    begin
      paths = conflicts.map { |item| Pathname(item).cleanpath }.select { |item| preserve_prefix_path?(item) }
      paths.sort_by { |item| item.each_filename.count }.each do |item|
        relative = item.relative_path_from(prefix)
        next if relative.absolute? || relative.each_filename.any? { |part| part == ".." }
        src = keg_root/relative
        next unless src.exist? || src.symlink?
        dest = hold_root/relative
        dest.parent.mkpath
        Utils::Interrupts.ignore do
          FileUtils.mv src, dest
          held << [src, dest]
        end
      end
    rescue Exception # Restore already moved files on Interrupt/SystemExit too.
      Utils::Interrupts.ignore { restore_held_entries(held) }
      raise
    end
    held
  end
  def restore_held_entries(held)
    return if held.nil? || held.empty?
    held.reverse_each do |src, dest|
      src = Pathname(src)
      dest = Pathname(dest)
      raise "Held keg path reappeared: #{src}" if src.exist? || src.symlink?
      src.parent.mkpath
      FileUtils.mv dest, src
    end
    hold_root = Pathname(held.first[1])
    hold_root = hold_root.parent until hold_root.basename.to_s == ".intelbrew-held-prefix" || hold_root.root?
    return unless hold_root.basename.to_s == ".intelbrew-held-prefix" && hold_root.directory?
    leftover = false
    hold_root.find { |entry| leftover = true if entry.file? || entry.symlink? }
    FileUtils.rm_rf hold_root unless leftover
  end
  def sources(name)
    f=core_formula(name);raise "Source collection requires local recipe" unless f.path.file?
    result=source_entries(f).map do |label,r|
      if GitSources.supported?(r)
        directory=Dir.mktmpdir("intelbrew-git-source-",ENV["RUNNER_TEMP"])
        GitSources.export(r,directory).merge("label"=>label)
      elsif SvnSources.supported?(r)
        directory=Dir.mktmpdir("intelbrew-svn-source-",ENV["RUNNER_TEMP"])
        SvnSources.export(r,directory).merge("label"=>label)
      else
        SourceMirrors.add_gnu_fallback(r)
        IntelbrewNativeSources.fetch(r,verify_download_integrity:true);c=r.cached_download
        raise "Non-archive/VCS resource needs review" unless c.file?
        raise "Resource lacks checksum" unless r.checksum
        {"label"=>label,"path"=>c.realpath.to_s,"url"=>r.url,"sha256"=>Digest::SHA256.file(c).hexdigest}
      end
    end
    text=f.path.read;{"formula_path"=>f.path.realpath.to_s,"formula_sha256"=>Digest::SHA256.hexdigest(text),"recipe_sha256"=>Digest::SHA256.hexdigest(text.gsub(/  bottle do.+?end\n\n?/m,"")),"resources"=>result}
  end
  def coverage
    installed=Formula.installed
    raise "Too many installed formulae" if installed.length>2000
    core=[];external=[]
    installed.each do |f|
      if f.tap&.name=="homebrew/core"
        core<<f.name
      else
        external<<f.full_name.to_s
      end
    end
    {"core"=>core.uniq.sort,"external_taps"=>external.uniq.sort}
  end
  def build_context
    {"homebrew_cache"=>HOMEBREW_CACHE.realpath.to_s,
     "go_mod_cache"=>Homebrew::PackageManagerCache.path("go_mod_cache").expand_path.to_s,
     "cargo_cache"=>Homebrew::PackageManagerCache.path("cargo_cache").expand_path.to_s}
  end
  def bottle_only_install(request)
    require "formula_installer";require "cmd/install";require_relative "bottle_only";methods=FormulaInstaller.instance_methods+FormulaInstaller.private_instance_methods;raise "Homebrew changed installer API" unless methods.include?(:build)&&FormulaInstaller.instance_method(:build).arity==0;raise "Homebrew changed install command API" unless defined?(Homebrew::Cmd::InstallCmd);FormulaInstaller.prepend(IntelbrewBottleOnly);FormulaInstaller.prepend(IntelbrewPreservePrefix) unless FormulaInstaller.ancestors.include?(IntelbrewPreservePrefix)
    target=request.fetch("target");name=request.fetch("name");before=metadata(name);raise "Refusing same-version reinstall/downgrade" if before["installed_current"]||before["installed_newer"];raise "Refusing foreign/options/HEAD/pinned" if before["foreign_install"]||before["installed_options"].any?||before["installed_head"]||before["pinned"];raise "Recipe changed" unless before["formula_sha256"]==request.fetch("formula_sha256")&&before["pkg_version"]==request.fetch("pkg_version")
    local_bottle=!target.start_with?("homebrew/core/")
    if local_bottle
      raise "Expected absolute local bottle" unless target.start_with?("/")&&target.end_with?(".tar.gz")&&File.file?(target)&&!File.symlink?(target)
      raise "Bottle changed" unless Digest::SHA256.file(target).hexdigest==request.fetch("sha256")
    else
      raise "Wrong target" unless target=="homebrew/core/#{name}"&&before["official_bottle"]
    end
    install=proc do
      if local_bottle
        local=Formulary.factory(target,force_bottle:true)
        raise "Local bottle identity/version differs" unless local.name==name&&local.tap&.name=="homebrew/core"&&local.pkg_version.to_s==request.fetch("pkg_version")
      end
      old=before["installed_versions"];args=["--force-bottle","--no-ask","--formula"];args<<"--as-dependency" if request["as_dependency"];args<<target;Homebrew::Cmd::InstallCmd.new(args).run;raise "Homebrew installation failed" if Homebrew.respond_to?(:failed?)&&Homebrew.failed?;installed=receipt(name);raise "Package was not poured as core bottle" unless installed["poured_from_bottle"]&&installed["tap"]=="homebrew/core";raise "Old keg unexpectedly removed" unless (old-installed["installed_versions"]).empty?
    end
    if local_bottle
      IntelbrewBottleOnly.with_local_bottle(&install)
    else
      install.call
    end
  end
end
begin
  request=JSON.parse($stdin.read);IntelbrewNative.check_platform!;case request.fetch("mode")
  when "inspect";names=request.fetch("names");raise "Too many formulae" unless names.is_a?(Array)&&names.length<=400;puts JSON.generate(names.to_h{|n|[n,IntelbrewNative.metadata(n)]})
  when "outdated";puts JSON.generate(Formula.installed.select{|f|f.tap&.name=="homebrew/core"&&!f.pinned?&&f.outdated?}.map(&:name).sort)
  when "receipt";puts JSON.generate(IntelbrewNative.receipt(request.fetch("name")))
  when "sources";saved=$stdout.dup;begin;$stdout.reopen($stderr);r=IntelbrewNative.sources(request.fetch("name"));ensure;$stdout.reopen(saved);saved.close;end;puts JSON.generate(r)
  when "coverage";puts JSON.generate(IntelbrewNative.coverage)
  when "build-context";puts JSON.generate(IntelbrewNative.build_context)
  when "install";IntelbrewNative.bottle_only_install(request)
  when "guard-test";require "formula_installer";require_relative "bottle_only";FormulaInstaller.prepend(IntelbrewBottleOnly);begin;FormulaInstaller.allocate.send(:build);raise "guard absent";rescue IntelbrewBottleOnly::SourceBuildRefused;puts JSON.generate({"guard"=>"passed"});end
  else;raise "Unknown bridge operation";end
rescue=>e;warn "intelbrew: #{e.class}: #{e.message}";exit 1;end
