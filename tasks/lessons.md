# Lessons

## ブランチの差分だけを取り消すときに origin/main から復元しない（2026-06-10）

- **何が起きたか**: ブランチが README.md に追記した分を取り消す際に `git restore --source origin/main` を使ったため、分岐点以降に main 側で入った無関係な文言変更まで取り込んでしまった。
- **ルール**: 「このブランチの差分だけを消す」場合は、復元元は origin/main ではなく **merge-base**（`git merge-base origin/main HEAD`）にする。復元後は `git diff <merge-base> -- <file>` が空であること、fixup 前の HEAD との差分が意図した削除のみであることを必ず確認する。
