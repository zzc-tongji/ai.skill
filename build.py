#!/usr/bin/env python3
"""Skill monorepo 构建脚本。

从 src/（通用正文）+ variants/<agent>/（各平台 head）拼装出完整 SKILL.md，
连同 assets/ scripts/ 一起输出到仓库根目录下的 dist/ 目录（已被 .gitignore 忽略）。

输出结构：dist/<agent>/<skill>/

用法：
    python build.py                 # 构建所有 skill 到 dist/（按 agent 分目录）
    python build.py --agent qwen    # 只构建 qwen
    python build.py --dry-run       # 只打印将做什么，不写文件
    python build.py --list          # 列出发现的 skill 与各平台 head 覆盖情况
    python build.py --clean         # 清空 dist/

铁律：只改 src/ 和 variants/，dist/ 是生成物，不要手改，也不要提交。
"""
import argparse
import os
import shutil
import sys

# 参与构建的平台（每个平台一个 variants 子目录）
AGENTS = ["qwen", "workbuddy", "claude", "codex"]

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(REPO_ROOT, "src")
VARIANTS_DIR = os.path.join(REPO_ROOT, "variants")
DIST_DIR = os.path.join(REPO_ROOT, "dist")


def list_skills():
    """返回 src/ 下所有 skill 名（按目录名，kebab-case）。"""
    if not os.path.isdir(SRC_DIR):
        return []
    return sorted(
        d for d in os.listdir(SRC_DIR)
        if os.path.isdir(os.path.join(SRC_DIR, d)) and not d.startswith(".")
    )


def read(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def head_for(agent, skill):
    """取某个 agent 对某个 skill 的 head 内容；无则返回空字符串。"""
    head = os.path.join(VARIANTS_DIR, agent, f"{skill}.head.md")
    if os.path.isfile(head):
        return read(head)
    return ""


def build_skill_md(agent, skill):
    """拼装 head + 正文。head 为空时退化为纯正文（无 frontmatter）。"""
    body = read(os.path.join(SRC_DIR, skill, "SKILL.md"))
    head = head_for(agent, skill)
    if head:
        # 确保 head 末尾有换行，避免与正文粘连
        if not head.endswith("\n"):
            head += "\n"
        return head + "\n" + body
    return body


def build_skill(skill, agents, dry_run):
    """把 skill 构建到 dist/<agent>/<skill>/。"""
    src_skill = os.path.join(SRC_DIR, skill)
    results = []
    for agent in agents:
        dst = os.path.join(DIST_DIR, agent, skill)
        md = build_skill_md(agent, skill)

        if dry_run:
            results.append((agent, "dry-run", f"将写入 {dst}"))
            continue

        # 写 SKILL.md，并同步 assets/ 与 scripts/
        os.makedirs(dst, exist_ok=True)
        with open(os.path.join(dst, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(md)
        for sub in ("assets", "scripts"):
            sub_src = os.path.join(src_skill, sub)
            sub_dst = os.path.join(dst, sub)
            if os.path.isdir(sub_src):
                shutil.rmtree(sub_dst, ignore_errors=True)
                shutil.copytree(sub_src, sub_dst)

        results.append((agent, "ok", dst))
    return results


def cmd_list():
    skills = list_skills()
    if not skills:
        print("src/ 下没有 skill。")
        return
    agents = AGENTS
    print(f"{'skill':<32} " + " ".join(a[:6].ljust(6) for a in agents))
    print("-" * 70)
    for s in skills:
        flags = []
        for a in agents:
            flags.append("H" if head_for(a, s) else "-")
        print(f"{s:<32} " + " ".join(f.ljust(6) for f in flags))
    print("\nH = 该平台有 head 文件，- = 无 head（将退化为纯正文）")


def main():
    ap = argparse.ArgumentParser(description="Skill monorepo 构建脚本")
    ap.add_argument("--agent", action="append", help="只处理指定 agent（可多次）")
    ap.add_argument("--skill", action="append", help="只处理指定 skill（可多次）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写")
    ap.add_argument("--list", action="store_true", help="列出 skill 与 head 覆盖")
    ap.add_argument("--clean", action="store_true", help="清空 dist/")
    args = ap.parse_args()

    if args.list:
        cmd_list()
        return

    if args.clean:
        if os.path.isdir(DIST_DIR):
            shutil.rmtree(DIST_DIR)
            print(f"已清空 {DIST_DIR}")
        else:
            print(f"{DIST_DIR} 不存在，无需清空。")
        return

    skills = list_skills()
    if args.skill:
        skills = [s for s in skills if s in args.skill]
    agents = args.agent or AGENTS

    if not skills:
        print("没有要构建的 skill。")
        return

    for skill in skills:
        print(f"\n[{skill}]")
        for agent, status, msg in build_skill(skill, agents, args.dry_run):
            mark = {"ok": "✔", "skip": "⊘", "dry-run": "→"}.get(status, "?")
            print(f"  {mark} {agent:<10} {msg}")

    print(f"\n完成。产物在 {DIST_DIR}")


if __name__ == "__main__":
    sys.exit(main())
