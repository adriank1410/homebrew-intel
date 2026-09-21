#!/bin/bash
# SPDX-License-Identifier: BSD-2-Clause
# EPHEMERAL GITHUB-HOSTED RUNNERS ONLY. Existing kegs are moved, not deleted.
set -euo pipefail
[[ "${GITHUB_ACTIONS:-}" == true && "${RUNNER_ENVIRONMENT:-}" == github-hosted && "${RUNNER_OS:-}" == macOS && "${RUNNER_ARCH:-}" == X64 ]] || { echo 'Refusing runner preparation outside GitHub-hosted macOS Intel.' >&2; exit 1; }
[[ "$(/usr/bin/sw_vers -productVersion)" == 15.* ]] || exit 1
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_CLEANUP=1 HOMEBREW_NO_ANALYTICS=1 HOMEBREW_NO_INSTALL_FROM_API=1 HOMEBREW_NO_ENV_HINTS=1 HOMEBREW_NO_ASK=1 HOMEBREW_CURL_RETRIES=4
project_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)";read -r brew_commit configured_core_commit < <(/usr/bin/python3 -c 'import json,sys;config=json.load(open(sys.argv[1]));print(config["brew_commit"],config["core_commit"])' "$project_dir/policy/config.json")
core_commit="${INTELBREW_CORE_COMMIT:-$configured_core_commit}"
[[ "$brew_commit" =~ ^[0-9a-f]{40}$ && "$configured_core_commit" =~ ^[0-9a-f]{40}$ && "$core_commit" == "$configured_core_commit" && "$(brew --prefix)" == /usr/local && "$(brew --cellar)" == /usr/local/Cellar ]] || exit 1
brew_dir="$(brew --repository)";[[ "$brew_dir" == /usr/local/Homebrew ]] || exit 1
brew_origin="$(/usr/bin/git -C "$brew_dir" remote get-url origin)";[[ "$brew_origin" == https://github.com/Homebrew/brew || "$brew_origin" == https://github.com/Homebrew/brew.git ]] || exit 1
bash "$project_dir/scripts/retry-fetch.sh" /usr/bin/git -C "$brew_dir" fetch --depth=1 origin "$brew_commit";/usr/bin/git -C "$brew_dir" checkout --detach "$brew_commit"
core_dir="$brew_dir/Library/Taps/homebrew/homebrew-core";core_origin="$(/usr/bin/git -C "$core_dir" remote get-url origin)";[[ "$core_origin" == https://github.com/Homebrew/homebrew-core || "$core_origin" == https://github.com/Homebrew/homebrew-core.git ]] || exit 1
backup_dir="$(/usr/bin/mktemp -d "${RUNNER_TEMP:?}/intelbrew-runner-backup.XXXXXXXX")";/bin/mv "$core_dir" "$backup_dir/homebrew-core-preseeded"
/usr/bin/python3 "$project_dir/scripts/quarantine-runner-links.py" "$backup_dir"
/usr/bin/git init "$core_dir"; /usr/bin/git -C "$core_dir" remote add origin https://github.com/Homebrew/homebrew-core.git;[[ "$(/usr/bin/git -C "$core_dir" remote get-url origin)" == https://github.com/Homebrew/homebrew-core.git ]] || exit 1
bash "$project_dir/scripts/retry-fetch.sh" /usr/bin/git -C "$core_dir" fetch --depth=1 origin "$core_commit";/usr/bin/git -C "$core_dir" checkout --detach "$core_commit"
[[ "$(/usr/bin/git -C "$brew_dir" rev-parse HEAD)" == "$brew_commit" && "$(/usr/bin/git -C "$core_dir" rev-parse HEAD)" == "$core_commit" ]] || exit 1
if command -v gh >/dev/null 2>&1;then /bin/cp -p "$(command -v gh)" "$backup_dir/gh";printf '%s\n' "$backup_dir" >> "${GITHUB_PATH:?}";fi
bash "$project_dir/scripts/retry-fetch.sh" brew vendor-install ruby
brew list --formula > "$backup_dir/formulae.txt";brew ruby -e 'require "keg"; HOMEBREW_CELLAR.children.select(&:directory?).each { |rack| rack.children.select(&:directory?).each { |prefix| Keg.new(prefix).unlink if (prefix/"INSTALL_RECEIPT.json").file? } }'
# GitHub's Intel image owns parts of /usr/local as root. sudo is used only after all
# github-hosted/Intel/Sequoia guards above and only to move/recreate fixed Homebrew paths
# on this disposable runner. It is never part of the client command.
for old_dir in /usr/local/Cellar /usr/local/opt /usr/local/var/homebrew/linked /usr/local/etc;do
  if [[ -e "$old_dir" || -L "$old_dir" ]];then /usr/bin/sudo /bin/mv "$old_dir" "$backup_dir/$(basename "$old_dir")";fi
  /usr/bin/sudo /bin/mkdir -p "$old_dir";/usr/bin/sudo /usr/sbin/chown "$(id -u):$(id -g)" "$old_dir"
done
printf 'Preseeded state retained at: %s\n' "$backup_dir";brew config
