# Eve MCP — Example Configs

Copy-paste MCP config snippets for connecting your AI tools to Eve Memory without the installer.

For the automated path (recommended), use `eve connect` instead.

---

## Claude Code

Add to `.claude/settings.json` under the `mcpServers` key:

```json
{
  "mcpServers": {
    "eve-memory": {
      "httpUrl": "https://mcp.evemem.com/mcp",
      "headers": {
        "X-API-Key": "eve_YOUR_TENANT_KEY_HERE",
        "X-Source-Agent": "claude_code"
      }
    }
  }
}
```

Get your API key from the [Eve dashboard](https://evemem.com/app/overview).

---

## Gemini CLI

Add to `~/.gemini/settings.json` under `mcpServers`:

```json
{
  "mcpServers": {
    "eve-memory": {
      "httpUrl": "https://mcp.evemem.com/mcp",
      "headers": {
        "X-API-Key": "eve_YOUR_TENANT_KEY_HERE",
        "X-Source-Agent": "gemini_cli"
      }
    }
  }
}
```

---

## Codex CLI

Add to `~/.codex/config.toml`:

```toml
[mcp_servers.eve-memory]
url = "https://mcp.evemem.com/mcp"
bearer_token_env_var = "EVE_CODEX_BEARER_TOKEN"
startup_timeout_sec = 60

[mcp_servers.eve-memory.headers]
X-Source-Agent = "codex_cli"
```

Set bearer token: `export EVE_CODEX_BEARER_TOKEN=<your-eve-oauth-token>`

---

## Other MCP Clients

| Setting         | Value                              |
| --------------- | ---------------------------------- |
| MCP URL         | `https://mcp.evemem.com/mcp`       |
| API key header  | `X-API-Key: eve_<tenant>_...`      |
| OAuth bearer    | `Authorization: Bearer <token>`    |
| Source header   | `X-Source-Agent: your_client_name` |
| Required scopes | `memory.read memory.write`         |
