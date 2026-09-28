"""`.out` からトレース行を抽出し、状態を追跡してレポートを作る。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from apdl_trace.analyze import DescribeOptions, Describer, loc
from apdl_trace.dictionary import Dictionary, fmt_val
from apdl_trace.records import Block, Iteration, Loop, Occ, Rec, parse_out


@dataclass
class ExtractOptions:
    expand_all: bool = False
    expand_ids: set[int] = field(default_factory=set)
    files_dir: Path | None = None
    dict_path: Path | None = None
    theta_eps: float = 0.01
    state_mode: str = "changed"


def _structural(r: Rec) -> bool:
    return r.kind in ("MAC", "LOOP")


class Analyzer:
    """トレース行を順に読み、実行の木を作りながら状態を追跡する。"""

    def __init__(self, entries: list[dict], describer: Describer):
        self.entries = {e["id"]: e for e in entries}
        self.desc = describer
        self.root = Block()
        self.stack: list[Block | Loop | Occ] = [self.root]
        self.cur: Occ | Loop | None = None
        self.unknown_ids: set[int] = set()

    def entry(self, tid: int) -> dict:
        e = self.entries.get(tid)
        if e is None:
            self.unknown_ids.add(tid)
            return {"id": tid, "kind": "cmd", "cat": "other", "text": f"（ID {tid}）"}
        return e

    def block(self) -> Block:
        for c in reversed(self.stack):
            if isinstance(c, Block):
                return c
        return self.root

    def feed(self, recs: list[Rec]) -> None:
        for r in recs:
            self._feed(r)
        self._close()

    def _feed(self, r: Rec) -> None:
        cur = self.cur
        if cur is not None and r.tid == cur.tid and not _structural(r):
            cur.recs.append(r)
            return
        self._close()
        if r.kind == "MAC":
            self._mac(r)
            return
        if r.kind == "LOOP":
            self._loop(r)
            return
        occ = Occ(r.tid, self.entry(r.tid), [r])
        self.block().items.append(occ)
        self.cur = occ

    def _mac(self, r: Rec) -> None:
        tag = r.tag()
        if tag == "CALL":
            occ = Occ(r.tid, self.entry(r.tid), [r], kind="call", body=Block())
            self.block().items.append(occ)
            self.stack += [occ, occ.body]
            self.cur = occ
        elif tag == "START":
            occ = Occ(r.tid, self.entry(r.tid), [r], kind="start")
            blk = self.block()
            # 呼び出しの直後の START は呼び出しの見出しで足りる
            parent = self.stack[-2] if len(self.stack) >= 2 else None
            in_call = (
                isinstance(parent, Occ)
                and parent.kind == "call"
                and parent.body is blk
                and not blk.items
            )
            occ.headed = not in_call
            blk.items.append(occ)
            self.cur = occ
        elif tag == "RET":
            for k in range(len(self.stack) - 1, 0, -1):
                c = self.stack[k]
                if isinstance(c, Occ) and c.kind == "call" and c.tid == r.tid:
                    c.returned = True
                    del self.stack[k:]
                    break

    def _loop(self, r: Rec) -> None:
        tag = r.tag()
        loop = self._find_loop(r.tid)
        if tag == "BEGIN" or (tag == "ITER" and loop is None):
            loop = Loop(r.tid, self.entry(r.tid), [r] if tag == "BEGIN" else [])
            self.block().items.append(loop)
            self.stack.append(loop)
            if tag == "BEGIN":
                self.cur = loop
                return
        if loop is None:
            return
        if tag == "ITER":
            k = self.stack.index(loop)
            del self.stack[k + 1 :]
            it = Iteration(len(loop.iters) + 1, r.vals.get("V"))
            loop.iters.append(it)
            self.stack.append(it.block)
        elif tag == "END":
            k = self.stack.index(loop)
            del self.stack[k:]
            loop.ended = True

    def _find_loop(self, tid: int) -> Loop | None:
        for c in reversed(self.stack):
            if isinstance(c, Loop) and c.tid == tid:
                return c
        return None

    def _close(self) -> None:
        cur = self.cur
        self.cur = None
        if cur is None:
            return
        if isinstance(cur, Loop):
            cur.lines = [f"[{loc(cur.entry)}] {cur.entry.get('text', '')}"]
            fields = cur.entry.get("fields", [])
            vals = []
            for k, v in cur.values().items():
                if not (k.startswith("A") and k[1:].isdigit()):
                    continue
                i = int(k[1:])
                name = fields[i - 1].strip() if i <= len(fields) else k
                vals.append(f"{name}（={fmt_val(v)}）")
            if vals:
                cur.lines.append("    → 値: " + ", ".join(vals))
            return
        if cur.kind == "call":
            cur.lines = self.desc.call_header(cur)
        elif cur.kind == "start":
            cur.lines = self.desc.start_lines(cur, cur.headed)
        else:
            cur.lines = self.desc.describe(cur)


class Renderer:
    def __init__(self, opts: ExtractOptions):
        self.opts = opts
        self.out: list[str] = []

    def emit(self, indent: int, lines: list[str]) -> None:
        pad = " " * indent
        self.out += [pad + x for x in lines]

    def block(self, b: Block, indent: int) -> None:
        for item in b.items:
            if isinstance(item, Loop):
                self.loop(item, indent)
                continue
            self.emit(indent, item.lines)
            if item.body is not None:
                self.block(item.body, indent + 2)
                if not item.returned:
                    self.emit(indent + 2, ["…（呼び出し元へ戻る記録がない）"])

    def loop(self, lp: Loop, indent: int) -> None:
        n = len(lp.iters)
        var = lp.entry.get("var", "")
        head = list(lp.lines) or [f"[{loc(lp.entry)}] {lp.entry.get('text', '')}"]
        head[0] += f"（{n}回, ID {lp.tid}）"
        self.emit(indent, head)
        expand = self.opts.expand_all or lp.tid in self.opts.expand_ids or n <= 2
        shown: list[Iteration | None]
        shown = list(lp.iters) if expand else [lp.iters[0], None, lp.iters[-1]]
        for it in shown:
            if it is None:
                span = "2回目" if n == 3 else f"2〜{n - 1}回目"
                self.emit(indent + 2, [f"… {span} 省略（{n - 2}回）"])
                continue
            v = fmt_val(it.value)
            label = f"── {it.n}回目" + (f"（{var}={v}）" if v is not None else "")
            self.emit(indent + 2, [label])
            self.block(it.block, indent + 2)
        if not lp.ended:
            self.emit(indent + 2, ["…（ループの終了の記録がない）"])


def build_report(
    recs: list[Rec], tmap: dict, opts: ExtractOptions, out_name: str = ""
) -> str:
    dic = Dictionary.load(opts.dict_path)
    describer = Describer(
        dic,
        DescribeOptions(
            theta_eps=opts.theta_eps,
            files_dir=opts.files_dir,
            state_mode=opts.state_mode,
        ),
    )
    an = Analyzer(tmap.get("entries", []), describer)
    an.feed(recs)
    r = Renderer(opts)
    header = [
        "APDL トレースレポート",
        f"出力ファイル: {out_name}" if out_name else "",
        f"トレース行: {len(recs)} 行 / トレースレベル: {tmap.get('level', '?')}"
        + (" / ドライラン" if tmap.get("dryrun") else ""),
        "",
        "━━ 実行の流れ ━━",
    ]
    r.out = [h for h in header if h is not None]
    r.block(an.root, 0)
    r.out.append("")
    r.out.append("━━ 付録 ━━")
    r.out += describer.appendix()
    if an.unknown_ids:
        r.out.append("")
        r.out.append(
            f"※ trace_map.json にない ID が {len(an.unknown_ids)} 件ある"
            "（変換版と trace_map.json の組み合わせを確認すること）"
        )
    return "\n".join(r.out) + "\n"


def run_extract(out_file: Path, map_file: Path, opts: ExtractOptions) -> str:
    tmap = json.loads(map_file.read_text(encoding="utf-8"))
    recs = parse_out(out_file)
    if opts.files_dir is None:
        opts.files_dir = out_file.parent
    return build_report(recs, tmap, opts, out_file.name)
