"""コマンドライン。

python -m apdl_trace convert <元マクロのディレクトリ> <出力ディレクトリ>
python -m apdl_trace extract <.out> --map <trace_map.json> -o <レポート>
python -m apdl_trace probe -o <プローブ用マクロ>
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from apdl_trace import __version__


def _cmd_convert(args: argparse.Namespace) -> int:
    from apdl_trace.convert import Converter, ConvertOptions

    exts = tuple(
        "" if e in ("", "none") else e if e.startswith(".") else "." + e
        for e in (args.ext or [".mac", ".inp", "none"])
    )
    opts = ConvertOptions(
        exts=exts,
        level=args.level,
        dryrun=not args.no_dryrun,
        ulib=tuple(args.ulib or ()),
        cmd_trace=args.cmd_trace,
        bulk_threshold=args.bulk_threshold,
        disable_nopr=args.disable_nopr,
        lists_gopr=args.lists_gopr,
        encoding=args.encoding,
    )
    src = Path(args.src)
    out = Path(args.out)
    if src.resolve() == out.resolve():
        print("出力先は元マクロと別のディレクトリにすること", file=sys.stderr)
        return 2
    conv = Converter(src, out, opts)
    conv.run()
    print(f"変換: {len(conv.files)} ファイル → {out}")
    print(f"トレースID: {len(conv.tmap.entries)} 件")
    if conv.warnings:
        print(f"警告: {len(conv.warnings)} 件（{out / 'convert_warnings.txt'}）")
    return 0


def _cmd_extract(args: argparse.Namespace) -> int:
    from apdl_trace.extract import ExtractOptions, run_extract

    opts = ExtractOptions(
        expand_all=args.all,
        expand_ids={int(x) for x in args.expand or ()},
        files_dir=Path(args.files_dir) if args.files_dir else None,
        dict_path=Path(args.dict) if args.dict else None,
        theta_eps=args.theta_eps,
        state_mode=args.state,
    )
    text = run_extract(Path(args.out_file), Path(args.map), opts)
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"レポート: {args.output}")
    else:
        sys.stdout.write(text)
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    from apdl_trace.probe import build_probe

    Path(args.output).write_text(build_probe(), encoding="latin-1", newline="\n")
    print(f"プローブ用マクロ: {args.output}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="apdl_trace", description="APDL マクロのトレーサー"
    )
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="command", required=True)

    c = sub.add_parser("convert", help="トレース用の変換版マクロを作る")
    c.add_argument("src", help="元マクロのディレクトリ")
    c.add_argument("out", help="変換版の出力ディレクトリ")
    c.add_argument(
        "--ext",
        action="append",
        help="対象の拡張子（複数可）。拡張子なしは none。既定: .mac .inp none",
    )
    c.add_argument(
        "--level", choices=["light", "standard", "detail"], default="standard"
    )
    c.add_argument(
        "--no-dryrun", action="store_true", help="SOLVE を置き換えずに実行する"
    )
    c.add_argument("--ulib", action="append", help="*ULIB のライブラリファイル名")
    c.add_argument(
        "--cmd-trace",
        choices=["all", "key"],
        default="all",
        help="all: すべてのコマンドの通過を記録 / key: 主要なコマンドだけ",
    )
    c.add_argument(
        "--bulk-threshold",
        type=int,
        default=500,
        help="N / E がこれより多いファイルでは、N / E をトレースしない",
    )
    c.add_argument(
        "--disable-nopr",
        action="store_true",
        help="/NOPR をコメントにする（トレース出力が抑止される場合に使う）",
    )
    c.add_argument(
        "--lists-gopr",
        action="store_true",
        help="定義一覧（ETLIST など）を /GOPR で出す",
    )
    c.add_argument("--encoding", default="latin-1", help=argparse.SUPPRESS)
    c.set_defaults(func=_cmd_convert)

    e = sub.add_parser("extract", help=".out からトレースレポートを作る")
    e.add_argument("out_file", help="MAPDL の出力ファイル（.out）")
    e.add_argument("--map", required=True, help="trace_map.json")
    e.add_argument("-o", "--output", help="レポートの出力先（省略時は標準出力）")
    e.add_argument("--all", action="store_true", help="ループを全回展開する")
    e.add_argument(
        "--expand", action="append", help="指定したトレースIDのループだけ全回展開する"
    )
    e.add_argument("--files-dir", help="CBDOF / BFINT の出力ファイルがあるディレクトリ")
    e.add_argument("--dict", help="引数辞書（既定: 同梱の apdl_dict.json）")
    e.add_argument(
        "--theta-eps",
        type=float,
        default=0.01,
        help="θ範囲を「ほぼ0」とみなすしきい値（度）",
    )
    e.add_argument(
        "--state",
        choices=["changed", "always"],
        default="changed",
        help="状態欄を変化時だけ出すか、毎行出すか",
    )
    e.set_defaults(func=_cmd_extract)

    pr = sub.add_parser("probe", help="実機の挙動を確かめるマクロを作る")
    pr.add_argument("-o", "--output", default="trace_probe.inp")
    pr.set_defaults(func=_cmd_probe)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
