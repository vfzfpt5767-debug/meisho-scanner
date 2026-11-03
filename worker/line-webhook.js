// LINE の Webhook を受け取る中継役（Cloudflare Workers に貼り付けて使う）。
//
// やること：
// 1. LINE からの合図か確かめる（署名 X-Line-Signature をチャネルシークレットで確認）
// 2. あなた（LINE_USER_ID）以外からのメッセージは無視する
// 3. 「残高」→ GitHub の data/status.json を読んで、すぐに返事する（返信メッセージは無料・月の通数に数えない）
// 4. ボタン（postback）や「買い 4445 1000」のような返信 → GitHub Actions（line-event）に渡し、
//    「受け付けました」とすぐ返事する。記録が終わると GitHub Actions から「記録しました：…」が届く
// 5. それ以外 → 使い方を返事する
//
// Cloudflare の「設定 → 変数とシークレット」に入れるもの（すべて「シークレット」の種類で）：
//   LINE_CHANNEL_SECRET        … LINE Developers の「チャネル基本設定」の「チャネルシークレット」
//   LINE_CHANNEL_ACCESS_TOKEN  … GitHub Secrets に入れたものと同じ「チャネルアクセストークン（長期）」
//   LINE_USER_ID               … GitHub Secrets に入れたものと同じ「あなたのユーザーID」（Uで始まる）
//   GITHUB_TOKEN               … GitHub の fine-grained personal access token（meisho-scanner の Contents を Read and write）
//   GITHUB_REPO                … vfzfpt5767-debug/meisho-scanner

const HELP = "使える言葉：\n・残高\n・買い 4445 10単位（または 1000株。値段を書かなければ、その日の終値で記録）\n・売り 6999 全部\n・買い 4445 10単位 1665円\n・ボタン（いちばん新しいAIの注文のボタンをもう一度出す）";

export default {
  async fetch(request, env) {
    if (request.method !== "POST") return new Response("ok"); // ブラウザで開いたとき用
    const body = await request.text();
    const ok = await verifySignature(body, request.headers.get("x-line-signature") || "", env.LINE_CHANNEL_SECRET);
    if (!ok) return new Response("bad signature", { status: 401 });

    let data;
    try { data = JSON.parse(body); } catch { return new Response("bad json", { status: 400 }); }
    for (const ev of data.events || []) {
      try {
        await handle(ev, env);
      } catch (e) {
        if (ev.replyToken) await reply(env, ev.replyToken, "中継役でエラーが起きました：" + String(e).slice(0, 200));
      }
    }
    return new Response("ok"); // LINE には常に 200 を返す（Webhookの「検証」もこれで通る）
  },
};

async function handle(ev, env) {
  if (!ev.source || ev.source.userId !== env.LINE_USER_ID) return; // 本人以外は無視
  const id = ev.webhookEventId || String(ev.timestamp || "");

  if (ev.type === "postback") {
    await dispatch(env, { type: "postback", data: String(ev.postback?.data || "").slice(0, 300), event_id: id, ts: ev.timestamp });
    return reply(env, ev.replyToken, "受け付けました。1分ほどで記録して結果を送ります。");
  }
  if (ev.type !== "message" || ev.message?.type !== "text") return;

  const text = String(ev.message.text || "").trim();
  const t = text.normalize("NFKC").replace(/\s/g, "");
  if (/^(残高|ざんだか|状況|保有)$/.test(t)) {
    const st = await readStatus(env);
    return reply(env, ev.replyToken, st);
  }
  if (/^(ヘルプ|使い方|help|\?)$/i.test(t)) return reply(env, ev.replyToken, HELP);
  if (/^(ボタン|ぼたん|button)$/i.test(t) || /買|売|buy|sell|かい|うり/i.test(t)) {
    await dispatch(env, { type: "text", text: text.slice(0, 500), event_id: id, ts: ev.timestamp });
    return reply(env, ev.replyToken, "受け付けました。1分ほどで記録して結果を送ります。");
  }
  return reply(env, ev.replyToken, "読み取れませんでした。\n" + HELP);
}

async function verifySignature(body, signature, secret) {
  if (!secret || !signature) return false;
  const key = await crypto.subtle.importKey("raw", new TextEncoder().encode(secret), { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
  const mac = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(body));
  const expected = btoa(String.fromCharCode(...new Uint8Array(mac)));
  if (expected.length !== signature.length) return false;
  let diff = 0;
  for (let i = 0; i < expected.length; i++) diff |= expected.charCodeAt(i) ^ signature.charCodeAt(i);
  return diff === 0;
}

async function reply(env, replyToken, text) {
  if (!replyToken) return;
  await fetch("https://api.line.me/v2/bot/message/reply", {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${env.LINE_CHANNEL_ACCESS_TOKEN}` },
    body: JSON.stringify({ replyToken, messages: [{ type: "text", text: text.slice(0, 4900) }] }),
  });
}

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      "User-Agent": "meisho-scanner-line-webhook",
      "X-GitHub-Api-Version": "2022-11-28",
      ...(init.headers || {}),
    },
  });
}

async function dispatch(env, payload) {
  const r = await gh(env, "/dispatches", {
    method: "POST",
    headers: { Accept: "application/vnd.github+json", "Content-Type": "application/json" },
    body: JSON.stringify({ event_type: "line-event", client_payload: payload }),
  });
  if (r.status !== 204) throw new Error(`GitHubに渡せませんでした（HTTP ${r.status}）。GITHUB_TOKEN の権限と期限を確認してください`);
}

async function readStatus(env) {
  const r = await gh(env, "/contents/data/status.json?ref=main", { headers: { Accept: "application/vnd.github.raw+json" } });
  if (!r.ok) return `残高を読めませんでした（HTTP ${r.status}）。少し待ってからもう一度送ってください。`;
  try {
    const j = JSON.parse(await r.text());
    return j.text || "残高の情報がありません。";
  } catch {
    return "残高の情報を読めませんでした。";
  }
}
