# buildo-bot

**Telegram-бот платформы Buildo** — AI-генерация сайтов в Telegram.

## Stack

- **aiogram 3.13+** + Redis FSM
- **aiogram-dialog** для декларативных сценариев
- **Supabase** (Auth + DB + Storage + Edge Functions)
- **OpenRouter** LLM chain (minimax-m3 → kimi-k2.6:free → opencode-1/2/3)
- **Docker** + GitHub Actions → SSH `108.165.164.85`

## Quick start (local)

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# edit .env with real values
python -m bot.main
```

## Deploy

Push to `main` → CI runs lint+test → SSH deploy to `108.165.164.85:~/Projects/buildo-bot`.

Server-side `.env` lives at `~/.buildo-bot.env` (NOT in git). CI copies it on each deploy.

## Architecture

```
bot/
  config.py          # pydantic-settings, all env-vars
  main.py            # entry point: bot + health
  handlers/
    common.py        # /start, /help, /cancel, fallback
    site_builder.py  # /site, /sites, FSM-flow
    admin.py         # /admin_* (admin only)
  services/
    admin.py         # admin queries (Supabase-stub)
    llm.py           # OpenRouter chain + fallback
    supabase.py      # lazy client wrapper
  middlewares/
    admin_filter.py  # allow only ADMIN_TG_ID
    logging.py       # structured log
```

## Admin

Single admin: TG ID `6318513424` (configurable via `ADMIN_TELEGRAM_ID` env).

Commands:

- `/admin` — admin menu
- `/admin_stats` — platform statistics
- `/admin_users` — recent users
- `/admin_payments` — recent payments
- `/admin_broadcast` — broadcast message
- `/admin_redeploy <site_id>` — re-deploy
- `/admin_kill <user_id>` — ban user
- `/admin_logs` — recent logs

## Required GitHub Secrets (for CI/CD)

- `SERVER_HOST` = `108.165.164.85`
- `SERVER_USER` = ssh user (probably `root` or `ubuntu`)
- `SERVER_SSH_KEY` = private SSH key with access to `~/Projects/buildo-bot`

## Status

- **Phase 0 / MVP skeleton** — bot imports, runs polling, admin commands work, FSM flows are stubs
- **Phase 1** — real LLM pipeline, Supabase persistence, Layero deploy integration
- See `nemo-team-docs/projects/buildo/` for full spec
