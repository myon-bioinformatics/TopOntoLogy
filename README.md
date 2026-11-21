# TopOntoLogy

**TopOntoLogy** は、Topology（トポロジー）と Ontology（オントロジー）を組み合わせた造語。

トポロジーが扱う「構造とつながり」と、オントロジーが扱う「意味と概念の体系」。
その対応関係を探るためのプロジェクトです。

## 関係の意味ごとに構造を調べる

OTL の nodes/edges JSON と TOO の分類表は引数で渡します。引数を省略したときは環境変数 `TOPONTOLOGY_GRAPH`、`TOPONTOLOGY_CLASSIFICATION`、`TOPONTOLOGY_PROFILE` を使います。引数が優先です。ONTO の `knowledgegraph.py` は `PYTHONPATH` で渡します。プログラムは隣の checkout を探しません。Python 3.11 以降の標準ライブラリだけを使います。

```sh
PYTHONPATH=../ONTO python -S topontology.py audit.json ../TaxOntOy/otl-relations.json audit
PYTHONPATH=../ONTO python -S topontology.py audit.json ../TaxOntOy/otl-relations.json audit --format text
PYTHONPATH=../ONTO python -S topontology.py before.json ../TaxOntOy/otl-relations.json concepts --compare after.json
PYTHONPATH=../ONTO python -S topontology.py audit.json ../TaxOntOy/otl-relations.json concepts --format mermaid --class hierarchy
TOPONTOLOGY_GRAPH=audit.json TOPONTOLOGY_CLASSIFICATION=../TaxOntOy/otl-relations.json TOPONTOLOGY_PROFILE=audit \
  PYTHONPATH=../ONTO python -S topontology.py --format text
```

導入した環境ではコマンド名は `topontology` です。読み取り専用で、ネットワークへは出ません。書き出すのは標準出力か、`-o` で指定したファイルだけです。

分類ごとに次を返します。

- 辺数、端点に現れるノード数、異なる複数の始点から到達する終点 ID
- 代表的な閉路（既定は最大 5 件、`--cycle-limit` で変更）。検出は ONTO の `relation_cycles`
- 次数の要約（入・出・合計の最大と、始点・終点の数）
- 弱連結成分の個数と大きさ
- 最長の有向路の長さ。ONTO の `topological_order`（`graphlib.TopologicalSorter`）が閉路を見つけた分類では `depth` は null で、`order_cycle` にその閉路を残します
- 独立閉路 `independent_cycles`。辺数を E、端点の数を V、弱連結成分の数を C として E − V + C。有向閉路がなくても、無向にしたときに近道があると 1 以上になります。同梱の hierarchy は A→B、B→C、A→C で、有向閉路はなく深さは 2、独立閉路は 1 です

分類表にない辺の数も返します。類似関係の閉路は相互リンクでも生じます。階層の循環を調べるときは hierarchy 欄を使います。

## 出力

どの出力にもツール名と版、入力したグラフと分類表のパス、それぞれの sha256 が入ります。比較では左右の両方を記録します。Mermaid では図の先頭コメントです。

JSON はキー順を固定した一つの文書です。`--format canonical` と `--format json` は同じバイト列で、`--json` は `--format json` の省略です。既定は json です。`--format text` は同じ内容の行です。

`--format mermaid --class 分類名` は、その分類の辺だけを ONTO の `graph_mermaid` で描きます。

`--compare 別のグラフ` は、同じ分類表と同じプロファイルで二つのグラフを比べ、差分は右から左を引いた値です。`--class 左 --compare-class 右` は、一つのグラフの二つの分類を比べます。

`tests/expected/` は、この決定的な出力を fixture から再生成したものです。CI が同じコマンドを実行し、差分があれば失敗します。

ノード ID は JSON の型のまま集計します。数値の `1` と文字列の `"1"` は別の頂点です。弱連結成分の起点や並びは `(型名, 文字列化した値)` で決め、比較できない型が混ざっても落ちません。`--format text` と Mermaid では表示のために文字列化します。同じ表示になる ID が複数あるとき、Mermaid の枠ラベルでも文字列以外を `型名:値` と書いて区別します（名前が重複しない節は、そのまま出します）。JSON では元の型のまま出します。

辺に `kind` も `relation` もないときは、トレースバックではなく `ValueError` を標準エラーに出して終了コード 1 で終わります。

## 互換

OnTautoLogy の `examples/wave_relations.py` は `from topology import summarize` を使います。その関数だけ残してあります。コマンドとしては `topology.py` は動きません。

## テスト

```sh
PYTHONPATH=../ONTO uv run --no-project python -S -m unittest discover -s tests -v
```

CI は Python 3.11 と 3.15 でこのコマンドを実行します。ONTO は main の first-parent マージ `8ca1ee5dc8c2714ddcdea6db6774f7168c0e617b`、TaxOntOy は main の first-parent マージ `78ed91aee522e9a7305e2bf964dab9be2bd3fff0` を checkout し、`PYTHONPATH` で ONTO を渡します。
