# Security policy

## Secrets handling

- `config.json` is **git-ignored** and must never be committed. It is written
  with file mode `0600` (owner read/write only) and atomic replace.
- Copy `config.example.json` to your config location to start.
- Prefer **environment variables** over storing tokens on disk:

  | Setting | Env var |
  |---|---|
  | Discord webhook | `ECHELON_DISCORD_WEBHOOK` |
  | Telegram bot token / chat | `ECHELON_TELEGRAM_BOT_TOKEN` / `ECHELON_TELEGRAM_CHAT_ID` |
  | GitHub token (github.com + Enterprise) | `ECHELON_GITHUB_TOKEN` |
  | GitLab token (gitlab.com / self-hosted) | `ECHELON_GITLAB_TOKEN` |
  | Gitea / Codeberg token | `ECHELON_GITEA_TOKEN` |
  | Bitbucket username + app password | `ECHELON_BITBUCKET_USER` / `ECHELON_BITBUCKET_APP_PASSWORD` |
  | OpenAI | `ECHELON_OPENAI_KEY` / `ECHELON_OPENAI_MODEL` |
  | Gemini | `ECHELON_GEMINI_KEY` / `ECHELON_GEMINI_MODEL` |
  | Ollama | `ECHELON_OLLAMA_ENDPOINT` / `ECHELON_OLLAMA_MODEL` |
  | Slack / generic webhook | `ECHELON_SLACK_WEBHOOK` / `ECHELON_GENERIC_WEBHOOK` |
  | SMTP / email | `ECHELON_SMTP_HOST`, `ECHELON_SMTP_PORT`, `ECHELON_SMTP_USER`, `ECHELON_SMTP_PASSWORD`, `ECHELON_EMAIL_FROM`, `ECHELON_EMAIL_TO` |
  | Config path / interval | `ECHELON_CONFIG` / `ECHELON_INTERVAL` |

- Env vars **override** `config.json` at runtime and are never written back.
- CLI output masks secrets (`webhook_url -> dis***`). `--export` strips
  secrets unless `--include-secrets` is passed explicitly.
- Tokens are prompted via `getpass` (hidden input); never pass tokens as CLI
  flags (they leak into shell history).

## Config location

1. `--config PATH` or `$ECHELON_CONFIG`
2. `./config.json` (backwards compatible, if it exists)
3. `$XDG_CONFIG_HOME/echelon/config.json` or `~/.config/echelon/config.json`

## If credentials leaked

1. **Rotate immediately**: Discord webhook (Server Settings -> Integrations ->
   Webhooks -> Regenerate), Telegram (`@BotFather` -> revoke), Gemini/OpenAI
   keys, GitHub token.
2. Remove the file from history (`git rm --cached config.json`), then rotate.
   History rewrite alone is not enough.
3. Run `echelon --check-secrets` in CI to catch `discord.com/api/webhooks`,
   `AIza`, `ghp_`, `sk-`, Telegram token patterns in tracked files.

## Network / input hardening

- Code URLs must be `https://` with a `#Lx-Ly` (or Bitbucket `#lines-x:y`)
  fragment. Supported: GitHub (+Enterprise), GitLab (incl. self-hosted and
  subgroups), Gitea/Codeberg, Bitbucket Cloud, SourceHut, and plain-text raw
  URLs. Namespace/branch/path are strictly validated (no `..` traversal).
- Provider APIs are used with least privilege: GitLab `read_api`/`read_repository`,
  Gitea `read:repository`, Bitbucket app password with Repositories:Read.
  Unauthenticated access works for public repos (rate limits apply).
- Webhook URLs must be `https://` (plain `http` allowed only for localhost,
  e.g. Ollama).
- Email headers are stripped of CR/LF (header-injection safe); Telegram HTML
  is escaped; SMTP passwords stay in memory only.
- Provider API failures (rate limit, private repo without token) degrade
  gracefully: monitoring continues, commit enrichment is skipped.
