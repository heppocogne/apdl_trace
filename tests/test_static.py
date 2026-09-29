from pathlib import Path

from apdl_trace.main import main
from apdl_trace.static import (
    ALL,
    NONE,
    StaticOptions,
    c_and,
    c_inv,
    c_minus,
    c_or,
    render,
    run_static,
)


def _run(tmp_path: Path, files: dict[str, str], entry: str = "m.inp", **kw) -> str:
    for name, text in files.items():
        (tmp_path / name).write_text(text, encoding="latin-1")
    return run_static(tmp_path / entry, None, StaticOptions(**kw))


def test_condition_algebra():
    a, b, c = ("t", "A"), ("t", "B"), ("t", "C")
    assert render(c_and(c_or(a, b), c)) == "（A または B） かつ C"
    assert render(c_or(c_and(a, b), c)) == "（A かつ B） または C"
    assert render(c_minus(a, b)) == "A から B を除く"
    assert c_and(ALL, a) == a and c_or(NONE, a) == a
    assert c_and(NONE, a) == NONE and c_or(ALL, a) == ALL
    assert c_inv(c_inv(a)) == a


def test_selection_chain_and_derived(tmp_path):
    text = (
        "CSYS,1\nNSEL,S,LOC,X,r0\nNSEL,A,LOC,X,r1\nNSEL,R,LOC,Z,0,h\n"
        "ESEL,S,MAT,,2\nESEL,U,TYPE,,5\nNSLE,S\nD,ALL,UY,0\n"
    )
    out = _run(tmp_path, {"m.inp": text})
    assert "選択条件（節点）: （R=r0 または R=r1） かつ Z=0〜h" in out
    assert "選択条件（要素）: MAT=2 から TYPE=5 を除く" in out
    assert "選択条件（節点）: 要素［MAT=2 から TYPE=5 を除く］の節点" in out
    assert "選択中の節点［要素［MAT=2 から TYPE=5 を除く］の節点］ の UY" in out


def test_branch_merge(tmp_path):
    text = (
        "NSEL,S,LOC,X,0\n*IF,k,EQ,1,THEN\nNSEL,R,LOC,Y,0\n*ELSE\n"
        "NSEL,R,LOC,Y,1\n*ENDIF\n*IF,k,EQ,2,THEN\nESEL,S,MAT,,1\n*ENDIF\n"
    )
    out = _run(tmp_path, {"m.inp": text})
    assert "分岐により異なる: 〔X=0 かつ Y=0〕 ／ 〔X=0 かつ Y=1〕" in out
    # ELSE のない *IF は「通らなかった場合」も合わせる
    assert "分岐により異なる: 〔MAT=1〕 ／ 〔全体〕" in out


def test_component_and_get(tmp_path):
    text = (
        "NSEL,S,LOC,Z,0\nCM,bot,NODE\nALLSEL\n*GET,n,NODE,0,COUNT\n"
        "CMSEL,S,bot\n*GET,m,NODE,0,COUNT\nCMSEL,S,unknown\nx=NX(NDNEXT(0))\n"
    )
    out = _run(tmp_path, {"m.inp": text})
    assert "n ← 選択中の節点数（NODE,0,COUNT）［選択中の節点: 全体］" in out
    assert "選択条件（節点）: コンポーネント BOT［Z=0］" in out
    assert (
        "m ← 選択中の節点数（NODE,0,COUNT）［選択中の節点: コンポーネント BOT［Z=0］］"
        in out
    )
    assert "コンポーネント UNKNOWN の種類が分からない" in out
    assert (
        "NDNEXT(): 次に大きい選択節点の番号［選択中の節点: コンポーネント BOT［Z=0］］"
        in out
    )
    assert "BOT（節点）: Z=0 @m.inp:2" in out


def test_coordinate_systems(tmp_path):
    text = (
        "LOCAL,11,1,0,0,10\nNSEL,S,LOC,Y,-5,5\nCSYS,cs\nNSEL,R,LOC,X,1\n"
        "CSYS,0\nRSYS,1\n*GET,s,NODE,5,S,X\nRSYS,SOLU\n"
    )
    out = _run(tmp_path, {"m.inp": text})
    assert "活性座標系: CSYS11（ローカル円筒 11）: X=R, Y=θ, Z=Z'" in out
    assert "選択条件（節点）: θ=-5〜5" in out
    assert "活性座標系: 不明（cs は変数）" in out
    assert "X（座標系は不明）=1" in out
    assert "結果の座標系: 1（グローバル円筒）" in out
    assert "［結果の座標系: RSYS 1］" in out
    assert "結果の座標系: SOLU（各要素・節点の解の座標系）" in out


def test_macros_are_followed(tmp_path):
    files = {
        "m.inp": (
            "*CREATE,sub,mac\nNSEL,A,NODE,,ARG1\n*END\nNSEL,S,LOC,X,0\n"
            "*USE,sub,4\nmymac,1\n*ULIB,lib,mlib\n*USE,LIBM\n/INPUT,part,inp\nloop\n"
            "nomacro_here,1\n*USE,missing\n"
        ),
        "mymac.mac": "ESEL,S,TYPE,,ARG1\n",
        "lib.mlib": "LIBM\nKSEL,S,KP,,1\n/EOF\n",
        "part.inp": "LSEL,S,LINE,,2\n",
        "loop.mac": "loop\n",
    }
    out = _run(tmp_path, files, exts=(".mac", ".inp", ".mlib"))
    assert "▼ SUB（*USE, ARG1=4 / 定義: *CREATE in m.inp:1）" in out
    assert "選択条件（節点）: X=0 または 番号 ARG1" in out
    assert "▼ mymac.mac（呼び出し, ARG1=1）" in out
    assert "▼ LIBM（*USE / 定義: *ULIB lib.mlib）" in out
    assert "選択条件（キーポイント）: 番号 1" in out
    assert "▼ part.inp（/INPUT）" in out
    assert "再帰呼び出しのため読まない" in out
    assert "MISSING は見つからない" in out
    # *CREATE の中身は定義の時点では読まない
    assert out.index("*USE,sub,4") < out.index("NSEL,A,NODE,,ARG1")


def test_brief_skips_other_commands(tmp_path):
    text = "/PREP7\nx=1\nET,1,185\nNSEL,S,LOC,X,0\ny=NX(1)\n"
    out = _run(tmp_path, {"m.inp": text}, brief=True)
    assert "ET,1,185" not in out and "x=1" not in out
    assert "NSEL,S,LOC,X,0" in out and "y=NX(1)" in out


def test_markdown_nests_for_folding(tmp_path):
    files = {
        "m.inp": "*IF,k,EQ,1,THEN\nmymac,1\n*ENDIF\nNSEL,S,NODE,,-2\n",
        "mymac.mac": "ESEL,S,TYPE,,ARG1\n",
    }
    out = _run(tmp_path, files, fmt="md")
    lines = out.splitlines()
    assert "## 流れ" in lines and "## 付録" in lines
    assert "- **▼ m.inp**" in lines
    # *IF の中の呼び出し → ▼ 見出し → マクロの中身、の順に 1 段ずつ深くなる
    assert "    - m.inp:2 `mymac,1`" in lines
    assert "      - **▼ mymac.mac（呼び出し, ARG1=1）**" in lines
    assert "        - mymac.mac:1 `ESEL,S,TYPE,,ARG1`" in lines
    assert "  - m.inp:4 `NSEL,S,NODE,,-2`" in lines
    # APDL の * などは強調として解釈されないようにする
    assert "  - m.inp:1 `*IF,k,EQ,1,THEN`" in lines
    assert any(x.startswith("    - → 条件 k = 1") for x in lines)
    assert "\\*GET" in out  # 付録の見出し


def test_cli_static(tmp_path, capsys):
    (tmp_path / "m.inp").write_text("NSEL,S,LOC,X,0\n", encoding="latin-1")
    report = tmp_path / "r.txt"
    assert main(["static", str(tmp_path / "m.inp"), "-o", str(report)]) == 0
    assert "選択条件（節点）: X=0" in report.read_text(encoding="utf-8")
    assert main(["static", str(tmp_path / "none.inp")]) == 2
