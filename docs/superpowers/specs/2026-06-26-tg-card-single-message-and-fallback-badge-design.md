# TG 卡片合一 + 回退徽章 — 设计

日期: 2026-06-26 · 触发: 用户 TG 截图 2026-06-26 — 多张卡缺按钮(无法 keep/drop)、多张卡未翻译(title=英文 repo 名)

## 问题(根因坐实)

### 病 1 — 按钮缺失

每张卡当前发**两条消息**([telegram_polling.py:80-85](../../../src/notifiers/telegram_polling.py)):

```python
await self._bot.send_message(..., text=cover, ...)         # 消息 1: 封面
msg = await self._bot.send_message(..., text=body, reply_markup=keyboard)  # 消息 2: 正文+按钮
```

两次调用**无事务**: cover 发成功、body 发失败 → tick.py 捕获 `BLE001` 仅记 log → 用户看到**孤儿封面、无按钮**(无法 keep/drop)。

最常触发的失败: `body=""`。当 `extractive_fallback` 触发且 `raw_summary` 为空(常见于 hf-models / 短 repo description) → `body=_trim_to_sentence("", N)=""` → tags 也常为 [] → `body_msg=""` → Telegram API 拒收空 text → 抛 → 孤儿。

### 病 2 — 未翻译(UX 病灶)

`mauriceboe/TREK` 卡 `title_zh == title_en` 完全相同字符串 → [interpret.py:101](../../../src/pipeline/interpret.py:101) `extractive_fallback` 触发(LLM 失败 / 返非法 JSON / 拒答)。回退结果:
- title = 英文 repo 全名
- body = raw_summary(原文)
- 用户看到一张看似"正常"的卡,但全是英文,误以为产品翻译模块坏了。

实际是 LLM 这条没解出来。降级路径**对用户不透明**。

## 目标 / 验收标准

1. **不可能孤儿**: 卡片要么完整(cover+body+按钮),要么干脆不发。
2. **空 body 不致命**: interpret 回退 + raw_summary 空时,卡仍发出且按钮可点(填占位文)。
3. **降级可见**: `extractive_fallback` 卡的封面带视觉徽章,用户立刻知道"这条 LLM 没解出来,按英文+原文摘要审"。
4. 外科手术式: 只改 [src/notifiers/telegram_polling.py](../../../src/notifiers/telegram_polling.py) + [src/pipeline/tick.py](../../../src/pipeline/tick.py) `_build_card`;不改 interpret、不改 webhook、不改 publish。

## 设计

### 改动 1 — 卡片合并为单消息(治病 1)

`_make_card_messages`(返回 `(cover, body)` tuple)改为 `_make_card_message`(返回单 str)。
cover 与 body 拼接,中间用 `\n\n` 分隔,按钮挂在这条唯一消息上。

```
[官方] mauriceboe/TREK
mauriceboe/TREK

95 分 ｜ ⭐ 1234
gh-trending-ai

(正文 body)

#tag1 #tag2 #tag3
```

`send_review_card` 一次 `send_message` 调用,返回 `msg.message_id`。

**长度核查**: cover ~80 + body ≤240(`body_max_chars`) + tags ~30 ≈ 350 chars,
Telegram 单消息上限 4096,远未触及。即便日后 body 加大也安全。

**防空**: 渲染前若 `body == ""`,填占位文 `(未生成解读，请参见原文链接)`,确保拼接后消息非空。

### 改动 2 — 回退徽章(治病 2,UX 透明)

`_build_card` 多透一个字段:`status = item.interpretation_status`(字符串,如 `"ok"` / `"extractive_fallback"`)。
`_make_card_message` 检测 `status == "extractive_fallback"` → cover 第一行加前缀 `⚠️ [未解读] `:

```
⚠️ [未解读] [官方] mauriceboe/TREK
mauriceboe/TREK
...
```

徽章固定中文 `⚠️ [未解读]`(简洁,与卡内 `[官方]` 类标签一致风格)。

### 不做(YAGNI / 留后续)

- **不诊断 interpret 为什么频繁回退**: 留独立 PR(先加 emit 观测哪些源/genre 触发回退最多,再改 prompt / 加重试)。本 PR 只让降级透明且可用。
- 不改 raw_summary 抽取(那是 collect 层)。
- 不改 final markdown report 渲染(独立通路)。

## 测试要点(TDD)

- `_make_card_message`(单返回)拼接 cover/body/tags;含按钮的 `send_message` 被调一次。
- body 为空 → 输出含占位文 `(未生成解读，请参见原文链接)`,消息非空。
- status="extractive_fallback" → 输出以 `⚠️ [未解读] ` 开头;status="ok" → 不含。
- `send_review_card` mock 验证 `send_message` 调用次数 == 1(从 2 降到 1)。
- 既有 TG 测试同步更新:`_make_card_messages` → `_make_card_message`、tuple → str。
