# APDL trace
APDLのマクロにトレース用のコマンドを挿入し、理解を助けるツール

元のマクロは変更せず、トレース用の `*MSG` などを挿入した変換版を別のディレクトリに作ります。
変換版を MAPDL でバッチ実行し、出力（`.out`）からレポートを作ります。
Python 3.11 以上の標準ライブラリだけで動きます。

## 使い方

**方法1：直接実行（推奨・setuptools 不要）**

```bash
export PYTHONPATH=<このディレクトリ>/src
python -m apdl_trace convert <元マクロのディレクトリ> <出力ディレクトリ>
python -m apdl_trace extract <実行結果の .out> --map <出力ディレクトリ>/trace_map.json -o report.txt
python -m apdl_trace probe -o trace_probe.inp
```

**方法2：インストール後に実行（setuptools が必要）**

```bash
pip install -e .
python -m apdl_trace convert <元マクロのディレクトリ> <出力ディレクトリ>
python -m apdl_trace extract <実行結果の .out> --map <出力ディレクトリ>/trace_map.json -o report.txt
python -m apdl_trace probe -o trace_probe.inp
```

### convert の主なオプション

| オプション | 内容 |
|---|---|
| `--ext` | 対象の拡張子（複数可）。拡張子なしは `none`。既定は `.mac` `.inp` `none` |
| `--level` | `light`（件数のみ）/ `standard`（既定。座標範囲・属性の内訳も）/ `detail`（R / θ 範囲・重心範囲も） |
| `--no-dryrun` | `SOLVE` を置き換えずに実行する（既定は置き換えて実行しない） |
| `--ulib` | `*ULIB` のライブラリファイル名（マクロ内の `*ULIB` からも自動で拾う） |
| `--cmd-trace key` | 主要なコマンドだけ通過を記録する（既定は全コマンド） |
| `--bulk-threshold` | `N` / `E` がこの数より多いファイルでは、それらをトレースしない（既定 500） |
| `--disable-nopr` | `/NOPR` をコメントにする（トレース出力が抑止される場合に使う） |

変換時の注意（SOLVE の後で結果を読んでいる箇所など）は `<出力ディレクトリ>/convert_warnings.txt` に出ます。

### extract の主なオプション

| オプション | 内容 |
|---|---|
| `--all` | ループを全回展開する（既定は初回と最終回のみ） |
| `--expand <ID>` | 指定したループだけ全回展開する（ID はレポートのループ見出しに出る） |
| `--files-dir` | CBDOF / BFINT の出力ファイルを探すディレクトリ（既定は `.out` と同じ場所） |
| `--dict` | 引数辞書を差し替える（既定は同梱の `apdl_dict.json`） |
| `--theta-eps` | θ範囲を「ほぼ0」とみなすしきい値（度、既定 0.01） |

## 実機での事前確認（probe）

変換は MAPDL のいくつかの挙動を前提にしています（`*MSG` の出力のされ方、`*GET` の座標の基準、
`*VOPER` の定数引数など）。本番のマクロで使う前に、確認用のマクロを実行して結果を確かめてください。

```
python -m apdl_trace probe -o trace_probe.inp
```

小さなモデルで各項目を 1 つずつ実行し、`PROBE|` と `TRACE|` で始まる行を出します。
マクロ内のコメントに、期待する値を書いてあります。

## 引数辞書

`src/apdl_trace/data/apdl_dict.json` に、コマンドごとの引数名・値の意味・説明文のテンプレートがあります。
手で追記できます。辞書にないコマンドはレポートの付録に出現回数が出るので、追加の候補にしてください。

## 開発

```
pip install -e ".[dev]"
pytest
ruff check .
ruff format .
```
