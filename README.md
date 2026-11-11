# agent-skills

AI エージェント用のスキル（[Agent Skills](https://agentskills.io/) の `SKILL.md` 形式）を集めたリポジトリ。スキルを 1 つずつ Claude Code のプラグインとして登録しているので、必要なスキルだけをインストールできる。

## 目次

- [スキルの一覧](#スキルの一覧)
- [インストール](#インストール)
- [更新](#更新)
- [構成](#構成)
- [資料](#資料)
- [ブランチ運用](#ブランチ運用)
- [ライセンス](#ライセンス)

## スキルの一覧

まだスキルはない。

<!-- スキルを足したら、ここに行を足す（→ docs/adding-a-skill.md）
| スキル | 説明 | 最新の版 |
| --- | --- | --- |
| `foo` | 〜する | `0.1.0` |
-->

## インストール

Claude Code で、このリポジトリをマーケットプレイスとして登録してから、使いたいスキルを選んでインストールする。マーケットプレイスの名前は `bachikawa-skills` である。

```text
/plugin marketplace add bachikawa/agent-skills
/plugin install <スキル名>@bachikawa-skills
```

ターミナルからは、次のコマンドでも同じことができる。

```bash
claude plugin marketplace add bachikawa/agent-skills
claude plugin install <スキル名>@bachikawa-skills
```

- インストールしたスキルは、`/<スキル名>:<スキル名>` で呼び出せる。依頼の内容に合えば、エージェントが自分で使うこともある。
- 外す時は `claude plugin uninstall <スキル名>@bachikawa-skills` を実行する。

## 更新

自動更新は、既定でオフである。次のどちらかで更新する。

- `/plugin` の「Marketplaces」タブで `bachikawa-skills` を選び、自動更新を有効にする。
- 手で更新する。

  ```bash
  claude plugin marketplace update bachikawa-skills
  claude plugin update <スキル名>@bachikawa-skills
  ```

更新が届くのは、スキルの版が上がった時だけである。各スキルの変更は、[Releases](https://github.com/bachikawa/agent-skills/releases) で確かめられる。

## 構成

```text
agent-skills/
├── .claude-plugin/
│   └── marketplace.json        # マーケットプレイスの定義（スキルの一覧）
├── plugins/
│   └── <スキル名>/              # 1 つのプラグインに、スキルを 1 つだけ入れる
│       ├── .claude-plugin/
│       │   └── plugin.json     # プラグインの定義と版
│       └── skills/
│           └── <スキル名>/
│               └── SKILL.md    # スキルの本体
└── docs/                       # 開発の資料
```

## 資料

| 資料 | 内容 |
| --- | --- |
| [スキルの足し方](docs/adding-a-skill.md) | 名前の決まり、ファイルの雛形、検証の方法 |
| [バージョンとリリースの決まり](docs/releasing.md) | スキルごとの版の上げ方、タグの形（`<スキル名>--vX.Y.Z`）、リリースの手順 |

## ブランチ運用

| ブランチ | 役割 | 切り元 | マージ先 |
| --- | --- | --- | --- |
| `main` | 前回のリリースの状態。利用者は、ここからインストールする | — | — |
| `release_yyyymm` | その月のリリースの準備（例: `release_202611`） | `main` | `main` |
| `dev_<Issue 番号>` | 1 つの Issue の作業 | リリースブランチ | リリースブランチ |

- `main` とリリースブランチには、直接コミットしない。変更は、必ず PR を通す。
- 月が変わる時に、リリースブランチを `main` にマージし、版が上がったスキルをリリースする（→ [リリースの手順](docs/releasing.md#リリースの手順)）。

## ライセンス

[MIT](LICENSE)
