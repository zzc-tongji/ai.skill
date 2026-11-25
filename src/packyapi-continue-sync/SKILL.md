
# PackyAPI 到 Continue 配置同步

从 PackyAPI 模型广场抓取分组与模型价格，按口径筛选后完成两件事：

1. **更新 Continue 配置**：改写 `~/.continue/config.yaml`——只改动 `models:` 段内 `# >>> packyapi-continue-sync:begin` 与 `:end` 注释标记之间的**设定区域**，区外内容一律不动；区域不存在则新建（若存在旧版脚本生成的 PackyAPI 分组块，一次性并入区域）；模型命名为 `分组.模型名`，密钥以 `${{ secrets.PACKYAPI_<分组大写> }}` 引用
2. **生成价格表**：写入 `~/.continue/packyapi.price.md`，格式见下方「价格表模板」

> 下文所有 `~/.continue/...` 路径在 Windows 上即 `%USERPROFILE%\.continue\...`。

> 🚨 **红线：`~/.continue/.env` 是存放全部 API Key 的关键敏感文件。任何情况下都禁止读取、写入、修改、重命名、删除或移动它，也不要在对话中展示、复制其内容。密钥变更只能由用户本人手动完成。**

## 使用方法

核心脚本：`scripts/sync_packyapi.py`（仅标准库，无第三方依赖）。

**默认行为是预览**：脚本打印将写入的价格表与 config.yaml 内容，不写任何文件。执行后的固定流程：

1. 无参数运行脚本，得到预览输出
2. 把预览内容（或要点摘要）发到对话里展示给用户
3. **询问用户是否落盘**，用户确认后才加 `--write` 再次运行写入
4. 未经用户明确确认，绝不加 `--write`

```bash
# 预览：抓取 → 筛选 → 打印价格表与 config 内容（不落盘）
python scripts/sync_packyapi.py

# 用户确认后落盘：写价格表 + 更新 config.yaml 的 models 段
python scripts/sync_packyapi.py --write

# 只抓取原始 JSON（排查数据用）
python scripts/sync_packyapi.py --fetch-only --raw packy-pricing.json

# 临时换分组口径
python scripts/sync_packyapi.py --groups codex-sale,cc --md 价格表.md
```

参数：`--groups`（分组列表）、`--md`（价格表路径）、`--config`（config.yaml 路径）、`--raw`、`--fetch-only`、`--write`（落盘开关，默认关）。后两者默认值即上文两个生成物路径。

## 抓取要点

全程静态抓取即可，无需浏览器。

- 数据端点：`GET https://www.packyapi.com/api/pricing`，一次返回全部数据：`usable_group`（分组名→说明）、`group_ratio`（分组倍率）、`model_group_ratio`（模型级特殊倍率）、`data`（模型明细）、`catalog_version`（快照版本号）
- curl/urllib 直接可用；若遇 403（Cloudflare），补浏览器 UA + Referer 头即可（脚本已内置）
- **避开** `/api/models`、`/api/models/catalog`——被 CF 盾拦死
- 备用端点：`/api/ratio_config`（纯倍率字典）、`/api/status`（站点配置）

## 价格换算

换算遵循 new-api 系标准。

```
输入价 $/M = model_ratio × 有效分组倍率 × 2
输出价 $/M = 输入价 × completion_ratio
有效倍率   = model_group_ratio[分组][模型]（模型级特价优先）否则 group_ratio[分组]
缓存读     = 输入价 × cache_ratio
```

校准锚点：`gpt-6.1-sol`（ratio=1）× codex 组（0.5）× 2 = $1/M，为官方 $2 的 5 折。

## 默认筛选口径

以下为脚本默认口径，调整时修改 `scripts/sync_packyapi.py` 顶部 CONFIG 区。

1. 分组：`codex-sale`、`codex`、`cc`、`claude-officially`
2. 厂商：仅 Anthropic / OpenAI（按接口 `vendors` 字段）
3. 剔除：模型名含 `image`、`codex-auto-review`（Auto-review 审查模型，非编码模型）
4. 同档位取最高版本：OpenAI 按 sol/terra/astra/luna 归并，Claude 按 opus/sonnet/haiku/fable 归并，每档只留版本号最高者；无法解析档位的模型一律剔除
5. 组内按输入单价从便宜到贵排列

## 价格表模板

价格表固定为 `# PackyAPI` 标题 + 单张表，无其他说明文字。模板如下（示例行仅示意）：

```markdown
# PackyAPI

| 分组.模型 | 厂商 | 输入 $/M | 输出 $/M | 缓存读 |
|---|---|---|---|---|
| `codex-sale.gpt-6.2-sol` | OpenAI | $0.60 | $3.00 | ×0.05 |
| `cc.claude-opus-5-5` | Anthropic | $10.00 | $50.00 | ×0.05 |
```

排序：按分组倍率升序、组内输入单价从便宜到贵。

## 生成物格式

- 价格表：按「价格表模板」生成
- config.yaml：只改动 `models:` 段内 begin/end 标记之间的设定区域，区外条目与文件其余部分原样保留；区域内分组注释含倍率与说明，组内注释「从便宜到贵」

## 注意事项

- 未计入价格：高峰时段上浮（工作日 09:00–12:00 / 14:00–18:00 Asia/Shanghai）、`quota_type=1` 按次计费模型、`:1h` 限时变体倍率
- `catalog_version` 变化表示站点更新过数据，重跑即可刷新
- 写完 config.yaml 后需 VSCode Reload Window 生效；若某分组令牌缺失，对应模型会 401——提醒用户自行检查 `.env`（勿代读代改），确认 `PACKYAPI_*` 值存在且无多余空格
