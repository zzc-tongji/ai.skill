#!/usr/bin/env python3
"""Skill monorepo 分发脚本。

从 src/（通用正文）+ variants/<agent>/（各平台 head）拼装出完整 SKILL.md，
连同 assets/ scripts/ 一起分发到各 Agent 的本地技能目录。

用法：
    python build.py                 # 分发所有 skill 到所有已配置的 Agent
    python build.py --agent qwen    # 只分发到 qwen
    python build.py --dry-run       # 只打印将做什么，不写文件
    python build.py --list          # 列出发现的 skill 与各平台 head 覆盖情况

铁律：只改 src/ 和 variants/，产物目录是生成物，不要手改。
"""
import argparse
import os
import shutil
import sys

# 各 Agent 的本地技能目录（不存在时跳过并提示，不报错）
TARGETS = {
    "qwen":      os.path.expanduser("~/.qwen/skills"),
    "workbuddy": os.path.expanduser("~/.workbuddy/skills"),
    "claude":    os.path.expanduser("~/.claude/skills"),
    "codex":     os.path.expanduser("~/.codex/skills"),
}

REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.join(REPO_ROOT, "src")
VARIANTS_DIR = os.path.join(REPO_ROOT, "variants")


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


def deploy(skill, agents, dry_run):
    src_skill = os.path.join(SRC_DIR, skill)
    results = []
    for agent in agents:
        target_root = TARGETS.get(agent)
        if not target_root:
            continue
        if not os.path.isdir(target_root):
            results.append((agent, "skip", f"目录不存在: {target_root}"))
            continue

        dst = os.path.join(target_root, skill)
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
    agents = sorted(TARGETS)
    print(f"{'skill':<32} " + " ".join(a[:6].ljust(6) for a in agents))
    print("-" * 70)
    for s in skills:
        flags = []
        for a in agents:
            flags.append("H" if head_for(a, s) else "-")
        print(f"{s:<32} " + " ".join(f.ljust(6) for f in flags))
    print("\nH = 该平台有 head 文件，- = 无 head（将退化为纯正文）")


def main():
    ap = argparse.ArgumentParser(description="Skill monorepo 分发脚本")
    ap.add_argument("--agent", action="append", help="只处理指定 agent（可多次）")
    ap.add_argument("--skill", action="append", help="只处理指定 skill（可多次）")
    ap.add_argument("--dry-run", action="store_true", help="只打印不写")
    ap.add_argument("--list", action="store_true", help="列出 skill 与 head 覆盖")
    args = ap.parse_args()

    if args.list:
        cmd_list()
        return

    skills = list_skills()
    if args.skill:
        skills = [s for s in skills if s in args.skill]
    agents = args.agent or list(TARGETS)

    if not skills:
        print("没有要分发的 skill。")
        return

    for skill in skills:
        print(f"\n[{skill}]")
        for agent, status, msg in deploy(skill, agents, args.dry_run):
            mark = {"ok": "✔", "skip": "⊘", "dry-run": "→"}.get(status, "?")
            print(f"  {mark} {agent:<10} {msg}")

    print("\n完成。")


if __name__ == "__main__":
    sys.exit(main())
