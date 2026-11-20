# TopOntOlogy

**TopOntOlogy** は、Topology（トポロジー）と Ontology（オントロジー）を組み合わせた造語。

トポロジーが扱う「構造とつながり」と、オントロジーが扱う「意味と概念の体系」。
その対応関係を探るためのプロジェクトです。

## Status

立ち上げ中。定義と基本的な考え方をここに整理していきます。

## 関係の意味ごとに構造を調べる

同じディレクトリに ONTO、TaxOntOy、TopOntoLogy を checkout し、TopOntoLogy から実行します。Python 3.11 以降の標準ライブラリと ONTO の `knowledgegraph.py` を使います。

```sh
PYTHONPATH=../ONTO python -S topology.py audit.json ../TaxOntOy/otl-relations.json audit
```

OTL の nodes/edges JSON と TOO の分類表を受け取り、分類ごとに辺数、端点に現れるノード数、異なる複数の始点から到達する終点 ID、代表的な閉路（最大5件）を JSON で返します。分類表にない辺の数も返します。類似関係の閉路は相互リンクでも生じます。階層の循環を調べるときは hierarchy 欄を使います。
