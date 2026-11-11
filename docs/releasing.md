# バージョンとリリースの決まり

このリポジトリでは、スキルごとにバージョンを付け、スキルごとに GitHub Releases に公開する。

## 目次

- [基本の決まり](#基本の決まり)
- [番号の決め方](#番号の決め方)
- [git-workflow との違い](#git-workflow-との違い)
- [リリースの手順](#リリースの手順)

## 基本の決まり

- バージョンは[セマンティックバージョニング 2.0.0](https://semver.org/lang/ja/)を守る。
- 番号は、スキルごとに `plugins/<スキル名>/.claude-plugin/plugin.json` の `version` に書く。マーケットプレイスのエントリには書かない。
- 新しいスキルの最初の番号は `0.1.0` にする。正式に公開する時点で `1.0.0` にする。
- タグは `<スキル名>--vX.Y.Z` の形にする（例: `foo--v1.2.0`）。Claude Code の `claude plugin tag` が作る形に合わせる。
- 一度公開したタグは、付け直し・削除・再利用をしない。問題があれば、新しい番号で直す。
- 利用者は、既定のブランチ（`main`）からインストールする。Claude Code は、`version` が変わった時だけ更新を届ける。
  - そのため、スキルのディレクトリ（`plugins/<スキル名>/`）を変えて `main` に入れる時は、そのスキルの番号を必ず上げる。
  - README や `docs/` だけの変更では、番号を上げない。

## 番号の決め方

スキルごとに、前回のタグから後にそのスキルのディレクトリを変えた PR を集め、いちばん大きい変更に合わせて番号を上げる。

| 変更の内容 | PR のラベル | コミットの type | 上げる番号 |
| --- | --- | --- | --- |
| 後方互換性のない変更（使い方・出力の形が変わるなど） | `breaking-change` | `BREAKING CHANGE:` か `!` が付いたもの | MAJOR |
| 後方互換性のある機能の追加 | `enhancement` | `feat`・`perf` | MINOR |
| 不具合の修正・文言の手直し・そのほかの変更 | `bug`・`documentation`・`refactor`・`test`・`chore`・`security` | `fix`・`docs`・`refactor`・`style`・`test`・`build`・`chore` など | PATCH |

- ラベルと type が食い違う時は、大きい方の番号に合わせる。
- `0.y.z` の間（正式に公開する前）は、後方互換性のない変更でも MINOR を上げてよい。

## git-workflow との違い

git-workflow は、管理者が Claude Code に使わせている Git の運用の決まりである（`bachikawa/claude-config` の `skills/git-workflow`。非公開）。ブランチの運用（`main` → `release_yyyymm` → `dev_<Issue 番号>`）と、月ごとのリリース PR は、git-workflow のとおりにする。

番号に関わる次の点だけを変える。この変更は、管理者が 2026-11-11 に了承した（#1）。

| 項目 | git-workflow の決まり | このリポジトリ |
| --- | --- | --- |
| 番号の単位 | リポジトリで 1 つ | スキルごと |
| タグ | `vX.Y.Z` | `<スキル名>--vX.Y.Z` |
| 最初の番号 | `v0.1.0` | スキルごとに `0.1.0` |
| 番号の決め方 | リリースに入った PR のラベル | そのスキルのディレクトリを変えた PR で、スキルごとに決める（→ [番号の決め方](#番号の決め方)） |
| リリース PR のタイトル | `リリース vX.Y.Z（release_yyyymm）` | `リリース release_yyyymm`。本文に、リリースするスキルと番号の表を書く |
| 前回のリリースの基準 | 前回のリリースのタグ | 番号の判定: そのスキルの前回のタグ（タグがなければ新しいスキル）。Issue の洗い出し: 前回のリリース PR のマージコミット（初回は最初のコミット） |
| タグを付ける前の確かめ | PR のタイトルと、バージョンのファイルを突き合わせる | リリース PR の本文の表と、`main` の各 `plugin.json` の `version` を突き合わせる |

## リリースの手順

### 1. 番号を上げる（リリースブランチの月のうちに）

1. スキルごとに、前回のタグを探す。タグがないスキルは、新しいスキルである。

   ```bash
   git fetch origin --tags
   git tag --list '<スキル名>--v*' --sort=-v:refname | head -1
   ```

2. 前回のタグから後に、そのスキルのディレクトリを変えた PR を洗い出す。

   ```bash
   git log --first-parent --merges --format='%s' <前回のタグ>..origin/release_yyyymm -- plugins/<スキル名>
   ```

3. 洗い出した PR のラベルとコミットの type から、次の番号を決める（→ [番号の決め方](#番号の決め方)）。新しいスキルは、`0.1.0` のまま出す。
4. リリース作業の Issue の開発ブランチで、各 `plugin.json` の `version` を上げ、開発 PR でリリースブランチに入れる。

### 2. リリース PR を作る（月が変わった時）

1. 番号の上げ忘れがないかを、すべてのスキルで確かめる。手順 1 の後に入った PR や、洗い出しの漏れを見つけるためである。

   ```bash
   # For each skill with a tag: any output means the skill changed since its last release.
   git log --oneline <前回のタグ>..origin/release_yyyymm -- plugins/<スキル名>
   git show origin/release_yyyymm:plugins/<スキル名>/.claude-plugin/plugin.json
   ```

   - 1 つ目に出力があるのに、`version` が前回のタグと同じスキルがあれば、リリース PR を作らない。先に手順 1 で番号を上げる。

2. タイトルは `リリース release_yyyymm` にする。
3. 本文に、リリースするスキルの表を書く。新しく足したスキルも入れ、前の番号は `—` にする。

   | スキル | 前の番号 | 新しい番号 | 主な変更 |
   | --- | --- | --- | --- |
   | `foo` | `0.1.0` | `0.2.0` | 〜を追加 |
   | `bar` | — | `0.1.0` | 新しく足した |

4. このリリースに入る Issue を、前回のリリース PR のマージコミットから後のマージで洗い出す。初回のリリースは、最初のコミットから洗い出す。

   ```bash
   git log --first-parent --merges --format='%s' <前回のリリース PR のマージコミット>..origin/release_yyyymm
   ```

### 3. リリースを作る（リリース PR のマージ後）

1. `main` の各 `plugin.json` の `version` が、リリース PR の本文の表と同じかを確かめる。違えばタグを付けず、管理者に伝える。

   ```bash
   git fetch origin
   git show origin/main:plugins/<スキル名>/.claude-plugin/plugin.json
   ```

2. `main` に切り替え、表のスキルごとにタグを付ける。`claude plugin tag` は、`plugin.json` とマーケットプレイスのエントリが食い違っていないかを確かめてから、今のコミットにタグを付ける。

   ```bash
   git switch main
   git pull --ff-only origin main
   claude plugin tag ./plugins/<スキル名> --dry-run
   claude plugin tag ./plugins/<スキル名> --push
   ```

3. 表のスキルごとに、日本語のリリースノートを書き、付けたタグでリリースを作る。

   ```bash
   gh release create <スキル名>--vX.Y.Z --verify-tag --title "<スキル名> vX.Y.Z" --notes-file <file>
   ```
