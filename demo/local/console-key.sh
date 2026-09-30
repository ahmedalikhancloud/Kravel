#!/usr/bin/env bash
set -Eeuo pipefail
. "$(dirname "$0")/common.sh"
[[ "$(kubectl config current-context)" == 'docker-desktop' ]] || die 'Only docker-desktop is permitted.'
# Clipboard only on Windows: do not place the credential in URLs, history, or files.
if command -v clip.exe >/dev/null; then
  kubectl -n kravel-system get secret kravel-operator-token -o jsonpath='{.data.token}' | base64 --decode | clip.exe
  printf 'Console key copied. Paste into the Human console unlock field. Do not share it.\n'
else
  printf 'Private console unlock key (do not share or screen-record):\n'
  kubectl -n kravel-system get secret kravel-operator-token -o jsonpath='{.data.token}' | base64 --decode
  printf '\n'
fi
