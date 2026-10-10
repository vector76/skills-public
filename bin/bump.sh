#!/usr/bin/env bash
# Bump one plugin's version in its plugin.json.
#
# Why this exists: an installed plugin is a COPY in a cache keyed by its version
# string. A pushed change reaches installs (via auto-update or `claude plugin
# update`) only if that plugin's "version" rose; push without a bump and every
# install reports "already at the latest version" and keeps the old copy.
#
# Each plugin versions independently, so a plugin that did not change is not
# bumped and its users see no update. Bump every plugin you changed, and only
# those; bin/verify.sh warns about a changed plugin whose version has not moved.
#
# This is a release step, not part of testing. To try an edit, start a session
# with `claude --plugin-dir ./plugins/<plugin>`, which reads the working tree
# directly and needs no bump. CLAUDE.md lists the release steps.
#
# Usage:
#   bin/bump.sh <plugin>                 bump patch   (the common case)
#   bin/bump.sh <plugin> minor|major     bump that component instead
#   bin/bump.sh <plugin> 1.4.0           set an explicit version
#   bin/bump.sh --check                  verify every plugin.json has a version
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Every plugin in the repo, discovered rather than listed — adding one should
# not require editing this script.
manifests() { find "$ROOT/plugins" -mindepth 3 -maxdepth 3 \
                -path '*/.claude-plugin/plugin.json' | sort; }
plugin_name_of() { basename "$(dirname "$(dirname "$1")")"; }

sv='[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*'
read_plugin() { sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\('"$sv"'\)".*/\1/p' "$1" | head -1; }

stamp() {
  # In-place rewrite via temp file — portable across GNU/BSD sed.
  sed 's|\("version"[[:space:]]*:[[:space:]]*"\)'"$sv"'"|\1'"$2"'"|' "$1" > "$1.tmp"
  mv "$1.tmp" "$1"
}

usage() {
  echo "usage: bin/bump.sh <plugin> [patch|minor|major|X.Y.Z]   or   bin/bump.sh --check" >&2
  echo "plugins: $(manifests | while IFS= read -r m; do plugin_name_of "$m"; done | tr '\n' ' ')" >&2
  exit 2
}

# Bind arguments once. Referencing a bare $1 inside the case body breaks under
# `set -u` when no argument was passed.
arg="${1:-}"
case "$arg" in
  "") usage ;;
  --check)
    rc=0
    while IFS= read -r m; do
      [ -n "$(read_plugin "$m")" ] || { echo "no X.Y.Z version in $(plugin_name_of "$m")/.claude-plugin/plugin.json" >&2; rc=1; }
    done < <(manifests)
    [ "$rc" -eq 0 ] && echo "ok: every plugin.json has a version"
    exit "$rc"
    ;;
esac

name="$arg"
manifest="$ROOT/plugins/$name/.claude-plugin/plugin.json"
[ -f "$manifest" ] || { echo "no plugin named '$name'" >&2; usage; }

part="${2:-patch}"
case "$part" in
  major|minor|patch) ;;
  [0-9]*.[0-9]*.[0-9]*) explicit="$part" ;;
  *) echo "unrecognized argument: $part" >&2; usage ;;
esac

cur="$(read_plugin "$manifest")"
[ -n "$cur" ] || { echo "no X.Y.Z version in $name's plugin.json" >&2; exit 1; }
if [ -n "${explicit:-}" ]; then
  new="$explicit"
else
  IFS=. read -r MA MI PA <<<"$cur"
  case "$part" in
    major) new="$((MA+1)).0.0" ;;
    minor) new="$MA.$((MI+1)).0" ;;
    patch) new="$MA.$MI.$((PA+1))" ;;
  esac
fi

stamp "$manifest" "$new"
echo "$name: $cur -> $new"
echo "Installs pick this up only once it is committed and pushed."
