"""引数辞書（apdl_dict.json）の読み込みと、説明文の組み立て。"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable
from dataclasses import dataclass
from importlib import resources
from pathlib import Path


def fmt_num(v: float) -> str:
    """数値を読みやすい文字列にする。整数なら小数点を付けない。"""
    if math.isnan(v) or math.isinf(v):
        return str(v)
    if v == int(v) and abs(v) < 1e15:
        return str(int(v))
    return f"{v:.6g}"


def parse_num(s: str | None) -> float | None:
    if s is None:
        return None
    t = s.strip().replace("D", "E").replace("d", "e")
    try:
        return float(t)
    except ValueError:
        return None


_TYPE_MARK = {"1": "配列", "2": "表", "4": "文字配列", "5": "文字列配列"}


def fmt_val(s: str | None) -> str | None:
    """トレース行の値を表示用に整える。"""
    if s is None:
        return None
    t = s.strip()
    if t.startswith("@"):
        return _TYPE_MARK.get(t[1:].strip(), f"型{t[1:].strip()}")
    v = parse_num(t)
    if v is not None:
        return fmt_num(v)
    return t


@dataclass
class ArgVal:
    """引数 1 つ。raw は元の文字列、runtime は実行時の値（あれば）。"""

    raw: str
    runtime: str | None = None
    substituted: str | None = None  # %name% を置き換えた文字列

    @property
    def is_empty(self) -> bool:
        return not self.raw.strip()

    @property
    def key(self) -> str:
        """表引きや条件判定に使う値。"""
        if self.runtime is not None and not self.runtime.startswith(("配列", "表")):
            return self.runtime
        if self.substituted is not None:
            return self.substituted
        return self.raw.strip()

    @property
    def disp(self) -> str:
        raw = self.raw.strip()
        if self.runtime is not None and self.runtime != raw:
            return f"{raw}（={self.runtime}）"
        if self.substituted is not None and self.substituted != raw:
            return f"{raw}（={self.substituted}）"
        return raw

    @classmethod
    def literal(cls, text: str) -> ArgVal:
        return cls(text)


# 書式つきの表示を作る関数: (書式名, 引数の位置(0始まり), 引数) → 文字列
Formatter = Callable[[str, int, ArgVal], str | None]

_PH_RE = re.compile(r"\{([^{}]+)\}")
# [[...]]: 中の引数がすべて空欄なら区間ごと消す
_OPT_RE = re.compile(r"\[\[(.*?)\]\]")


def _norm_key(k: str) -> str:
    t = k.strip().upper()
    v = parse_num(t)
    if v is not None and v == int(v):
        return str(int(v))
    return t


class Dictionary:
    def __init__(self, data: dict):
        self.data = data
        self.commands = {k.upper(): v for k, v in data.items() if not k.startswith("_")}
        self.elements: dict[str, dict] = data.get("_elements", {})
        self._by_ename = {v["name"].upper(): k for k, v in self.elements.items()}

    @classmethod
    def load(cls, path: Path | None = None) -> Dictionary:
        if path is not None:
            text = path.read_text(encoding="utf-8")
        else:
            text = (
                resources.files("apdl_trace")
                .joinpath("data/apdl_dict.json")
                .read_text(encoding="utf-8")
            )
        return cls(json.loads(text))

    def entry(self, name: str) -> dict | None:
        return self.commands.get(name.upper())

    # ---- 表 ----

    def coord(self, csys: int, locals_: dict[int, dict]) -> tuple[str, list[str], bool]:
        """座標系の (種類, 軸名, 未確認か)。"""
        if csys >= 11:
            d = locals_.get(csys)
            kcs = str(d.get("kcs", "0")) if d else "0"
            info = self.data.get("_local_kind", {}).get(kcs)
            if info:
                kind = f"{info['kind']} {csys}" if d else f"ローカル {csys}（定義不明）"
                return kind, info["names"], False
            return f"ローカル {csys}", ["X", "Y", "Z"], False
        info = self.data.get("_coord", {}).get(str(csys))
        if info:
            return info["kind"], info["names"], bool(info.get("unverified"))
        return f"CSYS{csys}", ["X", "Y", "Z"], False

    def coord_is_cyl(self, csys: int, locals_: dict[int, dict]) -> str | None:
        """回転済み節点の自由度の読み替えに使う種類（cyl / sph）。"""
        if csys in (1, 5):
            return "cyl"
        if csys in (2, 6):
            return "sph"
        if csys >= 11:
            d = locals_.get(csys)
            kcs = str(d.get("kcs", "0")) if d else "0"
            return {"1": "cyl", "2": "sph"}.get(kcs)
        return None

    def dof(self, lab: str, rotated: str | None = None) -> str:
        lab = lab.strip().upper()
        if rotated:
            r = self.data.get("_dof_rotated", {}).get(rotated, {}).get(lab)
            if r:
                return f"{lab}（{r}）"
        d = self.data.get("_dof", {}).get(lab)
        return f"{lab}（{d}）" if d else lab

    def load_label(self, lab: str) -> str:
        lab = lab.strip().upper()
        d = self.data.get("_loads", {}).get(lab)
        return f"{lab}（{d}）" if d else lab

    def routine(self, r: int | None) -> str:
        if r is None:
            return "不明"
        return self.data.get("_routines", {}).get(str(r), f"ROUT{r}")

    def element(self, key: str | int | None) -> dict | None:
        """ENAM の番号か要素名（"SOLID185" / "185"）から要素の情報を引く。"""
        if key is None:
            return None
        k = str(key).strip().upper()
        v = parse_num(k)
        if v is not None:
            k = str(int(v))
        if k in self.elements:
            return self.elements[k]
        num = self._by_ename.get(k)
        return self.elements.get(num) if num else None

    def element_number(self, key: str) -> int | None:
        k = key.strip().upper()
        v = parse_num(k)
        if v is not None:
            return int(v)
        num = self._by_ename.get(k)
        if num:
            return int(num)
        m = re.search(r"(\d+)$", k)
        return int(m.group(1)) if m else None

    def element_name(self, key: str | int | None) -> str:
        e = self.element(key)
        if e:
            return e["name"]
        if key is not None and parse_num(str(key)) == 0:
            return "ヌル要素（0）"
        return str(key) if key is not None else "?"

    def keyopt(self, enam: str | int | None, knum: int, value: str) -> str | None:
        e = self.element(enam)
        if not e:
            return None
        k = e.get("keyopt", {}).get(str(knum))
        if not k:
            return None
        v = k.get("values", {}).get(_norm_key(value))
        return f"{k['desc']}: {v}" if v else f"{k['desc']}: {value}"

    def get_item(self, entity: str, entnum: str, item1: str, it1num: str) -> str | None:
        ent = entity.strip().upper()
        n = "0" if _norm_key(entnum or "0") == "0" else "N"
        item = item1.strip().upper()
        table = self.data.get("_get", {})
        for key in (
            f"{ent},{n},{item},{it1num.strip().upper()}",
            f"{ent},{n},{item}",
            f"{ent},N,{item}" if n == "0" else "",
        ):
            if key and key in table:
                return table[key]
        return None

    # ---- 説明文 ----

    def describe(
        self,
        name: str,
        args: list[ArgVal],
        fmt: Formatter | None = None,
        extras: dict[str, str] | None = None,
    ) -> str | None:
        entry = self.entry(name)
        if entry is None:
            return None
        tpl = _choose_template(entry, args, self)
        if tpl is None:
            return entry.get("title")
        return render(tpl, entry, args, fmt, extras or {})


def _arg_index(entry: dict, name: str) -> int | None:
    for i, n in enumerate(entry.get("args", [])):
        if n.upper() == name.upper():
            return i
    return None


def _arg(entry: dict, args: list[ArgVal], name: str) -> ArgVal | None:
    i = _arg_index(entry, name)
    if i is None:
        return None
    av = args[i] if i < len(args) else ArgVal("")
    if av.is_empty:
        d = entry.get("defaults", {}).get(name)
        if d is not None:
            if "{" in d:
                return ArgVal.literal(render(d, entry, args, None, {}))
            return ArgVal.literal(d)
    return av


def _choose_template(entry: dict, args: list[ArgVal], _d: Dictionary) -> str | None:
    for cond, tpl in entry.get("variants", {}).items():
        ok = True
        for c in cond.split(","):
            k, _, v = c.partition("=")
            av = _arg(entry, args, k.strip())
            if av is None:
                ok = False
                break
            key = av.key.strip().upper()
            want = v.strip().upper()
            if not (key == want or (len(want) >= 4 and key[:4] == want[:4])):
                ok = False
                break
        if ok:
            return tpl
    return entry.get("desc")


def render(
    tpl: str,
    entry: dict,
    args: list[ArgVal],
    fmt: Formatter | None,
    extras: dict[str, str],
) -> str:
    def show(name: str, style: str | None) -> str:
        if name in extras:
            return extras[name]
        av = _arg(entry, args, name)
        if av is None:
            return name
        if style and fmt is not None:
            i = _arg_index(entry, name)
            r = fmt(style, i if i is not None else -1, av)
            if r is not None:
                return r
        table = entry.get(name)
        if isinstance(table, dict) and not av.is_empty:
            v = table.get(_norm_key(av.key))
            if v is not None:
                return v if av.runtime is None else f"{v}（{av.disp}）"
        return av.disp if not av.is_empty else "（省略）"

    def repl(m: re.Match[str]) -> str:
        inner = m.group(1)
        if inner.startswith("@"):
            try:
                start = int(inner[1:])
            except ValueError:
                return m.group(0)
            vals = [a.disp for a in args[start - 1 :] if not a.is_empty]
            return ", ".join(vals) if vals else "（なし）"
        if "〜" in inner:
            a, _, b = inner.partition("〜")
            da = show(a.strip(), None)
            bv = _arg(entry, args, b.strip())
            if bv is None or bv.is_empty:
                return da
            db = show(b.strip(), None)
            return da if da == db else f"{da}〜{db}"
        name, _, style = inner.partition(":")
        return show(name.strip(), style.strip() or None)

    def given(inner: str) -> bool:
        if inner.startswith("@"):
            return True
        for part in re.split(r"〜", inner.partition(":")[0]):
            name = part.strip()
            if name in extras:
                return True
            av = _arg(entry, args, name)
            if av is None or not av.is_empty:
                return True
        return False

    def optional(m: re.Match[str]) -> str:
        seg = m.group(1)
        inners = [x.group(1) for x in _PH_RE.finditer(seg)]
        return seg if not inners or any(given(x) for x in inners) else ""

    return _PH_RE.sub(repl, _OPT_RE.sub(optional, tpl))
