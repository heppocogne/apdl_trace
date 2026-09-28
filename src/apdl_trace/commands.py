"""APDL コマンド名の判定と、トレースでの扱いの分類。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CmdSpec:
    """コマンドごとのトレースでの扱い。

    フィールド番号はコマンド名を除いた 1 始まり（`NSEL,S,LOC` なら S が 1）。
    """

    name: str
    cat: str
    # ラベル（文字列の選択肢）を取るフィールド。実行時の値を出さない
    labels: frozenset[int] = frozenset()
    # 節点番号・要素番号を取るフィールド。座標や属性に展開する
    nodes: tuple[int, ...] = ()
    elems: tuple[int, ...] = ()
    # 選択を変える実体（N/E/K/L/A/V）
    sel: str = ""


def _s(
    name: str,
    cat: str,
    labels: tuple[int, ...] = (),
    nodes: tuple[int, ...] = (),
    elems: tuple[int, ...] = (),
    sel: str = "",
) -> CmdSpec:
    return CmdSpec(name, cat, frozenset(labels), nodes, elems, sel)


_ALL_ENT = "NEKLAV"

_SPECS: list[CmdSpec] = [
    # 選択
    _s("NSEL", "select", (1, 2, 3, 7), sel="N"),
    _s("ESEL", "select", (1, 2, 3, 7), sel="E"),
    _s("KSEL", "select", (1, 2, 3, 7), sel="K"),
    _s("LSEL", "select", (1, 2, 3, 7), sel="L"),
    _s("ASEL", "select", (1, 2, 3, 7), sel="A"),
    _s("VSEL", "select", (1, 2, 3, 7), sel="V"),
    _s("CMSEL", "select", (1, 2, 3), sel=_ALL_ENT),
    _s("ALLSEL", "select", (1, 2), sel=_ALL_ENT),
    _s("NSLE", "select", (1, 2), sel="N"),
    _s("NSLA", "select", (1,), sel="N"),
    _s("NSLL", "select", (1,), sel="N"),
    _s("NSLK", "select", (1,), sel="N"),
    _s("NSLV", "select", (1,), sel="N"),
    _s("ESLN", "select", (1, 3), sel="E"),
    _s("ESLA", "select", (1,), sel="E"),
    _s("ESLL", "select", (1,), sel="E"),
    _s("ESLV", "select", (1,), sel="E"),
    _s("KSLL", "select", (1,), sel="K"),
    _s("KSLN", "select", (1,), sel="K"),
    _s("LSLA", "select", (1,), sel="L"),
    _s("LSLK", "select", (1,), sel="L"),
    _s("ASLL", "select", (1,), sel="A"),
    _s("ASLV", "select", (1,), sel="A"),
    _s("VSLA", "select", (1,), sel="V"),
    # 処理系
    _s("/PREP7", "processor"),
    _s("/SOLU", "processor"),
    _s("/POST1", "processor"),
    _s("/POST26", "processor"),
    _s("FINISH", "processor"),
    # ジョブ・DB
    _s("/FILNAME", "jobname", (1,)),
    _s("RESUME", "resume", (1, 2)),
    _s("/CLEAR", "clear", (1,)),
    _s("SAVE", "save", (1, 2, 4)),
    # 座標系
    _s("CSYS", "csys"),
    _s("LOCAL", "csys"),
    _s("CLOCAL", "csys"),
    _s("RSYS", "rsys", (1,)),
    _s("CSKP", "csys"),
    _s("CSWPLA", "csys"),
    _s("NROTAT", "nrotat", nodes=(1, 2)),
    _s("ESYS", "attr"),
    _s("NMODIF", "coordmod", nodes=(1,)),
    _s("TRANSFER", "coordmod", nodes=(3, 4)),
    _s("NGEN", "coordmod", nodes=(3, 4)),
    _s("N", "coordmod"),
    # 定義
    _s("ET", "def", (2,)),
    _s("KEYOPT", "def"),
    _s("R", "def"),
    _s("RMORE", "def"),
    _s("RMODIF", "def"),
    _s("MP", "def", (1,)),
    _s("MPTEMP", "def"),
    _s("MPDATA", "def", (1,)),
    _s("TB", "def", (1, 5)),
    _s("TBTEMP", "def"),
    _s("TBDATA", "def"),
    _s("SECTYPE", "def", (2, 3, 4)),
    _s("SECDATA", "def"),
    _s("TYPE", "attr"),
    _s("MAT", "attr"),
    _s("REAL", "attr"),
    _s("SECNUM", "attr"),
    # 要素生成
    _s("E", "egen"),
    _s("EINTF", "egen", (3,)),
    _s("EGEN", "egen"),
    _s("ESURF", "egen", (2, 3)),
    _s("AMESH", "egen"),
    _s("VMESH", "egen"),
    _s("VSWEEP", "egen"),
    _s("LMESH", "egen"),
    _s("VDRAG", "egen"),
    _s("VEXT", "egen"),
    _s("VROT", "egen"),
    _s("EMODIF", "egen", (2,), elems=(1,)),
    _s("EDELE", "egen", elems=(1, 2)),
    _s("PSMESH", "egen", (2, 4, 12, 13)),
    _s("NUMMRG", "egen", (1, 4, 5)),
    # 荷重・拘束
    _s("D", "load", (2, 7, 8, 9, 10, 11, 12), nodes=(1, 5)),
    _s("DDELE", "load", (2,), nodes=(1, 3)),
    _s("DA", "load", (2,)),
    _s("DL", "load", (3,)),
    _s("DK", "load", (2, 7, 8, 9, 10, 11, 12)),
    _s("F", "load", (2,), nodes=(1, 5)),
    _s("FDELE", "load", (2,), nodes=(1, 3)),
    _s("FK", "load", (2,)),
    _s("SF", "load", (2,)),
    _s("SFA", "load", (3,)),
    _s("SFL", "load", (2,)),
    _s("SFE", "load", (3,), elems=(1,)),
    _s("SFDELE", "load", (2,)),
    _s("BF", "load", (2,), nodes=(1,)),
    _s("BFE", "load", (2,), elems=(1,)),
    _s("BFDELE", "load", (2,), nodes=(1,)),
    _s("BFUNIF", "load", (1,)),
    _s("TUNIF", "load"),
    _s("TREF", "load"),
    _s("CP", "load", (2,), nodes=(3, 4, 5, 6, 7, 8, 9, 10)),
    _s("CE", "load", (4, 7, 10, 13, 16, 19), nodes=(3, 6, 9)),
    _s("CEINTF", "load", (2, 3, 4, 5, 6, 7)),
    _s("SLOAD", "load", (2, 3, 4)),
    _s("OMEGA", "load"),
    _s("CMOMEGA", "load", (1,)),
    _s("ACEL", "load"),
    # マッピング
    _s("NWRITE", "map", (1, 2)),
    _s("CBDOF", "map", (1, 2, 4, 5, 8)),
    _s("BFINT", "map", (1, 2, 4, 5, 8)),
    _s("FILE", "map", (1, 2)),
    _s("SET", "map"),
    _s("/INPUT", "input", (1, 2, 3)),
    # コンポーネント
    _s("CM", "comp", (1, 2)),
    _s("CMGRP", "comp", tuple(range(1, 10))),
    _s("CMDELE", "comp", (1,)),
    # パラメータ
    _s("*SET", "set", (1,)),
    _s("*GET", "get", (1, 2, 4, 5, 6, 7)),
    _s("*DIM", "dim", (1, 2, 6, 7, 8)),
    _s("*DEL", "output", (1, 2, 3)),
    _s("*VGET", "vec", (1, 2, 4, 5, 6, 7)),
    _s("*VFUN", "vec", (1, 2, 3)),
    _s("*VOPER", "vec", (1, 2, 3, 4)),
    _s("*VFILL", "vec", (1, 2)),
    # 制御
    _s("*DO", "do", (1,)),
    _s("*DOWHILE", "dowhile", (1,)),
    _s("*ENDDO", "enddo"),
    _s("*IF", "if", (2, 4, 6, 8, 9)),
    _s("*ELSEIF", "elseif", (2, 4, 6, 8)),
    _s("*ELSE", "else"),
    _s("*ENDIF", "endif"),
    _s("*USE", "use", (1,)),
    _s("*CREATE", "create", (1, 2)),
    _s("*END", "end"),
    _s("*ULIB", "ulib", (1, 2, 3)),
    _s("*GO", "go", (1,)),
    _s("*EXIT", "exit"),
    _s("*CYCLE", "cycle"),
    _s("*RETURN", "return"),
    _s("/EOF", "eof"),
    # 荷重ステップ
    _s("ANTYPE", "step", (1, 2, 5)),
    _s("NLGEOM", "step", (1,)),
    _s("TIME", "step"),
    _s("NSUBST", "step", (4,)),
    _s("DELTIM", "step", (4,)),
    _s("AUTOTS", "step", (1,)),
    _s("KBC", "step"),
    _s("OUTRES", "step", (1, 2, 3)),
    _s("LSWRITE", "step"),
    _s("SOLVE", "solve"),
    _s("LSSOLVE", "solve"),
    # 出力の制御（トレース出力に影響しうる）
    _s("/NOPR", "output"),
    _s("/GOPR", "output"),
    _s("/OUTPUT", "output", (1, 2, 3, 4)),
    # 書式行を伴うコマンド
    _s("*VWRITE", "fmtcmd"),
    _s("*MSG", "fmtcmd", (1,)),
    _s("*VREAD", "fmtcmd", (1, 2, 3, 4)),
    _s("*MWRITE", "fmtcmd", (1, 2, 3, 4)),
    _s("*MREAD", "fmtcmd", (1, 2, 3, 4)),
    # データ行を伴うコマンド
    _s("NBLOCK", "block", tuple(range(1, 10))),
    _s("EBLOCK", "block", tuple(range(1, 10))),
    _s("CMBLOCK", "block", tuple(range(1, 10))),
    _s("SFEBLOCK", "block", tuple(range(1, 10))),
    _s("BFBLOCK", "block", tuple(range(1, 10))),
    _s("BFEBLOCK", "block", tuple(range(1, 10))),
    # コメント扱い
    _s("/COM", "comment"),
    # 結果を読むコマンド（ドライランで値が変わる）
    _s("ETABLE", "result", tuple(range(1, 10))),
    _s("PRNSOL", "result", tuple(range(1, 10))),
    _s("PRESOL", "result", tuple(range(1, 10))),
    _s("PRRSOL", "result", tuple(range(1, 10))),
    _s("NSORT", "result", tuple(range(1, 10))),
    _s("ESORT", "result", tuple(range(1, 10))),
    _s("FSUM", "result", tuple(range(1, 10))),
    _s("PLNSOL", "result", tuple(range(1, 10))),
    _s("PLESOL", "result", tuple(range(1, 10))),
    _s("/BATCH", "other", (1,)),
]

SPECS: dict[str, CmdSpec] = {s.name: s for s in _SPECS}

_BY_PREFIX: dict[str, list[str]] = {}
for _name in SPECS:
    if len(_name) >= 4:
        _BY_PREFIX.setdefault(_name[:4], []).append(_name)


def canonical(token: str) -> str | None:
    """コマンド名を正規化する。登録外なら None。

    4 文字以上は先頭 4 文字、4 文字未満は完全一致で判定する。
    `*ELSE` と `*ELSEIF` のように先頭 4 文字が同じものは、完全一致を優先し、
    次に前方一致で絞り込む。
    """
    t = token.strip().upper()
    if not t:
        return None
    if t in SPECS:
        return t
    if len(t) < 4:
        return None
    cands = _BY_PREFIX.get(t[:4], [])
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    for c in cands:
        if c.startswith(t) or t.startswith(c):
            # "*END" のような短い正式名より、入力を含む長い名前を優先する
            longer = [d for d in cands if d.startswith(t)]
            return longer[0] if longer else c
    return cands[0]


def spec(name: str) -> CmdSpec | None:
    c = canonical(name)
    return SPECS[c] if c else None


# `$` で分割しない（行全体がひとつの引数になる）コマンド
NO_SPLIT = frozenset({"/COM"})
NO_SPLIT_RAW = ("/TIT", "/STI", "/SYP", "/SYS")

FORMAT_LINE_COMMANDS = frozenset({"*VWRITE", "*MSG", "*VREAD", "*MWRITE", "*MREAD"})
BLOCK_COMMANDS = frozenset(
    {"NBLOCK", "EBLOCK", "CMBLOCK", "SFEBLOCK", "BFBLOCK", "BFEBLOCK"}
)

# 行数が多いファイルでトレースを省く、モデル生成のためだけのコマンド
BULK_COMMANDS = frozenset({"N", "E"})

# 座標を変えうるコマンド（チェックポイントの変化の候補）
COORD_CHANGING = frozenset(
    {"NMODIF", "TRANSFER", "NGEN", "N", "LOCAL", "CLOCAL", "NUMMRG"}
)

# ブロック構造や呼び出しで、挿入位置をずらせないもの
STRUCTURAL_CATS = frozenset(
    {
        "do",
        "dowhile",
        "enddo",
        "if",
        "elseif",
        "else",
        "endif",
        "create",
        "end",
        "go",
        "exit",
        "cycle",
        "return",
        "eof",
        "use",
        "input",
        "call",
    }
)
