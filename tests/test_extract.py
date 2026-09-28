import json
from pathlib import Path

from apdl_trace.convert import Converter, ConvertOptions
from apdl_trace.extract import ExtractOptions, build_report, run_extract
from apdl_trace.records import parse_lines

MACRO = """\
/PREP7
CSYS,1
NSEL,S,LOC,Y,ang0
NROTAT,ALL
D,ALL,UY,0
ESEL,S,MAT,,3
*DO,i,1,5
TIME,i
SOLVE
*ENDDO
"""


def _map(tmp_path: Path, files: dict[str, str]) -> tuple[dict, Path]:
    src = tmp_path / "src"
    src.mkdir()
    for name, text in files.items():
        (src / name).write_text(text, encoding="latin-1")
    out = tmp_path / "out"
    Converter(src, out, ConvertOptions()).run()
    return json.loads((out / "trace_map.json").read_text(encoding="utf-8")), out


def _ids(tmap: dict) -> dict[str, int]:
    """元のコマンド文字列 → トレースID（START はファイル名）。"""
    out = {}
    for e in tmap["entries"]:
        if e["kind"] == "start":
            out["START:" + e["macro"]] = e["id"]
        elif e["kind"] == "cmd":
            out.setdefault(e["text"], e["id"])
    return out


def _t(kind: str, tid: int, *rest: str) -> str:
    return "|".join(["TRACE", kind, f"{tid:06d}", *rest])


def _chk(tid: int) -> list[str]:
    return [
        _t("CHK", tid, "NM=      100", "EM=       50"),
        _t("CHKN", tid, "N=  100", "X0= -2.0", "X1=  2.0", "Y0=  0.0", "Y1= 1.0"),
        _t("CHKN+", tid, "Z0= -2.0", "Z1= 2.0"),
        _t("CHKNZ", tid, "R0=0", "R1=2", "T0=-180", "T1=180"),
        _t("CHKNY", tid, "R0=1", "R1=2", "T0=-90", "T1=90"),
        _t("CHKE", tid, "T=1", "EN=185", "R=1", "M=1", "N=40"),
        _t("CHKE", tid, "T=2", "EN=174", "R=3", "M=2", "N=6"),
        _t("CHKE", tid, "T=3", "EN=170", "R=3", "M=2", "N=4"),
    ]


def _out_lines(ids: dict[str, int]) -> list[str]:
    L = [" some mapdl banner", "   " + _t("MAC", ids["START:m.inp"], "START")]
    L += [_t("CMD", ids["/PREP7"]), _t("STATE", ids["/PREP7"], "ROUT", "R=      17")]
    L += [_t("CMD", ids["CSYS,1"]), _t("STATE", ids["CSYS,1"], "CSYS", "CS=  1")]
    n = ids["NSEL,S,LOC,Y,ang0"]
    L += [
        _t("CMD", n),
        _t("VAL", n, "A4= 30.000000"),
        _t("SEL", n, "CS=  1", "N=      1234"),
        _t(
            "SELR",
            n,
            "X0= 1.0",
            "X1= 2.0",
            "Y0= 30.0",
            "Y1= 30.0",
            "Z0= 0.0",
            "Z1= 1.0",
        ),
    ]
    L += [_t("CMD", ids["NROTAT,ALL"]), _t("STATE", ids["NROTAT,ALL"], "CSYS", "CS=1")]
    L += [_t("CMD", ids["D,ALL,UY,0"])]
    e = ids["ESEL,S,MAT,,3"]
    L += [
        _t("CMD", e),
        _t("SEL", e, "CS=1", "E=  120"),
        _t("SELA", e, "MAT", "ID=3", "N=120"),
        _t("SELA", e, "TYPE", "ID=1", "N=100"),
        _t("SELA", e, "TYPE", "ID=2", "N=20"),
    ]
    do = ids["*DO,i,1,5"]
    L += [_t("LOOP", do, "BEGIN")]
    for i in range(1, 6):
        L += [_t("LOOP", do, "ITER", f"V=  {i}.000000")]
        L += [_t("CMD", ids["TIME,i"]), _t("VAL", ids["TIME,i"], f"A1= {i}.0")]
        L += [_t("SOLVE", ids["SOLVE"], "DRY"), *_chk(ids["SOLVE"])]
    L += [_t("LOOP", do, "END")]
    return L


def test_report(tmp_path):
    tmap, _ = _map(tmp_path, {"m.inp": MACRO})
    ids = _ids(tmap)
    recs = parse_lines(_out_lines(ids))
    text = build_report(recs, tmap, ExtractOptions())
    assert "▼ m.inp" in text
    assert "節点を θ = ang0（=30） で新規選択" in text
    assert "1,234 節点 | R 1〜2 | θ 30 | Z 0〜1" in text
    assert "CSYS1（円筒（Z軸））の向きに回転" in text
    assert "の UY（θ方向（周方向）変位） を 0 に拘束" in text
    assert "→ 節点座標系: CSYS1（円筒（Z軸））で回転済み（NROTAT @m.inp:4）" in text
    assert "前回（@m.inp:9）から変化なし（3D）" in text
    assert "（同じ内容が続けて 5 回）" in text
    assert "MAT 3: 120" in text
    assert "（5回, ID" in text
    assert "… 2〜4回目 省略（3回）" in text
    assert "── 5回目（i=5）" in text
    assert "ステップ 5、時刻 5（ドライランのため未実行）" in text
    assert "3D（θ幅≈0" not in text
    # 接触ペア
    assert "REAL 3: TYPE 2 CONTA174 × 6 / TYPE 3 TARGE170 × 4" in text
    # 付録
    assert "■ チェックポイントの座標範囲の推移" in text
    assert "■ 荷重ステップ一覧" in text


def test_report_expand_all(tmp_path):
    tmap, _ = _map(tmp_path, {"m.inp": MACRO})
    recs = parse_lines(_out_lines(_ids(tmap)))
    text = build_report(recs, tmap, ExtractOptions(expand_all=True))
    assert "省略" not in text
    assert "── 3回目（i=3）" in text


def test_flat_model_detected(tmp_path):
    tmap, _ = _map(tmp_path, {"m.inp": "/SOLU\nSOLVE\n"})
    ids = _ids(tmap)
    sid = ids["SOLVE"]
    lines = [_t("SOLVE", sid, "DRY"), _t("CHK", sid, "NM=4", "EM=1")]
    lines += [
        _t("CHKN", sid, "N=4", "X0=1", "X1=2", "Y0=0", "Y1=1", "Z0=0"),
        _t("CHKN+", sid, "Z1=0"),
        _t("CHKNZ", sid, "R0=1", "R1=2.2", "T0=0", "T1=26"),
        _t("CHKNY", sid, "R0=1", "R1=2", "T0=0", "T1=0"),
        _t("CHKE", sid, "T=1", "EN=185", "R=1", "M=1", "N=1"),
    ]
    text = build_report(parse_lines(lines), tmap, ExtractOptions())
    assert "3D（θ幅≈0: Y軸基準）" in text


def test_truncated_lines_do_not_crash(tmp_path):
    """実行が途中で止まり、最後の行が切れていてもレポートを作る。"""
    tmap, _ = _map(tmp_path, {"m.inp": MACRO})
    lines = _out_lines(_ids(tmap))
    for k in range(1, len(lines) + 1):
        cut = lines[:k]
        cut[-1] = cut[-1][: len(cut[-1]) // 2]
        build_report(parse_lines(cut), tmap, ExtractOptions())


def test_detail_level_lines(tmp_path):
    tmap, _ = _map(tmp_path, {"m.inp": "NSEL,S,LOC,X,1\nESEL,S,TYPE,,1\n"})
    ids = _ids(tmap)
    n, e = ids["NSEL,S,LOC,X,1"], ids["ESEL,S,TYPE,,1"]
    lines = [
        _t("SEL", n, "CS=0", "N=4"),
        _t("SELGZ", n, "R0=1", "R1=2", "T0=0", "T1=45"),
        _t("SELGY", n, "R0=1", "R1=1", "T0=0", "T1=0"),
        _t("SEL", e, "CS=0", "E=2"),
        _t("SELC", e, "X0=0.5", "X1=1.5", "Y0=0", "Y1=0", "Z0=1", "Z1=1"),
    ]
    text = build_report(parse_lines(lines), tmap, ExtractOptions())
    assert "Z軸基準 R 1〜2 θ 0〜45°、Y軸基準 R 1 θ 0°" in text
    assert "要素の重心（グローバル）: X 0.5〜1.5 | Y 0 | Z 1" in text


def test_parse_continuation_and_list():
    lines = [
        "x TRACE|SEL|000001|CS=0|N=5",
        "TRACE|SEL+|000001|E=3",
        "TRACE|LIST|000002|BEGIN|ETLIST",
        " ELEMENT TYPE       1 IS SOLID185     3-D 8-NODE STRUCTURAL SOLID",
        "  KEYOPT( 1- 6)=        0      2      0         0      0      0",
        "TRACE|LIST|000002|END|ETLIST",
    ]
    recs = parse_lines(lines)
    assert recs[0].vals == {"CS": "0", "N": "5", "E": "3"}
    assert recs[1].tags == ["BEGIN", "ETLIST"]
    assert len(recs[1].raw) == 2


def test_cbdof_aggregate_and_input(tmp_path):
    macro = "/POST1\nSET,LAST\nCBDOF,nodes,node,,disp,cbdo\n/INPUT,disp,cbdo\n"
    tmap, out = _map(tmp_path, {"m.inp": macro})
    ids = _ids(tmap)
    cb = ids["CBDOF,nodes,node,,disp,cbdo"]
    inp = ids["/INPUT,disp,cbdo"]
    lines = [
        _t("CMD", ids["/POST1"]),
        _t("STATE", ids["/POST1"], "ROUT", "R=31"),
        _t("SETI", ids["SET,LAST"], "T=120.0", "LS=3", "SS=4"),
        _t("SEL", cb, "SNAP", "CS=0", "N=4"),
        _t("SETI", cb, "T=120.0", "LS=3", "SS=4"),
        _t("CMD", cb),
        _t("MAC", inp, "CALL"),
        _t("MAC", inp, "RET"),
    ]
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "DISP.CBDO").write_text(
        "/NOPR\nD,1,UX,0.1\nD,1,UY,-0.2\nD,2,UX,0.3\n", encoding="latin-1"
    )
    (run_dir / "job.out").write_text("\n".join(lines) + "\n", encoding="latin-1")
    text = run_extract(run_dir / "job.out", out / "trace_map.json", ExtractOptions())
    assert "時刻 120（ロードステップ 3, サブステップ 4）" in text
    assert "D 3件（UX 2 [0.1〜0.3] / UY 1 [-0.2]）" in text
    assert "CBDOF 出力（時刻 120" in text
    assert "変位を適用" in text
