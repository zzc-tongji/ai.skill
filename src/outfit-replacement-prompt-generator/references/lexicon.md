# Lexicon

## 单品特征维度清单

抽取单品时，优先按固定槽位标准化描述：

- 颜色：主色、辅色、渐变、明暗、饱和度。
- 材质：棉、麻、牛仔、皮革、针织、丝缎、金属、塑料、半透明、哑光、光泽。
- 版型：修身、宽松、直筒、A 字、廓形、oversized、短款、长款。
- 结构：领型、袖型、肩型、腰线、门襟、裤脚、鞋头、鞋跟、开口方式。
- 装饰：拼接、压线、褶皱、刺绣、印花、徽标、扣件、拉链、流苏、镂空。
- 细节状态：长度、厚薄、垂坠感、褶皱、磨损、新旧、光泽、透明度、边缘处理。

按可见信息尽量补齐以上槽位；缺失项可略过，但优先保持同一顺序输出。

## 保脸增强词库

- 中文：面部五官与原图完全一致、脸型轮廓不变、肤色与肤质一致、发型发色发际线一致、表情神态一致、眼神与朝向一致、身份特征保持、身材比例与原图一致。
- 英文：preserve the exact face and facial features、identical face identity、same skin tone and texture、unchanged hairstyle and hair color、same expression、maintain original body proportions、consistent facial structure。

## 负面词库

- 结构类：distorted body、stretched figure、extra limbs、missing limbs、bad anatomy、deformed hands、wrong proportions。
- 换脸类：changed face、different person、face swap、altered identity。
- 背景类：changed background、different scene、altered environment。
- 换装瑕疵类：mixed clothing、half replaced、garment floating、wrong layering、mismatched lighting on clothes、blurry outfit edge、seam artifacts。
- 画质类：low quality、jpeg artifacts、overexposed、oversaturated。

## 使用原则

- 通用版正文不要直接抄负面词。
- 需要负面控制时，优先取最小必要词集。
- 对话式模型把规避项改写成正向约束。
- 精修时先判断问题类别，再选对应词集补强。
- 单品描述优先使用上面的维度清单，保证不同轮次和不同单品的描述口径一致。
