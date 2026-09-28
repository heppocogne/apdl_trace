import json
import re
from pathlib import Path

import pytest

from apdl_trace.convert import Converter, ConvertOptions
from apdl_trace.emit import MAX_FORMAT, Emitter


def _convert(tmp_path: Path, files: dict[str, str], **kw) -> tuple[Path, dict]:
    src = tmp_path / "src"
    src.mkdir()
    for name, text in files.items():
        (src / name).write_text(text, encoding="latin-1", newline="")
    out = tmp_path / "out"
    Converter(src, out, ConvertOptions(**kw)).run()
    tmap = json.loads((out / "trace_map.json").read_text(encoding="utf-8"))
    return out, tmap


def _balance(lines: list[str]) -> None:
    """*IF / *DO の入れ子が壊れていないか。"""
    stack = []
    for ln in lines:
        head = ln.strip().split(",")[0].upper()
        if head == "*IF" and ln.strip().upper().rstrip().endswith("THEN"):
            stack.append("IF")
        elif head in ("*DO", "*DOWHILE"):
            stack.append("DO")
        elif head == "*ENDIF":
            assert stack and stack.pop() == "IF", ln
        elif head == "*ENDDO":
            assert stack and stack.pop() == "DO", ln
    assert not stack


SAMPLE = """\
/BATCH
/PREP7
x0 = 1.5  ! comment
ET,1,185 $ MP,EX,1,2e5
NSEL,S,LOC,X,x0,x0+1
*IF,x0,GT,1,THEN
  D,ALL,UX,0
*ELSE
  D,12,UY,val
*ENDIF
*DO,i,1,3
  sub1,i,2
*ENDDO
*VWRITE,x0
(F10.3)
*CREATE,cm1,mac
NSEL,S,NODE,,ARG1
*END
*USE,cm1,5
SOLVE
"""


def test_convert_structure(tmp_path):
    out, tmap = _convert(tmp_path, {"main.inp": SAMPLE, "sub1.mac": "F,ARG1,FX,ARG2\n"})
    text = (out / "main.inp").read_text(encoding="latin-1")
    lines = text.split("\n")
    _balance(lines)
    # 挿入行は ASCII のみ
    assert all(ord(c) < 128 for c in text)
    # 元のコマンドはすべて残る（SOLVE はドライランでコメント化）
    for cmd in (
        "x0 = 1.5  ! comment",
        "ET,1,185",
        "MP,EX,1,2e5",
        "  sub1,i,2",
        "(F10.3)",
    ):
        assert cmd in lines
    assert "!TRACE-DRYRUN SOLVE" in lines
    assert "SOLVE" not in lines
    # 書式行の直後に *VWRITE の後処理が入る（書式行との間には何も入らない）
    k = lines.index("*VWRITE,x0")
    assert lines[k + 1] == "(F10.3)"
    # /BATCH は先頭のまま
    assert lines[0] == "/BATCH"
    # マクロ呼び出しの前後に CALL / RET
    k = lines.index("  sub1,i,2")
    assert any("|CALL" in x for x in lines[k - 12 : k])
    assert "|RET" in lines[k + 2]
    # 挿入パラメータは TRC...
    names = set(re.findall(r"\b(TRC\w*)", text))
    assert names and all(n.endswith("_") for n in names)
    entries = {e["id"]: e for e in tmap["entries"]}
    assert any(
        e.get("cat") == "call" and e.get("target") == "sub1.mac"
        for e in entries.values()
    )


def test_format_lines_short(tmp_path):
    out, _ = _convert(tmp_path, {"main.inp": SAMPLE, "sub1.mac": "F,ARG1,FX,ARG2\n"})
    for ln in (out / "main.inp").read_text(encoding="latin-1").split("\n"):
        if ln.startswith("TRACE|"):
            assert len(ln) <= MAX_FORMAT + 10, ln


def test_create_block_contains_start(tmp_path):
    out, _ = _convert(tmp_path, {"main.inp": SAMPLE, "sub1.mac": "F,ARG1,FX,ARG2\n"})
    lines = (out / "main.inp").read_text(encoding="latin-1").split("\n")
    a = lines.index("*CREATE,cm1,mac")
    b = lines.index("*END")
    inside = lines[a:b]
    assert any("|START" in x for x in inside)
    assert any("ARG1=%G" in x for x in inside)
    # *CREATE 自体の CMD は外側（前）に出る
    assert any("TRACE|CMD" in x for x in lines[a - 3 : a])


def test_single_line_if_and_label(tmp_path):
    text = "*IF,a,EQ,1,:skip\nD,1,UX,0\n:skip\n*GO,:skip\n"
    out, _ = _convert(tmp_path, {"m.inp": text})
    lines = (out / "m.inp").read_text(encoding="latin-1").split("\n")
    k = lines.index("*IF,a,EQ,1,:skip")
    assert "|F" in lines[k + 2]
    k = lines.index(":skip")
    assert lines[k + 2].startswith("TRACE|CMD")
    _balance(lines)


def test_return_reference_shifts_insertion(tmp_path):
    text = "VMESH,ALL\nok=_RETURN\nD,1,UX,0\n"
    out, _ = _convert(tmp_path, {"m.inp": text})
    lines = (out / "m.inp").read_text(encoding="latin-1").split("\n")
    a = lines.index("VMESH,ALL")
    b = lines.index("ok=_RETURN")
    # VMESH の後処理は ok=_RETURN の後ろに移る（間は前処理のみ）
    between = lines[a + 1 : b]
    assert not any("|POST" in x for x in between)
    assert any("|POST" in x for x in lines[b:])


def test_return_reference_block_if_wraps(tmp_path):
    text = "VMESH,ALL\n*IF,_RETURN,EQ,0,THEN\nD,1,UX,0\n*ENDIF\n"
    out, _ = _convert(tmp_path, {"m.inp": text})
    lines = (out / "m.inp").read_text(encoding="latin-1").split("\n")
    k = lines.index("*IF,_RETURN,EQ,0,THEN")
    assert lines[k - 1] == "_STATUS=TRCSV_"
    assert "TRCRV_=_RETURN" in lines[:k]


def test_return_chain_keeps_value(tmp_path):
    """_RETURN を参照する行が続くとき、送った後処理も退避・復元の内側に入る。"""
    text = "VMESH,ALL\nok=_RETURN\n*IF,_RETURN,GT,0,THEN\nn=1\n*ENDIF\n"
    out, tmap = _convert(tmp_path, {"m.inp": text})
    lines = (out / "m.inp").read_text(encoding="latin-1").split("\n")
    vmesh = next(e["id"] for e in tmap["entries"] if e.get("text") == "VMESH,ALL")
    k = lines.index("ok=_RETURN")
    post = next(i for i, x in enumerate(lines) if x == f"TRACE|CMD|{vmesh:06d}")
    assert k < post
    save = lines.index("TRCRV_=_RETURN")
    restore = lines.index("_RETURN=TRCRV_")
    assert k < save < post < restore < lines.index("*IF,_RETURN,GT,0,THEN")
    assert "*GET" not in "".join(lines[k + 1 : save])


def test_ulib_library(tmp_path):
    lib = "MYMAC1\nD,ALL,UX,0\n/EOF\nMYMAC2\nF,1,FX,ARG1\n/EOF\n"
    main = "*ULIB,lib,mlib\n*USE,MYMAC1\nmymac2,5\n"
    out, tmap = _convert(
        tmp_path, {"main.inp": main, "lib.mlib": lib}, exts=(".inp", ".mlib")
    )
    lines = (out / "lib.mlib").read_text(encoding="latin-1").split("\n")
    assert lines[0] == "MYMAC1"
    assert "|START" in lines[2]
    k = lines.index("/EOF")
    assert (
        "TRACE|CMD" in lines[k - 1]
        or "*ENDIF" in lines[k - 1]
        or "TRACE" in lines[k - 1]
    )
    assert lines[k + 1] == "MYMAC2"
    entries = tmap["entries"]
    assert any(e.get("cat") == "call" for e in entries)


def test_bulk_file_not_traced(tmp_path):
    text = "".join(f"N,{i},{i},0,0\n" for i in range(1, 30))
    out, _ = _convert(tmp_path, {"mesh.inp": text}, bulk_threshold=10)
    body = (out / "mesh.inp").read_text(encoding="latin-1")
    assert body.count("TRACE|CMD") == 0


def test_no_trace_while_output_redirected(tmp_path):
    text = "a=1\n/OUT,res,dat\n*VWRITE,a\n(F5.1)\nb=2\n/OUT\nc=3\n"
    out, _ = _convert(tmp_path, {"m.inp": text})
    lines = (out / "m.inp").read_text(encoding="latin-1").split("\n")
    a, b = lines.index("/OUT,res,dat"), lines.index("/OUT")
    assert not any("TRACE|" in x or "TRC" in x for x in lines[a:b])
    # 切り替えの /OUT 自体は切り替え前に記録する
    assert lines[a - 1].startswith("TRACE|CMD")
    assert any("TRACE|CMD" in x for x in lines[b:])
    assert any("TRACE|CMD" in x for x in lines[:a])
    assert "/OUTPUT でファイルへ出力中" in (out / "convert_warnings.txt").read_text(
        encoding="utf-8"
    )


def test_no_dryrun_keeps_solve(tmp_path):
    out, _ = _convert(tmp_path, {"m.inp": "/SOLU\nSOLVE\n"}, dryrun=False)
    lines = (out / "m.inp").read_text(encoding="latin-1").split("\n")
    assert "SOLVE" in lines


def test_result_warning(tmp_path):
    src_text = "/SOLU\nSOLVE\n/POST1\nSET,LAST\n*GET,u,NODE,1,U,X\n"
    out, _ = _convert(tmp_path, {"m.inp": src_text})
    warn = (out / "convert_warnings.txt").read_text(encoding="utf-8")
    assert "SET,LAST" in warn
    assert "*GET,u,NODE,1,U,X" in warn


def test_sjis_bytes_roundtrip(tmp_path):
    text = "! 日本語コメント\r\nD,1,UX,0 ! ソ\r\n"
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.inp").write_bytes(text.encode("cp932"))
    out = tmp_path / "out"
    Converter(src, out, ConvertOptions()).run()
    data = (out / "m.inp").read_bytes()
    assert text.encode("cp932").split(b"\r\n")[0] in data
    assert "D,1,UX,0 ! ソ".encode("cp932") in data


@pytest.mark.parametrize("level", ["light", "standard", "detail"])
def test_levels_balanced(tmp_path, level):
    out, _ = _convert(
        tmp_path, {"main.inp": SAMPLE, "sub1.mac": "F,ARG1,FX,ARG2\n"}, level=level
    )
    _balance((out / "main.inp").read_text(encoding="latin-1").split("\n"))


def test_msg_chunking():
    em = Emitter()
    items = [(f"K{i}", f"TRCP{i}_", "%G") for i in range(9)]
    lines = em.msg("SEL", 12, items)
    msgs = [x for x in lines if x.startswith("*MSG")]
    fmts = [x for x in lines if x.startswith("TRACE")]
    assert len(msgs) == len(fmts) >= 2
    for m in msgs:
        assert len(m.split(",")) - 2 <= 8
    assert fmts[1].startswith("TRACE|SEL+|000012")
