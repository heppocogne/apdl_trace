from apdl_trace import commands, lexer


def test_canonical_four_chars():
    assert commands.canonical("nsel") == "NSEL"
    assert commands.canonical("NSELECT") == "NSEL"
    assert commands.canonical("/SOLUTION") == "/SOLU"
    assert commands.canonical("d") == "D"
    assert commands.canonical("DX") is None
    assert commands.canonical("NSE") is None


def test_canonical_same_prefix():
    assert commands.canonical("*ELSE") == "*ELSE"
    assert commands.canonical("*ELSEIF") == "*ELSEIF"
    assert commands.canonical("*END") == "*END"
    assert commands.canonical("*ENDDO") == "*ENDDO"
    assert commands.canonical("*ENDIF") == "*ENDIF"
    assert commands.canonical("*DO") == "*DO"
    assert commands.canonical("*DOWHILE") == "*DOWHILE"
    assert commands.canonical("/POST1") == "/POST1"
    assert commands.canonical("/POST26") == "/POST26"


def test_split_dollar_and_comment():
    stmts = lexer.lex("ET,1,185 $ MP,EX,1,2e5  ! mat\n")
    cmds = [s for s in stmts if s.kind == "cmd"]
    assert [s.name for s in cmds] == ["ET", "MP"]
    assert cmds[1].fields == ["EX", "1", "2e5"]
    assert cmds[1].render() == "MP,EX,1,2e5 ! mat"


def test_quotes_protect_separators():
    stmts = lexer.lex("a='x$y!z'\n")
    assert len(stmts) == 1
    assert stmts[0].name == "="
    assert stmts[0].fields == ["a", "'x$y!z'"]


def test_parens_protect_commas():
    st = lexer.lex("NSEL,S,LOC,X,a(1,2),b\n")[0]
    assert st.fields == ["S", "LOC", "X", "a(1,2)", "b"]


def test_comment_label_blank():
    stmts = lexer.lex("! c\nC*** old\n/COM,hello $ world\n:lab1\n\n")
    assert [s.kind for s in stmts] == [
        "comment",
        "comment",
        "comment",
        "label",
        "blank",
    ]
    assert stmts[3].name == "LAB1"


def test_format_lines_attached():
    text = "*VWRITE,a\n(F10.3)\n*MSG,INFO,a\nval %G &\nmore\nD,1,UX,0\n"
    stmts = lexer.lex(text)
    cmds = [s for s in stmts if s.kind == "cmd"]
    assert [s.name for s in cmds] == ["*VWRITE", "*MSG", "D"]
    assert [c for c, _ in cmds[0].attached] == ["(F10.3)"]
    assert [c for c, _ in cmds[1].attached] == ["val %G &", "more"]


def test_block_data_attached():
    text = "NBLOCK,6,SOLID\n(3i9,6e21.13e3)\n 1 0 0 1.0 2.0 3.0\n-1\nD,1,UX,0\n"
    stmts = lexer.lex(text)
    cmds = [s for s in stmts if s.kind == "cmd"]
    assert [s.name for s in cmds] == ["NBLOCK", "D"]
    assert len(cmds[0].attached) == 3


def test_cmblock_count():
    text = "CMBLOCK,C1,NODE,3\n(8i10)\n 1 2 3\nD,1,UX,0\n"
    cmds = [s for s in lexer.lex(text) if s.kind == "cmd"]
    assert [s.name for s in cmds] == ["CMBLOCK", "D"]


def test_crlf_and_sjis_bytes_preserved():
    raw = "D,1,UX,0 ! あソ\r\n".encode("cp932").decode("latin-1")
    stmts = lexer.lex(raw)
    assert stmts[0].newline == "\r\n"
    assert stmts[0].render().encode("latin-1") == raw[:-2].encode("latin-1")


def test_classify_value():
    assert lexer.classify_value("") == "empty"
    assert lexer.classify_value("1.5e3") == "number"
    assert lexer.classify_value("'abc'") == "string"
    assert lexer.classify_value("theta0") == "ident"
    assert lexer.classify_value("a(1,2)") == "array"
    assert lexer.classify_value("-theta0") == "expr"
    assert lexer.classify_value("x0+1") == "expr"
    assert lexer.classify_value("SIN(x)") == "expr"
    assert lexer.classify_value("file.ext") == "other"
    assert lexer.classify_value("STRCAT(a,b)") == "array"


def test_substitutions_and_args():
    assert lexer.substitutions("disp_%i%_%j%") == ["i", "j"]
    assert lexer.arg_refs("F,ARG1,FX,ar12 $ x=ARG10") == {"ARG1", "AR12"}
