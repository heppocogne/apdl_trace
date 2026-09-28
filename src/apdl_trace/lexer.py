"""APDL マクロの字句解析。

ファイルは latin-1 で読む前提（バイト列を 1 対 1 で文字に写すので、Shift_JIS の
コメントなどもバイト単位でそのまま書き戻せる）。構文上の区切り文字はすべて ASCII
なので、判定には影響しない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from apdl_trace import commands

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Stmt:
    """文。1 物理行に `$` で複数のコマンドがあれば、コマンドごとに 1 つ。"""

    kind: str  # "cmd" | "label" | "comment" | "blank"
    lineno: int  # 1 始まりの物理行番号
    raw: str  # 物理行（改行を除く）
    newline: str
    text: str = ""  # コマンド部分（コメントを除き、前後の空白を除いたもの）
    name: str = ""  # 正規化したコマンド名。登録外は大文字化した生の名前。代入は "="
    known: bool = False
    fields: list[str] = field(default_factory=list)  # コマンド名を除く引数
    seg_index: int = 0
    seg_count: int = 1
    indent: str = ""
    comment: str = ""  # 物理行末のコメント（"!" を含む）
    # 直後に続く書式行・データ行（改行を含む生の行）
    attached: list[tuple[str, str]] = field(default_factory=list)

    def code(self) -> str:
        return self.text

    def render(self) -> str:
        """元の見た目で 1 行を作る（改行なし）。"""
        if self.seg_count == 1:
            return self.raw
        line = self.indent + self.text
        if self.seg_index == self.seg_count - 1 and self.comment:
            line += " " + self.comment
        return line


def split_lines(text: str) -> list[tuple[str, str]]:
    """改行（\\n / \\r\\n）だけで分割する。

    `str.splitlines` は \\x85 などでも分割し、Shift_JIS の 2 バイト目を壊すため使わない。
    """
    out: list[tuple[str, str]] = []
    parts = text.split("\n")
    for i, p in enumerate(parts):
        last = i == len(parts) - 1
        if last and p == "":
            break
        nl = "" if last else "\n"
        if p.endswith("\r") and not last:
            p, nl = p[:-1], "\r\n"
        out.append((p, nl))
    return out


def split_comment(line: str) -> tuple[str, str]:
    """`!` 以降をコメントとして分ける（引用符の中は除く）。"""
    q = False
    for i, ch in enumerate(line):
        if ch == "'":
            q = not q
        elif ch == "!" and not q:
            return line[:i], line[i:]
    return line, ""


def _split_top(s: str, sep: str, parens: bool) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    q = False
    depth = 0
    for ch in s:
        if ch == "'":
            q = not q
        elif not q:
            if parens and ch == "(":
                depth += 1
            elif parens and ch == ")":
                depth = max(0, depth - 1)
            elif ch == sep and depth == 0:
                out.append("".join(buf))
                buf = []
                continue
        buf.append(ch)
    out.append("".join(buf))
    return out


def split_fields(text: str) -> list[str]:
    """カンマで区切る（引用符と括弧の中は区切らない）。"""
    return [f.strip() for f in _split_top(text, ",", True)]


def split_dollar(code: str) -> list[str]:
    return _split_top(code, "$", False)


def _find_assign(first: str) -> int:
    q = False
    depth = 0
    for i, ch in enumerate(first):
        if ch == "'":
            q = not q
        elif not q:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "=" and depth == 0:
                return i
    return -1


def parse_command(text: str) -> tuple[str, bool, list[str]]:
    """コマンド文字列を (名前, 登録済みか, 引数) に分ける。

    `name = value` 形式の代入は、名前を "=" とし、引数を [name, value, ...] にする。
    """
    parts = split_fields(text)
    first = parts[0]
    pos = _find_assign(first)
    if pos >= 0:
        target = first[:pos].strip()
        value = first[pos + 1 :].strip()
        return "=", True, [target, value, *parts[1:]]
    canon = commands.canonical(first)
    if canon:
        return canon, True, parts[1:]
    return first.strip().upper(), False, parts[1:]


def _is_minus_one(line: str) -> bool:
    s = line.strip()
    return s.startswith("-1") and (len(s) == 2 or not s[2].isdigit())


def _consume_attached(
    st: Stmt, lines: list[tuple[str, str]], i: int, warnings: list[str]
) -> int:
    """書式行・データ行を st.attached に取り込み、次の行位置を返す。"""
    name = st.name
    if name in commands.FORMAT_LINE_COMMANDS:
        while i < len(lines):
            content, nl = lines[i]
            st.attached.append((content, nl))
            i += 1
            if not content.rstrip().endswith("&"):
                break
        return i
    if name in commands.BLOCK_COMMANDS:
        if i < len(lines):  # 書式行
            st.attached.append(lines[i])
            i += 1
        if name == "CMBLOCK":
            try:
                need = int(float(st.fields[2]))
            except (IndexError, ValueError):
                need = 0
            got = 0
            while i < len(lines) and got < need:
                content, nl = lines[i]
                st.attached.append((content, nl))
                got += len(content.split())
                i += 1
            return i
        while i < len(lines):
            content, nl = lines[i]
            st.attached.append((content, nl))
            i += 1
            up = content.strip().upper()
            if _is_minus_one(content) or up.startswith(("N,R5.", "N,UNBL")):
                break
        else:
            warnings.append(f"{st.lineno}行: {name} のデータ終端が見つからない")
        return i
    return i


def lex(text: str, warnings: list[str] | None = None) -> list[Stmt]:
    """テキストを文の列にする。"""
    if warnings is None:
        warnings = []
    lines = split_lines(text)
    stmts: list[Stmt] = []
    i = 0
    while i < len(lines):
        content, nl = lines[i]
        lineno = i + 1
        i += 1
        stripped = content.strip()
        if not stripped:
            stmts.append(Stmt("blank", lineno, content, nl))
            continue
        if stripped.startswith("!") or stripped.upper().startswith("C***"):
            stmts.append(Stmt("comment", lineno, content, nl))
            continue
        if stripped.startswith(":"):
            code, comment = split_comment(content)
            stmts.append(
                Stmt(
                    "label",
                    lineno,
                    content,
                    nl,
                    text=code.strip(),
                    name=code.strip()[1:].strip().upper(),
                    comment=comment,
                )
            )
            continue

        code, comment = split_comment(content)
        indent = content[: len(content) - len(content.lstrip())]
        head = code.strip().split(",", 1)[0].strip().upper()
        head_canon = commands.canonical(head)
        if head_canon == "/COM":
            stmts.append(Stmt("comment", lineno, content, nl))
            continue
        if head_canon in commands.NO_SPLIT or head.startswith(commands.NO_SPLIT_RAW):
            segs = [code]
        else:
            segs = [s for s in split_dollar(code) if s.strip()]
        if not segs:
            stmts.append(Stmt("comment", lineno, content, nl))
            continue

        parsed = []
        for seg in segs:
            name, known, fields = parse_command(seg.strip())
            parsed.append((seg.strip(), name, known, fields))

        # 書式行・データ行を伴うコマンドが行の途中にあるときは分割しない
        needs_attach = [
            k
            for k, p in enumerate(parsed)
            if p[1] in commands.FORMAT_LINE_COMMANDS or p[1] in commands.BLOCK_COMMANDS
        ]
        if needs_attach and needs_attach[-1] != len(parsed) - 1:
            warnings.append(
                f"{lineno}行: 書式行を伴うコマンドが `$` の途中にあるため分割しない"
            )
            name, known, fields = parse_command(code.strip())
            parsed = [(code.strip(), name, known, fields)]

        n = len(parsed)
        for k, (seg, name, known, fields) in enumerate(parsed):
            st = Stmt(
                "cmd",
                lineno,
                content,
                nl,
                text=seg,
                name=name,
                known=known,
                fields=fields,
                seg_index=k,
                seg_count=n,
                indent=indent,
                comment=comment,
            )
            stmts.append(st)
        i = _consume_attached(stmts[-1], lines, i, warnings)
    return stmts


# ---- 引数の分類 ----

_NUM_RE = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([EeDd][+-]?\d+)?$")
_ARRAY_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\((.*)\)$")
_TOKEN_RE = re.compile(
    r"\s*(?:(?P<num>(\d+\.?\d*|\.\d+)([EeDd][+-]?\d+)?)|(?P<id>[A-Za-z_][A-Za-z0-9_]*)"
    r"|(?P<op>\*\*|[-+*/(),]))"
)
_SUBST_RE = re.compile(r"%([^%\s]+)%")

# 文字列を返す関数。これを含む式は数値として評価しない
CHAR_FUNCS = frozenset(
    {
        "STRCAT",
        "STRCOMP",
        "STRFILL",
        "STRLEFT",
        "STRSUB",
        "UPCASE",
        "LWCASE",
        "CHRVAL",
        "CHRHEX",
        "JOIN",
        "SPLIT",
    }
)


# 数値を返す組み込み関数。`NAME(...)` を配列参照ではなく式として扱う
NUM_FUNCS = frozenset(
    {
        "ABS",
        "SIGN",
        "CXABS",
        "EXP",
        "LOG",
        "LOG10",
        "SQRT",
        "NINT",
        "MOD",
        "RAND",
        "GDIS",
        "SIN",
        "COS",
        "TAN",
        "SINH",
        "COSH",
        "TANH",
        "ASIN",
        "ACOS",
        "ATAN",
        "ATAN2",
        "VALCHR",
        "VALOCT",
        "VALHEX",
        "STRPOS",
        "STRLENG",
        "NX",
        "NY",
        "NZ",
        "KX",
        "KY",
        "KZ",
        "NODE",
        "KP",
        "NSEL",
        "ESEL",
        "KSEL",
        "LSEL",
        "ASEL",
        "VSEL",
        "NDNEXT",
        "ELNEXT",
        "KPNEXT",
        "NNEAR",
        "ENEARN",
        "NELEM",
        "ENEXTN",
        "DISTND",
        "DISTKP",
        "CENTRX",
        "CENTRY",
        "CENTRZ",
        "ARNODE",
        "UX",
        "UY",
        "UZ",
        "ROTX",
        "ROTY",
        "ROTZ",
        "TEMP",
    }
)


def is_number(s: str) -> bool:
    return bool(_NUM_RE.match(s.strip()))


def is_ident(s: str) -> bool:
    return bool(_IDENT_RE.fullmatch(s.strip())) and len(s.strip()) <= 32


def array_ref(s: str) -> tuple[str, str] | None:
    """`A(1,2)` なら ("A", "1,2")。括弧の対応が取れていなければ None。"""
    m = _ARRAY_RE.match(s.strip())
    if not m:
        return None
    inner = m.group(2)
    depth = 0
    for ch in inner:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                return None
    if depth != 0:
        return None
    return m.group(1), inner


def is_expression(s: str) -> bool:
    """数値の式として安全に評価できる形か。

    数値・識別子・演算子・括弧だけから成り、演算子か括弧を含むものに限る。
    ファイル名（`a.b`）や引用符を含むものは式とみなさない。
    """
    s = s.strip()
    if not s or "'" in s or "%" in s:
        return False
    pos = 0
    has_op = False
    depth = 0
    idents: list[str] = []
    while pos < len(s):
        m = _TOKEN_RE.match(s, pos)
        if not m or m.end() == pos:
            return False
        if m.group("op"):
            op = m.group("op")
            has_op = True
            if op == "(":
                depth += 1
            elif op == ")":
                depth -= 1
                if depth < 0:
                    return False
            elif op == "," and depth == 0:
                return False
        elif m.group("id"):
            idents.append(m.group("id").upper())
        pos = m.end()
        while pos < len(s) and s[pos] == " ":
            pos += 1
    if depth != 0 or not has_op:
        return False
    return not any(i in CHAR_FUNCS for i in idents)


def substitutions(s: str) -> list[str]:
    """`%name%` の中身を順に返す。"""
    return [m.group(1) for m in _SUBST_RE.finditer(s)]


_ARG_RE = re.compile(
    r"(?<![A-Za-z0-9_])(ARG[1-9]|AR1[0-9])(?![A-Za-z0-9_])", re.IGNORECASE
)


def arg_refs(text: str) -> set[str]:
    """ARG1〜ARG9, AR10〜AR19 の参照を返す（大文字）。"""
    return {m.group(1).upper() for m in _ARG_RE.finditer(text)}


def classify_value(s: str) -> str:
    """引数の種類: empty / number / string / ident / array / expr / other"""
    s = s.strip()
    if not s:
        return "empty"
    if is_number(s):
        return "number"
    if s.startswith("'"):
        return "string"
    if is_ident(s):
        return "ident"
    ref = array_ref(s)
    if ref:
        if ref[0].upper() in NUM_FUNCS and is_expression(s):
            return "expr"
        return "array"
    if is_expression(s):
        return "expr"
    return "other"
