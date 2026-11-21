# Model Variants

## 总原则

- 先写通用版语义，再按模型语法封装外壳。
- 只改比例、负面位、权重位和一致性表达，不改核心内容。
- 用户未指定模型时，不输出变体。
- 纯文字模式下，不使用依赖图片的参数和入口（如 Midjourney 的 `--cref` / `--sref`、SD 的 IP-Adapter），改为提醒用户补传图片后再加。

## 负面词强度分级

- 轻度失真：`blurry outfit edge`、`slight distortion`、`minor proportion issues`、`weak layering`、`mismatched lighting on clothes`。
- 中度失真：`distorted body`、`wrong proportions`、`bad anatomy`、`mixed clothing`、`wrong layering`、`changed background`、`altered environment`。
- 重度失真：`stretched figure`、`extra limbs`、`missing limbs`、`deformed hands`、`face swap`、`different person`、`altered identity`、`garment floating`、`seam artifacts`。

使用规则：

- 轻度：用于边缘糊、局部光影不齐、轻微层次问题。
- 中度：用于比例、结构、混搭、背景误改等明显偏差。
- 重度：用于身份混乱、肢体错误、严重变形、服饰悬浮等强失真。
- 需要时，先选最接近的问题级别，再补少量相邻词，不要无脑堆满。

## Qwen-Image / 通义 / 千问

- 直接使用通用版自然语言。
- 比例写进【约束】段。
- 不依赖专门负面字段。

## Seedance / 即梦

- 保留三段式。
- 加强自然语言画面感和镜头/构图表达。
- 强调人物身份与原图保持一致。

## Midjourney

- 将三段式合并成流畅英文 prompt。
- 比例用 `--ar`。
- 规避项用 `--no`。
- 人脸一致性用 `--cref` 和 `--cw`。
- 风格一致性可用 `--sref`。
- 权重可用 `词::数值`。

## Stable Diffusion

- 正文用英文 tag 或短句。
- 负面词放进 Negative Prompt。
- 权重用括号语法。
- 分辨率和宽高在采样设置里处理，不靠正文控制。
- 如有需要，可提示结合 ControlNet 或 IP-Adapter 保结构。

## GPT-4o / Gemini / 豆包 / 元宝

- 直接使用自然语言指令。
- 用清晰、具体、有序的长句表达约束。
- 如平台支持参考图入口，提示用户同时挂上人物原图。

## 选词映射

- Qwen-Image / Seedance / GPT-4o / Gemini：把对应级别的负面词改写成正向约束。
- Midjourney：将对应级别的负面词放入 `--no`。
- Stable Diffusion：将对应级别的负面词放入 Negative Prompt。
