"""`.out` のトレース行と、それをまとめた実行の木。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from apdl_trace.dictionary import parse_num

MARK = "TRACE|"


@dataclass
class Rec:
    """トレース行 1 つ（分割された続きの行はまとめてある）。"""

    kind: str
    tid: int
    tags: list[str]
    vals: dict[str, str]
    lineno: int
    raw: list[str] = field(default_factory=list)  # LIST の間の行

    def tag(self, i: int = 0) -> str:
        return self.tags[i] if len(self.tags) > i else ""

    def num(self, key: str) -> float | None:
        return parse_num(self.vals.get(key))

    def int(self, key: str) -> int | None:
        v = self.num(key)
        return round(v) if v is not None else None


def parse_trace(text: str, lineno: int) -> Rec | None:
    parts = text.split("|")
    if len(parts) < 3:
        return None
    kind = parts[1].strip()
    try:
        tid = int(parts[2].strip())
    except ValueError:
        return None
    tags: list[str] = []
    vals: dict[str, str] = {}
    for p in parts[3:]:
        if "=" in p:
            k, _, v = p.partition("=")
            vals[k.strip()] = v.strip()
        elif p.strip():
            tags.append(p.strip())
    return Rec(kind, tid, tags, vals, lineno)


def parse_lines(lines: list[str]) -> list[Rec]:
    """出力の行からトレース行を拾う。行中のどこに TRACE| があってもよい。"""
    recs: list[Rec] = []
    capture: Rec | None = None
    for n, line in enumerate(lines, start=1):
        line = line.rstrip("\r\n")
        idx = line.find(MARK)
        if idx < 0:
            if capture is not None:
                capture.raw.append(line)
            continue
        rec = parse_trace(line[idx:], n)
        if rec is None:
            continue
        if rec.kind.endswith("+"):
            base = rec.kind[:-1]
            if recs and recs[-1].tid == rec.tid and recs[-1].kind == base:
                recs[-1].vals.update(rec.vals)
                continue
            rec.kind = base
        if rec.kind == "LIST":
            capture = rec if rec.tag() == "BEGIN" else None
        recs.append(rec)
    return recs


def parse_out(path: Path) -> list[Rec]:
    # トレース行は ASCII。ほかの行の文字コードは問わない
    text = path.read_text(encoding="latin-1")
    return parse_lines(text.split("\n"))


# ---- 実行の木 ----


@dataclass
class Block:
    items: list[Occ | Loop] = field(default_factory=list)


@dataclass
class Occ:
    """コマンド 1 回分の実行（前後に出たトレース行をまとめたもの）。"""

    tid: int
    entry: dict
    recs: list[Rec] = field(default_factory=list)
    kind: str = "cmd"  # cmd / call / start
    lines: list[str] = field(default_factory=list)
    body: Block | None = None
    returned: bool = False
    headed: bool = False  # 見出し（▼）を出すか

    def values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in self.recs:
            if r.kind == "VAL":
                out.update(r.vals)
        return out

    def find(self, kind: str, tag: str | None = None) -> list[Rec]:
        return [
            r for r in self.recs if r.kind == kind and (tag is None or tag in r.tags)
        ]

    def first(self, kind: str, tag: str | None = None) -> Rec | None:
        found = self.find(kind, tag)
        return found[0] if found else None


@dataclass
class Iteration:
    n: int
    value: str | None
    block: Block = field(default_factory=Block)


@dataclass
class Loop:
    tid: int
    entry: dict
    recs: list[Rec] = field(default_factory=list)
    iters: list[Iteration] = field(default_factory=list)
    lines: list[str] = field(default_factory=list)
    ended: bool = False

    def values(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for r in self.recs:
            if r.kind == "VAL":
                out.update(r.vals)
        return out
