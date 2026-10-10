#!/usr/bin/env bash
# Pre-release checks for every plugin in this repo. Everything here is
# mechanical: a thing either resolves on disk or it does not. Judgement calls
# stay with whoever releases; CLAUDE.md lists the release steps.
#
# Generic on purpose: it knows the marketplace only from its manifest, so the
# same file works in any marketplace repo laid out as plugins/<plugin>/.
#
# Why each check exists — every one of these has already shipped broken once:
#   - a SKILL.md linking reference/foo.md that is not there loads a dead link
#     into the model context, and that step silently has no instructions
#   - a name: that does not match its directory makes the skill uninvocable
#   - anything dropped under skills/ or commands/ is LIVE and gets offered to
#     the user, templates and READMEs included
#   - a CRLF in a .sh shebang breaks it on a fresh clone (see .gitattributes)
#   - a plugin changed and pushed without raising ITS version, or an installed
#     version above the repo's, means updates report "already at the latest
#     version" and refresh nothing
#   - a plugin directory the marketplace manifest never names ships to nobody,
#     and looks exactly like a plugin that shipped and did nothing
#   - an HTML comment in a SKILL.md, meant as a maintainer note, loads into
#     the model as if it were instructions
#
# Usage:
#   bin/verify.sh            run every check; exit 1 if any FAIL
#   bin/verify.sh --strict   treat WARN as failure too
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MANIFEST="$ROOT/.claude-plugin/marketplace.json"
if [ ! -f "$MANIFEST" ]; then
  echo "FAIL: no .claude-plugin/marketplace.json — this is not a marketplace repo" >&2
  exit 1
fi
# The marketplace's own "name" is the first one in the file, ahead of plugins[].
MARKETPLACE="$(sed -n 's/^[[:space:]]*"name"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$MANIFEST" | head -1)"

strict=0
[ "${1:-}" = "--strict" ] && strict=1

fails=0
warns=0
ok()   { printf 'ok:   %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; warns=$((warns+1)); }
fail() { printf 'FAIL: %s\n' "$*" >&2; fails=$((fails+1)); }

rel() { printf '%s' "${1#"$ROOT/"}"; }

CR=$(printf '\r')

# Every plugin in the repo, discovered rather than listed — adding one should
# not require editing this script.
plugin_names() {
  find "$ROOT/plugins" -mindepth 3 -maxdepth 3 -path '*/.claude-plugin/plugin.json' \
    | while IFS= read -r m; do basename "$(dirname "$(dirname "$m")")"; done | sort
}

# Frontmatter block of a markdown file; empty if it has no leading --- line.
frontmatter() {
  awk 'NR==1 && $0 !~ /^---[[:space:]]*$/ { exit }
       NR==1 { next }
       /^---[[:space:]]*$/ { exit }
       { print }' "$1"
}

# First value of a top-level frontmatter key: fm_get <key> "<frontmatter>"
fm_get() { printf '%s\n' "$2" | sed -n 's/^'"$1"':[[:space:]]*//p' | head -1; }

PLUGINS=()
while IFS= read -r p; do PLUGINS+=("$p"); done < <(plugin_names)
if [ "${#PLUGINS[@]}" -eq 0 ]; then
  fail "no plugins found under plugins/ — every one needs .claude-plugin/plugin.json"
  echo "1 failure(s), 0 warning(s)" >&2
  exit 1
fi
echo "== plugins: ${PLUGINS[*]}"

echo "== versions"
# Each plugin carries its own version in plugin.json; nothing else does. They
# move independently, so a stable plugin's users see no update when a sibling
# changes. The trap that comes with it: changing a plugin and forgetting to bump
# THAT plugin, which pushes a change no install will ever pick up.
if out="$(bash "$ROOT/bin/bump.sh" --check 2>&1)"; then
  ok "every plugin.json has a version"
else
  fail "$out"
fi

sv_re='^[0-9]+\.[0-9]+\.[0-9]+$'
plugin_version() {
  sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$1" 2>/dev/null | head -1
}
# ver_lt A B: true when A is a strictly lower version than B.
ver_lt() {
  [ "$1" != "$2" ] &&
    [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | head -1)" = "$1" ]
}

# What is pushed is what installs see, so compare against the upstream branch.
upstream="$(git -C "$ROOT" rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
[ -z "$upstream" ] && warn "no upstream branch — skipped the changed-but-not-bumped check"

CHANGELOG="$ROOT/CHANGELOG.md"
[ -f "$CHANGELOG" ] || warn "no CHANGELOG.md"

listing=""
if command -v claude >/dev/null 2>&1; then
  listing="$(claude plugin list 2>/dev/null)"
else
  warn "claude CLI not on PATH — skipped the installed-version comparison"
fi

for name in "${PLUGINS[@]}"; do
  pj="plugins/$name/.claude-plugin/plugin.json"
  ver="$(plugin_version "$ROOT/$pj")"
  [[ "$ver" =~ $sv_re ]] || continue   # bump.sh --check already failed on it

  released=0   # 1 when this version is not yet pushed, so it needs a note
  if [ -n "$upstream" ]; then
    pushed="$(git -C "$ROOT" show "$upstream:$pj" 2>/dev/null | sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' | head -1)"
    changed=0
    git -C "$ROOT" diff --quiet "$upstream" -- "plugins/$name" 2>/dev/null || changed=1
    [ -n "$(git -C "$ROOT" ls-files --others --exclude-standard -- "plugins/$name")" ] && changed=1
    if [ -z "$pushed" ]; then
      ok "$name: new plugin, first release at $ver"
      released=1
    elif [ "$changed" -eq 0 ]; then
      ok "$name: unchanged since $upstream ($ver)"
    elif [ "$ver" = "$pushed" ]; then
      # A warning, not a failure: mid-edit this is the normal state. The
      # release steps bump before committing, which clears it.
      warn "$name: changed since $upstream but still at $pushed — bump it (bin/bump.sh $name) or installs never see the change"
    elif ver_lt "$ver" "$pushed"; then
      fail "$name: version $ver is BELOW the pushed $pushed — versions must move forward"
    else
      ok "$name: $pushed -> $ver"
      released=1
    fi
  fi

  if [ "$released" -eq 1 ] && [ -f "$CHANGELOG" ]; then
    if grep -qF "$name $ver" "$CHANGELOG"; then
      ok "$name: CHANGELOG.md has an entry for $name $ver"
    else
      warn "$name: CHANGELOG.md has no entry for $name $ver — add one before committing the release"
    fi
  fi

  if [ -n "$listing" ]; then
    installed="$(printf '%s\n' "$listing" \
      | awk -v p="$name@$MARKETPLACE" '
          index($0, p) { found=1; next }
          found && /Version:/ { print $2; exit }')"
    if [ -z "$installed" ]; then
      # Expected for a project-scoped plugin outside its project, or a new one.
      warn "$name@$MARKETPLACE is not installed here — nothing to compare against"
    elif ver_lt "$ver" "$installed"; then
      # Behind is normal before a push; only AHEAD is a trap.
      fail "$name: installed $installed is AHEAD of $ver — a push at this version would refresh nothing; bump above $installed"
    fi
  fi
done

shopt -s nullglob

for name in "${PLUGINS[@]}"; do
  PLUGIN_DIR="$ROOT/plugins/$name"
  SKILLS="$PLUGIN_DIR/skills"
  CMDS="$PLUGIN_DIR/commands"

  echo "== $name: live entries"
  # Anything directly under skills/ that is not a directory holding a SKILL.md,
  # and anything under commands/ that is not a real command, is still offered to
  # the user as if it were one.
  for entry in "$SKILLS"/*; do
    if [ -f "$entry" ] && [ "$(basename "$entry")" != ".gitkeep" ]; then
      fail "$(rel "$entry") sits directly in skills/ — everything here is live; move it out of plugins/"
    fi
  done

  skill_count=0
  for dir in "$SKILLS"/*/; do
    d_name="$(basename "$dir")"
    case "$d_name" in
      _*|*template*) fail "$name/skills/$d_name looks like a template but is live — move it out of plugins/" ;;
    esac
    if [ ! -f "$dir/SKILL.md" ]; then
      fail "$name/skills/$d_name has no SKILL.md"
    else
      skill_count=$((skill_count + 1))
    fi
    # A skill's README.md holds maintainer notes and stays out of context only
    # while nothing links to it.
    if [ -f "$dir/README.md" ] && grep -qE '\]\((\./)?README\.md' "$dir/SKILL.md" "$dir"/reference/*.md 2>/dev/null; then
      warn "$name/skills/$d_name links its README.md — the maintainer notes then load into the model"
    fi
  done

  cmd_count=0
  for f in "$CMDS"/*.md; do
    c_name="$(basename "$f" .md)"
    case "$c_name" in
      _*|*template*) fail "$name/commands/$c_name.md is live as /$name:$c_name — move it out of plugins/" ;;
      README|readme) warn "$name/commands/$c_name.md is live as /$name:$c_name — a plugin registers every .md here as a command" ;;
      *) cmd_count=$((cmd_count + 1)) ;;
    esac
  done

  if [ "$skill_count" -eq 0 ] && [ "$cmd_count" -eq 0 ]; then
    # Not a failure: a plugin scaffolded ahead of its first skill is a real
    # state. But it installs and offers nothing, which is worth hearing.
    warn "$name has no skills and no commands — it installs but offers nothing"
  else
    ok "$name: $skill_count skill(s), $cmd_count command(s)"
  fi

  echo "== $name: frontmatter"
  for dir in "$SKILLS"/*/; do
    sk="$dir/SKILL.md"
    [ -f "$sk" ] || continue
    d_name="$(basename "$dir")"
    fm="$(frontmatter "$sk")"
    if [ -z "$fm" ]; then
      fail "$name/skills/$d_name/SKILL.md has no frontmatter (needs a --- block on line 1)"
      continue
    fi
    nm="$(fm_get name "$fm")"
    desc="$(fm_get description "$fm")"
    dmi="$(fm_get disable-model-invocation "$fm")"

    if [ -z "$nm" ]; then
      fail "$name/skills/$d_name: frontmatter has no name:"
    elif [ "$nm" != "$d_name" ]; then
      fail "$name/skills/$d_name: name: is '$nm' — it must match the directory name"
    fi

    if [ -z "$desc" ]; then
      fail "$name/skills/$d_name: no description: — it is the ONLY thing read when deciding relevance"
    elif [ "${desc#TEMPLATE}" != "$desc" ]; then
      fail "$name/skills/$d_name: description still starts with TEMPLATE"
    elif [ "${#desc}" -lt 80 ]; then
      warn "$name/skills/$d_name: description is ${#desc} chars — too short to name trigger conditions concretely"
    elif ! printf '%s' "$desc" | grep -qiE '\b(use|when|for)\b'; then
      warn "$name/skills/$d_name: description never says when to use it (no use/when/for)"
    fi

    case "$dmi" in
      true|false) : ;;
      "") warn "$name/skills/$d_name: disable-model-invocation is unset — set it explicitly so user-invoked-only is a decision, not a default" ;;
      *)  fail "$name/skills/$d_name: disable-model-invocation is '$dmi' — must be true or false" ;;
    esac
  done

  for f in "$CMDS"/*.md; do
    c_name="$(basename "$f" .md)"
    case "$c_name" in README|readme|_*|*template*) continue ;; esac
    desc="$(fm_get description "$(frontmatter "$f")")"
    [ -z "$desc" ] && warn "$name/commands/$c_name.md has no description: in frontmatter"
  done
  ok "$name: frontmatter checked"
done

# Every markdown file in the repo, not just the shipped ones — the docs
# cross-reference each other, and a link that rots there misleads a reader
# exactly as much as one inside a SKILL.md.
echo "== links"
link_fails=0
while IFS= read -r f; do
  d="$(dirname "$f")"
  # Markdown links, plus bare reference/*.md mentions in prose. Both forms
  # appear in these playbooks and both go stale the same way.
  targets="$(
    {
      # Fenced blocks and inline code spans are dropped first: shell code and
      # syntax notation such as `name[:x](,name...)` are not links.
      awk '/^[[:space:]]*(```|~~~)/ { fence = !fence; next } !fence' "$f" \
        | sed 's/`[^`]*`//g' | grep -oE '\]\([^)]+\)' | sed 's/^](//; s/)$//'
      # A mention with a directory before it, such as ../other-skill/reference/x.md,
      # is a path the link check above already covers, so it is skipped here.
      grep -oE '(^|[^/A-Za-z0-9._-])reference/[A-Za-z0-9._-]+\.md' "$f" | sed 's/^[^r]//'
    } | sort -u
  )"
  while IFS= read -r t; do
    [ -z "$t" ] && continue
    case "$t" in http://*|https://*|mailto:*|\#*) continue ;; esac
    t="${t%%#*}"
    [ -z "$t" ] && continue
    if [ ! -e "$d/$t" ]; then
      fail "$(rel "$f") links '$t' — no such file"
      link_fails=$((link_fails + 1))
    fi
  done <<< "$targets"
done < <( { find "$ROOT/plugins" "$ROOT/docs" "$ROOT/templates" "$ROOT/oneshot" "$ROOT/.claude" \
              -name '*.md' 2>/dev/null
            ls "$ROOT"/*.md 2>/dev/null
          } | sort -u )
[ "$link_fails" -eq 0 ] && ok "every relative link resolves"

# A reference file nobody links to never loads — usually a rename left behind.
while IFS= read -r ref; do
  owner="$(dirname "$(dirname "$ref")")"
  base="reference/$(basename "$ref")"
  if ! grep -rqF "$base" "$owner" --include='*.md' 2>/dev/null; then
    warn "$(rel "$ref") is linked from nothing — it will never load"
  fi
done < <(find "$ROOT/plugins" -path '*/reference/*.md' 2>/dev/null)

echo "== hidden text"
# The whole file loads into the model; an HTML comment hides nothing from it.
# Fenced code blocks are skipped so a playbook can still show HTML it emits.
comment_fails=0
while IFS= read -r f; do
  while IFS= read -r hit; do
    fail "$(rel "$f"):$hit has an HTML comment — the model reads it anyway; make it an instruction or move it out of plugins/"
    comment_fails=$((comment_fails + 1))
  done < <(awk '/^[[:space:]]*(```|~~~)/ { fence = !fence; next }
                !fence && /<!--/ { print NR }' "$f")
done < <(find "$ROOT/plugins" -name '*.md' 2>/dev/null | sort)
[ "$comment_fails" -eq 0 ] && ok "no HTML comments in shipped markdown"

echo "== manifests and line endings"
# The manifest is the only thing that decides what ships. A plugin directory
# missing from it is invisible; an entry pointing at nothing installs nothing.
for name in "${PLUGINS[@]}"; do
  if grep -qF "\"$name\"" "$MANIFEST"; then
    ok "marketplace.json names $name"
  else
    fail "marketplace.json does not name $name — that plugin ships to nobody"
  fi
  grep -qF "\"$name\"" "$ROOT/plugins/$name/.claude-plugin/plugin.json" \
    || fail "plugins/$name/.claude-plugin/plugin.json name does not match its directory"
done

while IFS= read -r src; do
  if [ -d "$ROOT/${src#./}" ]; then
    ok "marketplace source '$src' exists"
  else
    fail "marketplace.json source '$src' does not exist"
  fi
done < <(grep -oE '"source"[[:space:]]*:[[:space:]]*"[^"]*"' "$MANIFEST" \
           | sed 's/.*"\([^"]*\)"$/\1/')

# Shipped scripts too: a CRLF there breaks them on every consumer's macOS or
# Linux machine.
while IFS= read -r f; do
  if LC_ALL=C grep -q "$CR" "$f"; then
    fail "$(rel "$f") contains CRLF — a CRLF shebang breaks it on a fresh clone"
  fi
done < <( { ls "$ROOT"/bin/*.sh; find "$ROOT/plugins" -name '*.sh'; } 2>/dev/null | sort -u )
ok "shell scripts are LF"

echo
if [ "$fails" -gt 0 ]; then
  echo "$fails failure(s), $warns warning(s)" >&2
  exit 1
fi
if [ "$warns" -gt 0 ] && [ "$strict" -eq 1 ]; then
  echo "0 failures, $warns warning(s) — failing because --strict" >&2
  exit 1
fi
echo "clean: 0 failures, $warns warning(s)"
