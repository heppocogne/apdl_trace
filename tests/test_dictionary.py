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


def test_sel_range_shows_increment():
    # VINC を無視すると 1〜5 の連続範囲に見えるが、実際は 1, 3, 5 だけが対象になる
    assert (
        _desc("ESEL", "S", "ELEM", "", "1", "5", "2")
        == "要素番号 1〜5（2 きざみ） で新規選択"
    )
    assert (
        _desc("NSEL", "S", "NODE", "", "1", "5", "2")
        == "節点番号 1〜5（2 きざみ） で新規選択"
    )
    assert (
        _desc("KSEL", "S", "KP", "", "1", "5", "2")
        == "キーポイント番号 1〜5（2 きざみ） で新規選択"
    )
    assert (
        _desc("LSEL", "S", "LINE", "", "1", "5", "2")
        == "線番号 1〜5（2 きざみ） で新規選択"
    )
    assert (
        _desc("ASEL", "S", "AREA", "", "1", "5", "2")
        == "面積番号 1〜5（2 きざみ） で新規選択"
    )
    assert (
        _desc("VSEL", "S", "VOLU", "", "1", "5", "2")
        == "体積番号 1〜5（2 きざみ） で新規選択"
    )


def test_sel_range_no_increment_note_when_default():
    # VINC 省略時（既定値 1）は連続範囲のまま
    assert _desc("ESEL", "S", "ELEM", "", "1", "5") == "要素番号 1〜5 で新規選択"
    assert _desc("ESEL", "S", "ELEM", "", "1", "5", "1") == "要素番号 1〜5 で新規選択"


def test_sel_single_value_ignores_increment():
    assert _desc("ESEL", "S", "ELEM", "", "3", "", "2") == "要素番号 3 で新規選択"
