from apdl_trace.main import main


def test_convert_extract_probe_roundtrip(tmp_path, capsys):
    src = tmp_path / "src"
    src.mkdir()
    (src / "m.inp").write_text("/PREP7\nx=1\n", encoding="latin-1")
    (src / "s.mac").write_text("y=2\n", encoding="latin-1")
    (src / "note.txt").write_text("not a macro\n", encoding="latin-1")
    out = tmp_path / "out"
    assert main(["convert", str(src), str(out), "--ext", ".inp", ".mac"]) == 0
    assert (out / "m.inp").exists() and (out / "s.mac").exists()
    assert not (out / "note.txt").exists()

    run = tmp_path / "run.out"
    run.write_text("", encoding="latin-1")
    report = tmp_path / "r.txt"
    args = ["extract", str(run), "--map", str(out / "trace_map.json")]
    assert main([*args, "-o", str(report)]) == 0
    assert "APDL トレースレポート" in report.read_text(encoding="utf-8")

    probe = tmp_path / "p.inp"
    assert main(["probe", "-o", str(probe)]) == 0
    text = probe.read_text(encoding="latin-1")
    assert "P01" in text and "P13" in text


def test_missing_inputs_are_reported(tmp_path, capsys):
    assert main(["convert", str(tmp_path / "nope"), str(tmp_path / "o")]) == 2
    assert main(["extract", str(tmp_path / "x.out"), "--map", "m.json"]) == 2
    err = capsys.readouterr().err
    assert "見つからない" in err
