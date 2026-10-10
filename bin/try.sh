#!/usr/bin/env bash
# Start Claude Code in the CURRENT directory with this repo's plugin loaded
# from the working tree, so an edit can be tried in whatever project it is
# meant for. Run it from that project, by this script's full path:
#
#   cd ~/code/some-project
#   bash ~/path/to/skills-public/bin/try.sh            # extra args go to claude
#
# The working-tree copy takes priority over an installed copy of the same
# plugin, for this session only. Uncommitted edits are included; no bump or
# install is needed. Start a new session after each round of edits.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec claude --plugin-dir "$ROOT/plugins/jamie" "$@"
