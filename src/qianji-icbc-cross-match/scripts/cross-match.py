#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""钱迹账单 × 工商银行账单 交叉匹配。

用途：把钱迹导出的记账流水与 icbc-pdf-to-xlsx.py 产出的银行账单 xlsx 逐条匹配，
输出到技能模板的副本，供人工复核与后续记账操作。

依赖：openpyxl（无其他第三方依赖，不 import 任何本机技能模块）。

输入：
    --qianji    钱迹导出文件 Qianji_*.xlsx（首个工作表为账单明细）
    --bank-dir  icbc-pdf-to-xlsx.py 的输出目录（含各账户 xlsx 与 manifest.json）
    --template  模板 xlsx（默认使用技能内 assets/qianji-icbc-cross-match.template.xlsx）
    --out       输出路径（默认 ./qianji-icbc-cross-match.<yyyyMMdd.HHmmss>.xlsx）
    --card-map  可选，人工指定的 账号→卡号 JSON 文件，覆盖 PDF 页眉动态提取结果

输出列布局（动态列数 = 1 + Qianji列 + 信用卡列 + (储蓄卡列+1)）：
    A            Qianji 操作（留空，人工填写 Create / Delete+Create 等）
    B..          Qianji 原列
    ..           信用卡区块
    ..           储蓄卡区块（「账号」之后插入「储蓄卡号」）

配色约定（与模板「图例」工作表一致）：
    白 FFFFFFFF   Qianji 区块底色
    浅灰 FFF2F2F2 信用卡区块底色
    中灰 FFD9D9D9 储蓄卡区块底色
    橙 FFFFA500   Qianji 有、银行无
    黄 FFFFFF00   银行有、Qianji 无（独立行）
    粉 FFFFC0CB   一对多冲突。冲突双方都涂粉：占用列区的 Qianji 行，
                  以及因列区已满而另起一行的那条银行记录（独立行）
    蓝字 FF0000FF Qianji 账户1、账户2 都不命中任何银行账单（整行蓝字，跳过匹配）
    绿 FF92D050   人工复核确认——本脚本不生成，留给使用者手工标注

版本适配：匹配规则针对「特定版本的钱迹导出格式」（列名：时间/类型/金额/账户1/账户2）
与「工行网银电子版历史明细」（列名：入账日期/收支/入账金额 或 交易日期/账号/收入/支出金额）。
钱迹改版或工行改版都可能导致列名对不上而失败。若实际使用效果不佳，
请复制本脚本为副本后修改，不要直接改动技能内原件。
"""
import argparse
import collections
import json
import os
import shutil
import sys
from datetime import datetime

try:
    from openpyxl import load_workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("缺少依赖 openpyxl，请先安装：pip install openpyxl")

# ------------------------------------------------------------------ 可调参数
FONT_NAME, FONT_SIZE = "微软雅黑", 11
C_QJ, C_CR, C_DB = "FFFFFFFF", "FFF2F2F2", "FFD9D9D9"
C_ORANGE, C_YELLOW, C_PINK, C_BLUE = "FFFFA500", "FFFFFF00", "FFFFC0CB", "FF0000FF"
OP_HEADER, CARD_HEADER = "Qianji 操作", "储蓄卡号"
OP_WIDTH, CARD_WIDTH, DEFAULT_WIDTH = 14.0, 21.0, 8.43
MAX_WIDTH = 46
SAMPLE_ROWS_FOR_WIDTH = 600

# 钱迹侧必需列
QJ_TIME, QJ_TYPE, QJ_AMT, QJ_A1, QJ_A2, QJ_ID = "时间", "类型", "金额", "账户1", "账户2", "ID"
EXPENSE_TYPES = {"支出"}          # 账户2为空时按支出方向匹配的 Qianji 类型
INCOME_TYPES = {"收入", "退款"}    # 账户2为空时按收入方向匹配的 Qianji 类型

# 银行侧关键列
CREDIT_DATE, CREDIT_DIR, CREDIT_AMT = "入账日期", "收支", "入账金额"
DEBIT_DATE, DEBIT_ACCT, DEBIT_AMT = "交易日期", "账号", "收入/支出金额"
DIR_EXPENSE, DIR_INCOME = "借", "贷"


# ------------------------------------------------------------------ 工具函数
def to_minute(v):
    """取时间到分钟（秒不参与匹配）。兼容字符串与 datetime。"""
    if v is None or v == "":
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M")
    return str(v).strip()[:16]


def to_full_time(v):
    """完整时间文本，用于排序与独立行落位。"""
    if v is None or v == "":
        return ""
    if hasattr(v, "strftime"):
        return v.strftime("%Y-%m-%d %H:%M:%S")
    return str(v).strip()[:19]


def to_amount(v):
    """金额绝对值（两位小数）。无法解析返回 None，不静默当 0。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return round(abs(float(v)), 2)
    s = str(v).replace(",", "").replace("＋", "+").replace("－", "-").strip()
    try:
        return round(abs(float(s)), 2)
    except ValueError:
        return None


def signed_amount(v):
    """带符号金额（储蓄卡「收入/支出金额」列自带正负）。"""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace(",", "").replace("＋", "+").replace("－", "-").strip()
    try:
        return float(s)
    except ValueError:
        return None


def disp_width(v):
    s = "" if v is None else str(v)
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def nonempty(vals):
    return any(v not in (None, "") for v in vals)


# ------------------------------------------------------------------ 读源
def load_first_sheet(path):
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb[wb.sheetnames[0]]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        raise RuntimeError(f"{path}：空工作表")
    header = ["" if c is None else str(c).strip() for c in rows[0]]
    body = [list(r) for r in rows[1:]]
    return header, body


def build_db_header(db_header):
    """储蓄卡表头：在「账号」之后插入「储蓄卡号」。"""
    if DEBIT_ACCT not in db_header:
        raise RuntimeError(f"储蓄卡账单缺少必需列「{DEBIT_ACCT}」：{db_header}")
    i = db_header.index(DEBIT_ACCT)
    return db_header[:i + 1] + [CARD_HEADER] + db_header[i + 1:]


def load_banks(bank_dir, card_map_override):
    """读取银行账单 xlsx，构建记录池与 账号→卡号 映射。"""
    manifest_path = os.path.join(bank_dir, "manifest.json")
    manifest = {}
    if os.path.isfile(manifest_path):
        with open(manifest_path, encoding="utf-8") as f:
            manifest = json.load(f)
    acc_meta = {a["name"]: a for a in manifest.get("accounts", [])}

    accounts, warnings = {}, []
    card_by_acct = dict(card_map_override or {})

    for fn in sorted(os.listdir(bank_dir)):
        if not fn.endswith(".xlsx") or fn.startswith("~$"):
            continue
        stem = os.path.splitext(fn)[0]
        header, body = load_first_sheet(os.path.join(bank_dir, fn))
        kind = "credit" if CREDIT_DATE in header else ("debit" if DEBIT_DATE in header else None)
        if kind is None:
            warnings.append(f"{fn}：无法判定卡种（既无「{CREDIT_DATE}」也无「{DEBIT_DATE}」），已跳过")
            continue
        hi = {c: i for i, c in enumerate(header)}
        need = [CREDIT_DATE, CREDIT_DIR, CREDIT_AMT] if kind == "credit" else [DEBIT_DATE, DEBIT_ACCT, DEBIT_AMT]
        missing = [c for c in need if c not in hi]
        if missing:
            raise RuntimeError(f"{fn}：缺少必需列 {missing}")

        meta = acc_meta.get(stem, {})
        recs = []
        ledger = collections.Counter()
        for r in body:
            if kind == "credit":
                t = to_full_time(r[hi[CREDIT_DATE]])
                amt = to_amount(r[hi[CREDIT_AMT]])
                d = DIR_EXPENSE if str(r[hi[CREDIT_DIR]] or "").strip() == DIR_EXPENSE else DIR_INCOME
            else:
                t = to_full_time(r[hi[DEBIT_DATE]])
                sv = signed_amount(r[hi[DEBIT_AMT]])
                amt = None if sv is None else round(abs(sv), 2)
                d = DIR_EXPENSE if (sv is not None and sv < 0) else DIR_INCOME
                acct = str(r[hi[DEBIT_ACCT]] or "").strip()
                if acct:
                    ledger[acct] += 1
                    if acct not in card_by_acct:
                        card = meta.get("card_no")
                        if card:
                            card_by_acct[acct] = card
            recs.append({"stem": stem, "kind": kind, "full_time": t, "minute": t[:16],
                         "amount": amt, "direction": d, "values": list(r)})

        accounts[stem] = {"kind": kind, "header": header, "records": recs, "ledger": dict(ledger)}
        if kind == "debit" and meta.get("card_no") is None and not card_map_override:
            warnings.append(f"{stem}：manifest 中无卡号，「{CARD_HEADER}」列将为空（可用 --card-map 指定）")

    if not accounts:
        raise RuntimeError(f"{bank_dir} 中没有可识别的银行账单 xlsx")
    return accounts, card_by_acct, warnings


# ------------------------------------------------------------------ 匹配
class Matcher:
    """按四条件逐条认领（一对一消耗），并处理列区冲突与配色。"""

    def __init__(self, qj_header, qj_data, accounts, card_by_acct):
        self.qi = {c: i for i, c in enumerate(qj_header)}
        for col in (QJ_TIME, QJ_TYPE, QJ_AMT, QJ_A1, QJ_A2):
            if col not in self.qi:
                raise RuntimeError(f"钱迹账单缺少必需列「{col}」：{qj_header}")
        self.qj_data = qj_data
        self.accounts = accounts
        self.card_by_acct = card_by_acct

        self.pool = collections.defaultdict(list)
        self.all_recs = []
        for stem, acc in accounts.items():
            for rec in acc["records"]:
                rec["id"] = len(self.all_recs)
                self.all_recs.append(rec)
                if rec["amount"] is not None:
                    self.pool[(stem, rec["minute"], rec["amount"], rec["direction"])].append(rec)
        self.consumed = set()

    def claim(self, stem, minute, amount, direction):
        """一对一认领：同键未消耗记录中取时间最早的一条；无则返回 None。"""
        if amount is None:
            return None
        for rec in self.pool.get((stem, minute, amount, direction), ()):
            if rec["id"] not in self.consumed:
                self.consumed.add(rec["id"])
                return rec
        return None

    def run(self):
        qi, data = self.qi, self.qj_data
        order = sorted(range(len(data)), key=lambda i: to_full_time(data[i][qi[QJ_TIME]]))
        rows, pink = [], []

        for seq, i in enumerate(order):
            r = data[i]
            minute = to_minute(r[qi[QJ_TIME]])
            amt = to_amount(r[qi[QJ_AMT]])
            ty = str(r[qi[QJ_TYPE]] or "").strip()
            a1, a2 = r[qi[QJ_A1]], r[qi[QJ_A2]]
            a2_empty = a2 in (None, "")
            a1_hit, a2_hit = a1 in self.accounts, (not a2_empty) and (a2 in self.accounts)

            picked, used, got = {}, set(), set()
            style = "normal"
            row_pink = False

            if not a1_hit and not a2_hit:
                style = "blue"                      # 两个账户都不命中 → 跳过匹配
            else:
                # 账户1侧：账户2为空时方向由类型决定；账户2非空时固定按支出情形
                if a1_hit:
                    if a2_empty:
                        d1 = DIR_EXPENSE if ty in EXPENSE_TYPES else DIR_INCOME
                    else:
                        d1 = DIR_EXPENSE
                    rec = self.claim(a1, minute, amt, d1)
                    if rec and rec["kind"] not in used:
                        used.add(rec["kind"]); got.add(rec["kind"]); picked[rec["kind"]] = rec
                    elif rec:
                        pink.append(rec)            # 列区已被占用 → 一对多
                        row_pink = True
                # 账户2侧：固定按收入情形
                if a2_hit:
                    rec = self.claim(a2, minute, amt, DIR_INCOME)
                    if rec and rec["kind"] not in used:
                        used.add(rec["kind"]); got.add(rec["kind"]); picked[rec["kind"]] = rec
                    elif rec:
                        pink.append(rec)
                        row_pink = True
                if row_pink:
                    # 一对多：已占用列区的那条 Qianji 行同样标粉，与另起一行的记录呼应
                    style = "pink"
                elif not got:
                    style = "orange"                # Qianji 有、银行无

            rows.append({"sort": to_full_time(r[qi[QJ_TIME]]), "seq": seq,
                         "qj": list(r), "picked": picked, "style": style})

        yellow = [rec for rec in self.all_recs if rec["id"] not in self.consumed]
        return rows, yellow, pink


# ------------------------------------------------------------------ 写工作簿
THIN = Side(style="thin", color="FF000000")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def build_layout(qj_header, cr_header, db_header_src):
    """计算列布局。返回 (header, 区块边界, 储蓄卡账号列号, 储蓄卡号列号)。

    区块可能为空（例如只有信用卡账单），此时边界为 (lo, lo-1) 的空区间，
    账号列与卡号列返回 None。
    """
    db_header = build_db_header(db_header_src) if db_header_src else []
    header = [OP_HEADER] + list(qj_header) + list(cr_header) + list(db_header)
    qj_start = 2
    cr_start = qj_start + len(qj_header)
    db_start = cr_start + len(cr_header)
    if db_header:
        acct_col = db_start + db_header.index(DEBIT_ACCT)
        card_col = db_start + db_header.index(CARD_HEADER)
    else:
        acct_col = card_col = None
    bounds = {"qj": (qj_start, cr_start - 1), "cr": (cr_start, db_start - 1),
              "db": (db_start, len(header))}
    return header, bounds, acct_col, card_col


def assemble(rows, yellow, pink, layout, accounts, card_by_acct):
    """把匹配结果拼成最终行序列（按时间升序，独立行插入最近的 Qianji 时间下方）。

    返回 (final, expected)：final 为行数据列表，expected 为与之一一对应的
    期望状态列表 {"style", "has_qj"}，供保存后逐行严格核验配色与行形态。
    """
    header, bounds, acct_col, card_col = layout
    n_qj = bounds["qj"][1] - bounds["qj"][0] + 1
    n_cr = max(0, bounds["cr"][1] - bounds["cr"][0] + 1)
    n_db = max(0, bounds["db"][1] - bounds["db"][0] + 1)
    db_stems = [s for s, a in accounts.items() if a["kind"] == "debit"]
    db_hdr_src = accounts[db_stems[0]]["header"] if db_stems else None
    card_idx_in_src = db_hdr_src.index(DEBIT_ACCT) if db_hdr_src else None

    def split_card(values):
        """储蓄卡记录：在账号之后插入卡号，再补齐到区块宽度。"""
        v = list(values)
        acct = str(v[card_idx_in_src] or "").strip() if card_idx_in_src is not None else ""
        out = v[:card_idx_in_src + 1] + [card_by_acct.get(acct)] + v[card_idx_in_src + 1:]
        return out + [None] * (n_db - len(out))

    final = []
    for row in rows:
        cr = [None] * n_cr
        db = [None] * n_db
        for kind, rec in row["picked"].items():
            if kind == "credit":
                cr = list(rec["values"]) + [None] * (n_cr - len(rec["values"]))
            else:
                db = split_card(rec["values"])
        final.append((row["sort"], row["seq"],
                      {"qj": row["qj"], "cr": cr, "db": db, "style": row["style"]}))

    for style, recs in (("yellow", yellow), ("pink", pink)):
        for rec in recs:
            if rec["kind"] == "credit":
                cr = list(rec["values"]) + [None] * (n_cr - len(rec["values"]))
                db = [None] * n_db
            else:
                cr = [None] * n_cr
                db = split_card(rec["values"])
            final.append((rec["full_time"], 10 ** 9 + rec["id"],
                          {"qj": [None] * n_qj, "cr": cr, "db": db, "style": style}))

    final.sort(key=lambda x: (x[0], x[1]))
    rows_out = [x[2] for x in final]
    expected = [{"style": r["style"], "has_qj": nonempty(r["qj"])} for r in rows_out]
    return rows_out, expected


def write_sheet(ws, header, bounds, final):
    """写入表头与数据，套用边框、字体、分区底色、状态色、数字格式。"""
    fills = {"qj": PatternFill("solid", fgColor=C_QJ),
             "cr": PatternFill("solid", fgColor=C_CR),
             "db": PatternFill("solid", fgColor=C_DB),
             "orange": PatternFill("solid", fgColor=C_ORANGE),
             "yellow": PatternFill("solid", fgColor=C_YELLOW),
             "pink": PatternFill("solid", fgColor=C_PINK)}
    f_head = Font(name=FONT_NAME, size=FONT_SIZE, bold=True)
    f_body = Font(name=FONT_NAME, size=FONT_SIZE)
    f_blue = Font(name=FONT_NAME, size=FONT_SIZE, color=C_BLUE)
    al_l = Alignment(horizontal="left", vertical="center")
    al_c = Alignment(horizontal="center", vertical="center")
    al_r = Alignment(horizontal="right", vertical="center")
    ncol = len(header)

    def block_of(c):
        for name in ("qj", "cr", "db"):
            lo, hi = bounds[name]
            if lo <= c <= hi:
                return name
        return "db"           # 理论不可达；兜底避免 KeyError

    for c, name in enumerate(header, 1):
        cell = ws.cell(1, c)
        cell.value = name
        cell.font, cell.border, cell.alignment = f_head, BORDER, al_c
        cell.fill = fills[block_of(c)]

    widths = [disp_width(h) for h in header]
    for ri, row in enumerate(final, start=2):
        # 最左「Qianji 操作」列留空，必须占位，否则后续数据整体左移一格
        values = [None] + row["qj"] + row["cr"] + row["db"]
        if len(values) != ncol:
            raise RuntimeError(f"行{ri} 值数量 {len(values)} != 列数 {ncol}")
        st = row["style"]
        font = f_blue if st == "blue" else f_body
        state_fill = fills.get(st) if st in ("orange", "yellow", "pink") else None
        for c in range(1, ncol + 1):
            v = values[c - 1] if c - 1 < len(values) else None
            cell = ws.cell(ri, c)
            cell.value = v
            if isinstance(v, str) and v.startswith("="):
                cell.data_type = "s"            # 保留原文，不当公式
            cell.border, cell.font = BORDER, font
            cell.fill = state_fill if state_fill else fills[block_of(c)]
            if isinstance(v, (int, float)):
                cell.alignment, cell.number_format = al_r, "#,##0.00"
            else:
                cell.alignment = al_l
            if ri <= SAMPLE_ROWS_FOR_WIDTH + 1:
                w = disp_width(v)
                if w > widths[c - 1]:
                    widths[c - 1] = w

    for c in range(1, ncol + 1):
        if c == 1:
            w = OP_WIDTH
        elif header[c - 1] == CARD_HEADER:
            w = CARD_WIDTH
        else:
            w = min(max(widths[c - 1] + 3, 8), MAX_WIDTH)
        ws.column_dimensions[get_column_letter(c)].width = w
    ws.freeze_panes = "A2"
    return ncol


# ------------------------------------------------------------------ 校验
def color_of(cell):
    try:
        return cell.fill.fgColor.rgb if cell.fill.fill_type else None
    except Exception:
        return "THEME"


def verify(path, header, bounds, acct_col, card_col, card_by_acct, accounts,
           qj_header, qj_data, n_qj_expect, expected):
    """保存后独立回读校验。返回 (ok, messages)。

    expected：与最终行序一一对应的期望状态列表，每项为
    {"style": normal|blue|orange|yellow|pink, "has_qj": bool}。
    配色按此逐行严格比对；四条件、双向守恒、边框、排序、表头均独立推导。
    """
    msgs, ok = [], True

    def fail(m):
        nonlocal ok
        ok = False
        msgs.append("FAIL " + m)

    def col(name, lo, hi):
        """在 [lo, hi] 闭区间内按表头名找 1-based 列号；找不到返回 None。"""
        for c in range(lo, hi + 1):
            if header[c - 1] == name:
                return c
        return None

    wb = load_workbook(path, data_only=False)
    ws = wb[wb.sheetnames[0]]
    ncol = len(header)
    if ws.max_column != ncol:
        fail(f"列数 {ws.max_column} != 期望 {ncol}")
    got_hdr = [ws.cell(1, c).value for c in range(1, ncol + 1)]
    if got_hdr != header:
        fail("表头与期望不符")
    if ws.freeze_panes != "A2":
        fail(f"冻结窗格 {ws.freeze_panes} != A2")

    qj_lo, qj_hi = bounds["qj"]
    cr_lo, cr_hi = bounds["cr"]
    db_lo, db_hi = bounds["db"]

    # 钱迹区块内列号
    qj_col = {name: qj_lo + i for i, name in enumerate(qj_header)}
    # 银行区块内关键列号（直接按表头名在区块内定位）
    cr_date = col(CREDIT_DATE, cr_lo, cr_hi)
    cr_dir = col(CREDIT_DIR, cr_lo, cr_hi)
    cr_amt = col(CREDIT_AMT, cr_lo, cr_hi)
    db_date = col(DEBIT_DATE, db_lo, db_hi)
    db_acct = col(DEBIT_ACCT, db_lo, db_hi)
    db_amt = col(DEBIT_AMT, db_lo, db_hi)
    has_cr = None not in (cr_date, cr_dir, cr_amt)
    has_db = None not in (db_date, db_acct, db_amt)
    if not has_cr and not has_db:
        fail("银行区块关键列缺失，无法校验")
    if db_acct is not None and acct_col != db_acct:
        fail(f"账号列号 {acct_col} != 实际 {db_acct}")

    # 账号 -> 账户名（储蓄卡反查来源），源侧独立构建
    acct_to_stem = {}
    for stem, acc in accounts.items():
        if acc["kind"] != "debit":
            continue
        ai = acc["header"].index(DEBIT_ACCT)
        for rec in acc["records"]:
            a = str(rec["values"][ai] or "").strip()
            if a:
                acct_to_stem[a] = stem

    n_qj_rows = n_bank_only = 0
    qj_ids, bank_keys = [], []
    bad_border = 0
    times = []
    cnt = collections.Counter()
    bad_rule, bad_color, bad_card, bad_shape = [], [], [], []
    state_fill = {"orange": C_ORANGE, "yellow": C_YELLOW, "pink": C_PINK}

    if ws.max_row - 1 != len(expected):
        fail(f"数据行数 {ws.max_row - 1} != 期望 {len(expected)}")

    for r in range(2, ws.max_row + 1):
        row = [ws.cell(r, c).value for c in range(1, ncol + 1)]
        cells = [ws.cell(r, c) for c in range(1, ncol + 1)]
        for cell in cells:
            b = cell.border
            if not (b.left.style and b.right.style and b.top.style and b.bottom.style):
                bad_border += 1

        exp = expected[r - 2] if r - 2 < len(expected) else None
        exp_style = exp["style"] if exp else None

        qjv = row[qj_lo - 1: qj_hi]
        crv = row[cr_lo - 1: cr_hi] if cr_hi >= cr_lo else []
        dbv = row[db_lo - 1: db_hi] if db_hi >= db_lo else []
        fills = {color_of(c) for c in cells}
        fonts = {c.font.color.rgb if c.font.color else None for c in cells}
        has_qj, has_bank = nonempty(qjv), (nonempty(crv) or nonempty(dbv))

        def rec_time(kind):
            return to_full_time(row[(cr_date if kind == "credit" else db_date) - 1])

        def rec_amount(kind):
            return signed_amount(row[(cr_amt if kind == "credit" else db_amt) - 1])

        if not has_qj:
            n_bank_only += 1
            kind = "credit" if (crv and nonempty(crv)) else "debit"
            bank_keys.append((kind, rec_time(kind), rec_amount(kind)))
            times.append(rec_time(kind))
            if not has_bank:
                bad_color.append((r, "独立行无银行数据", None))
        else:
            n_qj_rows += 1
            qj_ids.append(str(row[qj_col[QJ_ID] - 1]))
            times.append(to_full_time(row[qj_col[QJ_TIME] - 1]))

        # ---- 行形态与配色：按匹配阶段输出的期望状态严格比对
        if exp is None:
            bad_color.append((r, "行数超出期望序列", None))
        else:
            if has_qj != exp["has_qj"]:
                bad_shape.append((r, f"has_qj={has_qj} != 期望 {exp['has_qj']}", None))
            if exp_style in state_fill:
                cnt[exp_style] += 1
                if fills != {state_fill[exp_style]}:
                    bad_color.append((r, f"应整行{exp_style}色", sorted(str(x) for x in fills)))
            else:
                cnt[exp_style] += 1
                for name, color in (("qj", C_QJ), ("cr", C_CR), ("db", C_DB)):
                    lo, hi = bounds[name]
                    if lo > hi:
                        continue
                    got = color_of(cells[lo - 1])
                    if got != color:
                        bad_color.append((r, f"{name}区块底色不符：{got} != {color}", got))
                if exp_style == "blue" and fonts != {C_BLUE}:
                    bad_color.append((r, "不命中行应为蓝字", sorted(str(x) for x in fonts)))
                if exp_style != "blue" and fonts != {None}:
                    bad_color.append((r, "命中行不应有彩色字体", sorted(str(x) for x in fonts)))

        if not has_qj:
            continue

        a1 = row[qj_col[QJ_A1] - 1]
        a2 = row[qj_col[QJ_A2] - 1]
        hit1, hit2 = a1 in accounts, (a2 not in (None, "")) and (a2 in accounts)

        if not hit1 and not hit2:
            if has_bank:
                bad_rule.append((r, "不命中行不应并入银行记录", None))
            continue

        # 四条件 + 储蓄卡号
        for kind, vals in (("credit", crv), ("debit", dbv)):
            if not vals or not nonempty(vals):
                continue
            # 守恒统计须先于任何 continue，确保每条并入记录都被计数
            bank_keys.append((kind, rec_time(kind), rec_amount(kind)))
            if kind == "credit":
                if not has_cr:
                    continue
                t_bk = to_minute(row[cr_date - 1])
                amt_bk = to_amount(row[cr_amt - 1])
                dir_bk = DIR_EXPENSE if str(row[cr_dir - 1] or "").strip() == DIR_EXPENSE else DIR_INCOME
                stem = a1 if (hit1 and accounts[a1]["kind"] == "credit") else (
                    a2 if (hit2 and accounts[a2]["kind"] == "credit") else None)
            else:
                if not has_db:
                    continue
                acct = str(row[db_acct - 1] or "").strip()
                stem = acct_to_stem.get(acct)
                t_bk = to_minute(row[db_date - 1])
                sv = signed_amount(row[db_amt - 1])
                amt_bk = None if sv is None else round(abs(sv), 2)
                dir_bk = DIR_EXPENSE if (sv is not None and sv < 0) else DIR_INCOME
                got_card = row[card_col - 1] if card_col else None
                if got_card != card_by_acct.get(acct):
                    bad_card.append((r, acct, got_card, card_by_acct.get(acct)))

            if stem not in (a1, a2):
                bad_rule.append((r, f"账户不符 stem={stem}", kind)); continue
            if to_minute(row[qj_col[QJ_TIME] - 1]) != t_bk:
                bad_rule.append((r, f"分钟不符 {t_bk}", kind)); continue
            if to_amount(row[qj_col[QJ_AMT] - 1]) != amt_bk:
                bad_rule.append((r, f"金额不符 {amt_bk}", kind)); continue
            a2_empty = a2 in (None, "")
            ty = str(row[qj_col[QJ_TYPE] - 1] or "").strip()
            if stem == a1:
                expect_dir = (DIR_EXPENSE if ty in EXPENSE_TYPES else DIR_INCOME) if a2_empty else DIR_EXPENSE
            else:
                expect_dir = DIR_INCOME
            if dir_bk != expect_dir:
                bad_rule.append((r, f"方向不符 {dir_bk} != {expect_dir} 类型={ty}", kind))

    if bad_border:
        fail(f"缺边框单元格 {bad_border}")
    if bad_shape:
        fail(f"行形态违规 {len(bad_shape)} 处，样本 {bad_shape[:3]}")
    if bad_rule:
        fail(f"四条件违规 {len(bad_rule)} 处，样本 {bad_rule[:3]}")
    if bad_color:
        fail(f"配色违规 {len(bad_color)} 处，样本 {bad_color[:3]}")
    if bad_card:
        fail(f"储蓄卡号填充违规 {len(bad_card)} 处，样本 {bad_card[:3]}")
    if times != sorted(times):
        bad = next((i for i in range(1, len(times)) if times[i] < times[i - 1]), None)
        fail(f"时间未按 A-Z 升序（首个逆序位置 行{bad + 2}）")
    if n_qj_rows != n_qj_expect:
        fail(f"Qianji 行数 {n_qj_rows} != 源 {n_qj_expect}")
    if sorted(qj_ids) != sorted(str(r[{c: i for i, c in enumerate(qj_header)}[QJ_ID]]) for r in qj_data):
        fail("Qianji 记录未守恒（按 ID 比对）")

    src_bank = []
    for stem, acc in accounts.items():
        hi = {c: i for i, c in enumerate(acc["header"])}
        for rec in acc["records"]:
            if rec["kind"] == "credit":
                src_bank.append(("credit", rec["full_time"], signed_amount(rec["values"][hi[CREDIT_AMT]])))
            else:
                src_bank.append(("debit", rec["full_time"], signed_amount(rec["values"][hi[DEBIT_AMT]])))
    if sorted(map(str, bank_keys)) != sorted(map(str, src_bank)):
        fail(f"银行记录未守恒：结果 {len(bank_keys)} vs 源 {len(src_bank)}")

    msgs.append(f"行数={ws.max_row - 1} Qianji行={n_qj_rows} 独立银行行={n_bank_only} "
                f"银行记录={len(bank_keys)}/{len(src_bank)} 配色={dict(cnt)}")
    wb.close()
    return ok, msgs


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(description="钱迹账单 × 工商银行账单 交叉匹配")
    ap.add_argument("--qianji", required=True, help="钱迹导出文件 Qianji_*.xlsx")
    ap.add_argument("--bank-dir", required=True, help="icbc-pdf-to-xlsx.py 的输出目录")
    ap.add_argument("--template", default=None, help="模板 xlsx（默认用技能内 assets 模板）")
    ap.add_argument("--out", default=None, help="输出路径（默认含时间戳）")
    ap.add_argument("--card-map", default=None, help="账号→卡号 JSON，覆盖 PDF 动态提取")
    ap.add_argument("--sheet", default="交叉比对", help="模板中数据工作表名（默认「交叉比对」）")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    template = args.template or os.path.join(here, "..", "assets",
                                            "qianji-icbc-cross-match.template.xlsx")
    template = os.path.abspath(template)
    if not os.path.isfile(template):
        sys.exit(f"模板不存在：{template}（请用 --template 指定）")

    out = args.out or os.path.join(os.getcwd(),
                                   f"qianji-icbc-cross-match.{datetime.now().strftime('%Y%m%d.%H%M%S')}.xlsx")
    out = os.path.abspath(out)

    card_override = None
    if args.card_map:
        with open(args.card_map, encoding="utf-8") as f:
            card_override = {str(k): str(v) for k, v in json.load(f).items()}

    qj_header, qj_data = load_first_sheet(args.qianji)
    accounts, card_by_acct, warns = load_banks(args.bank_dir, card_override)
    for w in warns:
        print("WARN " + w)

    cr_stems = [s for s, a in accounts.items() if a["kind"] == "credit"]
    db_stems = [s for s, a in accounts.items() if a["kind"] == "debit"]
    if len(db_stems) > 1:
        db_headers = {tuple(accounts[s]["header"]) for s in db_stems}
        if len(db_headers) > 1:
            sys.exit(f"多个储蓄卡账单表头不一致，无法共用一个列区：{db_stems}")
    cr_header = accounts[cr_stems[0]]["header"] if cr_stems else []
    db_header_src = accounts[db_stems[0]]["header"] if db_stems else []
    if not cr_header and not db_header_src:
        sys.exit("没有可用的银行账单")

    layout = build_layout(qj_header, cr_header, db_header_src)
    header, bounds, acct_col, card_col = layout
    print(f"[布局] 总列数={len(header)} Qianji={bounds['qj']} 信用卡={bounds['cr']} 储蓄卡={bounds['db']}")
    print(f"[布局] 账号列={acct_col} 储蓄卡号列={card_col}")
    print(f"[输入] Qianji {len(qj_data)} 行 | 银行账户 {len(accounts)} 个 "
          f"(信用卡 {len(cr_stems)}, 储蓄卡 {len(db_stems)}) | "
          f"银行记录 {sum(len(a['records']) for a in accounts.values())} 条")
    print(f"[卡号] 账号→卡号映射 {len(card_by_acct)} 组：{card_by_acct}")

    matcher = Matcher(qj_header, qj_data, accounts, card_by_acct)
    rows, yellow, pink = matcher.run()
    final, expected = assemble(rows, yellow, pink, layout, accounts, card_by_acct)

    n_blue = sum(1 for r in rows if r["style"] == "blue")
    n_orange = sum(1 for r in rows if r["style"] == "orange")
    n_pink_qj = sum(1 for r in rows if r["style"] == "pink")
    n_matched = len(rows) - n_blue - n_orange - n_pink_qj
    print(f"[匹配] 正常={n_matched} 蓝字={n_blue} 橙={n_orange} "
          f"粉(Qianji行)={n_pink_qj} 粉(独立行)={len(pink)} 黄={len(yellow)} "
          f"→ 总行数={len(final)}")

    # 输出到模板副本
    if os.path.exists(out):
        sys.exit(f"输出文件已存在，拒绝覆盖：{out}")
    shutil.copyfile(template, out)
    wb = load_workbook(out)
    if args.sheet not in wb.sheetnames:
        sys.exit(f"模板中没有工作表「{args.sheet}」，实际为 {wb.sheetnames}")
    ws = wb[args.sheet]
    # 空白判定看「有无非空值」，不看 max_row/max_column：
    # openpyxl 删除行/清空值后 dimension 仍会残留曾写入过的行列数
    stale = [(c.row, c.column) for row in ws.iter_rows() for c in row if c.value not in (None, "")]
    if stale:
        sys.exit(f"模板工作表「{args.sheet}」不是空白表，请先清空：非空格 {len(stale)} 个，"
                 f"首个 {stale[0]}")
    ncol = write_sheet(ws, header, bounds, final)
    wb.save(out)
    wb.close()

    ok, msgs = verify(out, header, bounds, acct_col, card_col, card_by_acct, accounts,
                      qj_header, qj_data, len(qj_data), expected)
    for m in msgs:
        print(("   " if m.startswith("FAIL") else "[核验] ") + m)
    if not ok:
        os.remove(out)
        sys.exit(1)
    print(f"\n[完成] {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
