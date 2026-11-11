# kundoku-bungo の表を作るツール

スキル `kundoku-bungo` の字の表（`kanji.tsv`）・語の表（`vocabulary.tsv`）・用例集（`examples.md`）の典拠を、ウィキソース日本語版から取り、作り直すためのスクリプトとデータ。スキルを使うのには要らないので、プラグインの外に置いている（スキルと一緒には配らない）。

経緯と計画は #9 にある。

## 必要な環境

- Python 3.10 以上（標準ライブラリだけを使う）
- ネットワーク（ウィキソースの API と、Unicode のサイト）

## ファイル

| ファイル | 内容 |
| --- | --- |
| `fetch_corpus.py` | ウィキソースのページ一覧・本文と、Unihan を取る |
| `clean_text.py` | 翻刻の本文（wikitext）から、編集者の注・テンプレート・番号を除く |
| `corpus_revisions.tsv` | 表に使うページの名前・版（`oldid`）・使い道の一覧。入力を固定する正本 |
| `line_exclusions.tsv` | 詔書の部分を含む法令のページで、除く行の範囲（手で書く） |
| `tests/` | テスト（ネットワークは使わない） |

### corpus_revisions.tsv の列

| 列 | 内容 |
| --- | --- |
| `title` | ウィキソースのページの名前 |
| `oldid` | 表を作るのに使う版 |
| `year` | ページの見出し（Header）の年 |
| `usage` | 使い道（下の表） |
| `source` | `pinned`（今の表が典拠にしている版）か `listed`（カテゴリから見つけた版） |
| `rule` | 使い道を決めた規則（`fetch_corpus.classify()`） |

| 使い道 | 意味 |
| --- | --- |
| `all` | 字の表・語の表・用例集のすべてに使う |
| `kanji_only` | 字の表の典拠だけに使う（詔書・勅語・勅諭・皇室令・憲法）。語の表と用例集には使わない |
| `examples_only` | 用例集の出典の版を固定するためだけに置く。表を作るスクリプトは読まない |

使い道は、次の順で決める（`fetch_corpus.classify()`）。迷うものは、語の表と用例集に使わない側に倒す。

1. 詔書の部分を含む法令のページ（`MIXED_PAGES`）は `all`。詔書の行は `line_exclusions.tsv` で除く
2. 条約と、満洲国など日本以外の政府の文書は、一覧に入れない
3. 見出しに年がないページと、1868〜1945 年の外のページは、一覧に入れない。名前に 1946 年以後の日付（「昭和二十一年」など）がある改正後の版も、見出しの年にかかわらず入れない
4. 次のどれかに当たるものは `kanji_only`
   - 名前に、詔・勅語・勅諭・憲法・皇室・華族令・皇族・天皇・皇后・皇太后・皇太子・皇男子・崩御・陵墓が入る（皇室典範・皇室令・華族令と、皇室についてのお知らせ）
   - カテゴリの名前に、詔勅・皇室令・勅語が入る
   - 公布番号が「皇室令第 n 号」か「宮内省達」である
5. ほかは `all`

今の表が典拠にしている版（`pinned`）は、カテゴリで新しい版が見つかっても、そのまま残す。使い道が食い違う時は `kanji_only` を優先する。一覧に入れなかったページは、作業用のディレクトリの `left_out.tsv` に、理由と一緒に書き出す。

## 使い方

作業用のディレクトリ（`WORK`）は、リポジトリの外に置く。取った本文には、ウィキソースの編集者の注（CC BY-SA）が混ざるので、リポジトリには入れない。リポジトリの中を指定すると、スクリプトは止まる。

### 同じ表を作り直す時

`corpus_revisions.tsv` の版と、決まった版の Unihan を取る。

```bash
python tools/kundoku-bungo/fetch_corpus.py fetch --work "$WORK"
python tools/kundoku-bungo/fetch_corpus.py unihan --work "$WORK"
```

- 本文は `WORK/pages/<oldid>.wiki` に保存する。すでにある版は取り直さない。
- Unihan は、UCD 18.0.0 の `Unihan.zip`（https://www.unicode.org/Public/18.0.0/ucd/Unihan.zip ）を取り、SHA-256 を確かめる。

表を作るスクリプトは、#9 の PR 2・PR 3 で足す。

### 典拠を広げる時

`corpus_revisions.tsv` を作り直すので、差分を確かめてからコミットする。

```bash
python tools/kundoku-bungo/fetch_corpus.py list --work "$WORK"
python tools/kundoku-bungo/fetch_corpus.py revisions --work "$WORK"
```

使い道の規則だけを直した時は、取り直さずに判定し直せる。

```bash
python tools/kundoku-bungo/fetch_corpus.py reclassify --work "$WORK"
```

- `list` は、「明治元年の法令」〜「昭和20年の法令」のカテゴリから、下のカテゴリを 2 段までたどり、`WORK/titles.json` に書く。詔勅・皇室令・条約・勅語を名前に含むカテゴリは、たどらない。
- `revisions` は、今の表の典拠の版と、`titles.json` のページの最新の版を取り、使い道を決めて `corpus_revisions.tsv` を書く。取った本文は `WORK/pages/` に、ページの名前とカテゴリは `WORK/pages.json` に保存する。
- `reclassify` は、`WORK/pages.json` と `WORK/pages/` から、使い道を決め直す（ネットワークを使わない）。

## 取得の決まり

- 1 回の問い合わせに 50 ページずつまとめ、問い合わせの間は 3 秒以上あける。`maxlag=5` を付ける。
- HTTP 429・503（要求が多すぎる・混んでいるという応答）と `maxlag` のエラーの時は、`Retry-After` の秒数（なければ 60 秒）待ってやり直す。6 回失敗したら止まる。
- User-Agent は、ツールの名前だけの一般的なもの（`kundoku-bungo-corpus-tools/0.1`）を送る。個人の名前・連絡先・リポジトリの URL は送らない。変える時は `--user-agent` で渡す（ウィキメディアの方針: https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy ）。

## 本文の取り扱い（clean_text.py）

| 落とし穴 | 扱い |
| --- | --- |
| `<ref>` の編集者の注 | 除く |
| 太字（`'''`）で書いた、翻刻者の付けた番号 | 除く |
| `{{異体字\|...}}`・`{{Lang\|...}}`・`{{r\|...}}`・`{{Ruby\|...}}` | 本文の字だけを残す |
| ほかのテンプレート・HTML のタグ・カテゴリ | 除く。リンクは表示する字だけを残す |
| 互換漢字（見た目は同じで、文字コードだけが違う字） | MediaWiki は保存の時に NFC で正規化するので、文字参照（`&#xFA45;` など）で書かれた字だけが互換漢字として残る。文字参照を字に戻した後に、正規化しない |
| 速記の注（「（拍手）」など） | 決まった語を含む全角のかっこだけを除く。原文のかっこ（「（別紙）」など）は残す |
| 天皇の名義でだけ使う言い方の行（「敕」を含む） | 語の表・用例集では、行ごと除く |
| 半角のかっこを含む行 | 語の表・用例集では、行ごと除く。編集者の注と原文のかっこを、機械では見分けられないため |
| 章・条の番号と、その後の空白 | 行の頭から除く |

## テスト

リポジトリのルートで実行する。

```bash
python -m unittest discover -s tools/kundoku-bungo/tests
```
