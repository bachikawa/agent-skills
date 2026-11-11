# kundoku-bungo の表を作るツール

スキル `kundoku-bungo` の字の表（`kanji.tsv`）・語の表（`vocabulary.tsv`）・用例集（`examples.md`）の典拠を、ウィキソース日本語版から取り、作り直すためのスクリプトとデータ。スキルを使うのには要らないので、プラグインの外に置いている（スキルと一緒には配らない）。

経緯と計画は #9 にある。

## 必要な環境

- Python 3.10 以上（標準ライブラリだけを使う）
- ネットワーク（ウィキソースの API と、Unicode のサイト）

## ファイル

| ファイル | 内容 |
| --- | --- |
| `fetch_corpus.py` | ウィキソースのページ一覧・本文と、Unihan（Unicode の漢字のデータベース）を取る |
| `clean_text.py` | 翻刻の本文（wikitext）から、編集者の注・テンプレート・番号を除く |
| `build_kanji_table.py` | 字の表（`kanji.tsv`）を作る。`--check` で、今の表と同じになるかを確かめる |
| `build_vocabulary.py` | 語の表（`vocabulary.tsv`）を作る。`--check` で、今の表と同じになるかを確かめる |
| `find_vocabulary.py` | 本文によく出てくる語を数え、語の表に足す候補を書き出す |
| `check_examples.py` | 用例集（`examples.md`）の「文語」が、出典の版の本文のとおりかを確かめる |
| `corpus_revisions.tsv` | 表に使うページの名前・版・ハッシュ値・使い道の一覧。入力を固定する正本 |
| `kanji_overrides.tsv` | 字の表の手で書く部分（語によって選ぶ字の印と説明、官報の画像で確かめた字の形の行） |
| `vocabulary_entries.tsv` | 語の表の手で書く部分（語・読み・種類・意味と、同じ形の別の語を避ける条件） |
| `line_exclusions.tsv` | 語の表・用例集で除く行の範囲（手で書く）。詔書・五箇条の御誓文を引く法令の範囲と、翻刻の誤りがある行 |
| `tests/` | テスト（ネットワークは使わない） |

### corpus_revisions.tsv の列

| 列 | 内容 |
| --- | --- |
| `title` | ウィキソースのページの名前 |
| `oldid` | 表を作るのに使う版（ウィキソースのページの、ある時点の版の番号） |
| `sha1` | その版の本文（wikitext を UTF-8 にしたもの）の SHA-1。API が返す値と同じ |
| `year` | ページの見出し（Header）の年 |
| `usage` | 使い道（下の表） |
| `source` | `pinned`（表が今引いている版か、前に `pinned` だった版）か `listed`（カテゴリから見つけた版） |
| `rule` | 使い道を決めた規則（下の「使い道の決め方」の規則の名前） |

| 使い道 | 意味 |
| --- | --- |
| `all` | 字の表・語の表・用例集のすべてに使う |
| `kanji_only` | 字の表の典拠だけに使う（詔書・勅語・勅諭・憲法・皇室典範・皇室令・華族令、皇室についてのお知らせ）。語の表と用例集には使わない |
| `text_only` | 語の表と用例集だけに使う。政府の文書でない演説・新聞（`NON_GOVERNMENT_PAGES`）。字の表の典拠にはしない |
| `examples_only` | 用例集の出典の版を固定するためだけに置く。表を作るスクリプトは読まない。用例集が引かなくなっても、人が行を消すまで残る（「株熱の余症恐るべし」など） |

### 使い道の決め方

使い道は、次の順で決める（`fetch_corpus.classify()`）。迷うものは、語の表と用例集に使わない側に倒す。語の一覧の正本は、`fetch_corpus.py` の正規表現（下の表の「正本」の列）である。

| 順 | 当たるもの | 使い道 | 規則の名前 | 正本 |
| --- | --- | --- | --- | --- |
| 1 | 詔書の部分を含む法令のページ。詔書の行は `line_exclusions.tsv` で除く | `all` | `mixed` | `MIXED_PAGES` |
| 1 | 政府の文書でない演説・新聞。名前で挙げたものだけで、2〜5 の規則は当てない（人が 1 件ずつ権利と年を確かめて足すため） | `text_only` | `non-government` | `NON_GOVERNMENT_PAGES` |
| 2 | 条約・協約・協定・議定書・覚書・降伏文書と、満洲国など日本以外の政府の文書（題名かカテゴリで見る）。題名が「〜宣言」で終わる国際的な宣言（題名だけで見る）。地名の「満洲」だけでは外さない | 入れない | `foreign-or-treaty` | `FOREIGN_OR_TREATY`・`FOREIGN_TITLE` |
| 3 | 今の時点まで改正を取り込んだ版（見出しに「現行法令掲載」か「最終改正:」「最終改正年月日:」がある） | 入れない | `current-version` | `CURRENT_VERSION` |
| 4 | 見出しに年がないページ | 入れない | `no-year` | `HEADER_YEAR` |
| 5 | 見出しの年が 1868〜1945 年の外のページと、題名に 1946 年以後の日付（「昭和二十一年」「平成」など）がある改正後の版 | 入れない | `out-of-period` | `POST_WAR_TITLE` |
| 6 | 題名が詔勅・憲法・皇室・華族令・皇室の行事などに当たるもの | `kanji_only` | `imperial-title` | `IMPERIAL_TITLE` |
| 7 | カテゴリの名前に、詔勅・皇室令・勅語が入るもの | `kanji_only` | `imperial-category` | `IMPERIAL_CATEGORY` |
| 8 | 見出しの公布番号が「皇室令第 n 号」か「宮内省達」のもの。本文は見ない（編集者が「伯爵」などを華族令にリンクしているため） | `kanji_only` | `imperial-number` | `IMPERIAL_LAW_NUMBER` |
| 9 | ほか | `all` | `default` | — |

- 今の表が典拠にしている版と、前に `pinned` だった版は、`pinned` として残す。カテゴリで新しい版が見つかっても、表が引かなくなっても残す。表を作り直すたびに、表の材料が減らないようにするため。規則の名前は `pinned` になる（6〜8 に当たる版は `kanji_only` で、規則の名前は 6〜8 のもの。演説・新聞は `text_only`）。
- `pinned` の版にも 2〜5 の規則を当てる。当たった時は止まる。その版を外すには、表がその版を引かないようにしてから（字の表なら、`corpus_revisions.tsv` からその行を消して `build_kanji_table.py` で作り直す）、`reclassify` を実行する。
- 使い道が食い違う時は `kanji_only` を優先する。用例集だけが引く版（`examples_only`）でも、6〜8 に当たれば `kanji_only` になる。
- 一覧に入れなかったページは、作業用のディレクトリの `left_out.tsv` に、規則の名前と一緒に書き出す。

## 使い方

リポジトリのルートで実行する。作業用のディレクトリ（`WORK`）は、リポジトリの外に置く（例: `WORK="$HOME/kundoku-work"`）。取った本文には、ウィキソースの編集者の注（CC BY-SA）が混ざるので、リポジトリには入れない。リポジトリの中を指定すると、スクリプトは止まる。

スクリプトは `python -I` で実行する。取ってきたファイルを読むので、作業用のディレクトリや今いるディレクトリの Python のファイルを読み込まないようにするため。

### 同じ表を作り直す時

`corpus_revisions.tsv` の版と、決まった版の Unihan を取る。

```bash
python -I tools/kundoku-bungo/fetch_corpus.py fetch --work "$WORK"
python -I tools/kundoku-bungo/fetch_corpus.py unihan --work "$WORK"
```

- 本文は `WORK/pages/<oldid>.wiki` に保存する。取った版は、SHA-1 が `corpus_revisions.tsv` と同じことを確かめる。違えば止まる。ページが改名されて題名だけが違う時は、警告を出して続ける（版はページと一緒に動くため）。
- すでに保存した版は、SHA-1 が合えば取り直さない。合わない（書き換わった・途中で切れた）版は取り直す。
- 空の作業用のディレクトリで実行すると、標準エラーに「1504 fetched, 1504 in total」と出る（数は `corpus_revisions.tsv` のデータの行の数）。もう一度実行すると「0 fetched, 1504 in total」になる。
- 50 版ずつ保存するので、途中で止まっても、取れた分は残る。
- Unihan は、UCD 18.0.0 の `Unihan.zip`（https://www.unicode.org/Public/18.0.0/ucd/Unihan.zip ）を取り、SHA-256 を確かめる。

取った版から、字の表を作る。`--check` を付けると、作った表がリポジトリの `kanji.tsv` と同じかを確かめ、違えば終了コード 1 になる。

```bash
python -I tools/kundoku-bungo/build_kanji_table.py --work "$WORK" --check
python -I tools/kundoku-bungo/build_kanji_table.py --work "$WORK"
```

- 字の組は、Unihan の `kJapaneseOldVariant`（常用漢字表の旧字体）から取る。
- 使い道が `all` か `kanji_only` の版の本文（`clean_text.kanji_lines()`。天皇の名義の言い方の行も含める）に旧字体が見つかった組だけを、表に入れる。
- 典拠は、見つかった版のうち、年・題名・版の番号の順でいちばん前のもの。今の表には頼らないので、何度作っても同じ表になる。
- `one_to_many` は、Unihan で旧字体が 2 つ以上ある組と、`kanji_overrides.tsv` で `1` にした組が `1` になる。`kanji_overrides.tsv` で `0` にすると、旧字体が 2 つ以上ある組も自動で変換する。`1` の組には、`kanji_overrides.tsv` の `note` が要る（ないと止まる）。
- `kanji_overrides.tsv` で `kind` を `exclude` にした組は、表に入れない（翻刻の誤りでしか見つからない組など。`note` に理由を書く）。
- `kind` が `design` の行（衆→眾）は、`kanji_overrides.tsv` から入れる。
- 表を作り直して典拠の版が変わった時は、`fetch_corpus.py reclassify` を実行して、新しく引く版を `pinned` にする（`revisions` を実行した作業用のディレクトリが要る）。しないと、`tests/test_corpus_revisions.py` が落ちる。
- 見つからなかった組は `WORK/kanji_missing.tsv` に、見つかった組ごとの版の数は `WORK/kanji_counts.tsv` に書き出す（`--check` の時は書き出さない）。1 つの版にしか見つからない組は、翻刻の誤りのことがあるので、原本で確かめるとよい。

### kanji_overrides.tsv の列

| 列 | 内容 |
| --- | --- |
| `modern`・`old` | 新字体と旧字体 |
| `kind` | 空・`design`・`exclude`。`design` は、Unihan にない字の形の行（典拠は国立国会図書館デジタルコレクションの URL に限る）。`exclude` は、表に入れない組 |
| `one_to_many` | `1` なら、スクリプトは変換せず、エージェントが語を見て選ぶ。新字体の字が戦前から別の字としても使われていた組（虫と蟲、勅と敕など）に付ける |
| `note` | 選び方の説明 |
| `evidence`・`evidence_url` | `design` の行の典拠 |

### 語の表を作る

取った版から、語の表を作る。`--check` の使い方は、字の表と同じ。

```bash
python -I tools/kundoku-bungo/build_vocabulary.py --work "$WORK" --check
python -I tools/kundoku-bungo/build_vocabulary.py --work "$WORK"
```

- 語・読み・種類・意味は、`vocabulary_entries.tsv` から取る。スクリプトは用例と出典だけを選ぶ。`vocabulary.tsv` は手で直さない。
- 用例は、使い道が `all` か `text_only` の版の行（`clean_text.vocabulary_lines()`）から探す。
- 探す順は、当時の字の形の翻刻を先に、編集者が字の形を新字体に直した翻刻（学制など、ほかに翻刻がない文書）を後にする。その中で、年・題名・版の番号の順。最初に見つかった一節を引く。
- 新字体に直した翻刻かどうかは、「新字体」などの節の見出しと、本文の字で見分ける。本文は、`kanji.tsv` で 1 対 1 に変換する組の字のうち、新字体の字が 5 字以上あって 9 割以上なら、新字体に直した翻刻とみなす（`clean_text.looks_modernized()`。政体のように見出しがないページのため）。字の表でも、この翻刻は典拠にしない。字の表の側では、Unihan の組のうち、旧字体が 1 つの組から、互換漢字の組（「者」のように、当時の翻刻でもふつうの字で残る）と `kanji_overrides.tsv` で語によって選ぶ組（勅など）を除いて数える（`build_kanji_table.modernizing_pairs()`）。
- 語は、`kanji.tsv` の旧字体の書き方でも探す（「予メ」は「豫メ」でも探す）。そのため、字の表を変えると語の表の用例も変わりうる。字の表を作り直した時は、`build_vocabulary.py --check` も確かめる。
- 一節は、語の前 20 字と後ろ 20 字（構文〈〜を含む語〉は 40 字）を目安に、前の文の切れ目（「。」や空白）で切る。漢字で始まる語は、前が漢字の時は長い語の一部とみなして飛ばす（「事故ニ」の「故ニ」など）。算用数字を含む一節と、章の番号で始まる一節も飛ばす。前に飛ばした同じ形がある時は、その後ろから一節を始める（一節の中の最初の形が、選んだ語になるように）。
- 用例が見つからない語があると止まる。
- 表を作り直した後は、字の表と同じく `fetch_corpus.py reclassify` を実行する。

### 語の表に語を足す

```bash
python -I tools/kundoku-bungo/find_vocabulary.py --work "$WORK"
```

- `WORK/vocabulary_candidates.tsv` に、次の 3 つを書き出す（`fixed` と `clause` は、それぞれ上位 300 語。`--top` で変えられる）。数は、語が出てくる文書の数。同じ法令の改正後の版が多いので、題名の「(」か「/」より前（「…中改正/道路取締令」の形では「/」の後ろ）を新字体にそろえて、同じなら 1 つの文書と数える（`find_vocabulary.law_key()`）。同じ法令でも、改正の布告（「…第九條改正」など）は別の文書になる。`entry` の行には、3 文書以下の語の文書の名前も書き出す。
  - `entry`: `vocabulary_entries.tsv` の語
  - `fixed`: 漢字の後ろの仮名（最初の 1 字）がいつも同じ語（9 割以上）。副詞・接続の語（悉ク・毫モ など）が上に来る。名詞に付く助詞（ノ・ハ・ヲ・ト・ヘ）は除く。前が漢字の語幹（長い語の一部）は数えない
  - `clause`: 文の頭の「漢字＋仮名」の語。名詞と助詞の組が多い
- 候補から、漢文訓読から来た語を人が選び、読み・意味を `vocabulary_entries.tsv` に書く。2 つ以上の文書に用例がある語だけを足す（1 つの文書だけだと、翻刻の誤りのことがあるため）。用例を読んで、意味の欄と合うことを確かめる。

### vocabulary_entries.tsv の列

| 列 | 内容 |
| --- | --- |
| `word` | 見出し語。新字体・カタカナ・濁点なしで書く。「〜」は構文の前後の言葉 |
| `reading` | 読み（今のひらがな） |
| `kind` | 種類（接続・副詞・構文・名詞・連体詞・助動詞、または「接続・副詞」など） |
| `meaning` | 今の意味 |
| `exclude_prev` | 語のすぐ前に来たら、その場所を用例にしない字。同じ形の別の語を避ける（「〜ニ及ヒ」の動詞と、接続の「及ヒ」など） |
| `exclude_next` | 語のすぐ後ろに来たら、その場所を用例にしない字（「若シク」と「若シ」など） |

### 用例集を確かめる

用例集（`examples.md`）は、人が一節を選んで書く。取った版で、各節の「文語」が出典の版の本文のとおりかを確かめる。違う節があると、終了コード 1 になる。

```bash
python -I tools/kundoku-bungo/check_examples.py --work "$WORK"
```

- 「文語」が、出典の版の 1 つの行の中に、そのままあるかを見る。
- 行は `clean_text.vocabulary_lines()` のもので、語の表と同じ決まりで作る。速記の注は取り除き、編集者の注・半角のかっこ・天皇の名義でだけ使う言い方を含む行と、`line_exclusions.tsv` の範囲の行は除く。
- 出典の版は、`corpus_revisions.tsv` で使い道が `all`・`text_only`・`examples_only` のものでなければならない。
- 新しい版を引く節を足した時は、`fetch_corpus.py list`・`revisions` からやり直す（`revisions` は `list` が書く `WORK/titles.json` を使う。`reclassify` は、取っていない版を引いていると止まる）。用例集だけが引く版は `examples_only` になる。
- `revisions` は、カテゴリのページを最新の版で取り直す。その間にウィキソースで編集されたページがあると、用例集と関係のない行も変わる。`corpus_revisions.tsv` の差分を確かめ、`build_kanji_table.py --check` と `build_vocabulary.py --check` も実行する。
- 演説・新聞のページは、`fetch_corpus.py` の `NON_GOVERNMENT_PAGES` にも名前を書く（使い道を手で変えても、字の表の典拠にならないようにするため）。

### 典拠を広げる時

`corpus_revisions.tsv` を作り直すので、差分を確かめてからコミットする。

```bash
python -I tools/kundoku-bungo/fetch_corpus.py list --work "$WORK"
python -I tools/kundoku-bungo/fetch_corpus.py revisions --work "$WORK"
```

使い道の規則だけを直した時は、取り直さずに判定し直せる。

```bash
python -I tools/kundoku-bungo/fetch_corpus.py reclassify --work "$WORK"
```

- `list` は、「明治元年の法令」〜「昭和20年の法令」のカテゴリから、下のカテゴリを 2 段までたどり、`WORK/titles.json` に書く。詔勅・皇室令・条約・勅語を名前に含むカテゴリは、たどらない。
- `revisions` は、今の表の典拠の版と、`titles.json` のページの最新の版を取り、使い道を決めて `corpus_revisions.tsv` を書く。取った本文は `WORK/pages/` に、ページの名前・SHA-1・カテゴリ・保存した日時と人は `WORK/pages.json` に保存する。
- `revisions` は、アカウントなし（仮アカウントを含む）で保存された版と、90 日以内に保存された版を `WORK/recent_edits.tsv` に書き出す。だれでも編集できるので、荒らされた版が入っていないかを、人が差分で確かめてからコミットする。
- `reclassify` は、`WORK/pages.json` と `WORK/pages/` から、使い道を決め直す（ネットワークを使わない）。保存した本文の SHA-1 が `pages.json` と違う時と、表が `pages.json` にない版を引いている時は止まる。`revisions` からやり直す。

## 取得の決まり

- 1 回の問い合わせに 50 ページずつまとめ、問い合わせの間は 3 秒以上あける。`maxlag=5` を付ける。
- 次の時は、待ってやり直す。6 回失敗したら止まる。
  - HTTP 429・500・502・503・504（要求が多すぎる・混んでいるなどの応答）: `Retry-After` の秒数を待つ（3〜300 秒の範囲に収める。数でなければ 60 秒）
  - `maxlag` のエラー、接続のエラー、途中で切れた応答、JSON でない応答: 60 秒待つ
- User-Agent は、ツールの名前だけの一般的なもの（`kundoku-bungo-corpus-tools/0.1`）を送る。個人の名前・連絡先・リポジトリの URL は送らない。変える時は `--user-agent` で渡す（ウィキメディアの方針: https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_User-Agent_Policy ）。

## 本文の取り扱い（clean_text.py）

| 落とし穴 | 扱い |
| --- | --- |
| 見出し（Header）のテンプレート | 除く。ページの頭でなくても、入れ子の波かっこを数えて閉じる所まで除く。閉じていなければ、そこから後をすべて除く |
| 編集者の節（外部リンク・関連項目・脚注・註・底本・凡例・法令情報・改廃経過・現代語訳・読み下し文・平仮名の版など） | 下の節も含めて除く（正本は `EDITOR_SECTION`） |
| 字の形だけを直した版の節（新字体・常用漢字版・JIS 版） | 字の表では、いつも除く（編集者が字を直した版は、字の形の典拠にならないため）。語の表・用例集では、ほかの節に本文が 1 字でもある時は除き、この節にしか本文がない時（学制など）は残す（正本は `NEW_FORM_SECTION`） |
| `<ref>` の編集者の注 | 中の字ごと除く。閉じていない `<ref>` は、行の終わりまで除く |
| `<sup>`・`<sub>`・`<small>` | 中の字ごと除く |
| 太字（`'''`）で書いた、翻刻者の付けた番号 | 行の頭（`<span>` などの中を含む）の太字は除く。行の途中の太字は除き、語の表・用例集では行ごと使わない |
| `{{異体字\|...}}`・`{{Lang\|...}}`・`{{r\|...}}`・`{{Ruby\|...}}` | 入れ子でも、本文の字だけを残す |
| ほかのテンプレート | 除く。字の表・語の表・用例集では、行の途中から除いた行を、行ごと使わない |
| ほかの HTML のタグ | タグだけを除き、中の字は残す |
| ファイル・カテゴリ・ほかの言語へのリンク | 除く。ほかのリンクは表示する字だけを残す |
| `<br>`（属性付きを含む） | 改行にする |
| 互換漢字（見た目は同じで、文字コードだけが違う字） | MediaWiki は保存の時に NFC で正規化するので、文字参照（`&#xFA45;` など）で書かれた字だけが互換漢字として残る。文字参照を字に戻した後に、正規化しない |
| 文字参照から戻った制御文字・方向の制御文字・ゼロ幅の文字 | 除く。タブとノーブレークスペースは空白にする |
| 速記の注（「（拍手）」「（大喝采）」「（謹聽）」など） | かっこの中がすべて注の語でできている時だけ除く。原文のかっこ（「（別紙）」など）は残す |
| 天皇の名義でだけ使う言い方の行（「敕」を含む） | 語の表・用例集では、行ごと除く |
| 半角のかっこを含む行 | 字の表・語の表・用例集では、行ごと除く。編集者の注と原文のかっこの両方に半角が使われ、機械では見分けられないため |
| 見出し（`=` で始まる行）・表の記法（`{\|`・`\|`・`!`）・区切り線（`----`）・`__NOTOC__` などの行、マークアップの残り（`<`・`>`・`{`・`}`・`\|`・`=`・閉じていない太字の `'`）を含む行 | 字の表・語の表・用例集では、行ごと除く |
| 章・条の番号（漢数字・大字・算用数字。「第一條ノ二ノ三」などの枝番を含む）と、その後の空白 | 番号の後が空白か行末の時だけ、行の頭から除く。「第三條ノ規定ニ依リ」のように文が続く時は残す |
| `line_exclusions.tsv` の範囲 | 始まりの語を含む行が 1 つだけの時に除く。見つからない時と、2 つ以上ある時は止まる |

## テスト

リポジトリのルートで実行する。

```bash
python -m unittest discover -s tools/kundoku-bungo/tests
```

- このテストは、リポジトリの一番上のファイルも確かめる。一番上の `LICENSE-UNICODE` がスキルの中の写しと同じこと（`test_license_copy.py`）と、`README.md` と文語のお遊び版 `README.bungo.md` のコマンド・コード・リンクの先がそろっていること（`test_readme_bungo.py`）である。片方を直した時は、もう片方も直す。
