#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PackyAPI 模型广场 → 筛选 → 同步 Continue 配置 + 生成价格表 md。

流程：抓取 /api/pricing（静态，无需浏览器）→ 按口径筛选 →
1) 替换 ~/.continue/config.yaml 的 models 段（模型名 = 分组.模型名），其余内容原样保留
2) 生成价格表 markdown（# PackyCode 标题 + 单张表，第一列为 分组.模型），
   默认写入 ~/.continue/packyapi.price.md

默认只预览不落盘；确认后加 --write 写入。仅用标准库。可调参数集中在下方 CONFIG 区。
"""
import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

# ======================== CONFIG（按需修改） ========================
PRICING_URL = "https://www.packyapi.com/api/pricing"

# Continue 配置里实际调用的中转域名（数据站是 www.packyapi.com，请求走 cf.api.fan）
API_BASE_OPENAI = "https://cf.api.fan/v1"      # OpenAI 协议分组
API_BASE_ANTHROPIC = "https://cf.api.fan"      # Anthropic 协议分组

# 保留的令牌分组（默认口径）
DEFAULT_GROUPS = ["codex-sale", "codex", "cc", "claude-officially"]

# 分组前缀 → 协议 provider（按前缀匹配，先命中先得）
GROUP_PROVIDER_PREFIX = [
    ("codex", "openai"),
    ("cc", "anthropic"),
    ("claude", "anthropic"),
]

# 仅保留这些厂商（接口 vendors 字段的 name 值）
VENDOR_ALLOW = ("Anthropic", "OpenAI")

# 模型名黑名单（子串匹配，忽略大小写）
EXCLUDE_SUBSTRINGS = ("image", "codex-auto-review")

# env 密钥命名规范：PACKYAPI_<分组大写，- 换 _>（.env 由用户自行维护，本脚本不碰）
ENV_KEY_PREFIX = "PACKYAPI_"

PRICE_UNIT = 2.0  # new-api 标准：1 倍率 = $2 / 百万 token（输入价基数）
# ====================================================================

BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/143.0.0.0",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.packyapi.com/pricing",
}


def fetch_pricing(url: str) -> dict:
    req = urllib.request.Request(url, headers=BROWSER_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def parse_model(name: str):
    """解析模型名 → (档位, 版本元组)。无法解析返回 None（视为非编码模型，剔除）。"""
    if name.startswith("claude-"):
        parts = name.split("-")
        tier, ver = parts[1], parts[2:]
        if tier not in ("opus", "sonnet", "haiku", "fable", "mythos"):
            return None
        try:
            return tier, tuple(int(x) for x in ver)
        except ValueError:
            return None
    m = re.match(r"^gpt-(\d+(?:\.\d+)?)(?:-([a-z]+))?$", name)
    if m:
        return (m.group(2) or "base"), tuple(int(x) for x in m.group(1).split("."))
    return None


def filter_models(raw: dict, groups: list) -> dict:
    """筛选口径：厂商白名单 → 模型名黑名单 → 指定分组 → 同档位取最高版本。"""
    vmap = {v["id"]: v["name"] for v in raw["vendors"]}
    best = {g: {} for g in groups}
    for m in raw["data"]:
        name = m["model_name"]
        if vmap.get(m["vendor_id"]) not in VENDOR_ALLOW:
            continue
        if any(s in name.lower() for s in EXCLUDE_SUBSTRINGS):
            continue
        p = parse_model(name)
        if p is None:
            continue
        tier, ver = p
        for g in m["enable_groups"]:
            if g in best and (tier not in best[g] or ver > best[g][tier][0]):
                best[g][tier] = (ver, m)

    out = {}
    for g in groups:
        models = [v[1] for v in best[g].values()]
        out[g] = sorted(models, key=lambda m: input_price(raw, g, m))  # 组内从便宜到贵
    return out


def eff_ratio(raw: dict, g: str, model_name: str):
    gr, mgr = raw["group_ratio"], raw["model_group_ratio"]
    return mgr.get(g, {}).get(model_name, gr.get(g))


def input_price(raw: dict, g: str, m: dict) -> float:
    return float(m["model_ratio"]) * eff_ratio(raw, g, m["model_name"]) * PRICE_UNIT


def group_provider(g: str) -> str:
    for prefix, provider in GROUP_PROVIDER_PREFIX:
        if g.startswith(prefix):
            return provider
    raise SystemExit(f"[错误] 分组 {g!r} 无法匹配协议，请补充 GROUP_PROVIDER_PREFIX")


def env_key_name(g: str) -> str:
    return ENV_KEY_PREFIX + g.upper().replace("-", "_")


def render_price_table(raw: dict, picked: dict) -> str:
    """生成价格表：# PackyAPI 标题 + 单张表，写入 ~/.continue/packyapi.price.md。"""
    vmap = {v["id"]: v["name"] for v in raw["vendors"]}
    lines = [
        "# PackyAPI",
        "",
        "| 分组.模型 | 厂商 | 输入 $/M | 输出 $/M | 缓存读 |",
        "|---|---|---|---|---|",
    ]
    for g in picked:
        for m in picked[g]:
            inp = input_price(raw, g, m)
            outp = inp * float(m["completion_ratio"])
            lines.append(
                f"| `{g}.{m['model_name']}` | {vmap.get(m['vendor_id'])} "
                f"| ${inp:.2f} | ${outp:.2f} | ×{m.get('cache_ratio', '')} |"
            )
    return "\n".join(lines) + "\n"


# 设定区域标记（只改动这对标记之间的内容，区外一律不动）
ZONE_BEGIN_TOKEN = "# >>> packyapi-continue-sync:begin"
ZONE_END_TOKEN = "# >>> packyapi-continue-sync:end"
# 旧版脚本生成的分组注释前缀（迁移用：无区域标记但存在旧生成块时并入区域）
LEGACY_TOKEN = "  # ===== PackyAPI"


def render_zone(raw: dict, picked: dict) -> list:
    """生成设定区域内容（不含 models: 头），首行为 begin 标记、末行为 end 标记。"""
    gr, ug = raw["group_ratio"], raw["usable_group"]
    lines = [f"  {ZONE_BEGIN_TOKEN} 本区域由 sync_packyapi.py 自动维护，区外内容不受影响"]
    for g in picked:
        provider = group_provider(g)
        base = API_BASE_OPENAI if provider == "openai" else API_BASE_ANTHROPIC
        ratio = gr.get(g, "?")
        desc = (ug.get(g) or "").strip()
        title = f"PackyAPI · {g} · 倍率 {ratio} · 从便宜到贵"
        if desc:
            title = f"PackyAPI · {g} · {desc} · 倍率 {ratio} · 从便宜到贵"
        lines.append(f"  # ===== {title} =====")
        for m in picked[g]:
            lines += [
                f"  - name: {g}.{m['model_name']}",
                f"    provider: {provider}",
                f"    model: {m['model_name']}",
                f"    apiKey: ${{{{ secrets.{env_key_name(g)} }}}}",
                f"    apiBase: {base}",
                "    roles: [chat, edit, apply]",
                "    capabilities: [tool_use]",
                "",
            ]
    lines.append(f"  {ZONE_END_TOKEN}")
    return lines


def splice_models(existing: str, zone_lines: list) -> str:
    """在现有配置中维护设定区域：
    - 区域已存在 → 只替换 begin/end 标记之间的内容，区外条目原样保留
    - 无区域但有旧版生成的 PackyAPI 分组块 → 整体并入新区域（一次性迁移）
    - 无区域也无旧生成块 → 在 models: 段末尾新建区域，现有条目全部保留
    - 没有 models: 段 → 新建 models: 段并写入区域
    models: 段之外的内容一律不动。
    """
    lines = existing.splitlines()
    start = next((i for i, l in enumerate(lines) if l.rstrip() == "models:"), None)
    if start is None:
        out = lines + ["", "models:"] + zone_lines
        return "\n".join(out) + "\n"
    # models 段终止于下一个顶层键（非缩进非空行）
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if lines[j].strip() and not lines[j].startswith((" ", "\t")):
            end = j
            break
    sec = lines[start + 1:end]
    b = next((i for i, l in enumerate(sec) if l.strip().startswith(ZONE_BEGIN_TOKEN)), None)
    e = next((i for i, l in enumerate(sec) if l.strip().startswith(ZONE_END_TOKEN)), None)
    if b is not None and e is not None:
        new_sec = sec[:b] + zone_lines + sec[e + 1:]
    elif any(l.startswith(LEGACY_TOKEN) for l in sec):
        new_sec = zone_lines  # 迁移：旧生成块整体并入区域
    else:
        new_sec = (sec + [""] if sec and sec[-1].strip() else sec) + zone_lines
    out = lines[:start + 1] + new_sec
    if end < len(lines):
        out += [""] + lines[end:]
    return "\n".join(out) + "\n"


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--groups", default=",".join(DEFAULT_GROUPS),
                    help="逗号分隔的分组列表（默认：%(default)s）")
    ap.add_argument("--md", default=str(Path.home() / ".continue" / "packyapi.price.md"),
                    help="价格表输出路径（默认：%(default)s）")
    ap.add_argument("--config", default=str(Path.home() / ".continue" / "config.yaml"),
                    help="Continue 配置路径（默认：%(default)s）")
    ap.add_argument("--raw", help="额外保存原始 pricing JSON 到指定路径")
    ap.add_argument("--fetch-only", action="store_true", help="只抓取并保存原始 JSON，不写配置/价格表")
    ap.add_argument("--write", action="store_true",
                    help="落盘写入价格表与 config.yaml（默认只预览，不写任何文件）")
    args = ap.parse_args()
    groups = [g.strip() for g in args.groups.split(",") if g.strip()]

    raw = fetch_pricing(PRICING_URL)
    print(f"[抓取] catalog_version={raw['catalog_version']}，"
          f"模型 {len(raw['data'])} 个，分组 {len(raw['group_ratio'])} 个")

    if args.fetch_only:
        out = args.raw or "packy-pricing.json"
        Path(out).write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"[保存] 原始 JSON → {out}")
        return

    picked = filter_models(raw, groups)
    for g in groups:
        names = ", ".join(m["model_name"] for m in picked[g]) or "(空)"
        print(f"[筛选] {g}（倍率 {raw['group_ratio'].get(g)}）: {names}")

    md_content = render_price_table(raw, picked)
    cfg_path = Path(args.config)
    zone_lines = render_zone(raw, picked)
    existing = cfg_path.read_text(encoding="utf-8") if cfg_path.exists() else ""
    cfg_content = (splice_models(existing, zone_lines) if existing
                   else "name: Main Config\nversion: 1.0.0\nschema: v1\n\nmodels:\n"
                        + "\n".join(zone_lines))

    # 默认只预览；--write 才落盘
    print("\n===== 价格表 =====")
    print(md_content)
    print("===== config.yaml =====")
    print(cfg_content)

    if not args.write:
        print("[预览] 以上为将写入的内容，未落盘。确认无误后加 --write 执行写入。")
        return

    md_path, cfg_path = Path(args.md), Path(args.config)
    md_path.write_text(md_content, encoding="utf-8")
    cfg_path.write_text(cfg_content, encoding="utf-8")
    print(f"[写入] 价格表 → {md_path}")
    print(f"[写入] Continue 配置 → {cfg_path}")
    keys = ", ".join(env_key_name(g) for g in groups)
    print(f"[提示] .env 为敏感文件，本脚本不读写，请用户本人确认 {keys} 已填；VSCode Reload Window 后生效。")


if __name__ == "__main__":
    main()
