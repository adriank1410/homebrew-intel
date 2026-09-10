# SPDX-License-Identifier: BSD-2-Clause
# frozen_string_literal: true

# Export a Homebrew Git resource pinned to an immutable commit as a normal
# checksum-backed archive. This file is loaded by `brew ruby`.
require "digest"
require "fileutils"
require "open3"
require "pathname"
require "tmpdir"

module GitSources
  module_function

  REVISION = /\A[0-9a-f]{40}\z/.freeze
  MAX_SUBMODULE_DEPTH = 16
  MAX_TREE_ENTRIES = 100_000

  def supported?(resource)
    strategy = resource.download_strategy
    strategy <= GitDownloadStrategy && REVISION.match?(resource.specs[:revision].to_s)
  rescue NoMethodError, TypeError
    false
  end

  def export(resource, outputdir)
    raise ArgumentError, "Unsupported Git resource" unless supported?(resource)

    resource.fetch
    repo = Pathname(resource.downloader.cached_location)
    revision = resource.specs.fetch(:revision)
    raise "Cached Git repository is missing" unless repo.directory? && !repo.symlink?
    head = git(repo, "rev-parse", "--verify", "HEAD").strip
    raise "Cached Git repository revision mismatch" unless head == revision

    destination = Pathname(outputdir)
    raise ArgumentError, "Output directory must be an existing directory" unless destination.directory? && !destination.symlink?
    filename = "git-source-#{revision}.tar"
    archive = destination/filename
    raise "Refusing to overwrite archive" if archive.exist? || archive.symlink?
    Dir.mktmpdir("git-source-", destination.to_s) do |temporary|
      archives = collect_archives(repo, revision, Pathname(temporary), "", 0, [0], [0])
      run_tar(archive, archives)
    end

    digest = Digest::SHA256.file(archive).hexdigest
    { "label" => "git", "path" => archive.realpath.to_s, "url" => resource.url.to_s,
      "sha256" => digest, "size" => archive.size }
  rescue Errno::ENOENT => e
    raise "Git export failed: #{e.message}"
  end

  def git(repo, *args)
    run_git(repo, *args)
  end

  def run_git(repo, *args)
    command = ["git", "-C", repo.to_s, *args]
    stdout, stderr, status = Open3.capture3(*command)
    raise "Git command failed: #{stderr.strip}" unless status.success?

    stdout
  end

  def collect_archives(repo, revision, temporary, prefix, depth, count, archive_index)
    raise "Git submodule nesting too deep" if depth > MAX_SUBMODULE_DEPTH
    entries = git(repo, "ls-tree", "-r", "-z", revision).split("\0").reject(&:empty?)
    count[0] += entries.length
    raise "Git tree exceeds entry limit" if count[0] > MAX_TREE_ENTRIES
    archive_index[0] += 1
    archive = temporary/"#{archive_index[0]}.tar"
    args = ["archive", "--format=tar", "--output", archive.to_s]
    args.concat(["--prefix", prefix]) unless prefix.empty?
    run_git(repo, *args, revision)
    result = [archive]
    entries.each do |entry|
      fields = entry.split("\t", 2)
      raise "Malformed Git tree entry" unless fields.length == 2
      mode, _type, sha = fields[0].split(" ", 3)
      path = fields[1]
      raise "Malformed Git tree entry" unless mode && sha&.match?(REVISION) && !path.empty?
      next unless mode == "160000"
      relative = Pathname(path)
      raise "Unsafe Git submodule path" if relative.absolute? || relative.each_filename.include?("..")
      location = repo/relative
      raise "Unsafe Git submodule path" unless location.directory? && !location.symlink?
      relative.each_filename.inject(repo) do |parent, component|
        current = parent/component
        raise "Unsafe Git submodule path" if current.symlink?
        current
      end
      actual = git(location, "rev-parse", "--verify", "HEAD").strip
      raise "Git submodule revision mismatch: #{path}" unless actual == sha
      result.concat(collect_archives(location, sha, temporary, "#{prefix}#{path}/", depth + 1, count, archive_index))
    end
    result
  end

  def run_tar(output, archives)
    command = ["/usr/bin/tar", "-cf", output.to_s, *archives.map { |item| "@#{item}" }]
    stdout, stderr, status = Open3.capture3(*command)
    raise "Tar command failed: #{stderr.strip}" unless status.success?
    stdout
  end
  private_class_method :collect_archives, :git, :run_git, :run_tar
end
