# 関係の性質と反例の形式検証

`formal/PropertyWitnesses.lean` は、`topontology.evaluate_properties()` が返す反例の意味を
形式化します。対象は対称性、推移性の検査、逆関係、端点の非共有、分類に含まれる関係の和集合です。
数学モデルの証明と、実際の Python の反例探索は接続テストで結びます。

共通ラッパー `lean_verify.py` と Lean 専用 CI は
[親リポジトリ](https://github.com/myon-bioinformatics/myon-bioinformatics/blob/27dedd9651efc816fa88d8922adb5f242725df69/lean_verify.py)
で管理します。通常実行の依存は追加せず、このリポジトリには固有の証明とテストを置きます。

## 推移性の正確な範囲

既存の `transitive` 検査は、選択した関係・分類の辺に `a→b→c` があり、
**a と c が異なるときに**直結辺 `a→c` があるかを調べます。
これは OnTautoLogy の `transitivity_witness` に明示された仕様です。
`a→b→a` という往復だけでは自己ループを要求しません。

Lean ではこの条件を `ChordClosed R` として表します。証明した対応は次です。

$$
\left[\forall a,b,c,\; R(a,b)\land R(b,c)\land a\ne c\Rightarrow R(a,c)\right]
\quad\Longleftrightarrow\quad
\operatorname{Transitive}(R\cup I),
$$

ここで `I` はすべての自己ループです。つまり、入力に自己ループを補った関係の推移性に相当します。
入力の関係そのものに通常の推移性がある、という主張には拡張しません。

## 証明と実装の対応

| 定理 | 保証する内容 | 対応箇所 |
| --- | --- | --- |
| `TopOntoLogy.symmetry_witness_refutes` | 辺があり、その逆向きがなければ対称性は成立しない | `_symmetry_witness` |
| `TopOntoLogy.chord_witness_refutes` | 二段経路の異なる始終点を結ぶ辺がなければ `ChordClosed` は成立しない | `_transitive_witness` |
| `TopOntoLogy.chord_closed_iff_reflexive_closure_transitive` | 直結辺の条件と、自己ループを補った関係の推移性が同値 | `transitive` の仕様 |
| `TopOntoLogy.chord_witness_refutes_closure` | 返された欠損辺の反例は、自己ループを補った関係の推移性も否定する | `_transitive_witness` |
| `TopOntoLogy.inverse_witness_refutes` | どちら側の関係でも、対応する逆向きの辺がなければ逆関係ではない | `_inverse_witness` の両方向の検査 |
| `TopOntoLogy.endpoint_witness_refutes` | 両関係の辺に共通の端点があれば、端点集合は交わる | `_disjoint_witness` |
| `TopOntoLogy.class_union_symmetric` | 分類に含まれる各関係が対称なら、その分類全体も対称 | `_edges_of_class` と対称性検査 |

`disjoint_with` は端点集合の非共有を調べます。二つの辺が別物でも、`A→B` と `B→C` のように
端点を共有すれば失敗します。Lean の定義もこの仕様に合わせています。

分類全体の検査では、同じ分類に属する複数の関係の辺を合わせます。
そのため `r:A→B` と `s:B→A` は、各関係では非対称でも、分類全体では対称です。
一方、`r:A→B` と `s:B→C` は、各関係の直結辺の条件を満たしていても、
同じ分類にまとめると `A→C` が必要になります。推移性の検査結果は分類の境界に依存します。

## Python 実装との接続

`tests/test_formal_bridge.py` は `evaluate_properties()` を実際に呼び、
独立した集合の関係演算と照合します。期待値を実装の反例探索から作りません。

- 自己ループを含む **3 頂点の全 512 有向グラフ**で、対称性と自己ループを補った関係の推移性を比較。
- **2 頂点の二関係の全 256 組**で、逆関係と端点集合の非共有を比較。
- 失敗した判定について、返された辺が入力にあり、欠けた逆向き・直結辺がないことを再検査。
- 同じ分類の関係を混ぜた経路、分類外の直結辺、分類全体で成立する対称性を検査。
- 辺の重複、順序、孤立ノードが判定を変えないことを検査。
- 整数 `1` と文字列 `"1"` を区別し、非対応の端点では `acyclic`・`transitive` が
  `holds=None` / `not applicable` になる境界を確認。

テストは 7 件です。既存の ONTO・OnTautoLogy の checkout を通常どおり兄弟位置に置くか、
`ONTO_ROOT`・`OTL_ROOT` で指定します。分類表はテスト自身で与えるため、この接続テストには
TaxOntOy の checkout は必要ありません。Lean や追加の Python パッケージも不要です。

```sh
python -S -m unittest discover -s tests -p test_formal_bridge.py -v
```

## Lean 専用の検証

`formal/lean-toolchain` は `leanprover/lean4:v4.36.0` です。
Lean 同梱の `Std` のみを使い、Mathlib や兄弟リポジトリの Lean ファイルは不要です。
親リポジトリを隣の `myon-bioinformatics` に置いた場合、次のように検証できます。

```sh
python -S ../myon-bioinformatics/lean_verify.py \
  --cwd formal --required --json-output build/lean-verification.json \
  --theorem TopOntoLogy.symmetry_witness_refutes \
  --theorem TopOntoLogy.chord_witness_refutes \
  --theorem TopOntoLogy.chord_closed_iff_reflexive_closure_transitive \
  --theorem TopOntoLogy.chord_witness_refutes_closure \
  --theorem TopOntoLogy.inverse_witness_refutes \
  --theorem TopOntoLogy.endpoint_witness_refutes \
  --theorem TopOntoLogy.class_union_symmetric \
  PropertyWitnesses.lean
```

共有ラッパーは Lean 未導入を `unverified` と記録します。`--required` を使う専用 CI は
未検証や証明失敗を失敗として扱い、通常の Python テストは Lean を呼びません。
定理の公理依存も監査します。自己ループを補った関係に関する 2 定理は Lean の標準的な
`Classical.choice`・`propext`・`Quot.sound` に依存し、それ以外の 5 定理には公理依存がありません。

## 保証の境界

証明対象は、選択された辺集合を関係として解釈したときの性質です。
Python の全実行、反例探索の完全性・最小性・選択順序、閉路探索、入力の解析、
分類表の意味そのものは形式証明していません。有限グラフの接続テストも全入力の保証ではありません。

推移性の探索は既存の OnTautoLogy の実装を使います。証明成功だけでは、将来の実装変更と
このモデルの対応は保証されないため、接続テストを通常の回帰検査として残します。
また、入力に辺がないという判定は、現実にその関係が存在しないことの証明ではありません。
自然言語の概念の意味や、宣言した性質がその概念に適切かどうかは今回の対象外です。
