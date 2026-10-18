# Nexa AI — Vercel deployment patch

This patch adapts the existing Nexa AI bot to Telegram webhooks on Vercel. It keeps the existing command handlers, provider order (Clean APIs → Gemini → Groq → OpenRouter), attachment parsing, and owner-only check.

## Files in this patch

Copy the included files/folders into the root of your existing GitHub repository, replacing files when prompted. The ZIP is structured at its root (no enclosing project folder).

## 1. Set up persistent PostgreSQL

Create a PostgreSQL database with a provider such as Neon. Copy its pooled or direct connection string as `DATABASE_URL` into Vercel Environment Variables. Do not use SQLite for production on Vercel because function filesystems are ephemeral and not shared reliably between invocations.

## 2. Vercel environment variables

In Vercel → Project → Settings → Environment Variables, set:

- `TELEGRAM_BOT_TOKEN` — from @BotFather
- `TELEGRAM_OWNER_ID` — your numeric Telegram user ID
- `TELEGRAM_WEBHOOK_SECRET` — random 32–64 character secret using letters, numbers, `_`, `-`
- `DATABASE_URL` — PostgreSQL connection string
- `CLEANAPI_API_KEY`
- `GEMINI_API_KEY`
- `GROQ_API_KEY`
- `OPENROUTER_API_KEY`

Optional model variables from `.env.example` can be configured too. Do not commit `.env` or paste credentials into chat.

## 3. Deploy

Commit/push these patch files to the same GitHub repository and redeploy on Vercel. The webhook endpoint is:

`https://YOUR-VERCEL-DOMAIN/api/webhook`

Opening the endpoint with a browser should return a small online status message.

## 4. Register the Telegram webhook

After deployment, register the webhook by making a POST request to Telegram's Bot API `setWebhook` endpoint. Use the bot token privately and set both `url` and `secret_token` fields. The `url` must be `https://YOUR-VERCEL-DOMAIN/api/webhook`; `secret_token` must exactly match `TELEGRAM_WEBHOOK_SECRET` in Vercel.

Do this privately in a trusted API client or terminal. Never share the token-containing URL, screenshots, or request output publicly. If your bot previously used polling, stop that polling process before registering the webhook; Telegram cannot use polling and webhook simultaneously.

## Important runtime notes

- Vercel serverless requests have execution limits. This implementation processes each Telegram update in a function invocation; slower AI provider responses or large files may exceed the function's configured duration.
- Telegram will retry webhook deliveries when the endpoint returns errors. For production-scale reliability, queue updates and process them asynchronously; this starter keeps the architecture simple.
- Only `TELEGRAM_OWNER_ID` is authorized by the existing bot handlers.
- Vercel/Python dependency versions and limits may change; check the Vercel deployment logs after the first deployment.
