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

| スキル | 説明 | プラグイン ID |
| --- | --- | --- |
| `kundoku-bungo` | 現代の日本語と、明治〜昭和 20 年ごろの公の書き言葉（漢文訓読体の普通文。法令・告諭・演説などの文体）を相互に変換する | `kundoku-bungo@bachikawa-skills` |

<!-- スキルを足したら、上の表に行を足す（→ docs/adding-a-skill.md） -->

インストールの方法は、下の「インストール」にある。

各スキルの最新の版と変更は、[Releases](https://github.com/bachikawa/agent-skills/releases) で確かめられる。

## インストール

Claude Code で、このリポジトリをマーケットプレイスとして登録してから、使いたいスキルを選んでインストールする。マーケットプレイスの名前は `bachikawa-skills` である。

```text
/plugin marketplace add bachikawa/agent-skills
/plugin install <スキル名>@bachikawa-skills
```

新しい版の Claude Code では、登録とインストールを 1 行でできる（`claude plugin validate` の案内では 2.1.275 以降。2.1.322 で確かめた）。

```text
/plugin install <スキル名> --marketplace bachikawa/agent-skills
```

ターミナルからは、次のコマンドでも同じことができる。

```bash
claude plugin marketplace add bachikawa/agent-skills
claude plugin install <スキル名>@bachikawa-skills
# or, in one line
claude plugin install <スキル名> --marketplace bachikawa/agent-skills
```

- インストールしたスキルは、`/<スキル名>:<スキル名>` で呼び出せる。依頼の内容に合えば、エージェントが自分で使うこともある。
- 外す時は `claude plugin uninstall <スキル名>@bachikawa-skills` を実行する。

### ほかのエージェント（npx skills）

Claude Code 以外のエージェントには、[skills](https://www.npmjs.com/package/skills)（`npx skills`）で入れられる。skills 1.8.1 で確かめた。1.8.1 は Node.js 22.20 以降が要る。

```bash
npx skills@1.8.1 add bachikawa/agent-skills --skill <スキル名>
```

- 版（`@1.8.1`）を付けると、確かめた版のまま実行できる。付けないと、その時の最新の版を取ってきて実行する。
- 既定では、今のディレクトリ（プロジェクト）に入れる。ユーザー全体に入れる時は `-g` を付ける。入れるエージェントは `--agent` で選べる。
- 入るのは、スキルのディレクトリ（`plugins/<スキル名>/skills/<スキル名>/`）の中身だけである。`kundoku-bungo` は、権利の表示（`LICENSE`・`LICENSE-UNICODE`・`NOTICE.md`）をこの中に置いているので、一緒に入る。
- `npx skills` は、使った記録を匿名で送る。送らない時は、環境変数 `DISABLE_TELEMETRY=1`（または `DO_NOT_TRACK=1`）を設定して実行する。bash では `DISABLE_TELEMETRY=1 npx skills@1.8.1 add …`、PowerShell では先に `$env:DISABLE_TELEMETRY = "1"` を実行する。
- 更新は `npx skills@1.8.1 update <スキル名>`、外す時は `npx skills@1.8.1 remove <スキル名>` で行う。`-g` で入れた時は、どちらにも `-g` を付ける。

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
├── tools/
│   └── <スキル名>/              # スキルのデータを作るツール（スキルと一緒には配らない）
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
- 月が変わる時に、リリースブランチを `main` にマージし、版が上がったスキルと新しく足したスキルをリリースする（→ [リリースの手順](docs/releasing.md#リリースの手順)）。

## ライセンス

[MIT](LICENSE)

- `kundoku-bungo` は、Unicode の Unihan データベースから作った字の表を含む。この表には Unicode License v3 も当てはまる（[LICENSE-UNICODE](plugins/kundoku-bungo/skills/kundoku-bungo/LICENSE-UNICODE)、出典は [NOTICE.md](plugins/kundoku-bungo/skills/kundoku-bungo/NOTICE.md)）。
