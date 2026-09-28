from apdl_trace.dictionary import ArgVal, Dictionary

D = Dictionary.load()


def _desc(name: str, *raw: str) -> str | None:
    return D.describe(name, [ArgVal(r) for r in raw])


def test_optional_segment_dropped_when_empty():
    assert _desc("ANTYPE", "STATIC") == "解析の種類: 静解析"
    assert _desc("ANTYPE", "STATIC", "REST") == "解析の種類: 静解析（リスタート）"
    assert _desc("NSUBST", "20", "", "5") == "サブステップ数 20、最小 5"


def test_empty_fields_use_defaults():
    assert _desc("ACEL", "", "9.8") == "全体に加速度 (0, 9.8, 0)"
    assert "= 0〜100（SOLVE" in _desc("SFL", "2", "PRES", "", "100")
    assert "= 100（SOLVE" in _desc("SFL", "2", "PRES", "100")
    assert _desc("SET", "LAST") == "結果（ロードステップ 最後）を読み込み"


def test_output_back_to_terminal():
    assert _desc("/OUTPUT") == "出力先を .out に戻す"
    assert _desc("/OUTPUT", "res", "dat").startswith("出力先を res.dat に変更")
