# SPDX-License-Identifier: BSD-2-Clause
# frozen_string_literal: true

module SourceMirrors
  module_function

  GNU_MIRROR = %r{\Ahttps://ftpmirror\.gnu\.org/gnu/[A-Za-z0-9._~:/%+\-]+\z}.freeze

  def add_gnu_fallback(resource)
    original = resource.url.to_s
    return nil unless GNU_MIRROR.match?(original)

    fallback = original.sub("https://ftpmirror.gnu.org/gnu/", "https://ftp.gnu.org/gnu/")
    resource.mirror(fallback) unless resource.mirrors.include?(fallback)
    fallback
  end
end
