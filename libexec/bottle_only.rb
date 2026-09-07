# SPDX-License-Identifier: BSD-2-Clause
# Process-local guard. Never installed into Homebrew's own source tree.
module IntelbrewBottleOnly
  class SourceBuildRefused < StandardError; end

  def build(*)
    raise SourceBuildRefused,
          "intelbrew: a Homebrew source build was requested. Refusing instead of compiling locally."
  end
end
