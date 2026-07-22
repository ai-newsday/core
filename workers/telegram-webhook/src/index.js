const ACTIONS = { keep: "✅ 已保留", drop: "❌ 已删除", skip: "⏭ 已跳过" };
const TTL = 604800; // 7 天

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/tg") {
      return handleWebhook(request, env);
    }
    if (request.method === "GET" && url.pathname === "/decisions") {
      return handleDecisions(request, env);
    }
    return new Response("not found", { status: 404 });
  },
};

async function tg(env, method, payload) {
  await fetch(`https://api.telegram.org/bot${env.TELEGRAM_BOT_TOKEN}/${method}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
}

async function handleWebhook(request, env) {
  if (request.headers.get("X-Telegram-Bot-Api-Secret-Token") !== env.WEBHOOK_SECRET) {
    return new Response("forbidden", { status: 403 });
  }
  try {
    const update = await request.json();
    const cq = update.callback_query;
    if (cq && typeof cq.data === "string") {
      const i = cq.data.indexOf(":");
      const itemId = i >= 0 ? cq.data.slice(0, i) : "";
      const action = i >= 0 ? cq.data.slice(i + 1) : "";
      if (itemId && ACTIONS[action]) {
        const label = ACTIONS[action];
        await tg(env, "answerCallbackQuery", { callback_query_id: cq.id, text: label });
        if (cq.message) {
          // editMessageText 会丢失原文的链接 entities（cq.message.text 是纯文本），
          // 改用 editMessageReplyMarkup 只换按钮为状态标记，正文（含链接）不动。
          await tg(env, "editMessageReplyMarkup", {
            chat_id: cq.message.chat.id,
            message_id: cq.message.message_id,
            reply_markup: { inline_keyboard: [[{ text: label, callback_data: "noop" }]] },
          });
        }
        await env.DECISIONS.put(`dec:${itemId}`, action, { expirationTtl: TTL });
      }
    }
  } catch (_e) {
    // 吞掉: 任何错误都回 200, 防 Telegram 无限重投
  }
  return new Response("ok");
}

async function handleDecisions(request, env) {
  if (request.headers.get("Authorization") !== `Bearer ${env.DECISIONS_API_SECRET}`) {
    return new Response("forbidden", { status: 403 });
  }
  const out = {};
  let cursor;
  do {
    const page = await env.DECISIONS.list({ prefix: "dec:", cursor });
    for (const k of page.keys) {
      const action = await env.DECISIONS.get(k.name);
      if (action) out[k.name.slice(4)] = action; // 去掉 "dec:" 前缀
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return Response.json(out);
}
