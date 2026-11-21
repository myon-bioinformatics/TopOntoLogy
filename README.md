# TopOntoLogy

**TopOntoLogy** は、Topology（トポロジー）と Ontology（オントロジー）を組み合わせた造語。

トポロジーが扱う「構造とつながり」と、オントロジーが扱う「意味と概念の体系」。
その対応関係を探るためのプロジェクトです。

## 関係の意味ごとに構造を調べる

同じディレクトリに ONTO、TaxOntOy、TopOntoLogy を checkout し、TopOntoLogy から実行します。Python 3.11 以降の標準ライブラリと ONTO の `knowledgegraph.py` を使います。

```sh
PYTHONPATH=../ONTO python -S topology.py audit.json ../TaxOntOy/otl-relations.json audit
PYTHONPATH=../ONTO python -S topology.py audit.json ../TaxOntOy/otl-relations.json audit --format text
```

OTL の nodes/edges JSON と TOO の分類表を受け取り、分類ごとに次を返します。

- 辺数、端点に現れるノード数、異なる複数の始点から到達する終点 ID
- 代表的な閉路（既定は最大5件、`--cycle-limit` で変更）
- 次数の要約（入・出・合計の最大と、始点・終点の数）
- 弱連結成分の個数と大きさ
- 最長の有向路の長さ（閉路がある分類では `depth` は null で、`order_cycle` に閉路を残します）

分類表にない辺の数も返します。類似関係の閉路は相互リンクでも生じます。階層の循環を調べるときは hierarchy 欄を使います。閉路検出とトポロジカル順は ONTO の `relation_cycles` と `topological_order` を再利用します。

既定の出力は JSON です。`--format text` で読みやすい摘要、`--json` は `--format json` の省略形です。

CI は ONTO のコミット `8ca1ee5dc8c2714ddcdea6db6774f7168c0e617b` を同じワークスペースに checkout して検査します。
