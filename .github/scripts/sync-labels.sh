#!/usr/bin/env bash
# .github/labels.yml の内容をリポジトリに反映する（追加 or 更新）。
# 削除はしないので、不要ラベルは手動で消す。
#
# 使い方: bash .github/scripts/sync-labels.sh
# 依存: gh, yq (https://github.com/mikefarah/yq)

set -euo pipefail

LABELS_FILE="$(dirname "$0")/../labels.yml"

if ! command -v gh >/dev/null; then
  echo "gh CLI が必要です" >&2; exit 1
fi
if ! command -v yq >/dev/null; then
  echo "yq が必要です (brew install yq)" >&2; exit 1
fi

count=$(yq '. | length' "$LABELS_FILE")

for i in $(seq 0 $((count - 1))); do
  name=$(yq ".[$i].name" "$LABELS_FILE")
  color=$(yq ".[$i].color" "$LABELS_FILE")
  desc=$(yq ".[$i].description" "$LABELS_FILE")

  if gh label list --limit 200 --json name -q '.[].name' | grep -Fxq "$name"; then
    gh label edit "$name" --color "$color" --description "$desc" >/dev/null
    echo "updated: $name"
  else
    gh label create "$name" --color "$color" --description "$desc" >/dev/null
    echo "created: $name"
  fi
done

echo "done."
