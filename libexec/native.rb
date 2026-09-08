# SPDX-License-Identifier: BSD-2-Clause
# Run only via `brew ruby`. Homebrew evaluates its own recipes and decides
# which older bottle tags and uses_from_macos dependencies are compatible.
require "json";require "digest";require "open3";require "formula";require "formulary";require "tab";require "utils/bottles"
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
  def metadata(name)
    f=core_formula(name);deps={"runtime"=>[],"build"=>[],"test"=>[]};active_dependencies(f).each{|d|df=d.to_formula;raise "Foreign dependency" unless df.tap&.name=="homebrew/core";deps[d.build? ? "build" : (d.test? ? "test" : "runtime")]<<df.name};b=f.bottle_for_tag(Utils::Bottles.tag);official=b&&f.pour_bottle?&&b.compatible_locations? ? {"tag"=>b.tag.to_s,"sha256"=>b.resource.checksum.hexdigest,"url"=>b.url,"cellar"=>b.cellar.to_s}:nil;k=f.any_installed_keg;t=Tab.for_keg(k) if k;foreign=!!(t&&t.source["tap"]!="homebrew/core")
    {"name"=>f.name,"tap"=>f.tap.name,"version"=>f.version.to_s,"revision"=>f.revision,"version_scheme"=>f.version_scheme,"pkg_version"=>f.pkg_version.to_s,"formula_sha256"=>formula_sha(f),"license"=>f.license,"official_bottle"=>official,"disabled"=>f.disabled?,"installed_current"=>f.latest_version_installed?&&!foreign,"installed_newer"=>!!(k&&k.version>f.pkg_version),"installed_options"=>t ? t.used_options.to_a.map(&:to_s):[],"installed_head"=>!!(t&&t.spec==:head),"foreign_install"=>foreign,"pinned"=>f.pinned?,"installed_versions"=>f.installed_kegs.map{|x|x.version.to_s},"runtime"=>deps["runtime"].uniq.sort,"build"=>deps["build"].uniq.sort,"test"=>deps["test"].uniq.sort}
  end
  def receipt(name);f=core_formula(name);raise "Expected installed current version" unless f.latest_version_installed?;t=Tab.for_formula(f);{"name"=>f.name,"pkg_version"=>f.pkg_version.to_s,"tap"=>t.source["tap"],"poured_from_bottle"=>t.poured_from_bottle,"built_as_bottle"=>t.built_as_bottle,"installed_versions"=>f.installed_kegs.map{|k|k.version.to_s}};end
  def sources(name)
    f=core_formula(name);raise "Source collection requires local recipe" unless f.path.file?;entries=[["main",f.stable.resource]];f.resources.each{|r|entries<<["resource-#{r.name}",r]};f.patchlist.each_with_index{|p,i|entries<<["patch-#{i}",p.resource] if p.respond_to?(:resource)};result=entries.map{|label,r|r.fetch(verify_download_integrity:true);c=r.cached_download;raise "Non-archive/VCS resource needs review" unless c.file?;raise "Resource lacks checksum" unless r.checksum;{"label"=>label,"path"=>c.realpath.to_s,"url"=>r.url,"sha256"=>Digest::SHA256.file(c).hexdigest}};text=f.path.read;{"formula_path"=>f.path.realpath.to_s,"formula_sha256"=>Digest::SHA256.hexdigest(text),"recipe_sha256"=>Digest::SHA256.hexdigest(text.gsub(/  bottle do.+?end\n\n?/m,"")),"resources"=>result}
  end
  def bottle_only_install(request)
    require "formula_installer";require "cmd/install";require_relative "bottle_only";methods=FormulaInstaller.instance_methods+FormulaInstaller.private_instance_methods;raise "Homebrew changed installer API" unless methods.include?(:build)&&FormulaInstaller.instance_method(:build).arity==0;raise "Homebrew changed install command API" unless defined?(Homebrew::Cmd::InstallCmd);FormulaInstaller.prepend(IntelbrewBottleOnly)
    target=request.fetch("target");name=request.fetch("name");before=metadata(name);raise "Refusing same-version reinstall/downgrade" if before["installed_current"]||before["installed_newer"];raise "Refusing foreign/options/HEAD/pinned" if before["foreign_install"]||before["installed_options"].any?||before["installed_head"]||before["pinned"];raise "Recipe changed" unless before["formula_sha256"]==request.fetch("formula_sha256")&&before["pkg_version"]==request.fetch("pkg_version")
    if target.start_with?("homebrew/core/");raise "Wrong target" unless target=="homebrew/core/#{name}"&&before["official_bottle"];else;raise "Expected absolute local bottle" unless target.start_with?("/")&&target.end_with?(".tar.gz")&&File.file?(target)&&!File.symlink?(target);raise "Bottle changed" unless Digest::SHA256.file(target).hexdigest==request.fetch("sha256");local=Formulary.factory(target,force_bottle:true);raise "Local bottle identity/version differs" unless local.name==name&&local.tap&.name=="homebrew/core"&&local.pkg_version.to_s==request.fetch("pkg_version");end
    old=before["installed_versions"];args=["--force-bottle","--no-ask","--formula"];args<<"--as-dependency" if request["as_dependency"];args<<target;Homebrew::Cmd::InstallCmd.new(args).run;raise "Homebrew installation failed" if Homebrew.respond_to?(:failed?)&&Homebrew.failed?;installed=receipt(name);raise "Package was not poured as core bottle" unless installed["poured_from_bottle"]&&installed["tap"]=="homebrew/core";raise "Old keg unexpectedly removed" unless (old-installed["installed_versions"]).empty?
  end
end
begin
  request=JSON.parse($stdin.read);IntelbrewNative.check_platform!;case request.fetch("mode")
  when "inspect";names=request.fetch("names");raise "Too many formulae" unless names.is_a?(Array)&&names.length<=400;puts JSON.generate(names.to_h{|n|[n,IntelbrewNative.metadata(n)]})
  when "outdated";puts JSON.generate(Formula.installed.select{|f|f.tap&.name=="homebrew/core"&&!f.pinned?&&f.outdated?}.map(&:name).sort)
  when "receipt";puts JSON.generate(IntelbrewNative.receipt(request.fetch("name")))
  when "sources";saved=$stdout.dup;begin;$stdout.reopen($stderr);r=IntelbrewNative.sources(request.fetch("name"));ensure;$stdout.reopen(saved);saved.close;end;puts JSON.generate(r)
  when "install";IntelbrewNative.bottle_only_install(request)
  when "guard-test";require "formula_installer";require_relative "bottle_only";FormulaInstaller.prepend(IntelbrewBottleOnly);begin;FormulaInstaller.allocate.send(:build);raise "guard absent";rescue IntelbrewBottleOnly::SourceBuildRefused;puts JSON.generate({"guard"=>"passed"});end
  else;raise "Unknown bridge operation";end
rescue=>e;warn "intelbrew: #{e.class}: #{e.message}";exit 1;end
