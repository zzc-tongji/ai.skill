# skill — 个人 Skill 单仓库（monorepo）

一个 Git 仓库统一管理我个人的多个 Agent Skill，用「单一事实源 + 每平台差异」的方式，把同一套 Skill 构建为各平台的技能包（zip）。

## 设计原则

- **唯一真源**：Skill 的通用正文只存在 `src/`，各平台的元数据（frontmatter head）存在 `variants/`。
- **产物不手改**：`dist/` 由 `build.py` 生成，是「构建产物」。**永远只改 `src/` 和 `variants/`**。
- **有 head 才生成**：只有 `variants/` 下存在对应 head 的 Agent 才会生成产物。
- **一个仓库管所有 Skill**：共享同一套规范和构建脚本；某个 Skill 将来要独立对外发布时，再用 `git subtree split` 拆出。

## 目录结构

```
skill/
├── src/<skill>/               # 唯一真源（通用正文 + 资产）
│   ├── SKILL.md               # 纯正文，不含 frontmatter
│   ├── assets/                # 技能要用的模板/资源
│   └── scripts/               # 技能要用的脚本（py/sh 等）
├── variants/<agent>/          # 各平台的差异（frontmatter head）
│   └── <skill>.head.md
├── dist/<agent>/<skill>.zip   # ★ 构建产物（gitignore，不提交）
├── build.py                   # 拼装 head+正文 → 打包 zip 到 dist/
└── .gitignore
```

支持的 Agent：`qwen` / `workbuddy` / `claude` / `codex`。

## 用法

### 查看现状

```bash
python build.py --list
```

### 构建到 dist/（仓库同目录下）

```bash
python build.py                 # 全部（有 head 的）agent → dist/<agent>/<skill>.zip
python build.py --agent qwen    # 只构建 qwen
python build.py --dry-run       # 只看不写
python build.py --clean         # 清空 dist/
```

产物是 zip 包，位于 `dist/<agent>/<skill>.zip`，该目录已被 `.gitignore` 忽略。zip 内含 `SKILL.md`（head+正文已拼好）及 `assets/`、`scripts/`。

### 新增一个 Skill

1. 建 `src/<skill>/`，写 `SKILL.md`（纯正文），放入 `assets/`、`scripts/`。
2. 为需要的平台各写一个 `variants/<agent>/<skill>.head.md`（frontmatter 头）。
3. `git add . && git commit -m "add: <skill>"`。
4. `python build.py`。

## 规范

### 命名

- Skill 目录名 = skill id，用 **kebab-case**：小写字母、连字符、无空格。
  - 例：`qianji-icbc-cross-match`、`code-review`、`meeting-notes`。
  - 原因：WorkBuddy 的 `manifest.json` id 要求小写无空格，统一用 kebab-case 最省心。

### 目录职责

| 位置 | 放什么 | 不放什么 |
|---|---|---|
| `src/<skill>/SKILL.md` | 通用正文（标题、指令、步骤、示例） | **不放 frontmatter 头** |
| `src/<skill>/assets/` | 技能用到的模板、xlsx、图片等静态资源 | 不放可执行逻辑（放 scripts/） |
| `src/<skill>/scripts/` | 技能用到的脚本（py/sh 等） | 不放数据文件 |
| `variants/<agent>/<skill>.head.md` | 该平台的 frontmatter 头（name/description 等） | 不放正文 |

### frontmatter 头（variants）

- 头文件只含 YAML frontmatter（`---` 包裹），`build.py` 会把它拼到正文前面。
- 通用字段：`name`（= skill id）、`description`（一句话，**含触发词**）。
- 各平台可能有额外字段（如 WorkBuddy/Claude 的触发规则、Qwen 的专属字段），
  按对应平台文档补充，**只在该平台的 head 文件里加**，不要污染通用正文。
- **没有 head 文件的平台不会生成产物**（在 `--list` 里显示为 `-`）。要启用某平台，就给它补一个 head。

### 版本控制纪律

1. 只提交 `src/`、`variants/`、`build.py`、`README.md`、`.gitignore`。
2. `dist/` 一律忽略，不提交。
3. 标准流程：改源 → `git commit` → `python build.py` → 在 Agent 里验证。
4. 验证失败就改源重新提交，**不要在产物里打补丁**。

## 拆仓时机

单个 Skill 需要独立对外发布、独立权限/可见性、或数量很大（几十上百）时，
再用 `git subtree split` 把它拆成独立仓库。默认从 monorepo 起步，不要提前拆。
