"""実機の挙動を確かめるためのマクロを作る。

変換が前提にしている MAPDL の挙動（*MSG の出力、*GET の座標の基準、
*VOPER の定数引数など）を、小さなモデルで 1 項目ずつ確かめる。各項目は
`PROBE|番号|...` の行を出す。途中でエラーになった場合も、どの項目まで
進んだかが分かる。

生成するマクロは ASCII のみ（コメントも英語）にしている。
"""

from __future__ import annotations

from apdl_trace.emit import Emitter


def _msg(tag: str, items: list[tuple[str, str, str]] | None = None) -> list[str]:
    items = items or []
    vals = ",".join(v for _, v, _ in items)
    fmt = f"PROBE|{tag}" + "".join(f"|{k}={f}" for k, _, f in items)
    return [f"*MSG,INFO{',' + vals if vals else ''}", fmt]


def _section(title: str) -> list[str]:
    return ["", f"! ---- {title} ----"]


def build_probe() -> str:
    em = Emitter("detail")
    L: list[str] = [
        "! apdl_trace probe macro",
        "! Run in batch mode and send back the .out file (and probe_redirect.txt).",
        "! Each check prints lines starting with PROBE| or TRACE|.",
        "/PREP7",
        "ET,1,185",
        "MP,EX,1,2.0E5",
        "MP,PRXY,1,0.3",
        "N,1,1,0,0",
        "N,2,2,0,0",
        "N,3,2,1,0",
        "N,4,1,1,0",
        "N,5,1,0,1",
        "N,6,2,0,1",
        "N,7,2,1,1",
        "N,8,1,1,1",
        "TYPE,1",
        "MAT,1",
        "E,1,2,3,4,5,6,7,8",
        "ET,2,14",
        "R,2,1000.0",
        "N,9,3,0,0",
    ]

    L += _section("P01 *MSG output (normal, /NOPR, /OUTPUT)")
    L += _msg("P01|NORMAL")
    L += ["/NOPR", *_msg("P01|UNDER_NOPR"), "/GOPR"]
    L += [
        "/OUTPUT,probe_redirect,txt",
        *_msg("P01|UNDER_OUTPUT"),
        "/OUTPUT",
        *_msg("P01|AFTER_OUTPUT"),
    ]

    L += _section("P02 *MSG limits (8 values, format length, %G width)")
    L += [
        "*MSG,INFO,1,2,3,4,5,6,7,8",
        "PROBE|P02|V8|A=%I|B=%I|C=%I|D=%I|E=%I|F=%I|G=%I|H=%I",
    ]
    for n in (80, 120, 160, 200):
        head = f"PROBE|P02|LEN{n}|"
        pad = "X" * (n - len(head) - 4)
        L += ["PRBV_=1.25", "*MSG,INFO,PRBV_", f"{head}{pad}V=%G"]
    L += [
        "PRBA_=1.23456789E10",
        "PRBB_=-0.5",
        "PRBC_=1.0E-12",
        "*MSG,INFO,PRBA_,PRBB_,PRBC_",
        "PROBE|P02|G|A=%G|B=%G|C=%G",
    ]

    L += _section("P03 definition lists under /NOPR (expect lists between markers)")
    L += ["/NOPR", *_msg("P03|BEGIN")]
    L += ["ETLIST", "MPLIST", "RLIST", "SLIST", "CMLIST"]
    L += [*_msg("P03|END"), "/GOPR"]

    L += _section("P04 *GET LOC / MNLOC basis (node 3 is at global 2,1,0)")
    for cs in (1, 0):
        L.append(f"CSYS,{cs}")
        for ax in "XYZ":
            L.append(f"*GET,PRB{ax}_,NODE,3,LOC,{ax}")
        L += _msg(
            f"P04|CSYS{cs}|LOC",
            [("X", "PRBX_", "%G"), ("Y", "PRBY_", "%G"), ("Z", "PRBZ_", "%G")],
        )
        L += [
            "NSEL,S,NODE,,3",
            "*GET,PRBA_,NODE,0,MNLOC,X",
            "*GET,PRBB_,NODE,0,MNLOC,Y",
            "NSEL,ALL",
        ]
        L += _msg(f"P04|CSYS{cs}|MNLOC", [("X", "PRBA_", "%G"), ("Y", "PRBB_", "%G")])
    L.append("! expected if active-csys based: CSYS1 LOC X=2.23607 Y=26.5651 Z=0")

    L += _section("P05 CSYS,5 components (node 7 is at global 2,1,1)")
    L.append("CSYS,5")
    for ax in "XYZ":
        L.append(f"*GET,PRB{ax}_,NODE,7,LOC,{ax}")
    L += _msg(
        "P05|CSYS5|LOC",
        [("X", "PRBX_", "%G"), ("Y", "PRBY_", "%G"), ("Z", "PRBZ_", "%G")],
    )
    L += ["NROTAT,7"]
    for a in ("XY", "YZ", "ZX"):
        L.append(f"*GET,PRBR{a}_,NODE,7,ANG,{a}")
    L += _msg(
        "P05|CSYS5|ANG",
        [("XY", "PRBRXY_", "%G"), ("YZ", "PRBRYZ_", "%G"), ("ZX", "PRBRZX_", "%G")],
    )
    L += ["CSYS,0"]

    L += _section("P06 CM with no selected nodes")
    L += [
        "NSEL,NONE",
        "CM,PRB6_,NODE",
        *_msg("P06|AFTER_CM", [("ST", "_STATUS", "%I")]),
        "NSEL,ALL",
        "CMSEL,S,PRB6_",
        "*GET,PRBN_,NODE,0,COUNT",
        *_msg("P06|AFTER_CMSEL", [("N", "PRBN_", "%I")]),
        "NSEL,ALL",
    ]

    L += _section("P07 assign _RETURN / _STATUS")
    L += [
        "_RETURN=7",
        "_STATUS=0",
        *_msg("P07|ASSIGN", [("R", "_RETURN", "%I"), ("S", "_STATUS", "%I")]),
    ]

    L += _section("P08 *GET PARM TYPE (undefined, numeric, char, array, table)")
    L += [
        "PRBNUM_=1",
        "PRBCHR_='ABC'",
        "*DIM,PRBARR_,ARRAY,3",
        "*DIM,PRBTAB_,TABLE,2",
    ]
    for p in ("PRBUNDEF_", "PRBNUM_", "PRBCHR_", "PRBARR_", "PRBTAB_"):
        L += [f"*GET,PRBT_,PARM,{p},TYPE", *_msg(f"P08|{p}", [("T", "PRBT_", "%I")])]

    L += _section("P09 %C with long character parameters")
    L += [
        "PRBC8_='ABCDEFGH'",
        "PRBC32_='ABCDEFGHIJKLMNOPQRSTUVWXYZ012345'",
        *_msg("P09|C8", [("C", "PRBC8_", "%C")]),
        *_msg("P09|C32", [("C", "PRBC32_", "%C")]),
    ]

    L += _section("P10 ACTIVE items (ROUT, JOBNAM, PRKEY, CSYS)")
    L += [
        "*GET,PRBA_,ACTIVE,0,ROUT",
        "*GET,PRBJ_,ACTIVE,0,JOBNAM",
        "*GET,PRBK_,ACTIVE,0,PRKEY",
        "*GET,PRBC_,ACTIVE,0,CSYS",
        *_msg(
            "P10",
            [
                ("ROUT", "PRBA_", "%I"),
                ("JOB", "PRBJ_", "%C"),
                ("PRKEY", "PRBK_", "%I"),
                ("CSYS", "PRBC_", "%I"),
            ],
        ),
    ]

    L += _section("P11 *VOPER with constant Par2, *VMASK, *VSCFUN, *DOWHILE")
    L += [
        "*DEL,PRBVA_,,NOPR",
        "*DEL,PRBVB_,,NOPR",
        "*DIM,PRBVA_,,3",
        "*DIM,PRBVB_,,3",
        "*VFILL,PRBVA_(1),DATA,1,3,2",
        "*VOPER,PRBVB_(1),PRBVA_(1),GT,1",
        *_msg(
            "P11|GT1",
            [
                ("B1", "PRBVB_(1)", "%G"),
                ("B2", "PRBVB_(2)", "%G"),
                ("B3", "PRBVB_(3)", "%G"),
            ],
        ),
        "*VOPER,PRBVB_(1),PRBVA_(1),MULT,-1",
        *_msg("P11|MULTM1", [("B1", "PRBVB_(1)", "%G"), ("B2", "PRBVB_(2)", "%G")]),
        "*VOPER,PRBVB_(1),PRBVA_(1),EQ,3",
        *_msg("P11|EQ3", [("B1", "PRBVB_(1)", "%G"), ("B2", "PRBVB_(2)", "%G")]),
        "*VFILL,PRBVB_(1),DATA,1,0,1",
        "*VMASK,PRBVB_(1)",
        "*VSCFUN,PRBA_,MAX,PRBVA_(1)",
        *_msg("P11|MASKED_MAX", [("MAX", "PRBA_", "%G")]),
        "! expected MASKED_MAX=2 (element 2 is masked out)",
        "PRBI_=3",
        "PRBK_=0",
        "*DOWHILE,PRBI_",
        "PRBK_=PRBK_+1",
        "PRBI_=PRBI_-1",
        "*ENDDO",
        *_msg("P11|DOWHILE", [("COUNT", "PRBK_", "%I")]),
        "! expected DOWHILE COUNT=3",
        "PRBA_=MOD(3000007,1000000)",
        "PRBB_=NINT((3000007-PRBA_)/1000000)",
        *_msg("P11|MOD", [("R", "PRBA_", "%I"), ("T", "PRBB_", "%I")]),
    ]

    L += _section("P12 ATN2 and *AFUN")
    L += [
        "*DEL,PRBVA_,,NOPR",
        "*DEL,PRBVB_,,NOPR",
        "*DEL,PRBVC_,,NOPR",
        "*DIM,PRBVA_,,1",
        "*DIM,PRBVB_,,1",
        "*DIM,PRBVC_,,1",
        "PRBVA_(1)=1",
        "PRBVB_(1)=1",
        "*VOPER,PRBVC_(1),PRBVA_(1),ATN2,PRBVB_(1)",
        "PRBF_=45/ATAN(1)",
        *_msg("P12|RAD", [("ATN2", "PRBVC_(1)", "%G"), ("F", "PRBF_", "%G")]),
        "*AFUN,DEG",
        "*VOPER,PRBVC_(1),PRBVA_(1),ATN2,PRBVB_(1)",
        "PRBF_=45/ATAN(1)",
        *_msg("P12|DEG", [("ATN2", "PRBVC_(1)", "%G"), ("F", "PRBF_", "%G")]),
        "*AFUN,RAD",
        "! expected: RAD ATN2=0.785398 F=57.2958 / DEG ATN2=45 F=1",
    ]

    L += _section("P13 inserted snippets (selection, checkpoint, expansions)")
    L += ["NSEL,S,LOC,X,2", *em.sel(9001, "N")]
    L += ["ESEL,ALL", *em.sel(9002, "E")]
    L += em.checkpoint(9003)
    L += em.node_expand(9004, 1, "7")
    L += em.elem_expand(9005, 1, "1")
    L += ["NSEL,ALL", "TYPE,2", "REAL,2", *em.counts(9006, "PRE"), "E,2,9"]
    L += [*em.counts(9006, "POST"), *em.spring(9006, (14, 39, 40, 214, 250))]
    L += ["TYPE,1", "REAL,1", "CSYS,1", *em.state_csys(9007), "CSYS,0"]
    L += em.value(9008, "A1", "PRBNUM_")
    L += em.value(9009, "A1", "PRBCHR_")
    L += em.value(9010, "A1", "PRBARR_(2)")
    L += em.value(9011, "A1", "PRBNUM_*2+1")
    L += em.lists(9012)
    L += ["FINISH", *em.state_rout(9013), *em.checkpoint(9014)]

    L += _section("END")
    L += _msg("END")
    L.append("FINISH")
    return "\n".join(L) + "\n"
