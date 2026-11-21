#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工商银行对账单 PDF → XLSX 转换。

用途：把工行网银导出的「历史明细（电子版）」PDF 转成结构化 xlsx，供 cross-match.py 使用。
同一张卡的多份 PDF（如 xxx.1.pdf / xxx.2.pdf）自动按分卷号合并为一个 xlsx。

依赖：pdfplumber、openpyxl（无其他第三方依赖，不 import 任何本机技能模块）。

输入：
    --pdf-dir   存放工行 PDF 的目录（递归一层）
    --out-dir   xlsx 输出目录（不存在则创建）

输出：
    <out-dir>/<账户名>.xlsx          每个账户一个文件，含全边框/表头加粗/冻结首行
    <out-dir>/manifest.json         账户元数据（卡号、账号、行数、分卷来源、警告）

校验（任一不通过即报错退出，不写文件）：
    1. 每页恰好 1 个表格，表头跨页一致
    2. 每页表格行数 == 该页页脚「本页交易笔数」
    3. 全账户提取行数 == 各页页脚声明之和
    4. 分卷合并后总页数 == PDF 声明的「共 N 页」
    5. 收入/支出金额合计 == 各页页脚「本页收入/支出算术合计」之和

版本适配：解析规则针对「工行网银电子版历史明细 PDF」这一特定版本，
依赖以下特征——斜向大字号水印（字号 >= 20）、每页单一表格、
页脚含「本页交易笔数 / 本页收入算术合计 / 本页支出算术合计 / 共 N 页 / 第 N 页」、
表头含「卡号」。若工行改版导致解析失败或结果异常，请复制本脚本为副本后修改，
不要直接改动技能内原件。
"""
import argparse
import collections
import json
import os
import re
import sys
from datetime import datetime

try:
    import pdfplumber
except ImportError:
    sys.exit("缺少依赖 pdfplumber，请先安装：pip install pdfplumber openpyxl")

try:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    sys.exit("缺少依赖 openpyxl，请先安装：pip install pdfplumber openpyxl")

# ------------------------------------------------------------------ 可调参数
WATERMARK_MIN_SIZE = 20.0      # 水印字号 22.32 / 27.32；正文最大 16（标题）
AMOUNT_HEADER_RE = re.compile(r"金额|余额")
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")
TIME_RE = re.compile(r"\d{2}:\d{2}(?::\d{2})?")
NUM_RE = re.compile(r"^[+-]?[\d,]+(?:\.\d+)?$")
CARD_RE = re.compile(r"卡号\s*[:：]?\s*(\d{12,25})")
PART_RE = re.compile(r"^(?P<name>.+)\.(?P<part>\d+)$")   # 文件名去掉 .pdf 后

# 信用卡/储蓄卡的列名特征（用于判定账户类型与关键列）
CREDIT_DATE, CREDIT_DIR, CREDIT_AMT = "入账日期", "收支", "入账金额"
CREDIT_CARD = "交易卡号"
DEBIT_DATE, DEBIT_ACCT, DEBIT_AMT = "交易日期", "账号", "收入/支出金额"


# ------------------------------------------------------------------ 文本清洗
def keep_non_watermark(obj):
    """pdfplumber.filter 保留返回 True 的对象；这里丢弃大字号水印字符。

    工行 PDF 的斜向水印（行名 + 下载编号/时间）会被 extract_tables 混入单元格，
    例如把金额 16.75 污染成 "2\\n-\\n1\\n116.75"。按字号过滤最可靠。
    """
    if obj.get("object_type") == "char":
        return float(obj.get("size") or 0) < WATERMARK_MIN_SIZE
    return True


def join_cell_lines(text):
    """把单元格内因列宽换行产生的多行文本拼回原值。

    日期行 + 时间行用空格连接（两个独立字段）；其余无缝拼接
    （中文长文本被折断，插入空格会破坏原文）。
    """
    if text is None:
        return ""
    lines = [ln.strip() for ln in str(text).split("\n") if ln.strip()]
    if not lines:
        return ""
    out = lines[0]
    for prev, cur in zip(lines, lines[1:]):
        out += (" " + cur) if (DATE_RE.fullmatch(prev) and TIME_RE.fullmatch(cur)) else cur
    return out


def to_number(text):
    """金额/余额列转数值；无法解析返回 None（调用方保留原文，不静默填 0）。"""
    s = str(text or "").replace(",", "").replace("＋", "+").replace("－", "-").strip()
    if s in ("", "-", "—", "--") or not NUM_RE.match(s):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def parse_footer(text):
    """解析页脚统计行，用于逐页校验。"""
    flat = re.sub(r"\s+", " ", text or "")
    info = {}
    for key, pat in (("count", r"本页交易笔数[：:]\s*(\d+)"),
                     ("total_pages", r"共\s*(\d+)\s*页"),
                     ("page_no", r"第\s*(\d+)\s*页"),
                     ("expense", r"本页支出算术合计[：:]\s*([+-]?[\d,]+(?:\.\d+)?)"),
                     ("income", r"本页收入算术合计[：:]\s*([+-]?[\d,]+(?:\.\d+)?)")):
        m = re.search(pat, flat)
        if m:
            info[key] = float(m.group(1).replace(",", "")) if key in ("expense", "income") else int(m.group(1))
    return info


def parse_card_no(text):
    """从页眉提取卡号（需先过滤水印，否则可能读到水印里的编号）。"""
    m = CARD_RE.search(re.sub(r"\s+", " ", text or ""))
    return m.group(1) if m else None


# ------------------------------------------------------------------ PDF 解析
def extract_pdf(path):
    """解析单个 PDF，返回 (header, rows, meta)。meta 含校验信息与警告。"""
    header, rows, amount_cols = None, [], set()
    problems, warnings, pages = [], [], []
    declared_total_pages = None
    card_no = None
    ledger_keys = collections.Counter()

    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages, 1):
            clean = page.filter(keep_non_watermark)
            text = clean.extract_text() or ""
            if card_no is None:
                card_no = parse_card_no(text)

            tables = clean.find_tables()
            if len(tables) != 1:
                problems.append(f"第{pno}页检测到 {len(tables)} 个表格（期望 1 个）")
                if not tables:
                    continue
            data = tables[0].extract()
            if not data:
                problems.append(f"第{pno}页表格为空")
                continue

            page_header = [join_cell_lines(c) for c in data[0]]
            if header is None:
                header = page_header
                amount_cols = {i for i, h in enumerate(header) if AMOUNT_HEADER_RE.search(h)}
            elif page_header != header:
                problems.append(f"第{pno}页表头与首页不一致：{page_header}")

            body = data[1:]
            footer = parse_footer(text)
            declared_total_pages = footer.get("total_pages", declared_total_pages)
            if "count" in footer and footer["count"] != len(body):
                problems.append(f"第{pno}页条数不符：表格 {len(body)} 行 vs 页脚声明 {footer['count']} 笔")

            hi = {c: i for i, c in enumerate(page_header)}
            # 信用卡取「交易卡号」，储蓄卡取「账号」——用于核对流水内的卡号/账号是否唯一
            key_col = hi.get(CREDIT_CARD) if CREDIT_DATE in hi else hi.get(DEBIT_ACCT)
            for raw in body:
                cells = [join_cell_lines(c) for c in raw]
                if len(cells) < len(header):
                    problems.append(f"第{pno}页存在列数不足的行（{len(cells)} 列）")
                    cells += [""] * (len(header) - len(cells))
                elif len(cells) > len(header):
                    problems.append(f"第{pno}页存在列数超出的行（{len(cells)} 列）")
                    cells = cells[:len(header)]
                out = []
                for i, v in enumerate(cells):
                    if i in amount_cols:
                        n = to_number(v)
                        out.append(n if n is not None else v)
                    else:
                        out.append(v)
                rows.append(out)
                if key_col is not None and key_col < len(out):
                    ledger_keys[str(out[key_col]).strip()] += 1

            pages.append({"page": pno, "rows": len(body), "footer_count": footer.get("count"),
                          "expense": footer.get("expense"), "income": footer.get("income")})

    if header is None:
        raise RuntimeError(f"{os.path.basename(path)}：未提取到任何表格，无法解析")
    if card_no is None:
        warnings.append("未能从页眉提取到卡号（表头缺「卡号」字段或被改版）")

    meta = {"file": os.path.basename(path), "pages": len(pages), "rows": len(rows),
            "declared_total_pages": declared_total_pages, "header": header,
            "amount_cols": sorted(amount_cols), "card_no": card_no,
            "ledger_keys": dict(ledger_keys), "page_reports": pages,
            "problems": problems, "warnings": warnings}
    return header, rows, meta


def verify_amounts(header, rows, meta, kind):
    """用页脚收支合计校验金额列，返回问题列表。"""
    issues = []
    idx = {c: i for i, c in enumerate(header)}
    if kind == "credit":
        if CREDIT_DIR not in idx or CREDIT_AMT not in idx:
            return [f"信用卡缺少必需列「{CREDIT_DIR}」或「{CREDIT_AMT}」"]
        dir_i, amt_i = idx[CREDIT_DIR], idx[CREDIT_AMT]
    else:
        if DEBIT_AMT not in idx:
            return [f"储蓄卡缺少必需列「{DEBIT_AMT}」"]
        dir_i, amt_i = None, idx[DEBIT_AMT]

    exp_sum = inc_sum = 0.0
    for r in rows:
        v = r[amt_i]
        if not isinstance(v, (int, float)):
            continue
        if kind == "credit":
            d = str(r[dir_i]).strip()
            if d == "借":
                exp_sum += abs(v)
            elif d == "贷":
                inc_sum += abs(v)
        elif v < 0:
            exp_sum += abs(v)
        else:
            inc_sum += v

    fexp = sum(p["expense"] or 0 for p in meta["page_reports"])
    finc = sum(p["income"] or 0 for p in meta["page_reports"])
    if abs(exp_sum - fexp) > 0.02:
        issues.append(f"支出合计不符：表格 {exp_sum:.2f} vs 页脚 {fexp:.2f}")
    if abs(inc_sum - finc) > 0.02:
        issues.append(f"收入合计不符：表格 {inc_sum:.2f} vs 页脚 {finc:.2f}")
    return issues


# ------------------------------------------------------------------ 文件名分组
def parse_filename(stem):
    """'工商银行 信用卡 0114.1' -> ('工商银行 信用卡 0114', 1)；无分卷 -> (stem, 0)。"""
    m = PART_RE.match(stem)
    if m:
        return m.group("name"), int(m.group("part"))
    return stem, 0


def detect_kind(name):
    if "信用卡" in name:
        return "credit"
    if "储蓄卡" in name or "借记卡" in name or "借记账户" in name:
        return "debit"
    return None


def group_pdfs(pdf_dir):
    """扫描目录，按账户名分组，返回 {账户名: [(分卷号, 路径, kind)]}。"""
    groups = collections.defaultdict(list)
    for f in sorted(os.listdir(pdf_dir)):
        if not f.lower().endswith(".pdf") or f.startswith("~$"):
            continue
        stem = os.path.splitext(f)[0]
        name, part = parse_filename(stem)
        kind = detect_kind(name)
        if kind is None:
            print(f"[跳过] 无法从文件名判定卡种（需含「信用卡」或「储蓄卡」）：{f}")
            continue
        groups[name].append((part, os.path.join(pdf_dir, f), kind))
    for name in groups:
        groups[name].sort(key=lambda x: x[0])
    return dict(groups)


# ------------------------------------------------------------------ 写 Excel
THIN = Side(style="thin", color="000000")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
HEADER_FONT = Font(name="微软雅黑", size=11, bold=True)
BODY_FONT = Font(name="微软雅黑", size=11)
HEADER_FILL = PatternFill("solid", fgColor="DDEBF7")


def disp_width(s):
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(s))


def write_xlsx(path, header, rows, sheet_name):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name[:31] or "Sheet1"
    ws.append(header)
    for r in rows:
        ws.append(r)

    n_rows, n_cols = len(rows) + 1, len(header)
    for row in ws.iter_rows(min_row=1, max_row=n_rows, min_col=1, max_col=n_cols):
        for cell in row:
            cell.border = BORDER
            cell.font = BODY_FONT
            if isinstance(cell.value, (int, float)):
                cell.alignment = Alignment(horizontal="right", vertical="center")
                cell.number_format = "#,##0.00"
            else:
                cell.alignment = Alignment(horizontal="left", vertical="center")
    for cell in ws[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.freeze_panes = "A2"

    for i in range(n_cols):
        widths = [disp_width(header[i])]
        for r in (rows if len(rows) <= 400 else rows[:400]):
            if i < len(r):
                widths.append(disp_width(r[i]))
        ws.column_dimensions[get_column_letter(i + 1)].width = min(max(widths) + 3, 46)

    wb.save(path)
    return n_rows - 1


# ------------------------------------------------------------------ 主流程
def main():
    ap = argparse.ArgumentParser(description="工商银行对账单 PDF → XLSX（含逐页校验）")
    ap.add_argument("--pdf-dir", required=True, help="存放工行 PDF 的目录")
    ap.add_argument("--out-dir", required=True, help="xlsx 输出目录")
    args = ap.parse_args()

    pdf_dir, out_dir = os.path.abspath(args.pdf_dir), os.path.abspath(args.out_dir)
    if not os.path.isdir(pdf_dir):
        sys.exit(f"PDF 目录不存在：{pdf_dir}")
    os.makedirs(out_dir, exist_ok=True)

    groups = group_pdfs(pdf_dir)
    if not groups:
        sys.exit(f"目录中没有可识别的工行 PDF：{pdf_dir}")

    accounts, fatal = [], []
    for name in sorted(groups):
        parts = groups[name]
        kind = parts[0][2]
        print(f"\n[账户] {name}  卡种={'信用卡' if kind == 'credit' else '储蓄卡'}  分卷数={len(parts)}")

        merged_header, merged_rows, metas = None, [], []
        for part, path, k in parts:
            if k != kind:
                fatal.append(f"{name}：分卷卡种判定不一致（{os.path.basename(path)} 判为 {k}）")
                continue
            header, rows, meta = extract_pdf(path)
            meta["kind"] = k
            meta["amount_issues"] = verify_amounts(header, rows, meta, k)
            metas.append(meta)
            if merged_header is None:
                merged_header = header
            elif header != merged_header:
                fatal.append(f"{name}：分卷表头不一致（{meta['file']}）")
            merged_rows.extend(rows)
            flag = "OK" if (not meta["problems"] and not meta["amount_issues"]) else "有异常"
            print(f"   [{flag}] {meta['file']}: 页数={meta['pages']} 声明总页数={meta['declared_total_pages']} "
                  f"行数={meta['rows']} 页脚声明={sum(p['footer_count'] or 0 for p in meta['page_reports'])} "
                  f"卡号={meta['card_no']}")
            for p in meta["problems"][:5]:
                print("      ! " + p)
            for p in meta["amount_issues"][:5]:
                print("      $ " + p)
            for w in meta["warnings"]:
                print("      ~ " + w)

        if merged_header is None:
            fatal.append(f"{name}：无可解析内容")
            continue

        # ---- 账户级校验
        warnings = []
        total_rows = len(merged_rows)
        footer_sum = sum(sum(p["footer_count"] or 0 for p in m["page_reports"]) for m in metas)
        if total_rows != footer_sum:
            fatal.append(f"{name}：提取行数 {total_rows} != 页脚声明合计 {footer_sum}")
        total_pages = sum(m["pages"] for m in metas)
        declared = {m["declared_total_pages"] for m in metas if m["declared_total_pages"]}
        if len(parts) > 1 and declared and total_pages != max(declared):
            warnings.append(f"分卷合并后总页数 {total_pages} != PDF 声明 {max(declared)}")
        elif len(parts) == 1 and declared and total_pages != max(declared):
            fatal.append(f"{name}：页数 {total_pages} != PDF 声明 {max(declared)}")

        all_problems = [p for m in metas for p in m["problems"]]
        all_amount_issues = [p for m in metas for p in m["amount_issues"]]
        if all_problems or all_amount_issues:
            fatal.append(f"{name}：逐页校验未通过（{len(all_problems)} 条结构问题，"
                         f"{len(all_amount_issues)} 条金额问题）")

        # ---- 卡号 / 账号
        card_nos = {m["card_no"] for m in metas if m["card_no"]}
        if len(card_nos) > 1:
            warnings.append(f"分卷页眉卡号不一致：{sorted(card_nos)}，取首个")
        card_no = metas[0]["card_no"]
        ledger = collections.Counter()
        for m in metas:
            ledger.update(m["ledger_keys"])

        tail = name.strip()[-4:]
        if card_no and tail.isdigit() and card_no[-4:] != tail:
            warnings.append(
                f"页眉卡号末4位（{card_no[-4:]}）与文件名尾号（{tail}）不符；"
                f"按 PDF 原文填入，如需改用文件名口径请人工确认")
        if kind == "credit":
            # 信用卡：ledger 统计的是流水中的「交易卡号」，应与页眉卡号一致
            if card_no and set(ledger) - {card_no}:
                warnings.append(f"流水中的交易卡号与页眉卡号不一致：{dict(ledger)}")
        elif len(ledger) != 1:
            # 储蓄卡：一份账单对应一个账号
            warnings.append(f"流水中出现 {len(ledger)} 个账号（期望 1 个）：{dict(ledger)}")

        # ---- 写出
        out_path = os.path.join(out_dir, name + ".xlsx")
        n = write_xlsx(out_path, merged_header, merged_rows, name)
        print(f"   [写出] {os.path.basename(out_path)}: {n} 行")

        accounts.append({
            "name": name, "kind": kind, "file": os.path.basename(out_path),
            "path": out_path, "rows": n, "header": merged_header,
            "sources": [m["file"] for m in metas],
            "pages": total_pages, "declared_total_pages": (max(declared) if declared else None),
            "card_no": card_no, "ledger_keys": dict(ledger), "warnings": warnings,
        })

    manifest = {"generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "pdf_dir": pdf_dir, "out_dir": out_dir,
                "accounts": accounts, "fatal_errors": fatal}
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    total_rows = sum(a["rows"] for a in accounts)
    print(f"\n[汇总] 账户数={len(accounts)} 账单行合计={total_rows} "
          f"警告={sum(len(a['warnings']) for a in accounts)} 致命错误={len(fatal)}")
    print(f"[汇总] manifest: {os.path.join(out_dir, 'manifest.json')}")
    if fatal:
        for e in fatal:
            print("   FATAL " + e)
        sys.exit(1)
    for a in accounts:
        for w in a["warnings"]:
            print(f"   WARN [{a['name']}] {w}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
