"""トレース行から状態を追跡し、コマンドごとの説明文と付録を作る。"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from apdl_trace.dictionary import ArgVal, Dictionary, fmt_num, fmt_val, parse_num
from apdl_trace.records import Occ, Rec

ENT_NAME = {
    "N": "節点",
    "E": "要素",
    "K": "キーポイント",
    "L": "線",
    "A": "面積",
    "V": "体積",
}
ENT_OF_ITEM = {
    "NODE": "N",
    "ELEM": "E",
    "KP": "K",
    "LINE": "L",
    "AREA": "A",
    "VOLU": "V",
}

# 選択系コマンドの対象
_SEL_ENT = {
    "NSEL": "N",
    "ESEL": "E",
    "KSEL": "K",
    "LSEL": "L",
    "ASEL": "A",
    "VSEL": "V",
    "NSLE": "N",
    "NSLA": "N",
    "NSLL": "N",
    "NSLK": "N",
    "NSLV": "N",
    "ESLN": "E",
    "ESLA": "E",
    "ESLL": "E",
    "ESLV": "E",
    "KSLL": "K",
    "KSLN": "K",
    "LSLA": "L",
    "LSLK": "L",
    "ASLL": "A",
    "ASLV": "A",
    "VSLA": "V",
}
_REL_TERM = {
    "NSLE": "選択要素の節点",
    "NSLA": "選択面積上の節点",
    "NSLL": "選択線上の節点",
    "NSLK": "選択キーポイント上の節点",
    "NSLV": "選択体積内の節点",
    "ESLN": "選択節点に接する要素",
    "ESLA": "選択面積上の要素",
    "ESLL": "選択線上の要素",
    "ESLV": "選択体積内の要素",
    "KSLL": "選択線上のキーポイント",
    "KSLN": "選択節点のあるキーポイント",
    "LSLA": "選択面積の線",
    "LSLK": "選択キーポイントを含む線",
    "ASLL": "選択線を含む面積",
    "ASLV": "選択体積の面積",
    "VSLA": "選択面積を含む体積",
}
_ITEM_DEFAULT = {
    "NSEL": "NODE",
    "ESEL": "ELEM",
    "KSEL": "KP",
    "LSEL": "LINE",
    "ASEL": "AREA",
    "VSEL": "VOLU",
}

# 要素を作るコマンド
_CREATES = {"E", "EINTF", "EGEN", "ESURF", "AMESH", "VMESH", "VSWEEP", "PSMESH"}


def loc(entry: dict) -> str:
    line = entry.get("line", 0)
    return f"{entry.get('file', '?')}:{line}" if line else str(entry.get("file", "?"))


def num_s(v: float | None) -> str:
    if v is None:
        return "?"
    if isinstance(v, int) or (isinstance(v, float) and v == int(v) and abs(v) >= 1000):
        return f"{int(v):,}"
    return fmt_num(float(v))


def _arg_name(n: int) -> str:
    return f"ARG{n}" if n < 10 else f"AR{n}"


def _rng(a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return "?"
    sa, sb = fmt_num(a), fmt_num(b)
    return sa if sa == sb else f"{sa}〜{sb}"


# ---- 状態 ----


@dataclass
class Sel:
    count: int | None = None
    cond: list[tuple[str, str]] = field(default_factory=list)
    csys: int | None = None
    ranges: dict[str, float] | None = None
    attrs: dict[str, list[tuple[int, int]]] = field(default_factory=dict)

    def cond_text(self) -> str:
        if not self.cond:
            return "（不明）"
        s = ""
        for op, t in self.cond:
            if op == "S" or not s:
                s = t
            elif op == "R":
                s += f" かつ {t}"
            elif op == "A":
                s += f" または {t}"
            elif op == "U":
                s += f"、ただし {t} を除く"
            elif op == "INVE":
                s = f"（{s}）の反転"
        return s

    def sig(self) -> tuple:
        rng = tuple(sorted((k, round(v, 6)) for k, v in (self.ranges or {}).items()))
        return (self.count, self.cond_text(), rng)


@dataclass
class State:
    rout: int | None = None
    job: str | None = None
    csys: int = 0
    locals: dict[int, dict] = field(default_factory=dict)
    model: str | None = None
    attrs: dict[str, str] = field(default_factory=dict)
    sel: dict[str, Sel] = field(default_factory=lambda: {e: Sel() for e in ENT_NAME})
    nrot: list[dict] = field(default_factory=list)
    nrot_unknown: bool = False  # RESUME 前の回転は分からない
    et: dict[int, dict] = field(default_factory=dict)
    reals: dict[int, list[str]] = field(default_factory=dict)
    last_real: int | None = None
    mats: dict[int, dict[str, str]] = field(default_factory=dict)
    tbs: dict[tuple[int, str], dict] = field(default_factory=dict)
    last_tb: tuple[int, str] | None = None
    sections: dict[int, dict] = field(default_factory=dict)
    comps: dict[str, dict] = field(default_factory=dict)
    loads_since: list[str] = field(default_factory=list)
    step: int = 0
    time: str | None = None
    solves: list[dict] = field(default_factory=list)
    setinfo: dict | None = None
    rfile: str | None = None
    nwrites: dict[str, dict] = field(default_factory=dict)
    mappings: list[dict] = field(default_factory=list)
    chks: list[dict] = field(default_factory=list)
    coord_cmds: list[str] = field(default_factory=list)
    springs: list[dict] = field(default_factory=list)
    psmesh: list[dict] = field(default_factory=list)
    sloads: list[dict] = field(default_factory=list)
    unknown: Counter[str] = field(default_factory=Counter)
    resumed: list[str] = field(default_factory=list)
    lists: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class DescribeOptions:
    theta_eps: float = 0.01
    files_dir: Path | None = None
    state_mode: str = "changed"


class Describer:
    def __init__(self, dic: Dictionary, opts: DescribeOptions | None = None):
        self.d = dic
        self.opts = opts or DescribeOptions()
        self.s = State()
        self._last_state: str | None = None

    # ---- 共通 ----

    def argvals(self, occ: Occ) -> list[ArgVal]:
        fields = occ.entry.get("fields", [])
        vals = occ.values()
        substs = occ.entry.get("subst", [])
        sub_map = {
            name.upper(): fmt_val(vals.get(f"S{k}"))
            for k, name in enumerate(substs, start=1)
        }
        out = []
        for i, raw in enumerate(fields, start=1):
            rt = fmt_val(vals.get(f"A{i}"))
            text = None
            if "%" in raw and sub_map:
                text = re.sub(
                    r"%([^%\s]+)%",
                    lambda m: sub_map.get(m.group(1).upper()) or m.group(0),
                    raw,
                )
            out.append(ArgVal(raw, rt, text))
        return out

    def state_text(self) -> str:
        s = self.s
        kind, _, unv = self.d.coord(s.csys, s.locals)
        model = s.model or "モデル不明"
        return (
            f"状態: {model} / {self.d.routine(s.rout)} / CSYS{s.csys}（{kind}"
            f"{'・要確認' if unv else ''}）"
        )

    def describe(self, occ: Occ) -> list[str]:
        """コマンド 1 回分の説明（1 行目は見出し、以降は字下げ済み）。"""
        e = occ.entry
        kind = e.get("kind", "cmd")
        if kind == "label":
            return [f"[{loc(e)}] {e.get('text', '')}"]
        cat = e.get("cat", "other")
        head = f"[{loc(e)}] {e.get('text', '')}"
        handler = getattr(self, f"_h_{cat}", self._h_other)
        body = handler(occ, self.argvals(occ))
        out = [head]
        st = self.state_text()
        if self.opts.state_mode == "always" or st != self._last_state:
            out.append("    " + st)
            self._last_state = st
        out += ["    " + b for b in body]
        return out

    def _desc(self, occ: Occ, args: list[ArgVal], **extras: str) -> str:
        name = occ.entry.get("name", "")
        text = self.d.describe(name, args, self._fmt_for(occ), extras)
        if text is None:
            self.s.unknown[name] += 1
            return "（辞書に未登録）"
        return text

    def _fmt_for(self, occ: Occ):
        def fmt(style: str, idx: int, av: ArgVal) -> str | None:
            fidx = idx + 1
            if style == "座標名":
                return self._coord_name(av.key)
            if style == "自由度":
                if occ.entry.get("name") not in ("D", "DDELE"):
                    return self.d.dof(av.key)
                rot, _ = self._rotation(occ, 1)
                return self.d.dof(av.key, rot if rot != "unknown" else None)
            if style == "荷重":
                return self.d.load_label(av.key)
            if style == "節点":
                return self._node_target(occ, fidx, av)
            if style == "要素":
                return self._elem_target(occ, fidx, av)
            if style == "要素名":
                return self.d.element_name(av.key)
            if style == "raw":
                return av.raw
            return None

        return fmt

    def _coord_name(self, comp: str) -> str:
        names = self.d.coord(self.s.csys, self.s.locals)[1]
        idx = {"X": 0, "Y": 1, "Z": 2}.get(comp.strip().upper()[:1])
        return names[idx] if idx is not None else comp

    def _sel_summary(self, ent: str, csys: int | None = None) -> str:
        sel = self.s.sel[ent]
        parts = [f"{num_s(sel.count)} {ENT_NAME[ent]}"]
        if sel.ranges:
            names = self.d.coord(
                sel.csys if sel.csys is not None else self.s.csys, self.s.locals
            )[1]
            for k, ax in enumerate("XYZ"):
                a, b = sel.ranges.get(f"{ax}0"), sel.ranges.get(f"{ax}1")
                if a is not None:
                    parts.append(f"{names[k]} {_rng(a, b)}")
        return " | ".join(parts)

    def _node_target(self, occ: Occ, fidx: int, av: ArgVal) -> str:
        key = av.key.strip().upper()
        if not key or key == "ALL":
            sel = self.s.sel["N"]
            return f"選択中の {num_s(sel.count)} 節点（条件: {sel.cond_text()}）"
        if key in ("P", "P51X"):
            return "GUI で選んだ節点"
        comp = self.s.comps.get(key)
        if comp:
            return f"コンポーネント {av.key}（{num_s(comp.get('count'))} 件）"
        rec = self._by_field(occ, "NODE", fidx)
        if rec is not None:
            return self._node_text(occ, rec, fidx)
        return f"節点 {av.disp}"

    def _node_text(self, occ: Occ, rec: Rec, fidx: int) -> str:
        cs = rec.int("CS") or 0
        names = self.d.coord(cs, self.s.locals)[1]
        xyz = ", ".join(
            f"{names[k]}={fmt_num(rec.num(ax) or 0.0)}" for k, ax in enumerate("XYZ")
        )
        text = f"節点 {rec.int('N')}（{xyz}"
        g = self._by_field(occ, "NODEG", fidx)
        if g is not None and cs != 0:
            gx = ", ".join(fmt_num(g.num(ax) or 0.0) for ax in "XYZ")
            text += f"、グローバル ({gx})"
        return text + "）"

    def _elem_target(self, occ: Occ, fidx: int, av: ArgVal) -> str:
        key = av.key.strip().upper()
        if not key or key == "ALL":
            sel = self.s.sel["E"]
            return f"選択中の {num_s(sel.count)} 要素（条件: {sel.cond_text()}）"
        comp = self.s.comps.get(key)
        if comp:
            return f"コンポーネント {av.key}（{num_s(comp.get('count'))} 件）"
        rec = self._by_field(occ, "ELEM", fidx)
        if rec is None:
            return f"要素 {av.disp}"
        c = self._by_field(occ, "ELEMC", fidx)
        name = self.d.element_name(rec.int("EN"))
        text = (
            f"要素 {rec.int('E')}（{name}, MAT {rec.int('MAT')}, TYPE {rec.int('TYPE')},"
            f" REAL {rec.int('REAL')}, SECNUM {rec.int('SEC')}"
        )
        if c is not None:
            text += (
                ", 重心 (" + ", ".join(fmt_num(c.num(a) or 0.0) for a in "XYZ") + ")"
            )
        return text + "）"

    @staticmethod
    def _by_field(occ: Occ, kind: str, fidx: int) -> Rec | None:
        for r in occ.recs:
            if r.kind == kind and f"F{fidx}" in r.tags:
                return r
        return None

    def _rotation(self, occ: Occ, fidx: int) -> tuple[str | None, str]:
        """対象節点の節点座標系。(読み替えの種類, 注記)。"""
        s = self.s
        if not s.nrot:
            if s.nrot_unknown:
                return (
                    "unknown",
                    "節点座標系は不明（RESUME 前の NROTAT は追跡できない）",
                )
            return None, ""
        fields = occ.entry.get("fields", [])
        target = fields[fidx - 1].strip().upper() if len(fields) >= fidx else ""
        if not target or target == "ALL":
            sig = s.sel["N"].sig()
            for ev in reversed(s.nrot):
                if ev.get("sig") == sig:
                    return self._rot_kind(ev)
        else:
            rec = self._by_field(occ, "NODE", fidx)
            n = rec.int("N") if rec else None
            for ev in reversed(s.nrot):
                if n is not None and n in ev.get("nodes", ()):
                    return self._rot_kind(ev)
        last = s.nrot[-1]
        return "unknown", (
            f"節点座標系は不明（NROTAT の履歴あり: 直近は CSYS{last['csys']} @{last['loc']}）"
        )

    def _rot_kind(self, ev: dict) -> tuple[str | None, str]:
        cs = ev["csys"]
        kind = self.d.coord_is_cyl(cs, self.s.locals)
        cname = self.d.coord(cs, self.s.locals)[0]
        note = f"CSYS{cs}（{cname}）で回転済み（NROTAT @{ev['loc']}）"
        if cs == 0:
            return None, note
        return kind or "unknown", note

    # ---- 状態の変化 ----

    def _apply_state_recs(self, occ: Occ) -> list[str]:
        out = []
        for r in occ.find("STATE"):
            t = r.tag()
            if t == "ROUT":
                self.s.rout = r.int("R")
            elif t == "JOB":
                self.s.job = r.vals.get("J", "").strip()
            elif t == "CSYS":
                cs = r.int("CS")
                if cs is not None:
                    self.s.csys = cs
        if occ.find("CHK"):
            out += self._handle_chk(occ)
        return out

    # ---- カテゴリごとの処理 ----

    def _h_processor(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        out = self._apply_state_recs(occ)
        lines = [f"→ {self._desc(occ, args)}"]
        if occ.find("CHK"):
            lines.append("→ 保留していたチェックポイントを出力")
        lines += out
        lines += self._handle_lists(occ)
        return lines

    def _h_jobname(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        self._apply_state_recs(occ)
        return [f"→ {self._desc(occ, args)}（ジョブ名: {self.s.job}）"]

    def _h_resume(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        s = self.s
        self._apply_state_recs_no_chk(occ)
        for sel in s.sel.values():
            sel.cond = [("S", "（RESUME 時の選択）")]
            sel.count = None
            sel.ranges = None
        s.nrot = []
        s.nrot_unknown = True
        s.resumed.append(loc(occ.entry))
        lines = [f"→ {self._desc(occ, args)}"]
        lines += self._handle_lists(occ)
        chk = self._handle_chk(occ)
        c = s.chks[-1] if chk and s.chks else None
        if c and c.get("nodes") is not None:
            lines.append(
                f"→ 読み込み後: 節点 {num_s(c['nodes'])} / 要素 {num_s(c['elems'])}"
            )
        lines += chk
        return lines

    def _apply_state_recs_no_chk(self, occ: Occ) -> None:
        for r in occ.find("STATE"):
            t = r.tag()
            if t == "ROUT":
                self.s.rout = r.int("R")
            elif t == "JOB":
                self.s.job = r.vals.get("J", "").strip()
            elif t == "CSYS":
                cs = r.int("CS")
                if cs is not None:
                    self.s.csys = cs

    def _h_clear(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        s = self.s
        keep = (s.chks, s.solves, s.mappings, s.unknown, s.springs, s.psmesh, s.sloads)
        self.s = State()
        (
            self.s.chks,
            self.s.solves,
            self.s.mappings,
            self.s.unknown,
            self.s.springs,
            self.s.psmesh,
            self.s.sloads,
        ) = keep
        self.s.step = s.step
        lines = [
            f"→ {self._desc(occ, args)}（定義・選択・コンポーネントの追跡をリセット）"
        ]
        return lines + self._apply_state_recs(occ)

    def _h_save(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        self._apply_state_recs(occ)
        return [f"→ {self._desc(occ, args)}"]

    def _h_csys(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        name = occ.entry.get("name")
        if name in ("LOCAL", "CLOCAL") and args:
            kcn = parse_num(args[0].key)
            if kcn is not None:
                kcs = args[1].key if len(args) > 1 and not args[1].is_empty else "0"
                self.s.locals[int(kcn)] = {
                    "kcs": fmt_num(parse_num(kcs) or 0.0),
                    "loc": loc(occ.entry),
                    "def": ", ".join(a.disp for a in args[1:] if not a.is_empty),
                    "relative": name == "CLOCAL",
                }
            self.s.coord_cmds.append(f"{name} @{loc(occ.entry)}")
        self._apply_state_recs(occ)
        kind, names, unv = self.d.coord(self.s.csys, self.s.locals)
        lines = [] if name == "CSYS" else [f"→ {self._desc(occ, args)}"]
        axes = ", ".join(f"{a}={b}" for a, b in zip("XYZ", names, strict=True))
        lines.append(
            f"→ 活性座標系: CSYS{self.s.csys}（{kind}）。以後 {axes}"
            + ("（成分の対応は要確認）" if unv else "")
        )
        return lines

    def _h_nrotat(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        self._apply_state_recs(occ)
        s = self.s
        target = args[0].key.strip().upper() if args else ""
        ev: dict = {"csys": s.csys, "loc": loc(occ.entry)}
        kind = self.d.coord(s.csys, s.locals)[0]
        if not target or target == "ALL":
            ev["sig"] = s.sel["N"].sig()
            what = f"選択中の {num_s(s.sel['N'].count)} 節点（条件: {s.sel['N'].cond_text()}）"
        else:
            nodes = []
            for fidx in (1, 2):
                r = self._by_field(occ, "NODE", fidx)
                if r is not None and r.int("N") is not None:
                    nodes.append(r.int("N"))
            if len(nodes) == 2:
                ev["nodes"] = range(nodes[0], nodes[1] + 1)
            else:
                ev["nodes"] = tuple(nodes)
            what = self._node_target(occ, 1, args[0])
        s.nrot.append(ev)
        return [f"→ {what} の節点座標系を CSYS{s.csys}（{kind}）の向きに回転"]

    def _h_attr(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        name = occ.entry.get("name", "")
        self._apply_state_recs(occ)
        if args:
            self.s.attrs[name] = args[0].key
        line = f"→ {self._desc(occ, args)}"
        if name == "TYPE" and args:
            n = parse_num(args[0].key)
            et = self.s.et.get(int(n)) if n is not None else None
            if et:
                line += f"（{et['ename']}）"
        return [line]

    def _h_coordmod(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        name = occ.entry.get("name", "")
        self.s.coord_cmds.append(f"{name} @{loc(occ.entry)}")
        lines = [f"→ {self._desc(occ, args)}"]
        rec = self._by_field(occ, "NODE", 1)
        if rec is not None:
            lines.append(f"→ 変更後: {self._node_text(occ, rec, 1)}")
        return lines

    # ---- 定義 ----

    def _h_def(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        s = self.s
        name = occ.entry.get("name", "")
        where = loc(occ.entry)
        lines = [f"→ {self._desc(occ, args)}"]

        def n(i: int) -> int | None:
            if i >= len(args):
                return None
            v = parse_num(args[i].key)
            return int(v) if v is not None else None

        def keys(start: int) -> list[str]:
            return [a.key for a in args[start:]]

        if name == "ET" and n(0) is not None:
            ename = args[1].key if len(args) > 1 else ""
            enam = self.d.element_number(ename)
            info = {"ename": self.d.element_name(ename), "enam": enam, "loc": where}
            info["keyopt"] = {
                str(k + 1): a.key for k, a in enumerate(args[2:8]) if not a.is_empty
            }
            s.et[n(0)] = info
            elem = self.d.element(enam)
            if elem:
                lines.append(f"→ {elem['name']}: {elem.get('desc', '')}")
            for k, v in info["keyopt"].items():
                meaning = self.d.keyopt(enam, int(k), v)
                if meaning:
                    lines.append(f"→ KEYOPT({k})={v}: {meaning}")
        elif name == "KEYOPT" and n(0) is not None and n(1) is not None:
            et = s.et.setdefault(n(0), {"ename": "?", "enam": None, "keyopt": {}})
            value = args[2].key if len(args) > 2 else "0"
            et.setdefault("keyopt", {})[str(n(1))] = value
            meaning = self.d.keyopt(et.get("enam"), n(1), value)
            if meaning:
                lines.append(f"→ {et['ename']} の {meaning}")
        elif name == "R" and n(0) is not None:
            s.reals[n(0)] = keys(1)
            s.last_real = n(0)
        elif name == "RMORE" and s.last_real is not None:
            s.reals[s.last_real] = s.reals.get(s.last_real, []) + keys(0)
        elif name == "RMODIF" and n(0) is not None and n(1) is not None:
            vals = s.reals.setdefault(n(0), [])
            for k, v in enumerate(keys(2)):
                pos = n(1) - 1 + k
                while len(vals) <= pos:
                    vals.append("")
                vals[pos] = v
        elif name == "MP" and n(1) is not None and args:
            s.mats.setdefault(n(1), {})[args[0].key.upper()] = (
                args[2].key if len(args) > 2 else ""
            )
        elif name == "MPDATA" and n(1) is not None and args:
            vals = ", ".join(v for v in keys(3) if v)
            s.mats.setdefault(n(1), {})[args[0].key.upper()] = f"温度依存（{vals}）"
        elif name == "TB" and n(1) is not None and args:
            key = (n(1), args[0].key.upper())
            s.tbs[key] = {"data": [], "loc": where, "opt": keys(4)[:1]}
            s.last_tb = key
        elif name == "TBDATA" and s.last_tb is not None:
            s.tbs[s.last_tb]["data"] += [v for v in keys(1) if v]
        elif name == "SECTYPE" and n(0) is not None:
            s.sections[n(0)] = {
                "type": args[1].key if len(args) > 1 else "",
                "subtype": args[2].key if len(args) > 2 else "",
                "name": args[3].key if len(args) > 3 else "",
                "loc": where,
            }
        return lines

    # ---- 選択 ----

    def _h_select(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        s = self.s
        name = occ.entry.get("name", "")
        typ = args[0].key.strip().upper() if args and not args[0].is_empty else "S"
        lines = [f"→ {self._desc(occ, args)}"]
        rec = occ.first("SEL")
        csys = rec.int("CS") if rec else None
        if csys is not None:
            s.csys = csys

        if name == "ALLSEL":
            ents = list(ENT_NAME)
            term, op = "全体", "S"
        elif name == "CMSEL":
            cname = args[1].key.strip().upper() if len(args) > 1 else ""
            comp = s.comps.get(cname)
            ents = [comp["entity"]] if comp and comp.get("entity") else list(ENT_NAME)
            term, op = f"コンポーネント {cname}", typ
            if typ == "ALL":
                term, op = "全コンポーネント", "S"
            elif typ == "NONE":
                term, op = "なし", "S"
        else:
            ents = [_SEL_ENT.get(name, "N")]
            term, op = self._sel_term(name, args), typ

        for e in ents:
            sel = s.sel[e]
            if op in ("S", "ALL", "NONE") or name == "ALLSEL":
                t = {"ALL": "全体", "NONE": "なし"}.get(op, term)
                sel.cond = [("S", t)]
            elif op in ("R", "A", "U"):
                sel.cond.append((op, term))
            elif op == "INVE":
                sel.cond.append(("INVE", ""))
            if rec is not None and rec.int(e) is not None:
                sel.count = rec.int(e)
                sel.csys = csys
                sel.ranges = None
                sel.attrs = {}

        if rec is None:
            lines.append("→ （選択の結果は出力されていない）")
            return lines

        self._apply_sel_details(occ, ents)
        if len(ents) == 1:
            e = ents[0]
            lines.append("→ " + self._sel_summary(e))
            lines += self._detail_lines(occ)
            if s.sel[e].attrs:
                lines.append("→ 属性: " + self._attrs_text(s.sel[e].attrs))
            if s.sel[e].count == 0:
                lines.append("→ 選択が0件になった")
            if len(s.sel[e].cond) > 1:
                lines.append(f"→ 累積条件: {s.sel[e].cond_text()}")
        else:
            counts = [
                f"{ENT_NAME[e]} {num_s(s.sel[e].count)}"
                for e in ents
                if s.sel[e].count is not None
            ]
            lines.append("→ " + " / ".join(counts))
            if s.sel["N"].ranges:
                lines.append("→ 節点: " + self._sel_summary("N"))
            if s.sel["E"].attrs:
                lines.append("→ 要素の属性: " + self._attrs_text(s.sel["E"].attrs))
        return lines

    def _apply_sel_details(self, occ: Occ, ents: list[str]) -> None:
        s = self.s
        r = occ.first("SELR")
        if r is not None and "N" in ents:
            s.sel["N"].ranges = {
                k: v
                for k in ("X0", "X1", "Y0", "Y1", "Z0", "Z1")
                if (v := r.num(k)) is not None
            }
        if "E" in ents:
            attrs: dict[str, list[tuple[int, int]]] = {}
            for a in occ.find("SELA"):
                attr = a.tags[-1] if a.tags else "?"
                if a.int("ID") is not None and a.int("N") is not None:
                    attrs.setdefault(attr, []).append((a.int("ID"), a.int("N")))
            if attrs:
                s.sel["E"].attrs = {k: sorted(v) for k, v in attrs.items()}

    @staticmethod
    def _detail_lines(occ: Occ) -> list[str]:
        """詳細レベルの出力（R / θ 範囲、要素の重心範囲）。"""
        out = []
        cyl = []
        for axis in ("Z", "Y"):
            r = occ.first("SELG" + axis)
            if r is not None:
                cyl.append(
                    f"{axis}軸基準 R {_rng(r.num('R0'), r.num('R1'))}"
                    f" θ {_rng(r.num('T0'), r.num('T1'))}°"
                )
        if cyl:
            out.append("→ " + "、".join(cyl))
        c = occ.first("SELC")
        if c is not None:
            out.append(
                "→ 要素の重心（グローバル）: "
                + " | ".join(
                    f"{ax} {_rng(c.num(ax + '0'), c.num(ax + '1'))}" for ax in "XYZ"
                )
            )
        return out

    def _attrs_text(self, attrs: dict[str, list[tuple[int, int]]]) -> str:
        parts = []
        label = {"MAT": "MAT", "TYPE": "TYPE", "REAL": "REAL", "SECN": "SECNUM"}
        for key in ("TYPE", "MAT", "REAL", "SECN"):
            items = attrs.get(key)
            if not items:
                continue
            txt = []
            for i, n in items:
                extra = ""
                if key == "TYPE" and i in self.s.et:
                    extra = f" {self.s.et[i]['ename']}"
                txt.append(f"{i}{extra}: {num_s(n)}")
            parts.append(f"{label[key]} " + ", ".join(txt))
        return " / ".join(parts)

    def _sel_term(self, name: str, args: list[ArgVal]) -> str:
        if name in _REL_TERM:
            return _REL_TERM[name]
        item = (
            args[1].key.strip().upper()
            if len(args) > 1 and not args[1].is_empty
            else _ITEM_DEFAULT.get(name, "")
        )
        comp = args[2].key.strip().upper() if len(args) > 2 else ""
        vmin = args[3].key if len(args) > 3 else ""
        vmax = args[4].key if len(args) > 4 and not args[4].is_empty else vmin
        rng = _rng(parse_num(vmin), parse_num(vmax))
        if rng == "?":
            rng = vmin if vmin == vmax else f"{vmin}〜{vmax}"
        if item.startswith("LOC"):
            return f"{self._coord_name(comp)}={rng}"
        if item[:4] in ("NODE", "ELEM", "KP", "LINE", "AREA", "VOLU"):
            return f"番号 {rng}"
        if item[:3] in ("MAT", "TYP", "REA", "SEC", "ESY"):
            label = {"TYP": "TYPE", "REA": "REAL", "SEC": "SECNUM", "ESY": "ESYS"}.get(
                item[:3], item
            )
            return f"{label}={rng}"
        if item.startswith("ENAM"):
            return f"要素名={self.d.element_name(vmin)}"
        if item.startswith("EXT"):
            return "外表面"
        if item.startswith("CENT"):
            return f"重心の{self._coord_name(comp)}={rng}"
        return f"{item} {comp} {rng}".strip()

    # ---- 要素生成 ----

    def _h_egen(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        s = self.s
        name = occ.entry.get("name", "")
        lines = [f"→ {self._desc(occ, args)}"]
        pre, post = occ.first("CNT", "PRE"), occ.first("CNT", "POST")
        if pre is not None and post is not None:
            de = (post.int("E") or 0) - (pre.int("E") or 0)
            dn = (post.int("N") or 0) - (pre.int("N") or 0)
            dme = (post.int("EM") or 0) - (pre.int("EM") or 0)
            if name in _CREATES:
                created = de if de > 0 else dme
                t = s.attrs.get("TYPE", "1")
                tn = parse_num(t)
                et = s.et.get(int(tn)) if tn is not None else None
                ename = f"（{et['ename']}）" if et else ""
                lines.append(f"→ TYPE {t}{ename} の要素を {num_s(created)} 個作成")
                if name == "PSMESH":
                    s.psmesh.append(
                        {
                            "secid": args[0].key if args else "",
                            "name": args[1].key if len(args) > 1 else "",
                            "elems": created,
                            "loc": loc(occ.entry),
                        }
                    )
            elif name == "EDELE":
                lines.append(f"→ 要素を {num_s(-de)} 個削除")
            elif name == "NUMMRG":
                lines.append(f"→ 選択中の節点 {dn:+,} / 要素 {de:+,}")
            if dn and name not in ("NUMMRG",):
                lines.append(f"→ 選択中の節点 {dn:+,}")
            s.sel["E"].count = post.int("E")
            s.sel["N"].count = post.int("N")
        for r in occ.find("SPR"):
            spr = {
                "E": r.int("E"),
                "T": r.int("T"),
                "EN": r.int("EN"),
                "R": r.int("R"),
                "K": r.vals.get("K"),
                "loc": loc(occ.entry),
            }
            for k in ("I", "J"):
                e = occ.first("SPR" + k)
                if e is not None:
                    spr[k] = (e.int("N"), e.num("X"), e.num("Y"), e.num("Z"))
            s.springs.append(spr)
            lines.append("→ " + self._spring_text(spr))
        if name == "EMODIF":
            rec = self._by_field(occ, "ELEM", 1)
            if rec is not None:
                lines.append("→ 変更後: " + self._elem_target(occ, 1, args[0]))
        return lines

    def _spring_text(self, spr: dict) -> str:
        name = self.d.element_name(spr.get("EN"))
        k = fmt_val(spr.get("K"))
        txt = f"バネ要素 {spr.get('E')}（{name}, REAL {spr.get('R')}, 剛性 {k}）"
        ends = []
        for key in ("I", "J"):
            p = spr.get(key)
            if p:
                ends.append(
                    f"{p[0]} ("
                    + ", ".join(fmt_num(v if v is not None else 0.0) for v in p[1:])
                    + ")"
                )
        if ends:
            txt += " 節点 " + " - ".join(ends)
        return txt

    # ---- 荷重 ----

    def _h_load(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        s = self.s
        name = occ.entry.get("name", "")
        extras = {}
        rot, note = self._rotation(occ, 1) if name in ("D", "DDELE") else (None, "")
        if name in ("D", "DK"):
            labs = [args[1]] if len(args) > 1 else []
            labs += [a for a in args[6:11] if not a.is_empty]
            dofs = [self.d.dof(a.key, rot if rot != "unknown" else None) for a in labs]
            extras["DOFS"] = "、".join(dofs) if dofs else "?"
        text = self._desc(occ, args, **extras)
        lines = [f"→ {text}"]
        if note:
            lines.append(f"→ 節点座標系: {note}")
        # NEND（範囲の終わり）の位置: D / F は 5 番目、DDELE / FDELE は 3 番目
        nend_i = {"D": 4, "F": 4, "DDELE": 2, "FDELE": 2}.get(name)
        if nend_i is not None and len(args) > nend_i and not args[nend_i].is_empty:
            lines.append(f"→ 範囲: 節点 {args[0].disp}〜{args[nend_i].disp}")
        if name == "SLOAD":
            s.sloads.append(
                {
                    "secid": args[0].key if args else "",
                    "text": text,
                    "step": s.step + 1,
                    "loc": loc(occ.entry),
                }
            )
        for a in args:
            if a.runtime == "表":
                lines.append(f"→ 値は表 {a.raw.strip('%')} で与えている")
        s.loads_since.append(f"[{loc(occ.entry)}] {occ.entry.get('text', '')} → {text}")
        return lines

    # ---- コンポーネント ----

    def _h_comp(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        s = self.s
        name = occ.entry.get("name", "")
        lines = [f"→ {self._desc(occ, args)}"]
        if name == "CM" and args:
            cname = args[0].key.strip().upper()
            ent = ENT_OF_ITEM.get(
                (args[1].key.strip().upper()[:4] if len(args) > 1 else ""), None
            )
            rec = occ.first("SEL", "CM")
            info: dict = {"entity": ent, "loc": loc(occ.entry), "count": None}
            if ent:
                sel = s.sel[ent]
                if rec is not None and rec.int(ent) is not None:
                    sel.count = rec.int(ent)
                r = occ.first("SELR", "CM")
                if r is not None and ent == "N":
                    sel.ranges = {
                        k: v
                        for k in ("X0", "X1", "Y0", "Y1", "Z0", "Z1")
                        if (v := r.num(k)) is not None
                    }
                    sel.csys = rec.int("CS") if rec else sel.csys
                info["count"] = sel.count
                info["summary"] = self._sel_summary(ent)
                info["cond"] = sel.cond_text()
                lines.append("→ " + info["summary"])
            s.comps[cname] = info
        elif name == "CMDELE" and args:
            s.comps.pop(args[0].key.strip().upper(), None)
        elif name == "CMGRP" and args:
            members = [a.key.strip().upper() for a in args[1:] if not a.is_empty]
            s.comps[args[0].key.strip().upper()] = {
                "entity": None,
                "loc": loc(occ.entry),
                "count": None,
                "members": members,
            }
        return lines

    # ---- マッピング ----

    def _fname(self, args: list[ArgVal], fi: int, ei: int, default_ext: str) -> str:
        fname = args[fi].key.strip().strip("'") if len(args) > fi else ""
        ext = args[ei].key.strip().strip("'") if len(args) > ei else ""
        if not fname:
            fname = self.s.job or "file"
        return f"{fname}.{ext or default_ext}"

    def _h_map(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        s = self.s
        name = occ.entry.get("name", "")
        lines = [f"→ {self._desc(occ, args)}"]
        seti = occ.first("SETI")
        if seti is not None:
            s.setinfo = {
                "time": seti.vals.get("T"),
                "ls": seti.int("LS"),
                "ss": seti.int("SS"),
            }
        if name in ("NWRITE", "CBDOF", "BFINT"):
            snap = occ.first("SEL", "SNAP")
            if snap is not None:
                sel = s.sel["N"]
                sel.count = snap.int("N")
                sel.csys = snap.int("CS")
                r = occ.first("SELR", "SNAP")
                if r is not None:
                    sel.ranges = {
                        k: v
                        for k in ("X0", "X1", "Y0", "Y1", "Z0", "Z1")
                        if (v := r.num(k)) is not None
                    }
                lines.append(f"→ 直前の選択: {self._sel_summary('N')}")
                lines.append(f"→ 累積条件: {sel.cond_text()}")
            lines += self._handle_chk(occ)
        if name == "NWRITE":
            f = self._fname(args, 0, 1, "node")
            s.nwrites[f.upper()] = {
                "loc": loc(occ.entry),
                "count": s.sel["N"].count,
                "cond": s.sel["N"].cond_text(),
            }
        elif name in ("CBDOF", "BFINT"):
            node_file = self._fname(args, 0, 1, "node")
            out_file = self._fname(args, 3, 4, "cbdo" if name == "CBDOF" else "bfin")
            m = {
                "kind": name,
                "loc": loc(occ.entry),
                "node_file": node_file,
                "out_file": out_file,
                "set": dict(s.setinfo) if s.setinfo else None,
                "rfile": s.rfile,
            }
            m["summary"] = self._aggregate(out_file, name)
            s.mappings.append(m)
            nw = s.nwrites.get(node_file.upper())
            if nw:
                lines.append(
                    f"→ 節点ファイル {node_file}: NWRITE @{nw['loc']}（{num_s(nw['count'])} 節点）"
                )
            else:
                lines.append(f"→ 節点ファイル {node_file}")
            lines.append(f"→ 読み込み中の結果: {self._set_text(m['set'])}")
            lines.append(f"→ 出力ファイル {out_file}: {self._agg_text(m['summary'])}")
        elif name == "SET":
            lines.append(f"→ 読み込んだ結果: {self._set_text(s.setinfo)}")
        elif name == "FILE":
            s.rfile = self._fname(args, 0, 1, "rst")
        return lines

    def _set_text(self, info: dict | None) -> str:
        if not info:
            return "（不明）"
        t = fmt_val(info.get("time"))
        return f"時刻 {t}（ロードステップ {info.get('ls')}, サブステップ {info.get('ss')}）"

    def _find_file(self, name: str) -> Path | None:
        d = self.opts.files_dir
        if d is None or not d.is_dir():
            return None
        target = name.lower()
        for p in d.iterdir():
            if p.name.lower() == target:
                return p
        return None

    def _aggregate(self, fname: str, kind: str) -> dict | None:
        """CBDOF / BFINT の出力ファイルを集計する。"""
        path = self._find_file(fname)
        if path is None:
            return None
        cmd = "D" if kind == "CBDOF" else "BF"
        pat = re.compile(
            rf"^\s*{cmd}\s*,\s*([^,]+?)\s*,\s*([A-Za-z]+)\s*,\s*([^,\s]+)",
            re.IGNORECASE,
        )
        labels: dict[str, list[float]] = {}
        count = 0
        for line in path.read_text(encoding="latin-1").split("\n"):
            m = pat.match(line)
            if not m:
                continue
            count += 1
            v = parse_num(m.group(3))
            lab = m.group(2).upper()
            if v is not None:
                labels.setdefault(lab, []).append(v)
        return {
            "file": str(path),
            "count": count,
            "cmd": cmd,
            "labels": {k: (len(v), min(v), max(v)) for k, v in sorted(labels.items())},
        }

    @staticmethod
    def _agg_text(summary: dict | None) -> str:
        if not summary:
            return "（ファイルが見つからないため集計なし）"
        parts = [
            f"{k} {num_s(n)} [{_rng(a, b)}]"
            for k, (n, a, b) in summary["labels"].items()
        ]
        return (
            f"{summary['cmd']} {num_s(summary['count'])}件（" + " / ".join(parts) + "）"
        )

    # ---- 呼び出し ----

    def call_header(self, occ: Occ) -> list[str]:
        e = occ.entry
        cat = e.get("cat")
        args = self.argvals(occ)
        offset = e.get("arg_offset", 1)
        arg_txt = [
            f"{_arg_name(k - offset + 1)}={a.key}"
            for k, a in enumerate(args, start=1)
            if k >= offset and not a.is_empty and cat != "input"
        ]
        target = e.get("target", e.get("name", "?"))
        how = {"use": "*USE", "call": "呼び出し", "input": "/INPUT"}.get(cat, cat)
        if cat == "input":
            fname = self._fname(args, 0, 1, "")
            fname = fname.rstrip(".")
            target = fname if e.get("external") else target
            mapped = self._match_mapping(fname)
            if mapped is not None:
                return [
                    f"▼ {fname}（/INPUT from {loc(e)}）",
                    "    → " + mapped,
                ]
        extra = "（変換対象外）" if e.get("external") else ""
        head = f"▼ {target}（{how} from {loc(e)}"
        head += (", " + ", ".join(arg_txt)) if arg_txt else ""
        return [head + "）" + extra]

    def _match_mapping(self, fname: str) -> str | None:
        target = Path(fname).name.upper()
        for m in reversed(self.s.mappings):
            if Path(m["out_file"]).name.upper() == target:
                what = "変位" if m["kind"] == "CBDOF" else "温度"
                text = (
                    f"{m['kind']} 出力（{self._set_text(m['set'])}）の{what}を適用"
                    f": {self._agg_text(m['summary'])}"
                )
                self.s.loads_since.append(f"[/INPUT {fname}] {text}")
                return text
        return None

    def start_lines(self, occ: Occ, top: bool) -> list[str]:
        e = occ.entry
        vals = occ.values()
        args = [f"{k}={fmt_val(v)}" for k, v in vals.items() if k.startswith("AR")]
        out = []
        if top:
            out.append(f"▼ {e.get('macro', e.get('file'))}")
        if args:
            out.append("    引数: " + ", ".join(args))
        return out

    # ---- パラメータ・制御 ----

    def _h_set(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        v = fmt_val(occ.values().get("V"))
        target = args[0].raw if args else "?"
        if v is None:
            return [f"→ {target} に代入"]
        return [f"→ {target} = {v}"]

    def _h_get(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        v = fmt_val(occ.values().get("V"))

        def k(i: int) -> str:
            return args[i].key if len(args) > i else ""

        item = self.d.get_item(k(1), k(2), k(3), k(4))
        what = f"{k(1)},{k(2)},{k(3)}" + (f",{k(4)}" if k(4) else "")
        target = args[0].raw if args else "?"
        text = f"→ {target} = {v if v is not None else '?'}（{what}"
        text += f": {item}）" if item else "）"
        return [text]

    def _h_dim(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        return [f"→ {self._desc(occ, args)}"]

    _h_vec = _h_dim

    def _h_if(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        cond = self._cond_text(occ, args)
        block = occ.entry.get("block", True)
        c = occ.first("BR", "C")
        taken = occ.first("BR", "T") is not None
        fell = occ.first("BR", "F") is not None
        if block:
            res = "成立 → この分岐を実行" if taken else "不成立"
        else:
            action = args[-1].raw if args else "?"
            res = "不成立" if fell else f"成立 → {action}"
        if c is None and not taken:
            res = "（評価の記録なし）"
        return [f"→ {cond}: {res}"]

    def _h_elseif(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        return [f"→ {self._cond_text(occ, args)}: 成立 → この分岐を実行"]

    def _h_else(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        return ["→ それまでの条件がすべて不成立 → この分岐を実行"]

    def _cond_text(self, occ: Occ, args: list[ArgVal]) -> str:
        ops = {
            "EQ": "=",
            "NE": "≠",
            "LT": "<",
            "GT": ">",
            "LE": "≤",
            "GE": "≥",
            "ABLT": "|<|",
            "ABGT": "|>|",
        }

        def a(i: int) -> str:
            return args[i].disp if len(args) > i else "?"

        def op(i: int) -> str:
            return ops.get(args[i].key.upper(), args[i].key) if len(args) > i else "?"

        text = f"条件 {a(0)} {op(1)} {a(2)}"
        if len(args) > 4 and args[3].key.upper() in ("AND", "OR", "XOR"):
            text += f" {args[3].key.upper()} {a(4)} {op(5)} {a(6)}"
        return text

    def _h_go(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        return [f"→ {self._desc(occ, args)}（実行）"]

    _h_exit = _h_go
    _h_cycle = _h_go
    _h_return = _h_go
    _h_eof = _h_go

    def _h_create(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        return [f"→ {self._desc(occ, args)}"]

    _h_ulib = _h_create
    _h_end = _h_create
    _h_output = _h_create

    def _h_solve(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        s = self.s
        s.step += 1
        dry = occ.first("SOLVE", "DRY") is not None
        lines = [
            f"→ ステップ {s.step}、時刻 {s.time if s.time is not None else '（未設定）'}"
            + ("（ドライランのため未実行）" if dry else "")
        ]
        loads = list(s.loads_since)
        if loads:
            lines.append(f"→ 前回の SOLVE 以降に設定した荷重・拘束（{len(loads)} 件）:")
            lines += [f"    {x}" for x in loads]
        else:
            lines.append("→ 前回の SOLVE 以降に設定した荷重・拘束: なし")
        chk_lines = self._handle_chk(occ)
        first = not s.solves
        s.solves.append(
            {
                "step": s.step,
                "time": s.time,
                "loc": loc(occ.entry),
                "loads": loads,
                "chk": s.chks[-1] if chk_lines and s.chks else None,
            }
        )
        s.loads_since = []
        lines += chk_lines
        if first:
            pairs = self.contact_pairs()
            if pairs:
                lines.append("→ 接触ペア（初回の SOLVE 時点）:")
                lines += [f"    {p}" for p in pairs]
        return lines

    def _h_step(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        name = occ.entry.get("name", "")
        if name == "TIME" and args:
            self.s.time = args[0].key
        return [f"→ {self._desc(occ, args)}"]

    def _h_other(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        name = occ.entry.get("name", "")
        if self.d.entry(name) is None:
            self.s.unknown[name] += 1
            vals = [a.disp for a in args if a.runtime is not None or a.substituted]
            return [f"→ 値: {', '.join(vals)}"] if vals else []
        return [f"→ {self._desc(occ, args)}"]

    def _h_result(self, occ: Occ, args: list[ArgVal]) -> list[str]:
        return self._h_other(occ, args) + [
            "→ 結果を読むコマンド（ドライランでは値が本番と異なる）"
        ]

    _h_fmtcmd = _h_other
    _h_block = _h_other
    _h_bulk = _h_other

    # ---- チェックポイント ----

    def _handle_chk(self, occ: Occ) -> list[str]:
        s = self.s
        head = occ.first("CHK")
        if head is None:
            return []
        if "DEFER" in head.tags:
            return [
                "→ チェックポイント: Begin レベルのため、次に処理系へ入った時点で出力"
            ]
        c: dict = {"loc": loc(occ.entry), "cmd": occ.entry.get("name", "")}
        n = occ.first("CHKN")
        if n is not None:
            c["nodes"] = n.int("N")
            c["xyz"] = {k: n.num(k) for k in ("X0", "X1", "Y0", "Y1", "Z0", "Z1")}
        else:
            c["nodes"] = 0
        for axis in ("Z", "Y"):
            r = occ.first("CHKN" + axis)
            if r is not None:
                c["r" + axis.lower()] = (r.num("R0"), r.num("R1"))
                c["t" + axis.lower()] = (r.num("T0"), r.num("T1"))
        elems = []
        for r in occ.find("CHKE"):
            elems.append(
                {
                    "T": r.int("T"),
                    "EN": r.int("EN"),
                    "R": r.int("R"),
                    "M": r.int("M"),
                    "N": r.int("N") or 0,
                }
            )
        c["elem_groups"] = elems
        c["elems"] = sum(x["N"] for x in elems)
        c["model"] = self._model_kind(c)
        s.model = c["model"]

        prev = s.chks[-1] if s.chks else None
        if prev is not None and self._same_chk(prev, c):
            s.coord_cmds = []
            s.chks.append(c)
            return [
                f"→ チェックポイント: 前回（@{prev['loc']}）から変化なし（{c['model']}）"
            ]

        lines = []
        if c.get("xyz"):
            x = c["xyz"]
            lines.append(
                f"→ チェックポイント: 節点 {num_s(c['nodes'])} | X {_rng(x['X0'], x['X1'])}"
                f" | Y {_rng(x['Y0'], x['Y1'])} | Z {_rng(x['Z0'], x['Z1'])}"
            )
        else:
            lines.append(f"→ チェックポイント: 節点 {num_s(c['nodes'])}")
        cyl = []
        for axis in ("z", "y"):
            if ("r" + axis) in c:
                r0, r1 = c["r" + axis]
                t0, t1 = c["t" + axis]
                cyl.append(f"{axis.upper()}軸基準 R {_rng(r0, r1)} θ {_rng(t0, t1)}°")
        if cyl:
            lines.append("→ " + "、".join(cyl))
        by_type: dict[int, list[int]] = {}
        for g in elems:
            by_type.setdefault(g["T"], [g["EN"], 0])[1] += g["N"]
        if by_type:
            lines.append(
                "→ 要素: "
                + ", ".join(
                    f"TYPE {t} {self.d.element_name(en)} {num_s(n)}"
                    for t, (en, n) in sorted(by_type.items())
                )
            )
        lines.append(f"→ モデル: {c['model']}")

        if prev is not None and self._changed(prev, c):
            cands = s.coord_cmds or ["（候補のコマンドは記録されていない）"]
            lines.append(
                f"→ 座標が変化（前回 @{prev['loc']} から）。候補: "
                + ", ".join(cands[:10])
                + (f" ほか {len(cands) - 10} 件" if len(cands) > 10 else "")
            )
            c["changed"] = True
        s.coord_cmds = []
        s.chks.append(c)
        return lines

    def _model_kind(self, c: dict) -> str:
        dims = set()
        for g in c.get("elem_groups", []):
            e = self.d.element(g["EN"])
            if e and e.get("dim") in (2, 3):
                dims.add(e["dim"])
        if not dims:
            return "モデル不明" if c.get("elems") else "要素なし"
        if 3 not in dims:
            return "2D"
        eps = self.opts.theta_eps
        flat = []
        for axis in ("z", "y"):
            t = c.get("t" + axis)
            if t and t[0] is not None and t[1] is not None and abs(t[1] - t[0]) < eps:
                flat.append(axis.upper())
        if flat:
            return "3D（θ幅≈0: " + "・".join(f"{a}軸基準" for a in flat) + "）"
        return "3D"

    @classmethod
    def _same_chk(cls, a: dict, b: dict) -> bool:
        """座標範囲・要素の内訳・モデル種別がすべて前回と同じか。"""
        if cls._changed(a, b) or a.get("model") != b.get("model"):
            return False
        for k in ("rz", "tz", "ry", "ty"):
            if a.get(k) != b.get(k):
                return False

        def groups(c: dict) -> list[tuple]:
            return sorted(tuple(sorted(g.items())) for g in c.get("elem_groups", []))

        return groups(a) == groups(b)

    @staticmethod
    def _changed(a: dict, b: dict) -> bool:
        if a.get("nodes") != b.get("nodes"):
            return True
        xa, xb = a.get("xyz") or {}, b.get("xyz") or {}
        for k in ("X0", "X1", "Y0", "Y1", "Z0", "Z1"):
            va, vb = xa.get(k), xb.get(k)
            if va is None or vb is None:
                continue
            scale = max(abs(va), abs(vb), 1.0)
            if abs(va - vb) > 1e-6 * scale:
                return True
        return False

    # ---- 定義一覧（RESUME 後） ----

    def _handle_lists(self, occ: Occ) -> list[str]:
        lines = []
        for r in occ.find("LIST", "BEGIN"):
            cmd = r.tag(1)
            self.s.lists[cmd] = r.raw
            n = self._parse_list(cmd, r.raw)
            lines.append(f"→ {cmd} の出力を取り込み（{n} 件）")
        return lines

    def _parse_list(self, cmd: str, raw: list[str]) -> int:
        s = self.s
        text = "\n".join(raw)
        if cmd == "ETLIST":
            count = 0
            cur = None
            for line in raw:
                m = re.search(r"ELEMENT TYPE\s+(\d+)\s+IS\s+(\S+)", line, re.IGNORECASE)
                if m:
                    cur = int(m.group(1))
                    ename = m.group(2)
                    s.et.setdefault(
                        cur,
                        {
                            "ename": self.d.element_name(ename),
                            "enam": self.d.element_number(ename),
                            "keyopt": {},
                            "loc": "ETLIST",
                        },
                    )
                    count += 1
                    continue
                m = re.search(
                    r"KEYOPT\(\s*(\d+)\s*-\s*(\d+)\)\s*=\s*(.*)", line, re.IGNORECASE
                )
                if m and cur is not None:
                    start = int(m.group(1))
                    for k, v in enumerate(m.group(3).split()):
                        if v not in ("0", "0.0"):
                            s.et[cur]["keyopt"].setdefault(str(start + k), v)
            return count
        if cmd == "RLIST":
            count = 0
            it = iter(raw)
            for line in it:
                m = re.search(r"REAL CONSTANT SET\s+(\d+)", line, re.IGNORECASE)
                if m:
                    vals = next(it, "").split()
                    s.reals.setdefault(int(m.group(1)), vals)
                    count += 1
            return count
        if cmd == "MPLIST":
            count = 0
            mat = None
            lines = [x for x in raw if x.strip()]
            for i, line in enumerate(lines):
                m = re.search(r"MATERIAL NUMBER\s+(\d+)", line, re.IGNORECASE)
                if m:
                    mat = int(m.group(1))
                    s.mats.setdefault(mat, {})
                    count += 1
                    continue
                m = re.search(
                    r"PROPERTY TABLE\s+(\S+)\s+MAT=\s*(\d+)", line, re.IGNORECASE
                )
                if m:
                    mat = int(m.group(2))
                    s.mats.setdefault(mat, {}).setdefault(
                        m.group(1).upper(), "（MPLIST）"
                    )
                    continue
                toks = line.split()
                if (
                    mat is not None
                    and toks
                    and toks[0].upper() == "TEMP"
                    and i + 1 < len(lines)
                ):
                    vals = lines[i + 1].split()
                    labels = toks[1:] if len(vals) < len(toks) else toks
                    for lab, v in zip(labels, vals[-len(labels) :], strict=False):
                        if lab.upper() != "TEMP":
                            s.mats[mat].setdefault(lab.upper(), v)
            return count
        if cmd == "SLIST":
            ids = re.findall(r"Section ID Number:\s*(\d+)", text, re.IGNORECASE)
            types = re.findall(r"Section Type:\s*(\S+)", text, re.IGNORECASE)
            names = re.findall(r"Section Name:\s*(\S*)", text, re.IGNORECASE)
            for k, sid in enumerate(ids):
                s.sections.setdefault(
                    int(sid),
                    {
                        "type": types[k] if k < len(types) else "",
                        "subtype": "",
                        "name": names[k] if k < len(names) else "",
                        "loc": "SLIST",
                    },
                )
            return len(ids)
        if cmd == "CMLIST":
            count = 0
            for line in raw:
                m = re.match(
                    r"^\s*([A-Za-z][\w]*)\s+(NODE|ELEM|KP|LINE|AREA|VOLU)\b(?:\s+(\d+))?",
                    line,
                    re.IGNORECASE,
                )
                if m and m.group(1).upper() not in ("NAME",):
                    s.comps.setdefault(
                        m.group(1).upper(),
                        {
                            "entity": ENT_OF_ITEM.get(m.group(2).upper()[:4]),
                            "count": int(m.group(3)) if m.group(3) else None,
                            "loc": "CMLIST",
                        },
                    )
                    count += 1
            return count
        return 0

    # ---- 付録 ----

    def contact_pairs(self) -> list[str]:
        """初回 SOLVE 時点のチェックポイントから、REAL ごとの接触ペアを作る。"""
        s = self.s
        chk = None
        for sv in s.solves:
            if sv.get("chk"):
                chk = sv["chk"]
                break
        if chk is None and s.chks:
            chk = s.chks[-1]
        if chk is None:
            return []
        by_real: dict[int, list[dict]] = {}
        for g in chk.get("elem_groups", []):
            by_real.setdefault(g["R"], []).append(g)
        out = []
        for r, groups in sorted(by_real.items()):
            kinds = {(self.d.element(g["EN"]) or {}).get("kind") for g in groups}
            if "contact" not in kinds:
                continue
            sides = []
            for g in groups:
                sides.append(
                    f"TYPE {g['T']} {self.d.element_name(g['EN'])} × {num_s(g['N'])}"
                )
            line = f"REAL {r}: " + " / ".join(sides)
            mats = {
                g["M"]
                for g in groups
                if (self.d.element(g["EN"]) or {}).get("kind") == "contact"
            }
            fr = []
            for m in sorted(x for x in mats if x):
                mu = s.mats.get(m, {}).get("MU")
                tb = s.tbs.get((m, "FRIC"))
                if mu is not None:
                    fr.append(f"MAT {m} MU={mu}")
                elif tb is not None:
                    fr.append(f"MAT {m} TB,FRIC {', '.join(tb['data'])}")
                else:
                    fr.append(f"MAT {m} 摩擦の定義なし（MU 未設定なら摩擦0）")
            if fr:
                line += " | 摩擦: " + ", ".join(fr)
            kops = []
            for g in groups:
                elem = self.d.element(g["EN"]) or {}
                if elem.get("kind") != "contact":
                    continue
                et = s.et.get(g["T"], {})
                for k in ("2", "12"):
                    v = et.get("keyopt", {}).get(k, "0")
                    meaning = self.d.keyopt(g["EN"], int(k), v)
                    if meaning:
                        kops.append(meaning)
            if kops:
                line += " | " + ", ".join(dict.fromkeys(kops))
            out.append(line)
        return out

    def appendix(self) -> list[str]:
        s = self.s
        out: list[str] = []

        def section(title: str, lines: list[str]) -> None:
            out.append("")
            out.append(f"■ {title}")
            out.extend(lines if lines else ["  （なし）"])

        et_lines = []
        for t, info in sorted(s.et.items()):
            e = self.d.element(info.get("enam")) or {}
            kops = []
            for k, v in sorted(info.get("keyopt", {}).items(), key=lambda x: int(x[0])):
                meaning = self.d.keyopt(info.get("enam"), int(k), v)
                kops.append(f"KEYOPT({k})={v}" + (f"（{meaning}）" if meaning else ""))
            et_lines.append(
                f"  TYPE {t}: {info.get('ename')}"
                + (f"（{e.get('desc')}）" if e.get("desc") else "")
                + (" " + ", ".join(kops) if kops else "")
                + f" @{info.get('loc', '?')}"
            )
        mat_lines = [
            f"  MAT {m}: " + ", ".join(f"{k}={v}" for k, v in props.items())
            for m, props in sorted(s.mats.items())
        ]
        for (m, lab), tb in sorted(s.tbs.items()):
            mat_lines.append(
                f"  MAT {m}: TB,{lab} {', '.join(tb['data'])} @{tb['loc']}"
            )
        real_lines = []
        for r, vals in sorted(s.reals.items()):
            names = self._real_names(r)
            items = []
            for k, v in enumerate(vals):
                if not v:
                    continue
                label = names[k] if k < len(names) else f"R{k + 1}"
                items.append(f"{label}={v}")
            real_lines.append(f"  REAL {r}: " + ", ".join(items))
        sec_lines = [
            f"  SECNUM {i}: {d.get('type')} {d.get('subtype')} {d.get('name')} @{d.get('loc')}"
            for i, d in sorted(s.sections.items())
        ]
        section(
            "ID→定義表",
            (["  [要素タイプ]"] + et_lines if et_lines else [])
            + (["  [材料]"] + mat_lines if mat_lines else [])
            + (["  [実定数]"] + real_lines if real_lines else [])
            + (["  [断面]"] + sec_lines if sec_lines else [])
            + (
                [
                    f"  ※ RESUME（{', '.join(s.resumed)}）以前の定義は ETLIST などの出力から補った"
                ]
                if s.resumed
                else []
            ),
        )
        section("接触ペア一覧", [f"  {p}" for p in self.contact_pairs()])

        bolt = [
            f"  断面 {p['secid']}（{p['name']}）: 要素 {num_s(p['elems'])} @{p['loc']}"
            for p in s.psmesh
        ]
        bolt += [f"  ステップ {x['step']} @{x['loc']}: {x['text']}" for x in s.sloads]
        section("ボルト（プリテンション）", bolt)

        groups: dict[tuple, list[dict]] = {}
        for spr in s.springs:
            d = None
            if spr.get("I") and spr.get("J"):
                i, j = spr["I"], spr["J"]
                vec = [(j[k] or 0.0) - (i[k] or 0.0) for k in (1, 2, 3)]
                length = sum(v * v for v in vec) ** 0.5
                d = (
                    tuple(round(v / length, 3) for v in vec)
                    if length > 0
                    else (0, 0, 0)
                )
            groups.setdefault((spr.get("EN"), spr.get("K"), d), []).append(spr)
        spr_lines = []
        for (en, k, d), items in groups.items():
            first = items[0]
            spr_lines.append(
                f"  {self.d.element_name(en)} 剛性 {fmt_val(k)} 向き {d if d else '（1節点）'}:"
                f" {len(items)} 本（例: {self._spring_text(first)}）"
            )
        section("バネ一覧", spr_lines)

        comp_lines = []
        for name, c in sorted(s.comps.items()):
            ent = ENT_NAME.get(c.get("entity") or "", "")
            txt = f"  {name}: {ent} {num_s(c.get('count'))}"
            if c.get("members"):
                txt += f"（アセンブリ: {', '.join(c['members'])}）"
            if c.get("summary"):
                txt += f" | {c['summary']}"
            if c.get("cond"):
                txt += f" | 条件: {c['cond']}"
            comp_lines.append(txt + f" @{c.get('loc')}")
        section("コンポーネント一覧", comp_lines)

        step_lines = []
        for sv in s.solves:
            step_lines.append(
                f"  ステップ {sv['step']} 時刻 {sv['time']} @{sv['loc']}: 荷重・拘束 {len(sv['loads'])} 件"
            )
            step_lines += [f"      {x}" for x in sv["loads"]]
        section("荷重ステップ一覧", step_lines)

        map_lines = []
        ms = s.mappings
        shown = ms if len(ms) <= 4 else ms[:2] + [None] + ms[-2:]
        for m in shown:
            if m is None:
                map_lines.append(f"  … {len(ms) - 4} 件省略")
                continue
            map_lines.append(
                f"  {m['kind']} @{m['loc']}: {m['node_file']} → {m['out_file']}"
                f" | {self._set_text(m['set'])} | {self._agg_text(m['summary'])}"
            )
        section("CBDOF / BFINT 出力の集計", map_lines)

        chk_lines = []
        prev: dict | None = None
        repeat = 0
        for c in [*s.chks, None]:
            # 同じ場所で同じ内容が続く分はまとめる
            if (
                c is not None
                and prev is not None
                and c["loc"] == prev["loc"]
                and self._same_chk(prev, c)
            ):
                repeat += 1
                continue
            if repeat:
                chk_lines[-1] += f"（同じ内容が続けて {repeat + 1} 回）"
                repeat = 0
            prev = c
            if c is None:
                break
            x = c.get("xyz") or {}
            txt = f"  @{c['loc']}（{c['cmd']}）: 節点 {num_s(c.get('nodes'))} 要素 {num_s(c.get('elems'))}"
            if x:
                txt += (
                    f" | X {_rng(x.get('X0'), x.get('X1'))} Y {_rng(x.get('Y0'), x.get('Y1'))}"
                    f" Z {_rng(x.get('Z0'), x.get('Z1'))}"
                )
            for axis in ("z", "y"):
                if ("t" + axis) in c:
                    t0, t1 = c["t" + axis]
                    txt += f" | θ({axis.upper()}軸) {_rng(t0, t1)}°"
            txt += f" | {c['model']}" + ("（座標が変化）" if c.get("changed") else "")
            chk_lines.append(txt)
        section("チェックポイントの座標範囲の推移", chk_lines)

        section(
            "辞書に未登録のコマンド（辞書の追加候補）",
            [f"  {k}: {v} 回" for k, v in s.unknown.most_common()],
        )
        return out

    def _real_names(self, r: int) -> list[str]:
        """REAL 番号を使っている要素の実定数名。チェックポイントの内訳から探す。"""
        for c in reversed(self.s.chks):
            for g in c.get("elem_groups", []):
                if g["R"] == r:
                    e = self.d.element(g["EN"]) or {}
                    if e.get("real"):
                        return e["real"]
        return []
