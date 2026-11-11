# 出典と権利の表示

このスキルのコードと文書は MIT ライセンスで公開する（リポジトリ https://github.com/bachikawa/agent-skills の `LICENSE`）。次のデータと資料を使っている。

## Unicode の Unihan データベース

- `references/kanji.tsv` の新字体と旧字体の対応は、Unicode Character Database 18.0.0 の Unihan データベース（`Unihan_Variants.txt` の `kJapaneseOldVariant`）から作った。この項目は、常用漢字表（平成 22 年内閣告示第 2 号）の括弧の中の字体（いわゆる旧字体）に当たる。
- Unihan データベースは Unicode License v3 で公開されている。著作権表示と許諾表示を [LICENSE-UNICODE](LICENSE-UNICODE) に置いた。
- 出典: https://www.unicode.org/Public/UCD/latest/ucd/Unihan.zip （2026-11-11 に取得）

## 当時の文書での用例（典拠）

`kanji.tsv` の列 `evidence`・`evidence_url` は、その旧字体が明治〜昭和 20 年ごろの公文書で使われていたことの典拠である。

- ウィキソース日本語版の翻刻（`https://ja.wikisource.org/w/index.php?oldid=...`）
  - 法令・詔勅などの翻刻で、原典は著作権法 13 条により権利の対象外か、保護期間が満了している。
  - このスキルには、翻刻の本文を入れていない。字が出てくるページの名前と、版の URL だけを記録した。
  - ウィキソースの編集者が付けた注・現代語訳は使っていない。
- 国立国会図書館デジタルコレクション（`https://dl.ndl.go.jp/pid/...`）
  - 官報の画像（インターネット公開・保護期間満了）で、字の形を確かめた。
  - このスキルには、画像を入れていない。

## 法令の用例

- `references/grammar.md` の条文の用例は、e-Gov 法令検索の「明治十七年太政官布告第三十二号（爆発物取締罰則）」から引用した。法令は著作権法 13 条により権利の対象外である。
- 出典: e-Gov 法令検索（https://laws.e-gov.go.jp/law/117DF1000000032 ）。2026-11-11 に法令 API で取得した。
