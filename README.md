# skill — 个人 Skill 单仓库（monorepo）

一个 Git 仓库统一管理我个人的多个 Agent Skill，用「单一事实源 + 每平台差异」的方式，把同一套 Skill 分发到多个 AI Agent（Qwen / WorkBuddy / Claude Code / Codex CLI）。

## 设计原则

- **唯一真源**：Skill 的通用正文只存在 `src/`，各平台的元数据（frontmatter head）存在 `variants/`。
- **产物不手改**：`dist/` 由 `build.py` 生成，是「构建产物」。**永远只改 `src/` 和 `variants/`**。
- **一个仓库管所有 Skill**：共享同一套规范和构建脚本；某个 Skill 将来要独立对外发布时，再用 `git subtree split` 拆出。

## 目录结构

```
skill/
├── src/<skill>/               # 唯一真源（通用正文 + 资产）
│   ├── SKILL.md               # 纯正文，不含 frontmatter
│   ├── assets/                # 技能要用的模板/资源
│   └── scripts/               # 技能要用的脚本
├── variants/<agent>/          # 各平台的差异（主要是 frontmatter head）
│   └── <skill>.head.md
├── dist/<agent>/<skill>/      # ★ 构建产物（gitignore，不提交）
├── build.py                   # 拼装 head+正文 → 输出到 dist/
├── scripts/new-skill.sh       # 一键新建 skill 脚手架
└── docs/CONVENTIONS.md        # 命名与 frontmatter 规范
```

支持的 Agent：`qwen` / `workbuddy` / `claude` / `codex`。

## 用法

### 查看现状

```bash
python build.py --list
```

### 构建到 dist/（仓库同目录下）

```bash
python build.py                 # 全部 skill → dist/<agent>/<skill>/
python build.py --agent qwen    # 只构建 qwen
python build.py --dry-run       # 只看不写
python build.py --clean         # 清空 dist/
```

产物输出到仓库根目录下的 `dist/<agent>/<skill>/`，该目录已被 `.gitignore` 忽略。

### 新增一个 Skill

```bash
bash scripts/new-skill.sh my-new-skill   # 生成骨架
# 1. 写 src/my-new-skill/SKILL.md（正文）
# 2. 填 variants/*/my-new-skill.head.md（各平台 head）
git add . && git commit -m "add: my-new-skill"
python build.py
```

## 铁律

1. 改内容 → 改 `src/`；改平台适配 → 改 `variants/`。
2. `dist/` 是生成物，**不要手改**（手改会被下次 build 覆盖），也**不要提交**。
3. 一次提交对应一个 Skill 或一个平台的适配变化，提交信息写清「改了什么」。
