# SPDX-License-Identifier: BSD-2-Clause
# Run only via `brew ruby`.
require "digest"
require "pathname"
require "tempfile"
require "uri"

module IntelbrewNativeSources
  MIN_INDEX_HASH_LENGTH = 7
  MAX_INDEX_HASH_LENGTH = 40
  MAX_NORMALIZE_BYTES = 16 * 1024 * 1024
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

  def fetch(resource, **options)
    resource.fetch(**options)
  rescue ChecksumMismatchError => error
    raise unless github_patch_url?(resource.url)

    cached = Pathname(resource.cached_download.to_s)
    stat = File.lstat(cached.to_s)
    raise error unless stat.file? && !stat.symlink? && stat.size <= MAX_NORMALIZE_BYTES

    repaired = normalize(File.binread(cached.to_s), error.expected)
    raise error unless repaired

    replace_atomically(cached, repaired, stat.mode & 0o7777)
    # Make Homebrew perform the authoritative checksum verification against the
    # repaired cache entry; the digest check above only selects the candidate.
    resource.fetch(**options)
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
