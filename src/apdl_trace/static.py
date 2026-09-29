"""静的解析: マクロを実行せずに読み、選択条件・取得している値・座標系を追う簡易レポート。

変数の値は追わない（式のまま表示する）。*IF は各分岐を読み、*ENDIF の時点で状態が
分岐によって違えば「分岐により異なる」とする。ループの中は 1 回分だけ読み、*GO の
ジャンプは追わない（上から順に読む）。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from apdl_trace import commands, lexer
from apdl_trace.analyze import _SEL_ENT, ENT_NAME, ENT_OF_ITEM, sel_term
from apdl_trace.convert import display
from apdl_trace.dictionary import ArgVal, Dictionary, fmt_num, parse_num
from apdl_trace.lexer import Stmt

# ---- 選択条件の木 ----
# ("all",) / ("none",) / ("t", 文) / ("and", 子) / ("or", 子) / ("minus", a, b)
# / ("inv", a) / ("branch", 子)
Cond = tuple
ALL: Cond = ("all",)
NONE: Cond = ("none",)


def c_and(a: Cond, b: Cond) -> Cond:
    if a == ALL:
        return b
    if b == ALL:
        return a
    if NONE in (a, b):
        return NONE
    items = (a[1] if a[0] == "and" else (a,)) + (b[1] if b[0] == "and" else (b,))
    return ("and", items)


def c_or(a: Cond, b: Cond) -> Cond:
    if a == NONE:
        return b
    if b == NONE:
        return a
    if ALL in (a, b):
        return ALL
    items = (a[1] if a[0] == "or" else (a,)) + (b[1] if b[0] == "or" else (b,))
    return ("or", items)


def c_minus(a: Cond, b: Cond) -> Cond:
    if a == NONE or b == ALL:
        return NONE
    if b == NONE:
        return a
    return ("minus", a, b)


def c_inv(a: Cond) -> Cond:
    if a == ALL:
        return NONE
    if a == NONE:
        return ALL
    if a[0] == "inv":
        return a[1]
    return ("inv", a)


def render(c: Cond, top: bool = True) -> str:
    k = c[0]
    if k == "all":
        return "全体"
    if k == "none":
        return "なし"
    if k == "t":
        return c[1]
    if k == "and":
        s = " かつ ".join(render(x, False) for x in c[1])
    elif k == "or":
        s = " または ".join(render(x, False) for x in c[1])
    elif k == "minus":
        s = f"{render(c[1], False)} から {render(c[2], False)} を除く"
    elif k == "inv":
        s = f"{render(c[1], False)} 以外"
    elif k == "branch":
        return "分岐により異なる: " + " ／ ".join(f"〔{render(x)}〕" for x in c[1])
    else:
        s = str(c)
    return s if top else f"（{s}）"


# 別の実体の選択から作る選択（{c} に元の実体の条件が入る）
_REL_FMT = {
    "NSLE": ("E", "要素［{c}］の節点"),
    "NSLA": ("A", "面積［{c}］上の節点"),
    "NSLL": ("L", "線［{c}］上の節点"),
    "NSLK": ("K", "キーポイント［{c}］上の節点"),
    "NSLV": ("V", "体積［{c}］内の節点"),
    "ESLN": ("N", "節点［{c}］に接する要素"),
    "ESLA": ("A", "面積［{c}］上の要素"),
    "ESLL": ("L", "線［{c}］上の要素"),
    "ESLV": ("V", "体積［{c}］内の要素"),
    "KSLL": ("L", "線［{c}］上のキーポイント"),
    "KSLN": ("N", "節点［{c}］のあるキーポイント"),
    "LSLA": ("A", "面積［{c}］の線"),
    "LSLK": ("K", "キーポイント［{c}］を含む線"),
    "ASLL": ("L", "線［{c}］を含む面積"),
    "ASLV": ("V", "体積［{c}］の面積"),
    "VSLA": ("A", "面積［{c}］を含む体積"),
}

# 式の中の取得関数（名前 → 意味）。値そのものは追わない。
GET_FUNCS = {
    "NX": "節点の X 座標（活性座標系）",
    "NY": "節点の Y 座標（活性座標系）",
    "NZ": "節点の Z 座標（活性座標系）",
    "KX": "キーポイントの X 座標",
    "KY": "キーポイントの Y 座標",
    "KZ": "キーポイントの Z 座標",
    "CENTRX": "要素の重心 X",
    "CENTRY": "要素の重心 Y",
    "CENTRZ": "要素の重心 Z",
    "NSEL": "節点の選択状態",
    "ESEL": "要素の選択状態",
    "KSEL": "キーポイントの選択状態",
    "LSEL": "線の選択状態",
    "ASEL": "面積の選択状態",
    "VSEL": "体積の選択状態",
    "NDNEXT": "次に大きい選択節点の番号",
    "ELNEXT": "次に大きい選択要素の番号",
    "KPNEXT": "次に大きい選択キーポイントの番号",
    "LSNEXT": "次に大きい選択線の番号",
    "ARNEXT": "次に大きい選択面積の番号",
    "VLNEXT": "次に大きい選択体積の番号",
    "NODE": "指定位置に最も近い選択節点の番号",
    "KP": "指定位置に最も近い選択キーポイントの番号",
    "NNEAR": "最も近い選択節点の番号",
    "KNEAR": "最も近い選択キーポイントの番号",
    "ENEARN": "節点に最も近い選択要素の番号",
    "DISTND": "節点間の距離",
    "DISTKP": "キーポイント間の距離",
    "NELEM": "要素の節点番号",
    "ENEXTN": "節点に接する要素の番号",
    "UX": "節点の UX 変位（結果）",
    "UY": "節点の UY 変位（結果）",
    "UZ": "節点の UZ 変位（結果）",
    "ROTX": "節点の ROTX（結果）",
    "ROTY": "節点の ROTY（結果）",
    "ROTZ": "節点の ROTZ（結果）",
    "TEMP": "節点の温度（結果）",
    "PRES": "節点の圧力（結果）",
    "VOLT": "節点の電位（結果）",
    "AREAND": "節点で囲まれた面積",
    "AREAKP": "キーポイントで囲まれた面積",
    "ARFACE": "要素の面の面積",
}
# 選択状態で結果が変わる取得（*GET の項目・取得関数）
_SEL_DEPENDENT_GET = {"COUNT", "NUM", "MNLOC", "MXLOC"}
_SEL_DEPENDENT_FUNC = {
    "NDNEXT": "N",
    "ELNEXT": "E",
    "KPNEXT": "K",
    "LSNEXT": "L",
    "ARNEXT": "A",
    "VLNEXT": "V",
    "NODE": "N",
    "KP": "K",
    "NNEAR": "N",
    "KNEAR": "K",
    "ENEARN": "E",
}
_COORD_FUNCS = {"NX", "NY", "NZ", "KX", "KY", "KZ", "CENTRX", "CENTRY", "CENTRZ"}
_FUNC_RE = re.compile(r"\b([A-Za-z][A-Za-z0-9_]*)\s*\(")

_OPS = {
    "EQ": "=",
    "NE": "≠",
    "LT": "<",
    "GT": ">",
    "LE": "≤",
    "GE": "≥",
    "ABLT": "|<|",
    "ABGT": "|>|",
}


@dataclass
class StaticOptions:
    exts: tuple[str, ...] = (".mac", ".inp", "")
    encoding: str = "latin-1"
    dict_path: Path | None = None
    brief: bool = False  # 選択・取得・座標系と呼び出しだけを出す
    max_depth: int = 30
    fmt: str = "text"  # text / md


@dataclass
class _Line:
    """流れの 1 行分（見出しと、その下に付く説明）。"""

    level: int
    head: str
    details: list[str]
    macro: bool = False  # ▼ の見出し（続く行はこの見出しの中身）


@dataclass
class SState:
    sel: dict[str, Cond] = field(default_factory=lambda: dict.fromkeys(ENT_NAME, ALL))
    csys: int | None = 0
    csys_note: str = ""
    rsys: str = "0"
    locals: dict[int, dict] = field(default_factory=dict)
    comps: dict[str, dict] = field(default_factory=dict)

    def copy(self) -> SState:
        return SState(
            dict(self.sel),
            self.csys,
            self.csys_note,
            self.rsys,
            dict(self.locals),
            dict(self.comps),
        )


def merge(states: list[SState]) -> SState:
    out = states[0].copy()
    for e in ENT_NAME:
        vals: list[Cond] = []
        for s in states:
            c = s.sel[e]
            for x in c[1] if c[0] == "branch" else (c,):
                if x not in vals:
                    vals.append(x)
        out.sel[e] = vals[0] if len(vals) == 1 else ("branch", tuple(vals))
    if len({(s.csys, s.csys_note) for s in states}) > 1:
        out.csys, out.csys_note = None, "分岐により異なる"
    if len({s.rsys for s in states}) > 1:
        out.rsys = "（分岐により異なる）"
    for s in states[1:]:
        out.locals.update(s.locals)
        out.comps.update(s.comps)
    return out


@dataclass
class _Macro:
    label: str  # 表示名
    rel: str  # 行番号の基準にするファイル
    stmts: list[Stmt]
    origin: str = ""  # ファイル以外で定義されたときの定義元


@dataclass
class _Frame:
    snap: SState
    ends: list[SState] = field(default_factory=list)
    has_else: bool = False


class StaticAnalyzer:
    def __init__(self, src: Path, opts: StaticOptions | None = None):
        self.src = src
        self.opts = opts or StaticOptions()
        self.d = Dictionary.load(self.opts.dict_path)
        self.files: dict[str, list[Stmt]] = {}
        self.by_name: dict[str, str] = {}  # 大文字のファイル名 → 相対パス
        self.macros: dict[str, _Macro] = {}  # 大文字のマクロ名 → 定義
        self.st = SState()
        self.out: list[_Line] = []
        self.unknown: Counter[str] = Counter()
        self.gets: list[str] = []
        self.coords: list[str] = []
        self.comp_log: list[str] = []
        self.missing: Counter[str] = Counter()
        self._load()

    # ---- 読み込み ----

    def _load(self) -> None:
        exts = {e.lower() for e in self.opts.exts}
        for p in sorted(self.src.rglob("*")):
            if not p.is_file() or p.suffix.lower() not in exts:
                continue
            rel = p.relative_to(self.src).as_posix()
            text = p.read_text(encoding=self.opts.encoding)
            self.files[rel] = lexer.lex(text, [])
            self.by_name[p.name.upper()] = rel
            if p.suffix.lower() == ".mac":
                self.macros.setdefault(
                    p.stem.upper(), _Macro(rel, rel, self.files[rel])
                )
        for rel, stmts in self.files.items():
            for i, st in enumerate(stmts):
                if st.kind != "cmd":
                    continue
                if st.name == "*CREATE" and st.fields:
                    self._register_create(rel, stmts, i)
                elif st.name == "*ULIB" and st.fields:
                    self._register_ulib(_file_name(st.fields))

    def _register_create(self, rel: str, stmts: list[Stmt], i: int) -> None:
        st = stmts[i]
        name = Path(st.fields[0].strip().strip("'")).name
        body = []
        for s in stmts[i + 1 :]:
            if s.kind == "cmd" and s.name == "*END":
                break
            body.append(s)
        m = _Macro(name.upper(), rel, body, f"*CREATE in {rel}:{st.lineno}")
        self.macros.setdefault(name.upper(), m)
        self.macros.setdefault(_file_name(st.fields), m)

    def _register_ulib(self, fname: str) -> None:
        rel = self.by_name.get(fname)
        if rel is None:
            return
        cur: list[Stmt] | None = None
        for s in self.files[rel]:
            if s.kind != "cmd":
                if cur is not None:
                    cur.append(s)
                continue
            if cur is None:
                name = s.text.split(",")[0].strip().upper()
                cur = []
                self.macros.setdefault(name, _Macro(name, rel, cur, f"*ULIB {rel}"))
            elif s.name == "/EOF":
                cur = None
            else:
                cur.append(s)

    # ---- 出力 ----

    def emit(self, level: int, head: str, details: list[str] = ()) -> None:
        self.out.append(_Line(level, head, list(details)))

    # ---- 本体 ----

    def run(self, entry: str) -> str:
        rel = self.by_name.get(Path(entry).name.upper(), entry)
        if rel not in self.files:
            raise FileNotFoundError(entry)
        self.out.append(_Line(0, f"▼ {rel}", [], macro=True))
        self.walk(self.files[rel], rel, 0, [rel.upper()])
        return self.report(rel)

    def walk(self, stmts: list[Stmt], rel: str, level: int, stack: list[str]) -> None:
        frames: list[_Frame] = []
        i = 0
        while i < len(stmts):
            st = stmts[i]
            i += 1
            if st.kind == "label":
                if not self.opts.brief:
                    self.emit(level + len(frames), f"[{rel}:{st.lineno}] {st.text}")
                continue
            if st.kind != "cmd":
                continue
            lv = level + len(frames)
            head = f"[{rel}:{st.lineno}] {display(st.text)}"
            name = st.name
            spec = commands.SPECS.get(name) if st.known else None
            cat = spec.cat if spec else ("set" if name == "=" else "other")
            if not st.known and name in self.macros:
                cat = "call"

            if cat == "create":
                while i < len(stmts) and not (
                    stmts[i].kind == "cmd" and stmts[i].name == "*END"
                ):
                    i += 1
                i += 1
                self.emit(lv, head, ["マクロを定義（中身は呼び出したときに読む）"])
                continue
            if cat == "eof" or name == "/EOF":
                self.emit(lv, head, ["ここでファイルの読み込みを終える"])
                break
            if cat == "if":
                self._if(st, head, lv, frames)
                continue
            if cat in ("elseif", "else"):
                if frames:
                    fr = frames[-1]
                    fr.ends.append(self.st)
                    self.st = fr.snap.copy()
                    fr.has_else = fr.has_else or cat == "else"
                lv = level + len(frames) - 1
                self.emit(lv, head, [] if cat == "else" else [self._cond_text(st)])
                continue
            if cat == "endif":
                self._endif(head, level, frames)
                continue
            if cat in ("do", "dowhile"):
                self.emit(lv, head, self._desc_list(st))
                frames.append(_Frame(self.st.copy(), has_else=True))
                continue
            if cat == "enddo":
                if frames:
                    fr = frames.pop()
                    fr.ends.append(self.st)
                    self.st = merge(fr.ends)
                self.emit(level + len(frames), head)
                continue
            if cat in ("call", "use", "input"):
                self._call(st, cat, head, lv, stack)
                continue

            handler = getattr(self, f"_h_{cat}", None)
            if handler is not None:
                details = handler(st, rel)
                if details or not self.opts.brief:
                    self.emit(lv, head, details)
            elif not self.opts.brief:
                self.emit(lv, head, self._desc_list(st))
        while frames:
            self._endif("（ファイルの終わり）", level, frames)

    # ---- 制御 ----

    def _if(self, st: Stmt, head: str, lv: int, frames: list[_Frame]) -> None:
        cond = self._cond_text(st)
        if _is_block_if(st):
            self.emit(lv, head, [cond])
            frames.append(_Frame(self.st.copy()))
            return
        action = display(st.fields[-1]) if st.fields else "?"
        self.emit(lv, head, [f"{cond} のとき {action}"])

    def _endif(self, head: str, level: int, frames: list[_Frame]) -> None:
        details: list[str] = []
        if frames:
            fr = frames.pop()
            fr.ends.append(self.st)
            if not fr.has_else:
                fr.ends.append(fr.snap)
            before = self.st
            self.st = merge(fr.ends)
            for e in ENT_NAME:
                c = self.st.sel[e]
                if c[0] == "branch" and before.sel[e] != c:
                    details.append(f"選択条件（{ENT_NAME[e]}）: {render(c)}")
            if self.st.csys is None and self.st.csys_note == "分岐により異なる":
                details.append("活性座標系: 分岐により異なる")
        self.emit(level + len(frames), head, details)

    def _cond_text(self, st: Stmt) -> str:
        f = [display(x) for x in st.fields]

        def a(i: int) -> str:
            return f[i] if len(f) > i else "?"

        def op(i: int) -> str:
            return _OPS.get(a(i).strip().upper(), a(i))

        text = f"条件 {a(0)} {op(1)} {a(2)}"
        if len(f) > 4 and f[3].strip().upper() in ("AND", "OR", "XOR"):
            text += f" {f[3].strip().upper()} {a(4)} {op(5)} {a(6)}"
        return text

    # ---- 呼び出し ----

    def _call(self, st: Stmt, cat: str, head: str, lv: int, stack: list[str]) -> None:
        f = st.fields
        if cat == "input":
            fname = f[0].strip() if f else ""
            ext = f[1].strip() if len(f) > 1 else ""
            key = (f"{fname}.{ext}" if ext else fname).upper()
            rel = self.by_name.get(key) or self.by_name.get(fname.upper())
            macro = _Macro(rel, rel, self.files[rel]) if rel else None
            args: list[str] = []
            how = "/INPUT"
        else:
            name = st.name if cat == "call" else (f[0].strip() if f else "")
            args = f if cat == "call" else f[1:]
            key = name.upper()
            macro = (
                self.macros.get(key)
                or self.macros.get(f"{key}.MAC")
                or self._file_macro(key)
            )
            how = "呼び出し" if cat == "call" else "*USE"
        arg_txt = ", ".join(
            f"ARG{k}={display(a)}" for k, a in enumerate(args, 1) if a.strip()
        )
        if macro is None:
            self.missing[key] += 1
            self.emit(lv, head, [f"{key} は見つからない（読まない）"])
            return
        if key in stack or len(stack) > self.opts.max_depth:
            self.emit(lv, head, [f"{macro.label} は再帰呼び出しのため読まない"])
            return
        tail = how + (f", {arg_txt}" if arg_txt else "")
        tail += f" / 定義: {macro.origin}" if macro.origin else ""
        self.emit(lv, head)
        self.out.append(_Line(lv + 1, f"▼ {macro.label}（{tail}）", [], macro=True))
        self.walk(macro.stmts, macro.rel, lv + 1, [*stack, key])

    def _file_macro(self, key: str) -> _Macro | None:
        rel = self.by_name.get(key)
        return _Macro(rel, rel, self.files[rel]) if rel else None

    # ---- 選択 ----

    def _coord_name(self, comp: str) -> str:
        idx = {"X": 0, "Y": 1, "Z": 2}.get(comp.strip().upper()[:1])
        if idx is None:
            return comp
        if self.st.csys is None:
            return f"{comp.strip().upper()}（座標系は不明）"
        return self.d.coord(self.st.csys, self.st.locals)[1][idx]

    def _h_select(self, st: Stmt, rel: str) -> list[str]:
        name = st.name
        f = st.fields
        typ = f[0].strip().upper() if f and f[0].strip() else "S"
        s = self.st
        lines = self._desc_list(st)
        if name == "ALLSEL":
            for e in ENT_NAME:
                s.sel[e] = ALL
            return [*lines, "選択条件: すべて「全体」に戻る"]
        if name == "CMSEL":
            return lines + self._cmsel(st, typ)
        ent = _SEL_ENT.get(name, "N")
        if name in _REL_FMT:
            src, fmt = _REL_FMT[name]
            term: Cond = ("t", fmt.format(c=render(s.sel[src])))
        else:
            args = [ArgVal(display(x)) for x in f]
            term = ("t", sel_term(self.d, self._coord_name, name, args))
        s.sel[ent] = _apply(s.sel[ent], typ, term)
        return [*lines, f"選択条件（{ENT_NAME[ent]}）: {render(s.sel[ent])}"]

    def _cmsel(self, st: Stmt, typ: str) -> list[str]:
        f = st.fields
        s = self.st
        if typ in ("ALL", "NONE"):
            term: Cond = ("t", "全コンポーネント")
            ents = sorted({c["entity"] for c in s.comps.values()}) or list(ENT_NAME)
            for e in ents:
                s.sel[e] = (c_or if typ == "ALL" else c_minus)(s.sel[e], term)
            return [f"選択条件（{ENT_NAME[e]}）: {render(s.sel[e])}" for e in ents]
        cname = f[1].strip().upper() if len(f) > 1 else ""
        comp = s.comps.get(cname)
        ent = comp["entity"] if comp else None
        if ent is None and len(f) > 2 and f[2].strip():
            ent = ENT_OF_ITEM.get(f[2].strip().upper()[:4])
        if ent is None:
            return [
                f"コンポーネント {cname} の種類が分からないため、選択条件に反映しない"
            ]
        text = f"コンポーネント {cname}"
        if comp:
            text += f"［{render(comp['cond'])}］"
        s.sel[ent] = _apply(s.sel[ent], typ, ("t", text))
        return [f"選択条件（{ENT_NAME[ent]}）: {render(s.sel[ent])}"]

    def _h_comp(self, st: Stmt, rel: str) -> list[str]:
        f = st.fields
        lines = self._desc_list(st)
        if st.name == "CM" and f:
            cname = f[0].strip().upper()
            ent = ENT_OF_ITEM.get(f[1].strip().upper()[:4]) if len(f) > 1 else None
            if ent is None:
                return [*lines, "種類が分からないため、選択条件は記録しない"]
            cond = self.st.sel[ent]
            self.st.comps[cname] = {"entity": ent, "cond": cond}
            self.comp_log.append(
                f"  {cname}（{ENT_NAME[ent]}）: {render(cond)} @{rel}:{st.lineno}"
            )
            return [*lines, f"登録する{ENT_NAME[ent]}の条件: {render(cond)}"]
        if st.name == "CMDELE" and f:
            self.st.comps.pop(f[0].strip().upper(), None)
        return lines

    # ---- 取得・代入 ----

    def _h_get(self, st: Stmt, rel: str) -> list[str]:
        f = [display(x).strip() for x in st.fields] + [""] * 7
        par, ent, num, it1, it1n = f[0], f[1].upper(), f[2], f[3].upper(), f[4]
        item = self.d.get_item(ent, num, it1, it1n)
        what = ",".join(x for x in (ent, num, it1, it1n) if x)
        text = f"{par} ← {item or '（辞書に未登録の項目）'}（{what}）"
        e = ENT_OF_ITEM.get(ent[:4])
        if e and (num in ("", "0")) and it1 in _SEL_DEPENDENT_GET:
            if it1 in ("MNLOC", "MXLOC"):
                text += f"［成分: {self._coord_name(it1n)}］"
            text += f"［選択中の{ENT_NAME[e]}: {render(self.st.sel[e])}］"
        if ent == "ACTIVE" and it1 == "CSYS":
            text += f"［この時点: {self._csys_text()}］"
        elif item and "結果" in item:
            text += f"［結果の座標系: RSYS {display(self.st.rsys)}］"
        elif it1 in ("LOC", "MNLOC", "MXLOC"):
            text += f"［座標系: {self._csys_text()}］"
        self.gets.append(f"  {rel}:{st.lineno}: {text}")
        return [text]

    def _h_set(self, st: Stmt, rel: str) -> list[str]:
        f = [display(x).strip() for x in st.fields]
        target = f[0] if f else "?"
        expr = (
            ", ".join(x for x in f[1:] if x)
            if st.name == "*SET"
            else (f[1] if len(f) > 1 else "")
        )
        funcs = []
        for m in _FUNC_RE.finditer(expr):
            fn = m.group(1).upper()
            meaning = GET_FUNCS.get(fn)
            if meaning is None:
                continue
            note = f"{fn}(): {meaning}"
            e = _SEL_DEPENDENT_FUNC.get(fn)
            if e:
                note += f"［選択中の{ENT_NAME[e]}: {render(self.st.sel[e])}］"
            if fn in _COORD_FUNCS:
                note += f"［座標系: {self._csys_text()}］"
            elif "結果" in meaning:
                note += f"［結果の座標系: RSYS {display(self.st.rsys)}］"
            if note not in funcs:
                funcs.append(note)
        text = f"{target} = {expr}"
        if "_RETURN" in expr.upper():
            funcs.append("_RETURN: 直前のコマンドの戻り値（コマンドごとに意味が違う）")
        if funcs:
            self.gets.append(f"  {rel}:{st.lineno}: {text}（{'; '.join(funcs)}）")
            return [text, *(f"取得関数 {x}" for x in funcs)]
        return [] if self.opts.brief else [text]

    # ---- 座標系 ----

    def _csys_text(self) -> str:
        s = self.st
        if s.csys is None:
            return f"不明（{s.csys_note}）"
        kind, names, unv = self.d.coord(s.csys, s.locals)
        axes = ", ".join(f"{a}={b}" for a, b in zip("XYZ", names, strict=True))
        return f"CSYS{s.csys}（{kind}）: {axes}" + (
            "（成分の対応は要確認）" if unv else ""
        )

    def _h_csys(self, st: Stmt, rel: str) -> list[str]:
        f = st.fields
        s = self.st
        lines = [] if st.name == "CSYS" else self._desc_list(st)
        raw = f[0].strip() if f and f[0].strip() else "0"
        n = parse_num(raw)
        if st.name != "CSYS" and n is not None:
            kcs = f[1].strip() if len(f) > 1 and f[1].strip() else "0"
            k = parse_num(kcs)
            s.locals[int(n)] = {
                "kcs": fmt_num(k) if k is not None else "?",
                "loc": f"{rel}:{st.lineno}",
            }
        if n is None:
            s.csys, s.csys_note = None, f"{display(raw)} は変数"
        else:
            s.csys, s.csys_note = int(n), ""
        text = f"活性座標系: {self._csys_text()}"
        self.coords.append(f"  {rel}:{st.lineno}: {display(st.text)} → {text}")
        return [*lines, text]

    def _h_rsys(self, st: Stmt, rel: str) -> list[str]:
        raw = (
            st.fields[0].strip().upper() if st.fields and st.fields[0].strip() else "0"
        )
        table = (self.d.entry("RSYS") or {}).get("KCN", {})
        n = parse_num(raw)
        if raw in table:
            meaning = table[raw]
        elif n is not None and n >= 11:
            meaning = self.d.coord(int(n), self.st.locals)[0]
        else:
            meaning = f"{display(raw)}（変数のため静的には不明）" if n is None else raw
        self.st.rsys = raw
        text = f"結果の座標系: {display(raw)}（{meaning}）"
        self.coords.append(f"  {rel}:{st.lineno}: {display(st.text)} → {text}")
        return [text]

    # ---- DB の入れ替え ----

    def _h_clear(self, st: Stmt, rel: str) -> list[str]:
        self.st = SState()
        return [
            *self._desc_list(st),
            "選択条件・座標系・コンポーネントを初期状態に戻す",
        ]

    def _h_resume(self, st: Stmt, rel: str) -> list[str]:
        s = SState()
        for e in ENT_NAME:
            s.sel[e] = ("t", "RESUME した DB の選択")
        s.csys, s.csys_note = None, "RESUME した DB の値"
        self.st = s
        return [
            *self._desc_list(st),
            "選択条件・座標系は読み込んだ DB 次第（静的には不明）",
        ]

    # ---- 辞書の説明 ----

    def _desc_list(self, st: Stmt) -> list[str]:
        args = [ArgVal(display(x)) for x in st.fields]
        extras: dict[str, str] = {}
        if st.name in ("D", "DK"):
            labs = [args[1]] if len(args) > 1 else []
            labs += [a for a in args[6:11] if not a.is_empty]
            extras["DOFS"] = "、".join(self.d.dof(a.key) for a in labs) or "?"
        text = self.d.describe(st.name, args, self._fmt, extras)
        if text is None:
            self.unknown[st.name] += 1
            return []
        return [text]

    def _fmt(self, style: str, idx: int, av: ArgVal) -> str | None:
        key = av.key.strip().upper()
        if style == "座標名":
            return self._coord_name(av.key)
        if style == "自由度":
            return self.d.dof(av.key)
        if style == "荷重":
            return self.d.load_label(av.key)
        if style in ("節点", "要素"):
            ent = "N" if style == "節点" else "E"
            if not key or key == "ALL":
                return f"選択中の{style}［{render(self.st.sel[ent])}］"
            if key in ("P", "P51X"):
                return f"GUI で選んだ{style}"
            if key in self.st.comps:
                return f"コンポーネント {av.key}"
            return f"{style} {av.raw.strip()}"
        if style == "要素名":
            return self.d.element_name(av.key)
        if style == "raw":
            return av.raw
        return None

    # ---- レポート ----

    def _sections(self) -> list[tuple[str, list[str]]]:
        return [
            ("コンポーネント（登録時の条件）", self.comp_log),
            ("取得している値（*GET・取得関数）", self.gets),
            ("座標系の切り替え（CSYS / LOCAL / RSYS など）", self.coords),
            (
                "見つからないマクロ・ファイル",
                [f"  {k}: {n} 回" for k, n in self.missing.most_common()],
            ),
            (
                "辞書に未登録のコマンド（辞書の追加候補）",
                [f"  {k}: {n} 回" for k, n in self.unknown.most_common()],
            ),
        ]

    def report(self, entry: str) -> str:
        if self.opts.fmt == "md":
            return self._report_md(entry)
        head = [
            "APDL 静的レポート（実行せずに読んだ結果）",
            f"入口: {entry}",
            "",
            *(f"※ {n}" for n in _NOTES),
            "",
            "━━ 流れ ━━",
        ]
        flow: list[str] = []
        for ln in self.out:
            pad = "  " * ln.level
            flow.append(pad + ln.head)
            flow += [f"{pad}    → {d}" for d in ln.details]
        tail = ["", "━━ 付録 ━━", ""]
        for title, lines in self._sections():
            tail.append(f"■ {title}")
            tail.extend(lines or ["  （なし）"])
            tail.append("")
        return "\n".join(head + flow + tail)

    def _report_md(self, entry: str) -> str:
        """折りたたみできるエディタ向け。入れ子リストと見出しで構造を表す。"""
        out = [
            "# APDL 静的レポート（実行せずに読んだ結果）",
            "",
            f"入口: {_md_code(entry)}",
            "",
            *(f"> - {_md_text(n)}" for n in _NOTES),
            "",
            "## 流れ",
            "",
        ]
        # ▼ の見出しは同じ深さの行を中身として持つため、見出しの数だけ深くする
        macros: list[int] = []
        for ln in self.out:
            while macros and ln.level < macros[-1]:
                macros.pop()
            pad = "  " * (ln.level + len(macros))
            out.append(f"{pad}- {_md_head(ln)}")
            out += [f"{pad}  - → {_md_text(d)}" for d in ln.details]
            if ln.macro:
                macros.append(ln.level)
        out += ["", "## 付録", ""]
        for title, lines in self._sections():
            out += [f"### {_md_text(title)}", ""]
            items = [f"- {_md_text(x.strip())}" for x in lines]
            out += [*(items or ["（なし）"]), ""]
        return "\n".join(out)


_NOTES = (
    "変数の値は追わない（式のまま表示）。",
    "*IF は各分岐を読み、*ENDIF で選択条件が分岐によって違えば「分岐により異なる」とする。",
    "ループの中は 1 回分だけ読み、*GO のジャンプは追わない（上から順に読む）。",
)

_LOC_RE = re.compile(r"^\[([^\]]+)\] (.*)$")
_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>~|])")


def _md_text(s: str) -> str:
    t = _MD_SPECIAL.sub(r"\\\1", s)
    # 行頭がリストや見出しの記号に見えないようにする
    t = re.sub(r"^([-+#>])", r"\\\1", t)
    return re.sub(r"^(\d+)([.)])(?=\s|$)", r"\1\\\2", t)


def _md_code(s: str) -> str:
    n = 1
    while "`" * n in s:
        n += 1
    fence = "`" * n
    pad = " " if s.startswith("`") or s.endswith("`") else ""
    return f"{fence}{pad}{s}{pad}{fence}"


def _md_head(ln: _Line) -> str:
    if ln.macro:
        return f"**{_md_text(ln.head)}**"
    m = _LOC_RE.match(ln.head)
    if m is None:
        return _md_text(ln.head)
    return f"{_md_text(m.group(1))} {_md_code(m.group(2))}"


def _apply(cur: Cond, typ: str, term: Cond) -> Cond:
    if typ == "ALL":
        return ALL
    if typ == "NONE":
        return NONE
    if typ == "INVE":
        return c_inv(cur)
    if typ == "R":
        return c_and(cur, term)
    if typ == "A":
        return c_or(cur, term)
    if typ == "U":
        return c_minus(cur, term)
    if typ == "S":
        return term
    return cur  # STAT など


def _is_block_if(st: Stmt) -> bool:
    return any(f.strip().upper() == "THEN" for f in st.fields)


def _file_name(fields: list[str]) -> str:
    fname = fields[0].strip().strip("'") if fields else ""
    ext = fields[1].strip().strip("'") if len(fields) > 1 else ""
    base = re.split(r"[\\/]", fname)[-1]
    return (f"{base}.{ext}" if ext else base).upper()


def run_static(entry: Path, src: Path | None, opts: StaticOptions) -> str:
    """entry を入口に読む。マクロは src（省略時は entry と同じディレクトリ）から探す。"""
    base = src if src is not None else entry.parent
    try:
        rel = entry.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        rel = entry.name
    return StaticAnalyzer(base, opts).run(rel)
