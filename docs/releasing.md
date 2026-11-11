# バージョンとリリースの決まり

このリポジトリでは、スキルごとにバージョンを付け、スキルごとに GitHub Releases に公開する。

## 目次

- [基本の決まり](#基本の決まり)
- [git-workflow との違い](#git-workflow-との違い)
- [リリースの手順](#リリースの手順)

## 基本の決まり

- バージョンは[セマンティックバージョニング 2.0.0](https://semver.org/lang/ja/)を守る。
- 番号は、スキルごとに `plugins/<スキル名>/.claude-plugin/plugin.json` の `version` に書く。マーケットプレイスのエントリには書かない。
- 新しいスキルの最初の番号は `0.1.0` にする。正式に公開する時点で `1.0.0` にする。
- タグは `<スキル名>--vX.Y.Z` の形にする（例: `foo--v1.2.0`）。Claude Code の `claude plugin tag` が作る形に合わせる。
- 一度公開したタグは、付け直し・削除・再利用をしない。問題があれば、新しい番号で直す。
- 利用者は、既定のブランチ（`main`）からインストールする。Claude Code は、`version` が変わった時だけ更新を届ける。そのため、`main` に入れる変更は、必ず番号を上げる。

| 変更の内容 | 上げる番号 |
| --- | --- |
| 後方互換性のない変更（使い方・出力の形が変わるなど） | MAJOR |
| 後方互換性のある機能の追加 | MINOR |
| 後方互換性のある不具合の修正・文言の手直し | PATCH |

## git-workflow との違い

ブランチの運用（`main` → `release_yyyymm` → `dev_<Issue 番号>`）と、月ごとのリリース PR は、git-workflow（管理者が Claude Code に使わせている Git の運用の決まり。`bachikawa/claude-config` の `skills/git-workflow`）のとおりにする。番号に関わる次の点だけを変える。この変更は、管理者が 2026-11-11 に了承した（#1）。

| 項目 | git-workflow の決まり | このリポジトリ |
| --- | --- | --- |
| 番号の単位 | リポジトリで 1 つ | スキルごと |
| タグ | `vX.Y.Z` | `<スキル名>--vX.Y.Z` |
| 最初の番号 | `v0.1.0` | スキルごとに `0.1.0` |
| 番号の決め方 | リリースに入った PR のラベル | そのスキルのディレクトリを変えた PR のラベルで、スキルごとに決める。1 つの PR が複数のスキルを変えた時は、スキルごとに数える |
| リリース PR のタイトル | `リリース vX.Y.Z（release_yyyymm）` | `リリース release_yyyymm`。本文に、版が上がるスキルと番号の表を書く |
| 前回のリリースの基準 | 前回のリリースのタグ | 番号の判定: そのスキルの前回のタグ（タグがなければ、最初の番号のまま）。Issue の洗い出し: 前回のリリース PR のマージコミット（初回は最初のコミット） |
| タグを付ける前の確かめ | PR のタイトルと、バージョンのファイルを突き合わせる | リリース PR の本文の表と、`main` の各 `plugin.json` の `version` を突き合わせる |

## リリースの手順

### 1. 番号を上げる（リリースブランチの月のうちに）

1. 前回のタグから後に変わったスキルを洗い出す。

   ```bash
   git fetch origin --tags
   # For each skill: commits since its last tag. A skill without a tag is new.
   git log --oneline <スキル名>--v<前回の番号>..origin/release_yyyymm -- plugins/<スキル名>
   ```

2. スキルごとに、そのディレクトリを変えた PR のラベルとコミットの type から、次の番号を決める（→ 上の表）。新しいスキルは、`0.1.0` のまま出す。
3. リリース作業の Issue の開発ブランチで、各 `plugin.json` の `version` を上げ、開発 PR でリリースブランチに入れる。

### 2. リリース PR を作る（月が変わった時）

- タイトルは `リリース release_yyyymm` にする。
- 本文に、版が上がるスキルの表を書く。

  | スキル | 前の番号 | 新しい番号 | 主な変更 |
  | --- | --- | --- | --- |
  | `foo` | `0.1.0` | `0.2.0` | 〜を追加 |

- このリリースに入る Issue は、前回のリリース PR のマージコミットから後のマージで洗い出す。

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
