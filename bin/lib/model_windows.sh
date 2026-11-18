# shellcheck shell=bash
# The core's model windows, set by bin/spine at boot (sourced, never run).
#
# The core never asks a local-openai endpoint for a model's window and budgets
# an unknown one at a few thousand tokens: less than its own system prompt, so
# the trim drops the user's message and the provider answers "messages: at
# least one message is required" (every claude-fable-5.2 turn of the studio
# preview, 2026-11-18). register_windows gives the core a window for
# SPINE_MODEL (SPINE_CONTEXT_WINDOW wins, else the endpoint's listing) and for
# bin/studio's STUDIO_WRITER_MODEL and STUDIO_EDITOR_MODEL (the environment,
# then secrets/teeny.env). Needs log, die and oh (bin/lib/teeny.sh), and
# SPINE_MODEL, LADDER_URL, LADDER_API_KEY. The key goes in a header file,
# never in curl's argv. bin/test/model_windows_test.sh covers it.

register_windows() {
  local studio_models wanted listing registry studio_registry window w m e all studio_model
  # The studio's models: the process environment first, then
  # secrets/teeny.env, as bin/lib/studio.py's Config reads them.
  studio_models="$(STUDIO_WRITER_MODEL="${STUDIO_WRITER_MODEL:-}" STUDIO_EDITOR_MODEL="${STUDIO_EDITOR_MODEL:-}" \
    python3 - "$TEENY_SECRETS/teeny.env" <<'PY'
import os, sys
filed = {}
try:
    lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
except OSError:
    lines = []
for raw in lines:
    key, _, value = raw.strip().removeprefix("export ").partition("=")
    filed[key.strip()] = value.strip().strip("\"'")
seen = []
for name in ("STUDIO_WRITER_MODEL", "STUDIO_EDITOR_MODEL"):
    value = (os.environ.get(name) or "").strip() or filed.get(name, "")
    if value and value not in seen:
        seen.append(value)
print(" ".join(seen))
PY
  )" || studio_models=""
  wanted=""
  for studio_model in $studio_models; do
    [ "$studio_model" = "$SPINE_MODEL" ] || wanted="$wanted $studio_model"
  done
  # The listing is fetched only when something needs a window from it.
  listing=""
  if [ -z "${SPINE_CONTEXT_WINDOW:-}" ] || [ -n "$wanted" ]; then
    listing="$(curl -fsS -m 10 -H @<(printf 'authorization: Bearer %s\n' "${LADDER_API_KEY:-}") \
      "${LADDER_URL%/}/models" 2>/dev/null)" || listing=""
  fi
  window_of() { # MODEL -> its window from the endpoint's listing, or nothing
    jq -r --arg m "$1" '[.data[]? | select(.id == $m or ((.aliases // []) | index($m)))
      | (.context_length // .context_window // empty)][0] // empty' <<<"$listing" 2>/dev/null || true
  }
  entry() { jq -cn --arg m "$1" --argjson w "$2" '{id: $m, provider: "local-openai", context_window: $w}'; }
  registry='[]'
  window="${SPINE_CONTEXT_WINDOW:-}"
  [ -n "$window" ] || window="$(window_of "$SPINE_MODEL")"
  if [[ "$window" =~ ^[1-9][0-9]*$ ]]; then
    registry="$(jq -c --argjson e "$(entry "$SPINE_MODEL" "$window")" '. + [$e]' <<<"$registry")"
    log "model: $SPINE_MODEL has a $window-token window"
  else
    log "model: no window for $SPINE_MODEL from the endpoint; keeping the stored one (set SPINE_CONTEXT_WINDOW if turns come back empty)"
  fi
  studio_registry='[]'
  for studio_model in $wanted; do
    w="$(window_of "$studio_model")"
    if [[ "$w" =~ ^[1-9][0-9]*$ ]]; then
      studio_registry="$(jq -c --argjson e "$(entry "$studio_model" "$w")" '. + [$e]' <<<"$studio_registry")"
    else
      log "model: no window for the studio's $studio_model from the endpoint; its turns may come back empty"
    fi
  done
  # One write with everything. Should the core refuse it, SPINE_MODEL's own
  # window is written alone (its failure still stops the boot), then each
  # studio model on its own, so one bad entry cannot cost the others.
  all="$(jq -c --argjson s "$studio_registry" '. + $s' <<<"$registry")"
  if [ "$all" != '[]' ] && oh inference_update_model_settings "$(jq -cn --argjson r "$all" '{model_registry: $r}')" >/dev/null; then
    jq -r '.[] | "\(.id) \(.context_window)"' <<<"$studio_registry" | while read -r m w; do
      log "model: the studio's $m has a $w-token window"
    done
  elif [ "$all" != '[]' ]; then
    if [ "$registry" != '[]' ]; then
      oh inference_update_model_settings "$(jq -cn --argjson r "$registry" '{model_registry: $r}')" >/dev/null ||
        die "model: the core did not take the $window-token window"
    fi
    while IFS= read -r e; do
      m="$(jq -r .id <<<"$e")"
      if oh inference_update_model_settings "$(jq -cn --argjson r "$registry" --argjson e "$e" '{model_registry: ($r + [$e])}')" >/dev/null; then
        registry="$(jq -c --argjson e "$e" '. + [$e]' <<<"$registry")"
        log "model: the studio's $m has a $(jq -r .context_window <<<"$e")-token window"
      else
        log "model: the core refused a window for the studio's $m; its turns may come back empty"
      fi
    done < <(jq -c '.[]' <<<"$studio_registry")
  fi
}
