# SPDX-License-Identifier: BSD-2-Clause
# frozen_string_literal: true

# Export a Homebrew Subversion resource pinned to an immutable revision
# as a normal checksum-backed archive. This file is loaded by `brew ruby`.
require "digest"
require "fileutils"
require "open3"
require "pathname"
require "tmpdir"

module SvnSources
  module_function

  REVISION = /\A\d+\z/.freeze

  def supported?(resource)
    strategy = resource.download_strategy
    (strategy <= SubversionDownloadStrategy) == true && !REVISION.match(resource.specs[:revision].to_s).nil?
  rescue NoMethodError, TypeError
    false
  end

  def export(resource, outputdir)
    raise ArgumentError, "Unsupported Subversion resource" unless supported?(resource)

    resource.fetch
    repo = Pathname(resource.downloader.cached_location)
    revision = resource.specs.fetch(:revision).to_s
    raise "Cached Subversion repository is missing" unless repo.directory? && !repo.symlink?

    destination = Pathname(outputdir)
    raise ArgumentError, "Output directory must be an existing directory" unless destination.directory? && !destination.symlink?

    filename = "svn-source-#{revision}.tar"
    archive = destination/filename
    raise "Refusing to overwrite archive" if archive.exist? || archive.symlink?

    Dir.mktmpdir("svn-config-", destination.to_s) do |config_dir|
      head = svn(repo, "--config-dir", config_dir, "--non-interactive", "info", "--show-item", "revision").strip
      head = svn(repo, "--config-dir", config_dir, "--non-interactive", "info")[/^Revision:\s*(\d+)$/, 1]&.strip if head.empty?
      raise "Cached Subversion repository revision mismatch: expected #{revision}, got #{head}" unless head == revision

      Dir.mktmpdir("svn-source-", destination.to_s) do |temporary|
        export_dir = Pathname(temporary)/"export"
        run_svn(repo, "--config-dir", config_dir, "--non-interactive", "export", "--force", "-r", revision, repo.to_s, export_dir.to_s)
        children = Dir.children(export_dir.to_s).sort
        run_tar(archive, export_dir, children)
      end
    end

    digest = Digest::SHA256.file(archive).hexdigest
    { "label" => "svn", "path" => archive.realpath.to_s, "url" => resource.url.to_s,
      "sha256" => digest, "size" => archive.size }
  rescue Errno::ENOENT => e
    raise "Subversion export failed: #{e.message}"
  end

  def svn(repo, *args)
    run_svn(repo, *args)
  end

  def run_svn(repo, *args)
    command = ["svn", *args]
    stdout, stderr, status = Open3.capture3(*command, chdir: repo.to_s)
    raise "Subversion command failed: #{stderr.strip}" unless status.success?

    stdout
  end

  def run_tar(output, chdir, files)
    command = if files.empty?
      ["/usr/bin/tar", "-cf", output.to_s, "--files-from", "/dev/null"]
    else
      # Prefix each child so names beginning with '-' or '@' cannot be
      # interpreted as tar options or archive-list directives.
      ["/usr/bin/tar", "-cf", output.to_s, "-C", chdir.to_s, *files.map { |name| "./#{name}" }]
    end
    stdout, stderr, status = Open3.capture3(*command)
    raise "Tar command failed: #{stderr.strip}" unless status.success?

    stdout
  end

  private_class_method :run_svn, :run_tar, :svn
end
