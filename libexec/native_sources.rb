# SPDX-License-Identifier: BSD-2-Clause
# Run only via `brew ruby`.
require "digest"
require "open3"
require "pathname"
require "tempfile"
require "uri"

module IntelbrewNativeSources
  MIN_INDEX_HASH_LENGTH = 7
  MAX_INDEX_HASH_LENGTH = 40
  MAX_NORMALIZE_BYTES = 16 * 1024 * 1024
  # nmap.org's certificate can expire while the formula checksum stays valid.
  # Homebrew does not try mirrors when that failure happens during URL
  # resolution, so keep one checksum-gated HTTPS copy of the upstream tarball.
  MAX_FALLBACK_BYTES = 64 * 1024 * 1024
  # Some upstreams (for example linuxtv.org behind a bot challenge) return an
  # HTML page instead of the archive. ci.py can then extract the inputs of the
  # root's previously published, attested source bundle into this directory,
  # one file per SHA-256. A file is used only when its digest equals the
  # formula checksum, so the recipe stays the trust anchor.
  SOURCE_SEEDS_ENV = "HOMEBREW_INTELBREW_SOURCE_SEEDS"
  MAX_SEED_BYTES = 2 * 1024 * 1024 * 1024
  NMAP_DISTFILE = %r{\Ahttps://nmap\.org/dist/(nmap-[0-9]+(?:\.[0-9]+){1,4}(?:_[0-9]+)?\.tar\.bz2)\z}
  INDEX_LINE = /\Aindex ([0-9a-f]+)\.\.([0-9a-f]+)([^\r\n]*)(\r?\n)?\z/

  module_function

  # GitHub compare patches have occasionally changed the abbreviated object
  # hash width without changing the patch content. Homebrew's checksum remains
  # authoritative; this only returns a byte payload whose SHA-256 is exactly
  # the expected formula checksum.
  def normalize(payload, expected_sha)
    expected = expected_sha.respond_to?(:hexdigest) ? expected_sha.hexdigest : expected_sha.to_s
    return unless expected.match?(/\A[0-9a-f]{64}\z/)

    bytes = payload.dup.force_encoding(Encoding::BINARY)
    lines = bytes.each_line.to_a
    matches = lines.filter_map { |line| line.match(INDEX_LINE) }
    return if matches.empty?

    full = matches.map { |match| [match[1].length, match[2].length].min }.min
    full = [full, MAX_INDEX_HASH_LENGTH].min
    return if full < MIN_INDEX_HASH_LENGTH

    MIN_INDEX_HASH_LENGTH.upto(full) do |length|
      candidate = lines.map do |line|
        match = line.match(INDEX_LINE)
        if match
          "index #{match[1][0, length]}..#{match[2][0, length]}#{match[3]}#{match[4]}"
        else
          line
        end
      end.join.force_encoding(Encoding::BINARY)
      next if candidate == bytes
      return candidate if Digest::SHA256.hexdigest(candidate) == expected
    end
    nil
  end

  def github_patch_url?(url)
    uri = URI.parse(url.to_s)
    uri.scheme == "https" && uri.host == "github.com" && uri.user.nil? && uri.password.nil? &&
      (uri.path.end_with?(".diff") || uri.path.end_with?(".patch"))
  rescue URI::InvalidURIError
    false
  end

  def fallback_url(url)
    match = NMAP_DISTFILE.match(url.to_s)
    return unless match

    "https://distfiles.macports.org/nmap/#{match[1]}"
  end

  def https_reader=(reader)
    @https_reader = reader
  end

  def cache_path_for(url)
    text = url.to_s
    uri = URI.parse(text)
    base = File.basename(uri.path.to_s)
    unless uri.is_a?(URI::HTTPS) && uri.user.nil? && uri.password.nil? && uri.query.nil? &&
           uri.fragment.nil? && base.match?(/\A[A-Za-z0-9._+-]+\z/) && base == uri.path.split("/").last
      raise ArgumentError, "unsafe source URL"
    end

    HOMEBREW_CACHE/"downloads/#{Digest::SHA256.hexdigest(text)}--#{base}"
  end

  def fetch(resource, **options)
    resource.fetch(**options)
  rescue ChecksumMismatchError => error
    unless github_patch_url?(resource.url)
      raise error unless seed_published_source(resource)

      # Homebrew verifies the seeded cache entry against the formula checksum.
      return resource.fetch(**options)
    end

    cached = Pathname(resource.cached_download.to_s)
    stat = File.lstat(cached.to_s)
    raise error unless stat.file? && !stat.symlink? && stat.size <= MAX_NORMALIZE_BYTES

    repaired = normalize(File.binread(cached.to_s), error.expected)
    raise error unless repaired

    replace_atomically(cached, repaired, stat.mode & 0o7777)
    # Make Homebrew perform the authoritative checksum verification against the
    # repaired cache entry; the digest check above only selects the candidate.
    resource.fetch(**options)
  rescue DownloadError => error
    raise error unless seed_expired_certificate(resource, error) || seed_published_source(resource)

    resource.fetch(**options)
  end

  def seed_published_source(resource)
    directory = ENV.fetch(SOURCE_SEEDS_ENV, "")
    return false if directory.empty?

    expected = resource.checksum
    return false unless expected.respond_to?(:hexdigest) && expected.hexdigest.match?(/\A[0-9a-f]{64}\z/)

    root = Pathname(directory)
    return false unless root.absolute? && !root.symlink? && root.directory?

    candidate = root/expected.hexdigest
    stat = File.lstat(candidate.to_s)
    return false unless stat.file? && stat.size <= MAX_SEED_BYTES

    target = Pathname(resource.cached_download.to_s)
    target.dirname.mkpath
    raise ArgumentError, "refusing to replace a symlink" if target.symlink?
    return false unless copy_verified(candidate, target, expected.hexdigest)

    warn "intelbrew: seeded #{target.basename} from the previously published source bundle"
    true
  rescue ArgumentError, SystemCallError
    false
  end

  # Copy without following links, hash the bytes actually copied and publish
  # the cache entry only when they match the expected digest.
  def copy_verified(source, target, expected)
    temporary = Tempfile.new([".intelbrew-seed-", ".partial"], target.dirname.to_s)
    begin
      temporary.binmode
      digest = Digest::SHA256.new
      copied = 0
      File.open(source.to_s, File::RDONLY | (defined?(File::NOFOLLOW) ? File::NOFOLLOW : 0)) do |input|
        input.binmode
        while (chunk = input.read(1024 * 1024))
          copied += chunk.bytesize
          return false if copied > MAX_SEED_BYTES

          digest.update(chunk)
          temporary.write(chunk)
        end
      end
      return false unless digest.hexdigest == expected

      temporary.flush
      temporary.fsync
      File.chmod(0o644, temporary.path)
      temporary.close
      File.rename(temporary.path, target.to_s)
      true
    ensure
      temporary.close!
    end
  end
  private_class_method :copy_verified

  def seed_expired_certificate(resource, error)
    return false unless certificate_expired?(error)

    mirror = fallback_url(resource.url)
    expected = resource.checksum
    return false unless mirror && expected.respond_to?(:hexdigest) &&
                        expected.hexdigest.match?(/\A[0-9a-f]{64}\z/)

    bytes = download_https(mirror).dup.force_encoding(Encoding::BINARY)
    return false if bytes.bytesize > MAX_FALLBACK_BYTES
    return false unless Digest::SHA256.hexdigest(bytes) == expected.hexdigest

    path = cache_path_for(resource.url)
    path.dirname.mkpath
    raise ArgumentError, "refusing to replace a symlink" if path.symlink?

    replace_atomically(path, bytes, 0o644)
    true
  rescue ArgumentError, SocketError, SystemCallError
    false
  end

  def certificate_expired?(error)
    text = error.message.to_s
    text = "#{text}\n#{error.cause.message}" if error.respond_to?(:cause) && error.cause
    text.downcase.include?("certificate has expired")
  end

  def download_https(url)
    reader = @https_reader
    return reader.call(url) if reader

    uri = URI.parse(url.to_s)
    raise ArgumentError, "mirror must be https" unless uri.is_a?(URI::HTTPS) && uri.user.nil? && uri.password.nil?

    directory = HOMEBREW_CACHE/"downloads"
    directory.mkpath
    temporary = Tempfile.new([".intelbrew-fallback-", ".partial"], directory.to_s)
    begin
      temporary.close
      _stdout, stderr, status = Open3.capture3(
        "/usr/bin/curl", "-fsSL", "--proto", "=https", "--tlsv1.2",
        "--max-filesize", MAX_FALLBACK_BYTES.to_s, "--retry", "0",
        "--output", temporary.path, url.to_s
      )
      raise ArgumentError, stderr unless status.success?

      bytes = File.binread(temporary.path)
      raise ArgumentError, "mirror payload is too large" if bytes.bytesize > MAX_FALLBACK_BYTES

      bytes
    ensure
      temporary.close!
    end
  end

  def replace_atomically(target, payload, mode)
    temporary = Tempfile.new([".intelbrew-source-", ".partial"], target.dirname.to_s)
    begin
      temporary.binmode
      temporary.write(payload)
      temporary.flush
      temporary.fsync
      File.chmod(mode, temporary.path)
      temporary.close
      File.rename(temporary.path, target.to_s)
    ensure
      temporary.close!
    end
  end
  private_class_method :replace_atomically
end
