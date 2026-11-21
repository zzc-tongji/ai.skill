# 约定与规范（CONVENTIONS）

## 命名

- Skill 目录名 = skill id，用 **kebab-case**：小写字母、连字符、无空格。
  - 例：`qianji-icbc-cross-match`、`code-review`、`meeting-notes`。
  - 原因：WorkBuddy 的 `manifest.json` id 要求小写无空格，统一用 kebab-case 最省心。

## 目录职责

| 位置 | 放什么 | 不放什么 |
|---|---|---|
| `src/<skill>/SKILL.md` | 通用正文（标题、指令、步骤、示例） | **不放 frontmatter 头** |
| `src/<skill>/assets/` | 技能用到的模板、xlsx、图片等静态资源 | 不放可执行逻辑（放 scripts/） |
| `src/<skill>/scripts/` | 技能用到的脚本（py/sh 等） | 不放数据文件 |
| `variants/<agent>/<skill>.head.md` | 该平台的 frontmatter 头（name/description 等） | 不放正文 |

## frontmatter 头（variants）

- 头文件只含 YAML frontmatter（`---` 包裹），`build.py` 会把它拼到正文前面。
- 通用字段：`name`（= skill id）、`description`（一句话，**含触发词**）。
- 各平台可能有额外字段（如 WorkBuddy/Claude 的触发规则、Qwen 的专属字段），
  按对应平台文档补充，**只在该平台的 head 文件里加**，不要污染通用正文。
- 某平台暂时没有专属要求时，也可以不给它建 head 文件——此时 `build.py`
  会退化为「纯正文、无 frontmatter」，在 `--list` 里显示为 `-`。

## 版本控制纪律

1. 只提交 `src/`、`variants/`、`build.py`、`scripts/`、`docs/`、`README.md`、`.gitignore`。
2. 各 Agent 产物目录（`.qwen/`、`.workbuddy/`、`.claude/`、`.codex/`）一律忽略。
3. 标准流程：改源 → `git commit` → `python build.py` → 在 Agent 里验证。
4. 验证失败就改源重新提交，**不要在产物目录里打补丁**。

## 拆仓时机

单个 Skill 需要独立对外发布、独立权限/可见性、或数量很大（几十上百）时，
再用 `git subtree split` 把它拆成独立仓库。默认从 monorepo 起步，不要提前拆。
