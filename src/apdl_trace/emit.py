"""挿入する APDL の断片を作る。

出力はすべて ASCII にする（変換版は元ファイルと同じバイト列で書き戻すため）。
使うパラメータは `TRC` で始まり `_` で終わる名前に限る。
"""

from __future__ import annotations

from apdl_trace import lexer

LEVELS = ("light", "standard", "detail")

ENTITY = {"N": "NODE", "E": "ELEM", "K": "KP", "L": "LINE", "A": "AREA", "V": "VOLU"}

# ROUT の値
ROUT_BEGIN = 0
ROUT_PREP7 = 17
ROUT_POST1 = 31

# 1 つの *MSG に載せる値の数と、書式行の長さの上限
MAX_VALS = 6
MAX_FORMAT = 76

# 要素の TYPE と REAL を 1 つの数にまとめるときの係数
KEY_SCALE = 1000000

LIST_COMMANDS = ("ETLIST", "MPLIST", "RLIST", "SLIST", "CMLIST")


def tid_str(tid: int) -> str:
    return f"{tid:06d}"


class Emitter:
    def __init__(self, level: str = "standard", lists_gopr: bool = False):
        if level not in LEVELS:
            raise ValueError(f"不明なトレースレベル: {level}")
        self.level = level
        self.lists_gopr = lists_gopr

    # ---- 基本 ----

    def msg(
        self,
        kind: str,
        tid: int,
        items: list[tuple[str, str, str]] | None = None,
        tags: tuple[str, ...] = (),
    ) -> list[str]:
        """`*MSG,INFO` と書式行を作る。

        items は (キー, 値にするパラメータ, 書式) の並び。値が多いときや書式行が
        長くなるときは分割し、続きの行は種別の末尾に "+" を付ける。
        """
        items = items or []
        head = f"TRACE|{kind}|{tid_str(tid)}" + "".join(f"|{t}" for t in tags)
        if not items:
            return ["*MSG,INFO", head]
        out: list[str] = []
        chunk: list[tuple[str, str, str]] = []
        cur_head = head

        def flush() -> None:
            vals = ",".join(v for _, v, _ in chunk)
            fmt = cur_head + "".join(f"|{k}={f}" for k, _, f in chunk)
            out.extend([f"*MSG,INFO,{vals}", fmt])

        for item in items:
            trial = chunk + [item]
            fmt_len = len(cur_head) + sum(len(k) + len(f) + 2 for k, _, f in trial)
            if chunk and (len(trial) > MAX_VALS or fmt_len > MAX_FORMAT):
                flush()
                chunk = []
                cur_head = f"TRACE|{kind}+|{tid_str(tid)}"
            chunk.append(item)
        flush()
        return out

    def cmd(self, tid: int) -> list[str]:
        return self.msg("CMD", tid)

    # ---- 値 ----

    def value(self, tid: int, key: str, text: str) -> list[str]:
        """引数の実行時の値を出す。数値リテラル・文字列・ラベルなどは何も出さない。"""
        kind = lexer.classify_value(text)
        text = text.strip()
        if kind == "ident":
            return self._val_param(tid, key, text, None)
        if kind == "array":
            ref = lexer.array_ref(text)
            assert ref is not None
            return self._val_param(tid, key, ref[0], text)
        if kind == "expr":
            return [f"TRCV_={text}", *self.msg("VAL", tid, [(key, "TRCV_", "%G")])]
        return []

    def _val_param(self, tid: int, key: str, name: str, elem: str | None) -> list[str]:
        """パラメータの型を実行時に調べ、数値なら %G、文字なら %C で出す。"""
        target = elem or name
        num_cond = "*IF,TRCT_,EQ,0,THEN" if elem is None else "*IF,TRCT_,LE,2,THEN"
        chr_type = "3" if elem is None else "4"
        lines = [f"*GET,TRCT_,PARM,{name},TYPE"]
        if elem is not None:
            # 配列要素: 1=数値配列, 2=表, 4=文字配列
            lines += ["*IF,TRCT_,GE,1,THEN", num_cond]
        else:
            lines.append(num_cond)
        lines += self.msg("VAL", tid, [(key, target, "%G")])
        lines.append(f"*ELSEIF,TRCT_,EQ,{chr_type},THEN")
        lines += self.msg("VAL", tid, [(key, target, "%C")])
        lines.append("*ELSEIF,TRCT_,GT,0,THEN")
        lines += self.msg("VAL", tid, [(key, "TRCT_", "@%I")])
        lines.append("*ENDIF")
        if elem is not None:
            lines.append("*ENDIF")
        return lines

    def subst_value(self, tid: int, key: str, inner: str) -> list[str]:
        """`%name%` の中身の値を出す。表（型 2）は "@2" になる。"""
        inner = inner.strip()
        if lexer.is_ident(inner):
            return self._val_param(tid, key, inner, None)
        ref = lexer.array_ref(inner)
        if ref:
            return self._val_param(tid, key, ref[0], inner)
        return []

    # ---- 状態 ----

    def state_rout(self, tid: int) -> list[str]:
        return [
            "*GET,TRCRT_,ACTIVE,0,ROUT",
            *self.msg("STATE", tid, [("R", "TRCRT_", "%I")], ("ROUT",)),
        ]

    def state_job(self, tid: int) -> list[str]:
        return [
            "*GET,TRCJN_,ACTIVE,0,JOBNAM",
            *self.msg("STATE", tid, [("J", "TRCJN_", "%C")], ("JOB",)),
        ]

    def state_csys(self, tid: int) -> list[str]:
        return [
            "*GET,TRCCS_,ACTIVE,0,CSYS",
            *self.msg("STATE", tid, [("CS", "TRCCS_", "%I")], ("CSYS",)),
        ]

    # ---- マクロ・制御 ----

    def mac(self, tid: int, tag: str) -> list[str]:
        return self.msg("MAC", tid, tags=(tag,))

    def loop(self, tid: int, tag: str, var: str | None = None) -> list[str]:
        items = [("V", var, "%G")] if var else []
        return self.msg("LOOP", tid, items, (tag,))

    def br(self, tid: int, tag: str) -> list[str]:
        return self.msg("BR", tid, tags=(tag,))

    # ---- 選択 ----

    def sel(self, tid: int, ents: str, tag: str | None = None) -> list[str]:
        """選択の件数、節点の座標範囲、要素の属性内訳を出す。選択は変えない。"""
        tags = (tag,) if tag else ()
        lines = ["*GET,TRCCS_,ACTIVE,0,CSYS"]
        items = [("CS", "TRCCS_", "%I")]
        for e in ents:
            lines.append(f"*GET,TRCS{e}_,{ENTITY[e]},0,COUNT")
            items.append((e, f"TRCS{e}_", "%I"))
        lines += self.msg("SEL", tid, items, tags)
        if self.level == "light":
            return lines
        if "N" in ents:
            lines += self._node_ranges(tid, tags)
        if "E" in ents:
            lines += self._elem_attrs(tid, tags)
        if self.level == "detail":
            if "N" in ents:
                lines += [
                    "*IF,TRCSN_,GT,0,THEN",
                    "*GET,TRCCS_,ACTIVE,0,CSYS",
                    "CSYS,0",
                    *self._node_vectors(tid, "SELG", selected_only=True, tags=tags),
                    "CSYS,TRCCS_",
                    "*ENDIF",
                ]
            if "E" in ents:
                lines += self._elem_centroids(tid, tags)
        return lines

    def _node_ranges(self, tid: int, tags: tuple[str, ...]) -> list[str]:
        """選択中の節点の座標範囲（MNLOC / MXLOC）。"""
        lines = ["*IF,TRCSN_,GT,0,THEN"]
        items = []
        for ax in "XYZ":
            lines.append(f"*GET,TRC{ax}0_,NODE,0,MNLOC,{ax}")
            lines.append(f"*GET,TRC{ax}1_,NODE,0,MXLOC,{ax}")
            items += [(f"{ax}0", f"TRC{ax}0_", "%G"), (f"{ax}1", f"TRC{ax}1_", "%G")]
        lines += self.msg("SELR", tid, items, tags)
        lines.append("*ENDIF")
        return lines

    def _alloc(self, names: tuple[str, ...], size: str) -> list[str]:
        lines = [f"*DEL,{n},,NOPR" for n in names]
        lines += [f"*DIM,{n},,{size}" for n in names]
        return lines

    def _free(self, names: tuple[str, ...]) -> list[str]:
        return [f"*DEL,{n},,NOPR" for n in names]

    @staticmethod
    def _distinct_loop(body: list[str]) -> list[str]:
        """TRCVA_ に入った正の値を、大きい順に 1 種類ずつ処理する。

        body では TRCI_（現在の値）と TRCVB_（TRCI_ と等しい所が 1 の配列）、
        TRCK_（その件数）が使える。
        """
        return [
            "*VSCFUN,TRCI_,MAX,TRCVA_(1)",
            "*DOWHILE,TRCI_",
            "*VOPER,TRCVB_(1),TRCVA_(1),EQ,TRCI_",
            "*VSCFUN,TRCK_,SUM,TRCVB_(1)",
            *body,
            "*VOPER,TRCVB_(1),TRCVB_(1),MULT,TRCI_",
            "*VOPER,TRCVA_(1),TRCVA_(1),SUB,TRCVB_(1)",
            "*VSCFUN,TRCI_,MAX,TRCVA_(1)",
            "*ENDDO",
        ]

    def _elem_attrs(self, tid: int, tags: tuple[str, ...]) -> list[str]:
        """選択中の要素の MAT / TYPE / REAL / SECNUM ごとの件数。"""
        arrays = ("TRCVS_", "TRCVA_", "TRCVB_")
        lines = [
            "*IF,TRCSE_,GT,0,THEN",
            "*GET,TRCEM_,ELEM,0,NUM,MAXD",
            *self._alloc(arrays, "TRCEM_"),
            "*VGET,TRCVS_(1),ELEM,1,ESEL",
            "*VOPER,TRCVS_(1),TRCVS_(1),GT,0",
        ]
        for attr in ("MAT", "TYPE", "REAL", "SECN"):
            lines += [
                f"*VGET,TRCVA_(1),ELEM,1,ATTR,{attr}",
                "*VOPER,TRCVA_(1),TRCVA_(1),MULT,TRCVS_(1)",
                *self._distinct_loop(
                    self.msg(
                        "SELA",
                        tid,
                        [("ID", "TRCI_", "%I"), ("N", "TRCK_", "%I")],
                        (*tags, attr),
                    ),
                ),
            ]
        lines += self._free(arrays)
        lines.append("*ENDIF")
        return lines

    def _elem_centroids(self, tid: int, tags: tuple[str, ...]) -> list[str]:
        """選択中の要素の重心範囲（グローバル）。"""
        arrays = ("TRCVS_", "TRCVX_", "TRCVY_", "TRCVZ_")
        lines = [
            "*IF,TRCSE_,GT,0,THEN",
            "*GET,TRCCS_,ACTIVE,0,CSYS",
            "CSYS,0",
            "*GET,TRCEM_,ELEM,0,NUM,MAXD",
            *self._alloc(arrays, "TRCEM_"),
            "*VGET,TRCVS_(1),ELEM,1,ESEL",
            "*VOPER,TRCVS_(1),TRCVS_(1),GT,0",
        ]
        items = []
        for ax in "XYZ":
            lines.append(f"*VGET,TRCV{ax}_(1),ELEM,1,CENT,{ax}")
            lines += self._masked_minmax(f"TRCV{ax}_", f"TRC{ax}0_", f"TRC{ax}1_")
            items += [(f"{ax}0", f"TRC{ax}0_", "%G"), (f"{ax}1", f"TRC{ax}1_", "%G")]
        lines += self.msg("SELC", tid, items, tags)
        lines += self._free(arrays)
        lines += ["CSYS,TRCCS_", "*ENDIF"]
        return lines

    def _masked_minmax(self, arr: str, pmin: str, pmax: str) -> list[str]:
        return [
            "*VMASK,TRCVS_(1)",
            f"*VSCFUN,{pmin},MIN,{arr}(1)",
            "*VMASK,TRCVS_(1)",
            f"*VSCFUN,{pmax},MAX,{arr}(1)",
        ]

    def _node_vectors(
        self,
        tid: int,
        kind: str,
        selected_only: bool,
        tags: tuple[str, ...] = (),
    ) -> list[str]:
        """節点のグローバル XYZ 範囲と、Z 軸・Y 軸基準の R / θ 範囲。

        CSYS,0 にしてから呼ぶこと。θ は度で出す（*AFUN の設定によらないよう、
        ATAN(1) から換算係数を求める）。Y 軸基準の θ は atan2(-Z, X)。
        """
        arrays = ("TRCVS_", "TRCVX_", "TRCVY_", "TRCVZ_", "TRCVA_", "TRCVB_")
        lines = [
            "*GET,TRCNM_,NODE,0,NUM,MAXD",
            "TRCAF_=45/ATAN(1)",
            *self._alloc(arrays, "TRCNM_"),
            "*VGET,TRCVS_(1),NODE,1,NSEL",
        ]
        if selected_only:
            lines.append("*VOPER,TRCVS_(1),TRCVS_(1),GT,0")
        else:
            # -1（非選択）と 1（選択）を 1、0（未定義）を 0 にする
            lines.append("*VOPER,TRCVS_(1),TRCVS_(1),MULT,TRCVS_(1)")
        lines.append("*VSCFUN,TRCK_,SUM,TRCVS_(1)")
        items = [("N", "TRCK_", "%I")]
        for ax in "XYZ":
            lines.append(f"*VGET,TRCV{ax}_(1),NODE,1,LOC,{ax}")
            lines += self._masked_minmax(f"TRCV{ax}_", f"TRC{ax}0_", f"TRC{ax}1_")
            items += [(f"{ax}0", f"TRC{ax}0_", "%G"), (f"{ax}1", f"TRC{ax}1_", "%G")]
        lines += self.msg(kind, tid, items, tags)
        for axis, second in (("Z", "TRCVY_"), ("Y", "TRCVZ_")):
            if axis == "Y":
                lines.append("*VOPER,TRCVZ_(1),TRCVZ_(1),MULT,-1")
            lines += [
                "*VOPER,TRCVA_(1),TRCVX_(1),MULT,TRCVX_(1)",
                f"*VOPER,TRCVB_(1),{second}(1),MULT,{second}(1)",
                "*VOPER,TRCVA_(1),TRCVA_(1),ADD,TRCVB_(1)",
                "*VFUN,TRCVA_(1),SQRT,TRCVA_(1)",
                f"*VOPER,TRCVB_(1),{second}(1),ATN2,TRCVX_(1)",
                *self._masked_minmax("TRCVA_", "TRCR0_", "TRCR1_"),
                *self._masked_minmax("TRCVB_", "TRCT0_", "TRCT1_"),
                "TRCT0_=TRCT0_*TRCAF_",
                "TRCT1_=TRCT1_*TRCAF_",
                *self.msg(
                    kind + axis,
                    tid,
                    [
                        ("R0", "TRCR0_", "%G"),
                        ("R1", "TRCR1_", "%G"),
                        ("T0", "TRCT0_", "%G"),
                        ("T1", "TRCT1_", "%G"),
                    ],
                    tags,
                ),
            ]
        lines += self._free(arrays)
        return lines

    # ---- チェックポイント ----

    def checkpoint(self, tid: int) -> list[str]:
        """全節点・全要素の範囲と内訳。選択は変えない（配列で計算する）。

        Begin レベルでは実行できないので、保留フラグ TRCPC_ を立てて、次に
        処理系に入ったときに出す。
        """
        return [
            "*GET,TRCRT_,ACTIVE,0,ROUT",
            f"*IF,TRCRT_,EQ,{ROUT_BEGIN},THEN",
            *self.msg("CHK", tid, tags=("DEFER",)),
            "TRCPC_=1",
            "*ELSE",
            *self._checkpoint_body(tid),
            "*ENDIF",
        ]

    def _checkpoint_body(self, tid: int) -> list[str]:
        lines = [
            "*GET,TRCCS_,ACTIVE,0,CSYS",
            "CSYS,0",
            "*GET,TRCNM_,NODE,0,NUM,MAXD",
            "*GET,TRCEM_,ELEM,0,NUM,MAXD",
            *self.msg("CHK", tid, [("NM", "TRCNM_", "%I"), ("EM", "TRCEM_", "%I")]),
            "*IF,TRCNM_,GT,0,THEN",
            *self._node_vectors(tid, "CHKN", selected_only=False),
            "*ENDIF",
            "*IF,TRCEM_,GT,0,THEN",
            *self._elem_type_real(tid),
            "*ENDIF",
            "CSYS,TRCCS_",
            "TRCPC_=0",
        ]
        return lines

    def _elem_type_real(self, tid: int) -> list[str]:
        """定義済みの全要素を (TYPE, REAL) ごとに数え、要素名と MAT も出す。"""
        arrays = ("TRCVS_", "TRCVA_", "TRCVB_", "TRCVC_", "TRCVD_")
        body = [
            f"TRCRE_=MOD(TRCI_,{KEY_SCALE})",
            f"TRCTY_=NINT((TRCI_-TRCRE_)/{KEY_SCALE})",
            "*VOPER,TRCVD_(1),TRCVB_(1),MULT,TRCVC_(1)",
            "*VSCFUN,TRCAM_,MAX,TRCVD_(1)",
            "*GET,TRCEN_,ETYP,TRCTY_,ATTR,ENAM",
            *self.msg(
                "CHKE",
                tid,
                [
                    ("T", "TRCTY_", "%I"),
                    ("EN", "TRCEN_", "%I"),
                    ("R", "TRCRE_", "%I"),
                    ("M", "TRCAM_", "%I"),
                    ("N", "TRCK_", "%I"),
                ],
            ),
        ]
        return [
            *self._alloc(arrays, "TRCEM_"),
            "*VGET,TRCVS_(1),ELEM,1,ESEL",
            "*VOPER,TRCVS_(1),TRCVS_(1),MULT,TRCVS_(1)",
            "*VGET,TRCVA_(1),ELEM,1,ATTR,TYPE",
            "*VGET,TRCVB_(1),ELEM,1,ATTR,REAL",
            "*VGET,TRCVC_(1),ELEM,1,ATTR,MAT",
            f"*VOPER,TRCVA_(1),TRCVA_(1),MULT,{KEY_SCALE}",
            "*VOPER,TRCVA_(1),TRCVA_(1),ADD,TRCVB_(1)",
            "*VOPER,TRCVA_(1),TRCVA_(1),MULT,TRCVS_(1)",
            *self._distinct_loop(body),
            *self._free(arrays),
        ]

    def deferred(self, tid: int, with_lists: bool) -> list[str]:
        """処理系に入った直後に、保留中のチェックポイントと定義一覧を出す。"""
        lines = [
            "*GET,TRCT_,PARM,TRCPC_,TYPE",
            "*IF,TRCT_,EQ,0,THEN",
            "*IF,TRCPC_,EQ,1,THEN",
            *self._checkpoint_body(tid),
            "*ENDIF",
            "*ENDIF",
        ]
        if with_lists:
            lines += [
                "*GET,TRCT_,PARM,TRCPL_,TYPE",
                "*IF,TRCT_,EQ,0,THEN",
                "*IF,TRCPL_,EQ,1,THEN",
                *self.lists(tid),
                "*ENDIF",
                "*ENDIF",
            ]
        return lines

    def lists(self, tid: int) -> list[str]:
        """ETLIST などの定義一覧を、開始・終了マーカーで挟んで出す。"""
        lines: list[str] = []
        if self.lists_gopr:
            lines += ["*GET,TRCPK_,ACTIVE,0,PRKEY", "/GOPR"]
        for cmd in LIST_COMMANDS:
            lines += self.msg("LIST", tid, tags=("BEGIN", cmd))
            lines.append(cmd)
            lines += self.msg("LIST", tid, tags=("END", cmd))
        if self.lists_gopr:
            lines += ["*IF,TRCPK_,EQ,0,THEN", "/NOPR", "*ENDIF"]
        lines.append("TRCPL_=0")
        return lines

    def after_resume(self, tid: int) -> list[str]:
        """RESUME 直後: PREP7 なら一覧を出し、それ以外は保留する。"""
        return [
            "*GET,TRCRT_,ACTIVE,0,ROUT",
            f"*IF,TRCRT_,EQ,{ROUT_PREP7},THEN",
            *self.lists(tid),
            "*ELSE",
            "TRCPL_=1",
            "*ENDIF",
            *self.checkpoint(tid),
        ]

    # ---- 番号の展開 ----

    def _resolve_number(self, target: str, text: str) -> list[str] | None:
        """引数を番号として target に入れる。番号でなければ None。"""
        kind = lexer.classify_value(text)
        text = text.strip()
        if kind == "number":
            try:
                v = float(text.replace("D", "E").replace("d", "e"))
            except ValueError:
                return None
            if v <= 0 or v != int(v):
                return None
            return [f"{target}={int(v)}"]
        if kind == "ident":
            return [
                f"*GET,TRCT_,PARM,{text},TYPE",
                f"{target}=0",
                "*IF,TRCT_,EQ,0,THEN",
                f"{target}={text}",
                "*ENDIF",
            ]
        if kind == "array":
            ref = lexer.array_ref(text)
            assert ref is not None
            return [
                f"*GET,TRCT_,PARM,{ref[0]},TYPE",
                f"{target}=0",
                "*IF,TRCT_,EQ,1,OR,TRCT_,EQ,2,THEN",
                f"{target}={text}",
                "*ENDIF",
            ]
        if kind == "expr":
            return [f"{target}={text}"]
        return None

    def node_expand(self, tid: int, fidx: int, text: str) -> list[str]:
        """節点番号を、活性座標系とグローバルの座標に展開する。"""
        head = self._resolve_number("TRCND_", text)
        if head is None:
            return []
        tags = (f"F{fidx}",)
        loc = [f"*GET,TRC{ax}_,NODE,TRCND_,LOC,{ax}" for ax in "XYZ"]
        xyz = [(ax, f"TRC{ax}_", "%G") for ax in "XYZ"]
        return [
            *head,
            "*IF,TRCND_,GT,0,THEN",
            "*GET,TRCNS_,NODE,TRCND_,NSEL",
            "*IF,TRCNS_,NE,0,THEN",
            "*GET,TRCCS_,ACTIVE,0,CSYS",
            *loc,
            *self.msg(
                "NODE",
                tid,
                [("N", "TRCND_", "%I"), ("CS", "TRCCS_", "%I"), *xyz],
                tags,
            ),
            "CSYS,0",
            *loc,
            "CSYS,TRCCS_",
            *self.msg("NODEG", tid, xyz, tags),
            "*ENDIF",
            "*ENDIF",
        ]

    def elem_expand(self, tid: int, fidx: int, text: str) -> list[str]:
        """要素番号を、属性と重心（グローバル）に展開する。"""
        head = self._resolve_number("TRCEL_", text)
        if head is None:
            return []
        tags = (f"F{fidx}",)
        return [
            *head,
            "*IF,TRCEL_,GT,0,THEN",
            "*GET,TRCNS_,ELEM,TRCEL_,ESEL",
            "*IF,TRCNS_,NE,0,THEN",
            *self._elem_attr_gets("TRCEL_"),
            *self.msg(
                "ELEM",
                tid,
                [
                    ("E", "TRCEL_", "%I"),
                    ("MAT", "TRCAM_", "%I"),
                    ("TYPE", "TRCTY_", "%I"),
                    ("REAL", "TRCRE_", "%I"),
                    ("SEC", "TRCSC_", "%I"),
                    ("EN", "TRCEN_", "%I"),
                ],
                tags,
            ),
            "*GET,TRCCS_,ACTIVE,0,CSYS",
            "CSYS,0",
            *[f"*GET,TRC{ax}_,ELEM,TRCEL_,CENT,{ax}" for ax in "XYZ"],
            "CSYS,TRCCS_",
            *self.msg("ELEMC", tid, [(ax, f"TRC{ax}_", "%G") for ax in "XYZ"], tags),
            "*ENDIF",
            "*ENDIF",
        ]

    def _elem_attr_gets(self, elem: str) -> list[str]:
        return [
            f"*GET,TRCAM_,ELEM,{elem},ATTR,MAT",
            f"*GET,TRCTY_,ELEM,{elem},ATTR,TYPE",
            f"*GET,TRCRE_,ELEM,{elem},ATTR,REAL",
            f"*GET,TRCSC_,ELEM,{elem},ATTR,SECN",
            "*GET,TRCEN_,ETYP,TRCTY_,ATTR,ENAM",
        ]

    # ---- 要素生成 ----

    def counts(self, tid: int, tag: str) -> list[str]:
        """選択中の節点数・要素数と、最大番号。要素生成の前後に出す。"""
        return [
            "*GET,TRCCN_,NODE,0,COUNT",
            "*GET,TRCCE_,ELEM,0,COUNT",
            "*GET,TRCMN_,NODE,0,NUM,MAXD",
            "*GET,TRCME_,ELEM,0,NUM,MAXD",
            *self.msg(
                "CNT",
                tid,
                [
                    ("N", "TRCCN_", "%I"),
                    ("E", "TRCCE_", "%I"),
                    ("NM", "TRCMN_", "%I"),
                    ("EM", "TRCME_", "%I"),
                ],
                (tag,),
            ),
        ]

    def spring(self, tid: int, spring_enames: tuple[int, ...]) -> list[str]:
        """直前に作った要素がバネなら、両端節点の座標と剛性を出す。"""
        conds: list[str] = []
        for i in range(0, len(spring_enames), 2):
            pair = spring_enames[i : i + 2]
            if len(pair) == 2:
                conds.append(f"*IF,TRCEN_,EQ,{pair[0]},OR,TRCEN_,EQ,{pair[1]},THEN")
            else:
                conds.append(f"*IF,TRCEN_,EQ,{pair[0]},THEN")
        flag = ["TRCSP_=0"]
        for c in conds:
            flag += [c, "TRCSP_=1", "*ENDIF"]
        ends = []
        for k, p in (("I", "TRCNI_"), ("J", "TRCNJ_")):
            ends += [
                f"*IF,{p},GT,0,THEN",
                *[f"*GET,TRC{ax}_,NODE,{p},LOC,{ax}" for ax in "XYZ"],
                *self.msg(
                    "SPR" + k,
                    tid,
                    [("N", p, "%I"), *[(ax, f"TRC{ax}_", "%G") for ax in "XYZ"]],
                ),
                "*ENDIF",
            ]
        return [
            "*GET,TRCEL_,ELEM,0,NUM,MAXD",
            "*IF,TRCEL_,GT,0,THEN",
            "*GET,TRCTY_,ELEM,TRCEL_,ATTR,TYPE",
            "*GET,TRCEN_,ETYP,TRCTY_,ATTR,ENAM",
            *flag,
            "*IF,TRCSP_,EQ,1,THEN",
            "*GET,TRCRE_,ELEM,TRCEL_,ATTR,REAL",
            "*GET,TRCNI_,ELEM,TRCEL_,NODE,1",
            "*GET,TRCNJ_,ELEM,TRCEL_,NODE,2",
            "TRCK_=0",
            "*IF,TRCRE_,GT,0,THEN",
            "*GET,TRCK_,RCON,TRCRE_,CONST,1",
            "*ENDIF",
            *self.msg(
                "SPR",
                tid,
                [
                    ("E", "TRCEL_", "%I"),
                    ("T", "TRCTY_", "%I"),
                    ("EN", "TRCEN_", "%I"),
                    ("R", "TRCRE_", "%I"),
                    ("K", "TRCK_", "%G"),
                ],
            ),
            "*GET,TRCCS_,ACTIVE,0,CSYS",
            "CSYS,0",
            *ends,
            "CSYS,TRCCS_",
            "*ENDIF",
            "*ENDIF",
        ]

    # ---- マッピング ----

    def set_info(self, tid: int) -> list[str]:
        """POST1 で読んでいる結果の時刻・ロードステップ・サブステップ。"""
        return [
            "*GET,TRCRT_,ACTIVE,0,ROUT",
            f"*IF,TRCRT_,EQ,{ROUT_POST1},THEN",
            "*GET,TRCST_,ACTIVE,0,SET,TIME",
            "*GET,TRCSL_,ACTIVE,0,SET,LSTP",
            "*GET,TRCSB_,ACTIVE,0,SET,SBST",
            *self.msg(
                "SETI",
                tid,
                [("T", "TRCST_", "%G"), ("LS", "TRCSL_", "%I"), ("SS", "TRCSB_", "%I")],
            ),
            "*ENDIF",
        ]

    def comp_count(self, tid: int, entity: str | None) -> list[str]:
        """CM で作ったコンポーネントの件数（その実体の選択数）。"""
        return self.sel(tid, entity or "NEKLAV", "CM")

    # ---- _RETURN / _STATUS ----

    @staticmethod
    def save_status() -> list[str]:
        return ["TRCRV_=_RETURN", "TRCSV_=_STATUS"]

    @staticmethod
    def restore_status() -> list[str]:
        return ["_RETURN=TRCRV_", "_STATUS=TRCSV_"]
