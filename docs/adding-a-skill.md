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
- `claude-`・`anthropic-` で始めない。

## 手順

1. スキルを足す Issue と、開発ブランチ `dev_<Issue 番号>` を作る（→ [README の「ブランチ運用」](../README.md#ブランチ運用)）。
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
5. README の「スキルの一覧」に、行を足す。
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

- `version` は、このファイルだけに書く。マーケットプレイスのエントリにも書くと、こちらが優先され、検証で警告が出る。

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

作業ツリーをマーケットプレイスとして登録し、インストールできるかを確かめる。確かめた後は、登録を外す。

```bash
claude plugin marketplace add ./
claude plugin install <スキル名>@bachikawa-skills
# 確かめた後
claude plugin uninstall <スキル名>@bachikawa-skills
claude plugin marketplace remove bachikawa-skills
```
