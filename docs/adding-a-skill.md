# スキルの足し方

新しいスキルを、プラグインとしてこのリポジトリに足す手順を説明する。1 つのプラグインには、スキルを 1 つだけ入れる。

## 目次

- [名前の決まり](#名前の決まり)
- [手順](#手順)
- [雛形](#雛形)
- [検証](#検証)

## 名前の決まり

次の 3 つを、同じ名前にそろえる。

- プラグイン名（`plugins/<スキル名>/` のディレクトリ名と `plugin.json` の `name`）
- スキルのディレクトリ名（`plugins/<スキル名>/skills/<スキル名>/`）
- `SKILL.md` の frontmatter の `name`

名前の制約は次のとおり（[Agent Skills の仕様](https://agentskills.io/specification)と、Claude Code の決まり）。

- 小文字の英数字とハイフンだけを使う。64 文字以内にする。
- 先頭と末尾にハイフンを置かない。ハイフンを続けない（`--`）。
- `claude`・`anthropic` を名前に含めない。Claude Code は、Anthropic の公式のプラグインと紛らわしい名前を、検証でエラーか警告にする（[Marketplace reference](https://code.claude.com/docs/en/plugins/marketplace-reference.md#validation-messages)）。

## 手順

1. スキルを足す Issue と、開発ブランチ `dev_<Issue 番号>` を作る（→ README の「[開発する人へ](../README.md#開発する人へ)」の「ブランチ運用」）。
2. 次のディレクトリとファイルを作る。

   ```text
   plugins/<スキル名>/
   ├── .claude-plugin/
   │   └── plugin.json
   └── skills/
       └── <スキル名>/
           └── SKILL.md
   ```

3. `plugin.json` と `SKILL.md` を、下の雛形から書く。最初の `version` は `0.1.0` にする（→ [リリースの決まり](releasing.md)）。
4. `.claude-plugin/marketplace.json` の `plugins` に、エントリを足す。
5. README の「スキル」に、`kundoku-bungo` と同じ形の節を足す。中身は、README の並びのとおり、見出し（`### <スキル名>`）・版のバッジ・説明・例・特徴の箇条書き・注意書き（要る時）・プラグイン ID（`<スキル名>@bachikawa-skills`）と、Releases・権利の表示（`NOTICE.md` を置いたスキルだけ）へのリンクである。
   - 版のバッジは、shields.io の dynamic JSON（外部の JSON の値を読んで表示するバッジ）で、`main` の `plugins/<スキル名>/.claude-plugin/plugin.json` の `version` を読む。版を README に固定で書かない（版を書く場所は `plugin.json` だけ）。利用者が入れる `main` の版を示すので、スキルが `main` に入る（リリース PR がマージされる）までは値が出ない。
   - 「インストール」の、スキルの名前を書いた 1 行（`/plugin install <スキル名> --marketplace bachikawa/agent-skills`）と呼び出し方、「ほかのエージェント（npx skills）」のコマンドに、そのスキルの分を足す。足した後、「検証」の `claude plugin validate .` で、「This README has no line that installs」の助言が出ないことを確かめる。
   - スキルのデータを作るツール（`tools/<スキル名>/`）がある時は「開発する人へ」の「資料」の表に、第三者のデータを含む時は「ライセンス」に、そのスキルの項を足す。
   - README の目次（見出しの絵の下のリンクの行）は、スキルごとには足さない。
6. 下の「検証」を行う。

## 雛形

### `plugins/<スキル名>/.claude-plugin/plugin.json`

```json
{
  "name": "<スキル名>",
  "version": "0.1.0",
  "description": "<スキルの説明>",
  "author": {
    "name": "bachikawa"
  },
  "repository": "https://github.com/bachikawa/agent-skills",
  "license": "MIT"
}
```

- `version` は、このファイルだけに書く。マーケットプレイスのエントリにも書くと、`plugin.json` の値が優先され、検証で警告が出る。

### `plugins/<スキル名>/skills/<スキル名>/SKILL.md`

```markdown
---
name: <スキル名>
description: <何をするスキルか、いつ使うか（1,024 文字以内）>
---

# <スキルの見出し>

<スキルの本文>
```

- `description` は、エージェントがスキルを使うかを決める手がかりになる。何をするか・どんな依頼で使うかを具体的に書く。
- frontmatter には、Agent Skills の仕様にある項目（`name`・`description`・`license`・`compatibility`・`metadata`・`allowed-tools`）だけを書く。Claude Code だけの項目を書くと、ほかのツールで使えなくなることがある。

### 安全のための決まり

スキルは、利用者の環境でエージェントが読んで動く。次を守る。

- `allowed-tools` は、スキルに要るツールとコマンドだけに絞る（例: `Bash(git status:*)`）。制限のない `Bash` などを付けない。
- `SKILL.md` と同梱のファイルに、秘密情報（キー・トークン・パスワード・接続情報）を書かない。
- 外部から取ってきたスクリプトなどを、そのまま動かす手順を書かない。
- スキルを足す PR のレビューでも、この 3 点を確かめる。

### `.claude-plugin/marketplace.json` のエントリ

```json
{
  "name": "<スキル名>",
  "source": "./plugins/<スキル名>",
  "description": "<スキルの説明>"
}
```

## 検証

### ファイルの検証

マーケットプレイスの検証では、各プラグインの中のスキルのファイルを見ない。そのため、プラグインごとにも実行する。

```bash
claude plugin validate .
claude plugin validate ./plugins/<スキル名>
```

- どちらも終了コードが 0 で、エラーがないことを確かめる。

### インストールの確かめ

作業ツリーをマーケットプレイスとして登録し、インストールできるかを確かめる。

普段の設定のまま行わず、一時の設定ディレクトリ（`CLAUDE_CONFIG_DIR`）で行う。普段の設定に GitHub 版の `bachikawa-skills` を登録していると、名前が衝突する。最後の `remove` で、普段使っている登録まで外れる。

```bash
export CLAUDE_CONFIG_DIR="$(mktemp -d)"
claude plugin marketplace add ./
claude plugin install <スキル名>@bachikawa-skills
claude plugin list
# 確かめた後
unset CLAUDE_CONFIG_DIR
```

- 一時の設定ディレクトリは、確かめた後に消してよい。
