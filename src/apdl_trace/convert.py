"""元マクロを読み、トレース用の APDL を挿入した変換版を出力する。"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from apdl_trace import commands, lexer
from apdl_trace.emit import Emitter
from apdl_trace.lexer import Stmt

MAP_VERSION = 1

# バネとして扱う要素（ETYP の ENAM）
SPRING_ENAMES = (14, 39, 40, 214, 250)

# 既知コマンドの値フィールドで、パラメータとして調べない語
_RESERVED_KNOWN = frozenset({"ALL", "NONE", "P", "P51X", "STAT", "DEFA"})

# 未知コマンドで、パラメータとして調べない語（ラベルの可能性が高いもの）
_RESERVED_UNKNOWN = _RESERVED_KNOWN | frozenset(
    {
        "S",
        "R",
        "A",
        "U",
        "INVE",
        "ON",
        "OFF",
        "YES",
        "NO",
        "LAST",
        "FIRST",
        "NEXT",
        "NEAR",
        "LIST",
        "BELOW",
        "INIT",
        "FULL",
        "LOC",
        "NODE",
        "ELEM",
        "KP",
        "LINE",
        "AREA",
        "VOLU",
        "X",
        "Y",
        "Z",
        "UX",
        "UY",
        "UZ",
        "ROTX",
        "ROTY",
        "ROTZ",
        "TEMP",
        "PRES",
        "FX",
        "FY",
        "FZ",
        "MX",
        "MY",
        "MZ",
        "HEAT",
        "TYPE",
        "MAT",
        "REAL",
        "SECN",
        "ESYS",
        "CART",
        "CYL",
        "SPH",
        "TOR",
        "BOTH",
        "COMP",
        "TOTAL",
        "SUM",
        "MAX",
        "MIN",
        "NOPR",
        "APPEND",
        "ERASE",
        "DB",
        "RST",
        "TXT",
        "OUT",
        "INP",
        "MAC",
    }
)

# 結果を読む *GET / *VGET の項目（ドライランで値が変わる）
_RESULT_GET = {
    "NODE": {"U", "ROT", "TEMP", "S", "EPEL", "EPPL", "EPTH", "EPTO", "RF", "HEAT"},
    "ELEM": {"ETAB", "SMISC", "NMISC", "S", "EPEL", "EPPL", "EPTH", "EPTO"},
    "ACTIVE": {"SET", "SOLU"},
    "SORT": None,
    "FSUM": None,
    "PLNSOL": None,
    "PRERR": None,
}


@dataclass
class ConvertOptions:
    exts: tuple[str, ...] = (".mac", ".inp", "")
    level: str = "standard"
    dryrun: bool = True
    ulib: tuple[str, ...] = ()
    cmd_trace: str = "all"  # "all" | "key"
    bulk_threshold: int = 500
    disable_nopr: bool = False
    lists_gopr: bool = False
    encoding: str = "latin-1"


@dataclass
class Unit:
    """出力の単位。文 1 つと、その前後に挿入する行。"""

    stmt: Stmt | None
    body: list[str]
    pre: list[str] = field(default_factory=list)
    post: list[str] = field(default_factory=list)
    cat: str = ""
    refs_ret: bool = False


@dataclass
class _Ctx:
    kind: str  # "file" | "create" | "lib"
    name: str
    do_stack: list[int] = field(default_factory=list)
    if_stack: list[int] = field(default_factory=list)


class TraceMap:
    def __init__(self) -> None:
        self.entries: list[dict] = []

    def add(self, **kw: object) -> int:
        tid = len(self.entries) + 1
        entry = {"id": tid}
        entry.update({k: v for k, v in kw.items() if v not in (None, [], {}, "")})
        self.entries.append(entry)
        return tid

    def get(self, tid: int) -> dict:
        return self.entries[tid - 1]


def display(s: str) -> str:
    """latin-1 で読んだ文字列を、表示用に元の文字コードで読み直す。"""
    raw = s.encode("latin-1", errors="replace")
    for enc in ("utf-8", "cp932"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return s


_RET_RE = re.compile(r"_RETURN|_STATUS", re.IGNORECASE)


class Converter:
    def __init__(self, src: Path, out: Path, opts: ConvertOptions | None = None):
        self.src = src
        self.out = out
        self.opts = opts or ConvertOptions()
        self.em = Emitter(self.opts.level, self.opts.lists_gopr)
        self.tmap = TraceMap()
        self.warnings: list[str] = []
        self.stats: Counter[str] = Counter()
        self.files: list[str] = []
        self.file_names: dict[str, str] = {}  # 大文字のファイル名 → 相対パス
        self.macro_names: dict[str, str] = {}  # 大文字のマクロ名 → 定義元
        self.library_files: set[str] = set()

    # ---- 全体 ----

    def collect(self) -> list[str]:
        exts = {e.lower() for e in self.opts.exts}
        out_res = self.out.resolve()
        files = []
        for p in sorted(self.src.rglob("*")):
            if not p.is_file():
                continue
            if out_res in p.resolve().parents:
                continue
            if p.suffix.lower() not in exts:
                continue
            files.append(p.relative_to(self.src).as_posix())
        return files

    def run(self) -> None:
        self.files = self.collect()
        lexed: dict[str, list[Stmt]] = {}
        texts: dict[str, str] = {}
        for rel in self.files:
            text = (self.src / rel).read_text(encoding=self.opts.encoding)
            texts[rel] = text
            warns: list[str] = []
            lexed[rel] = lexer.lex(text, warns)
            self.warnings += [f"{rel}: {w}" for w in warns]
            name = Path(rel).name.upper()
            self.file_names[name] = rel
            if Path(rel).suffix.lower() == ".mac":
                self.macro_names[Path(rel).stem.upper()] = rel
        self._scan_definitions(lexed)

        for rel in self.files:
            out_lines = self.convert_file(rel, lexed[rel])
            text = texts[rel]
            nl = "\r\n" if "\r\n" in text else "\n"
            body = nl.join(out_lines)
            if text.endswith("\n"):
                body += nl
            dest = self.out / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(body, encoding=self.opts.encoding, newline="")

        self.write_map(self.out / "trace_map.json")
        self.write_warnings(self.out / "convert_warnings.txt")

    def _scan_definitions(self, lexed: dict[str, list[Stmt]]) -> None:
        """*ULIB のライブラリ、*CREATE で作るマクロを先に調べる。"""
        ulib_targets = {Path(u).name.upper() for u in self.opts.ulib}
        for rel, stmts in lexed.items():
            for st in stmts:
                if st.kind != "cmd":
                    continue
                if st.name == "*ULIB" and st.fields:
                    ulib_targets.add(_file_name(st.fields))
                elif st.name == "*CREATE" and st.fields:
                    fname = Path(st.fields[0].strip("'")).name.upper()
                    self.macro_names.setdefault(fname, f"*CREATE in {rel}")
                    self.macro_names.setdefault(
                        _file_name(st.fields), f"*CREATE in {rel}"
                    )
        for target in ulib_targets:
            rel = self.file_names.get(target)
            if rel:
                self.library_files.add(rel)
        for rel in self.library_files:
            expecting = True
            for st in lexed[rel]:
                if st.kind != "cmd":
                    continue
                if expecting:
                    name = st.text.split(",")[0].strip().upper()
                    self.macro_names.setdefault(name, f"*ULIB {rel}")
                    expecting = False
                elif st.name == "/EOF":
                    expecting = True

    # ---- ファイル ----

    def convert_file(self, rel: str, stmts: list[Stmt]) -> list[str]:
        library = rel in self.library_files
        units: list[Unit] = []
        bulk_count = sum(
            1 for s in stmts if s.kind == "cmd" and s.name in commands.BULK_COMMANDS
        )
        bulk = bulk_count > self.opts.bulk_threshold
        if bulk:
            self.warnings.append(
                f"{rel}: N / E が {bulk_count} 行あるため、これらはトレースしない"
            )
        ctxs = [_Ctx("lib" if library else "file", rel)]
        expecting_libname = library
        seen_solve = False
        redirected = False

        for idx, st in enumerate(stmts):
            ctx = ctxs[-1]
            if st.kind in ("blank", "comment"):
                units.append(Unit(st, [st.raw], cat=st.kind))
                continue
            if library and expecting_libname and st.kind == "cmd":
                name = st.text.split(",")[0].strip()
                tid = self.tmap.add(
                    kind="start", file=rel, line=st.lineno, macro=name.upper()
                )
                args = self._scope_args(stmts, idx + 1, {"/EOF"})
                units.append(
                    Unit(st, [st.raw], post=self._start(tid, args), cat="libname")
                )
                expecting_libname = False
                continue
            if st.kind == "label":
                tid = self.tmap.add(
                    kind="label", file=rel, line=st.lineno, text=display(st.text)
                )
                units.append(Unit(st, [st.raw], post=self.em.cmd(tid), cat="label"))
                continue

            if bulk and st.name in commands.BULK_COMMANDS:
                units.append(self._plain(st, "bulk"))
                continue
            if st.name == "/BATCH":
                units.append(self._plain(st, "other"))
                continue

            spec = commands.SPECS.get(st.name) if st.known else None
            cat = spec.cat if spec else ("set" if st.name == "=" else "other")
            if not st.known and st.name in self.macro_names:
                cat = "call"

            if library and st.name == "/EOF":
                units.append(self._plain(st, "eof"))
                expecting_libname = True
                continue
            if cat == "end" and ctx.kind == "create":
                self._check_balance(ctx, rel)
                ctxs.pop()
                units.append(self._plain(st, "end"))
                continue

            if cat == "solve":
                seen_solve = True
            elif seen_solve and self._reads_result(st, cat):
                self.warnings.append(
                    f"{rel}:{st.lineno}: SOLVE の後で結果を読んでいる可能性"
                    f"（ドライランでは値が変わる）: {display(st.text)}"
                )
            if st.name in ("/NOPR", "/OUTPUT", "*DEL"):
                self.stats[st.name] += 1

            unit = self._convert_cmd(st, rel, cat, spec, ctx)
            if st.name == "/OUTPUT":
                head = st.fields[0].strip().upper() if st.fields else ""
                redirected = head not in ("", "TERM")
            if redirected:
                # ファイルへの出力中は、トレース行がそのファイルに混ざるため挿入しない
                if unit.pre or unit.post:
                    self.stats["redirected"] += 1
                unit.pre, unit.post = [], []
            units.append(unit)
            if cat == "create":
                name = _file_name(st.fields) if st.fields else ""
                ctxs.append(_Ctx("create", name))
                args = self._scope_args(stmts, idx + 1, {"*END"})
                start_tid = self.tmap.add(
                    kind="start", file=rel, line=st.lineno, macro=name, created=True
                )
                unit.post += self._start(start_tid, args)

        for ctx in ctxs:
            self._check_balance(ctx, rel)

        if not library:
            tid = self.tmap.add(kind="start", file=rel, line=0, macro=rel)
            start = Unit(None, [], post=self._start(tid, _file_args(stmts)))
            pos = 0
            first = next(
                (k for k, u in enumerate(units) if u.cat not in ("blank", "comment")),
                None,
            )
            if (
                first is not None
                and units[first].stmt is not None
                and (units[first].stmt.name == "/BATCH")
            ):
                pos = first + 1
            units.insert(pos, start)
        return self._assemble(units, rel)

    def _scope_args(self, stmts: list[Stmt], start: int, ends: set[str]) -> set[str]:
        text = []
        for st in stmts[start:]:
            if st.kind == "cmd" and st.name in ends:
                break
            if st.kind == "cmd":
                text.append(st.text)
        return lexer.arg_refs(" ".join(text))

    def _start(self, tid: int, args: set[str]) -> list[str]:
        lines = self.em.mac(tid, "START")
        for a in sorted(args, key=_arg_order):
            lines += self.em.value(tid, a, a)
        return lines

    def _check_balance(self, ctx: _Ctx, rel: str) -> None:
        if ctx.do_stack:
            self.warnings.append(f"{rel}: {ctx.name} で *DO と *ENDDO の対応が取れない")
        if ctx.if_stack:
            self.warnings.append(f"{rel}: {ctx.name} で *IF と *ENDIF の対応が取れない")
        ctx.do_stack.clear()
        ctx.if_stack.clear()

    def _plain(self, st: Stmt, cat: str) -> Unit:
        return Unit(st, self._body(st), cat=cat, refs_ret=bool(_RET_RE.search(st.text)))

    @staticmethod
    def _body(st: Stmt) -> list[str]:
        return [st.render(), *(c for c, _ in st.attached)]

    def _reads_result(self, st: Stmt, cat: str) -> bool:
        if cat == "result" or st.name in ("/POST1", "/POST26", "SET"):
            return True
        if st.name in ("*GET", "*VGET") and len(st.fields) >= 4:
            ent = st.fields[1].upper()
            item = st.fields[3].upper()
            for key, items in _RESULT_GET.items():
                if ent.startswith(key[:4]):
                    return items is None or item in items
        return False

    # ---- コマンド ----

    def _add_entry(self, st: Stmt, rel: str, cat: str, **extra: object) -> int:
        substs: list[str] = []
        for f in st.fields:
            for inner in lexer.substitutions(f):
                if inner.upper() not in (s.upper() for s in substs):
                    substs.append(inner)
        return self.tmap.add(
            kind="cmd",
            file=rel,
            line=st.lineno,
            name=st.name,
            cat=cat,
            text=display(st.text),
            fields=[display(f) for f in st.fields],
            subst=[display(s) for s in substs],
            **extra,
        )

    def _vals(
        self,
        tid: int,
        st: Stmt,
        spec: commands.CmdSpec | None,
        only: set[int] | None = None,
    ) -> list[str]:
        """非リテラルの引数と `%name%` の実行時の値を出す。"""
        lines: list[str] = []
        reserved = _RESERVED_KNOWN if st.known else _RESERVED_UNKNOWN
        for i, f in enumerate(st.fields, start=1):
            if only is not None and i not in only:
                continue
            if spec is not None and i in spec.labels:
                continue
            kind = lexer.classify_value(f)
            if kind == "ident" and f.strip().upper() in reserved:
                continue
            if kind == "expr" and not st.known:
                continue
            if kind in ("ident", "array", "expr"):
                lines += self.em.value(tid, f"A{i}", f)
        entry = self.tmap.get(tid)
        for k, inner in enumerate(entry.get("subst", []), start=1):
            lines += self.em.subst_value(tid, f"S{k}", inner)
        return lines

    def _expand(self, tid: int, st: Stmt, spec: commands.CmdSpec | None) -> list[str]:
        if spec is None:
            return []
        lines: list[str] = []
        nodes = list(spec.nodes)
        elems = list(spec.elems)
        if st.name in ("NSEL", "ESEL") and len(st.fields) >= 2:
            item = st.fields[1].strip().upper()
            if st.name == "NSEL" and item.startswith("NODE"):
                nodes = [4, 5]
            if st.name == "ESEL" and item.startswith("ELEM"):
                elems = [4, 5]
        for i in nodes:
            if _is_number_field(st.fields, i):
                lines += self.em.node_expand(tid, i, st.fields[i - 1])
        for i in elems:
            if _is_number_field(st.fields, i):
                lines += self.em.elem_expand(tid, i, st.fields[i - 1])
        return lines

    def _convert_cmd(
        self,
        st: Stmt,
        rel: str,
        cat: str,
        spec: commands.CmdSpec | None,
        ctx: _Ctx,
    ) -> Unit:
        em = self.em
        body = self._body(st)
        pre: list[str] = []
        post: list[str] = []
        name = st.name
        extra: dict[str, object] = {}

        if cat == "comment":
            return Unit(st, body, cat=cat)

        if cat == "call":
            extra = {"target": self.macro_names[name], "arg_offset": 1}
        elif cat == "use" and st.fields:
            target = _file_name(st.fields[:1])
            resolved = self.macro_names.get(target) or self.file_names.get(target)
            extra = {"target": resolved or display(st.fields[0]), "arg_offset": 2}
            if not resolved:
                extra["external"] = True
        elif cat == "input" and st.fields:
            target = _file_name(st.fields)
            resolved = self.file_names.get(target)
            extra = {"target": resolved or display(target)}
            if not resolved:
                extra["external"] = True
            if len(st.fields) >= 3 and st.fields[2].strip() and resolved:
                self.warnings.append(
                    f"{rel}:{st.lineno}: /INPUT にディレクトリ指定がある。"
                    f"実行時に変換版が読まれるか確認すること: {display(st.text)}"
                )
        elif cat in ("do", "dowhile") and st.fields:
            extra = {"var": display(st.fields[0])}
        elif cat == "if":
            extra = {"block": _is_block_if(st)}
        elif cat == "enddo":
            extra = {"do_id": ctx.do_stack[-1] if ctx.do_stack else None}
        elif cat in ("elseif", "else"):
            extra = {"if_id": ctx.if_stack[-1] if ctx.if_stack else None}

        tid = self._add_entry(st, rel, cat, **extra)
        if cat == "if" and _is_block_if(st):
            ctx.if_stack.append(tid)
        elif cat == "endif":
            if ctx.if_stack:
                ctx.if_stack.pop()
            else:
                self.warnings.append(f"{rel}:{st.lineno}: 対応する *IF がない *ENDIF")
        elif cat in ("do", "dowhile"):
            ctx.do_stack.append(tid)
        elif cat == "enddo":
            if ctx.do_stack:
                ctx.do_stack.pop()
            else:
                self.warnings.append(f"{rel}:{st.lineno}: 対応する *DO がない *ENDDO")

        vals = self._vals(tid, st, spec)

        if cat == "select":
            post = (
                em.cmd(tid)
                + vals
                + self._expand(tid, st, spec)
                + em.sel(tid, spec.sel if spec else "N")
            )
        elif cat == "processor":
            post = em.cmd(tid) + em.state_rout(tid)
            if name in ("/PREP7", "/SOLU", "/POST1"):
                post += em.deferred(tid, with_lists=name == "/PREP7")
        elif cat == "jobname":
            post = em.cmd(tid) + vals + em.state_job(tid)
        elif cat == "resume":
            post = (
                em.cmd(tid)
                + vals
                + em.state_rout(tid)
                + em.state_job(tid)
                + em.after_resume(tid)
            )
        elif cat == "clear":
            post = em.cmd(tid) + em.state_rout(tid) + em.state_job(tid)
            post += em.checkpoint(tid)
        elif cat == "save":
            post = em.cmd(tid) + vals + em.state_job(tid)
        elif cat == "csys":
            post = em.cmd(tid) + vals + em.state_csys(tid)
        elif cat == "nrotat":
            post = em.cmd(tid) + vals + em.state_csys(tid) + self._expand(tid, st, spec)
        elif cat in ("attr", "def", "step", "vec", "dim", "coordmod"):
            post = em.cmd(tid) + vals + self._expand(tid, st, spec)
        elif cat == "egen":
            if name == "EDELE":
                pre = self._expand(tid, st, spec)
            pre = em.counts(tid, "PRE") + pre
            post = em.cmd(tid) + vals + em.counts(tid, "POST")
            if name == "E":
                post += em.spring(tid, SPRING_ENAMES)
            if name != "EDELE":
                post += self._expand(tid, st, spec)
        elif cat == "load":
            post = em.cmd(tid) + vals + self._expand(tid, st, spec)
        elif cat == "comp":
            post = em.cmd(tid) + vals
            if name == "CM":
                ent = _entity_letter(st.fields[1] if len(st.fields) > 1 else "")
                post += em.comp_count(tid, ent)
        elif cat == "map":
            if name in ("NWRITE", "CBDOF", "BFINT"):
                pre = em.checkpoint(tid) + em.sel(tid, "N", "SNAP")
                if name != "NWRITE":
                    pre += em.set_info(tid)
                post = em.cmd(tid) + vals
            elif name == "SET":
                post = em.cmd(tid) + vals + em.set_info(tid)
            else:
                post = em.cmd(tid) + vals
        elif cat in ("call", "use", "input"):
            pre = em.mac(tid, "CALL") + vals
            post = em.mac(tid, "RET")
        elif cat == "set":
            target = st.fields[0] if st.fields else ""
            post = em.cmd(tid) + em.value(tid, "V", target)
        elif cat == "get":
            target = st.fields[0] if st.fields else ""
            post = em.cmd(tid) + vals + em.value(tid, "V", target)
        elif cat in ("do", "dowhile"):
            var = st.fields[0] if st.fields else ""
            pre = em.loop(tid, "BEGIN") + vals
            post = em.loop(tid, "ITER", var if lexer.is_ident(var) else None)
        elif cat == "enddo":
            do_id = extra.get("do_id")
            if isinstance(do_id, int):
                post = em.loop(do_id, "END")
        elif cat == "if":
            pre = em.br(tid, "C") + vals
            post = em.br(tid, "T" if _is_block_if(st) else "F")
        elif cat == "elseif":
            post = em.br(tid, "T") + vals
        elif cat == "else":
            post = em.br(tid, "T")
        elif cat == "endif":
            pass
        elif cat in ("go", "exit", "cycle", "return", "eof") or cat == "create":
            pre = em.cmd(tid) + vals
        elif cat == "ulib":
            post = em.cmd(tid) + vals
        elif cat == "solve":
            tag = "DRY" if self.opts.dryrun else "RUN"
            pre = em.msg("SOLVE", tid, tags=(tag,)) + em.checkpoint(tid)
            if self.opts.dryrun:
                body = [f"!TRACE-DRYRUN {st.text}"]
            else:
                post = em.cmd(tid)
        elif cat == "output":
            if name == "/NOPR" and self.opts.disable_nopr:
                body = [f"!TRACE-NOPR {st.text}"]
            post = em.cmd(tid) + vals
        elif cat == "end":
            post = em.cmd(tid)
        else:
            # other / result / fmtcmd / block / 未登録
            if self.opts.cmd_trace == "all" or cat == "result":
                post = em.cmd(tid) + vals
        return Unit(
            st, body, pre, post, cat=cat, refs_ret=bool(_RET_RE.search(st.text))
        )

    # ---- 組み立て ----

    def _assemble(self, units: list[Unit], rel: str) -> list[str]:
        out: list[str] = []
        for u in units:
            if u.refs_ret and u.pre:
                u.pre = Emitter.save_status() + u.pre + Emitter.restore_status()
                self._warn_status(rel, u)
        pending: list[str] = []
        for i, u in enumerate(units):
            out += u.pre
            out += u.body
            if pending:
                out += pending
                pending = []
            if not u.post:
                continue
            nxt = _next_exec(units, i)
            if nxt is not None and nxt.refs_ret:
                if (
                    nxt.cat not in commands.STRUCTURAL_CATS
                    and not nxt.pre
                    and (nxt.cat != "label")
                ):
                    pending = u.post
                    continue
                out += Emitter.save_status() + u.post + Emitter.restore_status()
                self._warn_status(rel, nxt)
                continue
            out += u.post
        out += pending
        return out

    def _warn_status(self, rel: str, u: Unit) -> None:
        if u.stmt is not None:
            self.warnings.append(
                f"{rel}:{u.stmt.lineno}: _RETURN / _STATUS を退避・復元して挿入した"
                f"（復元できるかは要確認）: {display(u.stmt.text)}"
            )

    # ---- 出力 ----

    def write_map(self, path: Path) -> None:
        data = {
            "version": MAP_VERSION,
            "level": self.opts.level,
            "dryrun": self.opts.dryrun,
            "files": self.files,
            "libraries": sorted(self.library_files),
            "entries": self.tmap.entries,
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8"
        )

    def write_warnings(self, path: Path) -> None:
        lines = list(self.warnings)
        for name in ("/NOPR", "/OUTPUT", "*DEL"):
            if self.stats[name]:
                lines.append(
                    f"{name} が {self.stats[name]} 箇所ある"
                    "（トレース出力が抑止・分散される可能性）"
                )
        if self.stats["redirected"]:
            lines.append(
                f"/OUTPUT でファイルへ出力中の {self.stats['redirected']} コマンドは"
                "トレースしていない（出力ファイルへの混入を避けるため）"
            )
        path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def _is_number_field(fields: list[str], i: int) -> bool:
    """番号として展開する対象か（空・ALL・GUI の選択・コンポーネント名らしきものを除く）。"""
    if i > len(fields):
        return False
    f = fields[i - 1].strip()
    return bool(f) and f.upper() not in _RESERVED_KNOWN


def _file_args(stmts: list[Stmt]) -> set[str]:
    """*CREATE 〜 *END の外で使われている ARGn。"""
    text = []
    in_create = False
    for st in stmts:
        if st.kind != "cmd":
            continue
        if st.name == "*CREATE":
            in_create = True
        elif st.name == "*END" and in_create:
            in_create = False
        elif not in_create:
            text.append(st.text)
    return lexer.arg_refs(" ".join(text))


def _next_exec(units: list[Unit], i: int) -> Unit | None:
    for u in units[i + 1 :]:
        if u.stmt is not None and u.stmt.kind in ("cmd", "label"):
            return u
    return None


def _is_block_if(st: Stmt) -> bool:
    return any(f.strip().upper() == "THEN" for f in st.fields)


def _file_name(fields: list[str]) -> str:
    """(Fname, Ext) から大文字のファイル名を作る。Fname のディレクトリ部分は除く。"""
    fname = fields[0].strip().strip("'") if fields else ""
    ext = fields[1].strip().strip("'") if len(fields) > 1 else ""
    base = re.split(r"[\\/]", fname)[-1]
    return (f"{base}.{ext}" if ext else base).upper()


def _entity_letter(text: str) -> str | None:
    t = text.strip().upper()[:4]
    return {
        "NODE": "N",
        "ELEM": "E",
        "KP": "K",
        "LINE": "L",
        "AREA": "A",
        "VOLU": "V",
    }.get(t)


def _arg_order(a: str) -> int:
    return int(a[3:]) if a.startswith("ARG") else int(a[2:])
