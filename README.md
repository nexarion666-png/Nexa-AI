# Nexa AI — Personal Telegram Assistant

A private Telegram AI bot with ordered provider fallback, conversation history, saved memories, and attachment analysis. Supports local polling and Vercel webhook deployment.

## Provider priority

The application tries configured providers in this exact order:

1. Clean APIs
2. Google Gemini
3. Groq
4. OpenRouter

Unconfigured providers are skipped. The fallback loop catches provider/request errors and tries the next configured provider. Configure model IDs to match the models enabled for your account. The app does not claim every model supports every input type.

## Features

- Natural multi-turn conversation with SQLite conversation history
- Private owner-only access using `TELEGRAM_OWNER_ID`
- Image understanding via vision-capable models
- PDF text extraction and scanned PDF support through Gemini where available
- DOCX and XLSX text/table extraction
- ZIP inspection in memory (no archive extraction to disk)
- Code/text file analysis
- Live web search through the Tavily Search API (`/search` plus automatic lookup for freshness-sensitive prompts)
- GitHub repository listing, code search, file reading and owner-authorized file commits (`/github`)
- `/new`, `/clear`, `/remember`, `/memory`, `/forget`, `/models`, `/help`
- Environment-based secret configuration
- Dockerfile and optional Render background-worker blueprint

## 1. Create Telegram bot

1. Open [@BotFather](https://t.me/BotFather) in Telegram.
2. Send `/newbot`, choose a display name and unique username.
3. Copy the bot token into your private environment configuration. Never commit it.

## 2. Get your numeric Telegram user ID

Use a trusted Telegram ID bot or a small bot you control to find your numeric account ID. Put that number in `TELEGRAM_OWNER_ID`. Do not use your username; usernames can change.

## 3. Configure environment

Copy `.env.example` to `.env` and fill in:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_OWNER_ID`
- At least one provider key
- Optional: `TAVILY_API_KEY` for live web search
- Optional: `GITHUB_TOKEN` and `GITHUB_ALLOWED_REPOS` for GitHub integration
- Recommended: all four provider keys

Provider keys are optional individually; missing providers are skipped. Never upload `.env` to GitHub.

Default model IDs are examples and can change or may not be enabled on your account:
- Gemini: `gemini-2.5-flash`
- Groq: `llama-3.3-70b-versatile`
- OpenRouter: `openai/gpt-4o-mini`
- Clean APIs: if `CLEANAPI_MODEL` is blank, the bot attempts `GET /models` and chooses the first returned model.

If Clean APIs uses a different base URL for your account, set `CLEANAPI_BASE_URL` to the documented API base URL. Default is `https://cleanapis.com/v1`.

## 4. Run locally

Python 3.11+ recommended.

```bash
python -m venv .venv
# Linux/macOS/Termux:
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your own values
python -m nexa_ai.bot
```

On Android, running in Termux is possible but the process may stop when Android kills Termux. For always-on use, deploy as a background worker.

## 5. Deploy on Vercel (webhook mode)

The Vercel version uses Telegram webhooks rather than long polling. Copy the included `api/webhook.py` and `vercel.json` to the repository root. Vercel detects Python functions under `api/`.

**Persistent database required:** create a PostgreSQL database (for example, Neon) and set `DATABASE_URL` in Vercel Environment Variables. Do not use the local SQLite file for production on Vercel; serverless filesystems are ephemeral and not shared reliably between invocations. The bot still uses SQLite automatically for local development when `DATABASE_URL` is unset.

Set these Vercel environment variables:

- `TELEGRAM_BOT_TOKEN`
- `TELEGRAM_OWNER_ID`
- `TELEGRAM_WEBHOOK_SECRET` (random secret, matching Telegram's webhook secret)
- `DATABASE_URL`
- Any provider API keys you use
- `TAVILY_API_KEY` (optional, for Tavily live search)
- `GITHUB_TOKEN` and `GITHUB_ALLOWED_REPOS` (optional, for GitHub tools)

Deploy, then set Telegram's webhook URL to `https://YOUR-VERCEL-DOMAIN/api/webhook`, with Telegram `secret_token` matching `TELEGRAM_WEBHOOK_SECRET`. Do this privately; never publish a token-containing Bot API URL. Stop any old polling process before switching to webhooks.

**Serverless limits:** AI responses and large attachments can take longer than a function's execution limit. The supplied setup is a starter deployment; check Vercel's current Python runtime, duration, and plan limits. For stronger reliability, a queued background worker may be needed.

## 6. Local polling deployment

For local development or a traditional background-worker host, run `python -m nexa_ai.bot`. The included `render.yaml` describes a Docker worker. Check the hosting provider's current plan and pricing; continuous workers may not be free.

## Attachment notes and limits

- Default application limit is 15 MB per attachment (`MAX_ATTACHMENT_MB`).
- Telegram Bot API download limits and host memory limits also apply.
- Images are sent to the first provider that accepts the request; the selected model must support vision.
- Text-based PDFs are extracted locally. Scanned/image PDFs are passed to Gemini if configured and supported.
- DOCX and XLSX are extracted locally.
- ZIP files are inspected in memory; paths are checked and files are not extracted to disk. Only recognized text/code files are included, with size and total-text caps.
- Legacy `.doc`, `.ppt`, and `.pptx` are not fully parsed by this starter version. Convert to PDF or DOCX where needed.
- Audio/video analysis is not implemented in this build.
- ZIP output is context-limited; large projects may need targeted follow-up files.

## Privacy and security

- Only the Telegram account ID configured in `TELEGRAM_OWNER_ID` is authorized.
- On Vercel, conversation history and memories are stored in PostgreSQL through `DATABASE_URL`. Locally, they are stored in SQLite. Do not rely on Vercel local files for persistent data.
- Use private repository settings if pushing this code to GitHub.
- Do not commit `.env`, database files, or API keys.
- Uploaded content is sent to the selected AI provider when needed for analysis. Review each provider's privacy and retention terms.
- GitHub access requires a fine-grained personal access token with only the required repository permissions. Restrict it to selected repositories and list the same repositories in `GITHUB_ALLOWED_REPOS`. For reading/searching, grant repository metadata and contents read access; for file commits, grant contents read/write. The bot does not need account administration, organization administration, Actions secrets, or workflow permissions for the implemented commands.
- `/github write` creates or updates a text file and commits directly to the repository's default branch unless the API is later extended with a branch option. Review content carefully before using it. The bot does not delete files, repositories, branches, or secrets.
- Tavily search snippets and GitHub files are treated as untrusted external content; the AI is instructed not to follow instructions embedded in them.
- The bot does not execute source code.

## Troubleshooting

- `401` / `403`: check provider key and account access.
- `404 model not found`: change the model ID to one available in your account.
- `429`: provider rate limit; the bot should try the next configured provider.
- `No AI provider is configured`: add at least one API key.
- `Clean APIs returned no models`: set `CLEANAPI_MODEL` explicitly after checking the model list in your Clean APIs account.
- Bot replies that owner access is restricted: check `TELEGRAM_OWNER_ID` is your numeric Telegram user ID.


## Live web search (Brave)

Create a Tavily Search API subscription and set \`TAVILY_API_KEY\` in the deployment environment. No search key is needed to run the bot without live search.

- Use \`/search your query\` to explicitly search and ask the configured AI provider to synthesize the results.
- Freshness-sensitive messages containing terms such as "latest", "today", "currently", or "search online" trigger an automatic lookup when the Brave key is configured.
- Responses are instructed to cite the returned source URLs. Brave result snippets are treated as untrusted input.
- The integration requests up to six results and applies a request timeout.

## GitHub integration

Configure a **fine-grained personal access token** in \`GITHUB_TOKEN\`. Restrict the token to only the repositories Nexa AI needs, and set \`GITHUB_ALLOWED_REPOS\` to a comma-separated allowlist of \`owner/repository\` names. Do not use a classic account-wide token.

For the current commands, grant:
- Metadata: read-only
- Contents: read-only for repository listing/file reading/code search
- Contents: read and write only if you want \`/github write\` to create/update files

Commands (owner-only Telegram bot):
- \`/github repos\`
- \`/github read owner/repo path/to/file [branch]\`
- \`/github search owner/repo search terms\`
- \`/github write owner/repo path/to/file | commit message | complete file content\`

File writes commit directly to the repository's default branch. There is no file deletion, secret access, repository administration, or workflow management in this initial integration. Keep the token in Vercel/Render environment variables or a local untracked \`.env\`; never commit it.
