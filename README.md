# Eve MCP Client

`eve-client` is the local installer and integration CLI for connecting supported AI tools to the hosted Eve memory service.

It detects local tools, configures MCP, installs Eve-managed prompt and hook files where supported, manages auth, and can verify, repair, or remove the integration later. It can also import local history from supported clients.

## Install

Before installing the client, get an Eve workspace:

- Request access: [https://evemem.com](https://evemem.com)
- After your workspace is provisioned, sign in to the Eve dashboard:
  - [https://evemem.com/app/overview](https://evemem.com/app/overview)
- If you plan to use API-key auth, create or rotate a tenant key from the Eve admin/user setup flow before connecting your client.

### From GitHub now

Preferred:

```bash
uv tool install git+https://github.com/sherifkozman/eve-mcp.git
```

Alternative:

```bash
pipx install git+https://github.com/sherifkozman/eve-mcp.git
```

Bootstrap helper from a clone of this repo:

```bash
bash scripts/install-eve-client.sh
```

Then confirm:

```bash
eve version
eve --help
```

### From PyPI later

Once published, the package install will become:

```bash
uv tool install eve-client
```

or:

```bash
pipx install eve-client
```

## What Eve Client Does

- detects supported local AI clients
- shows an install plan before changing files
- writes Eve-managed MCP config
- installs prompt/companion files where supported
- installs hooks where supported
- stores auth locally using file-based credential storage
- provides `eve memory search` and `eve memory status` commands for direct memory access
- verifies, repairs, rolls back, and uninstalls Eve-managed changes
- imports local conversation history into Eve from supported clients

## Importing Local History

Supported importer sources today:
- Claude Code
- Codex CLI
- Gemini CLI

Core commands:

```bash
eve import scan --source claude-code
eve import preview --source claude-code --path <session_file>
eve auth login --tool claude-code
eve import upload --job <scan_job_id> --use-auth-from claude-code
eve import resume --run <run_id>
eve import cleanup --days 30
eve import cleanup --days 30 --apply
```

How it works:
- parses local history on your machine
- uploads normalized batches to the managed Eve service
- keeps a local SQLite ledger for scan jobs, upload runs, replay, and resume
- supports dry-run cleanup of old completed local importer runs by default

Importer maintenance:
- `eve import cleanup` is a generic local ledger maintenance command, not a client-specific import source feature

Auth note:
- avoid passing secrets directly on the command line when possible
- prefer `eve auth login ...` first, then run importer commands without repeating the API key
- in an interactive shell, `eve auth login --tool claude-code` prompts for the key securely

Current rollout note:
- correctness and resume are solid
- very large imports, especially Claude-heavy ones, may still be slower than ideal while batching defaults continue to be tuned

## Supported Clients

### Claude Code

Supported today:
- MCP config
- `CLAUDE.md` companion file
- package-managed hooks
- importer support: `scan`, `preview`, `upload`, `resume`

Primary auth path today:
- Eve API key

### Gemini CLI

Supported today:
- MCP config
- `GEMINI.md` companion file
- package-managed hooks
- importer support: `scan`, `preview`, `upload`, `resume`

Primary auth path today:
- Eve API key

### Codex CLI

Supported today:
- MCP config
- Eve-owned OAuth login
- runtime bearer token injection
- importer support: `scan`, `preview`, `upload`, `resume`

Primary auth path today:
- Eve OAuth

Important:
- native Codex MCP login is **not** the supported Eve path

### Claude Desktop

Not auto-configured locally for hosted Eve.

## Authentication Requirements

You need an Eve workspace and either:

- an Eve API key, or
- Eve OAuth access for supported flows

Current auth expectations:

- Claude Code: API key
- Gemini CLI: API key
- Codex CLI: Eve OAuth

OAuth browser flow exists for more clients, but it is still experimental outside the Codex path and should not be treated as the default production installer flow yet.

## Basic Usage

### First run

```bash
eve quickstart
eve connect --tool claude-code
eve verify --tool claude-code
```

### API key flow

```bash
eve auth login --tool claude-code --api-key <eve-key>
eve install --tool claude-code --apply --yes
eve verify --tool claude-code
```

### Codex OAuth flow

```bash
eve auth login --tool codex-cli --auth-mode oauth
eve install --tool codex-cli --auth-mode oauth --apply --yes
eve verify --tool codex-cli --auth-mode oauth
```

### Memory commands

```bash
eve memory search "what database do we use" --context naya
eve memory search "auth patterns" --limit 5 --json
eve memory status
eve memory status --json
```

### Common commands

```bash
eve quickstart
eve connect
eve install --dry-run
eve install --apply --yes --tool claude-code
eve status
eve doctor
eve verify
eve repair --tool gemini-cli --apply --yes
eve uninstall --tool claude-code --yes
eve run --tool codex-cli -- exec "Use Eve memory to search for my latest preference."
```

## Manual MCP Configuration

If you do not want the installer to edit client config for you, add the hosted Eve MCP server manually.

### Shared hosted MCP endpoint

```text
https://mcp.evemem.com/mcp
```

### Claude Code / Gemini CLI (API key)

Use a tenant API key from the Eve admin panel.

```json
{
  "eve-memory": {
    "httpUrl": "https://mcp.evemem.com/mcp",
    "headers": {
      "X-API-Key": "eve_<tenant>_...",
      "X-Source-Agent": "claude_code"
    }
  }
}
```

For Gemini, keep the same shape and change:

```text
X-Source-Agent = gemini_cli
```

### Codex CLI (OAuth)

Codex should use an Eve-owned bearer token, not native `codex mcp login`.

```toml
[mcp_servers.eve-memory]
url = "https://mcp.evemem.com/mcp"
bearer_token_env_var = "EVE_CODEX_BEARER_TOKEN"
startup_timeout_sec = 60

[mcp_servers.eve-memory.headers]
X-Source-Agent = "codex_cli"
```

OAuth resource:

```text
https://mcp.evemem.com/mcp
```

Codex prompt seeding belongs in the active project `AGENTS.md`. Eve manages its
own marked block inside that file rather than creating a parallel sidecar file.

### Other MCP clients

For other remote MCP clients, the minimum working setup is:

1. MCP URL:

```text
https://mcp.evemem.com/mcp
```

2. One source-agent header:

```text
X-Source-Agent: your_client_name
```

3. Choose one auth style:

- API key:

```text
X-API-Key: eve_<tenant>_...
```

- OAuth bearer:

```text
Authorization: Bearer <token>
```

4. For OAuth-capable clients, use this resource identifier:

```text
https://mcp.evemem.com/mcp
```

5. Required scopes:

```text
memory.read
memory.write
```

If the client supports a companion instruction file, point it at the active
instruction file for that tool rather than inventing a second parallel file.

### Which auth path to use

- Claude Code: API key
- Gemini CLI: API key
- Codex CLI: Eve OAuth

If you want Eve to manage the config, prompts, hooks, and verification for you, use `eve connect` instead of editing files manually.

## Safety Model

- no silent shell profile mutation
- no undocumented hooks
- no overwrite of user-authored instruction files
- backup and manifest tracking before mutation
- explicit rollback and uninstall support
- uninstall removes Eve-managed credentials and tool entries, and warns when a user-modified Eve-owned file requires manual review

## Development

Run tests:

```bash
uv run pytest --cov=eve_client --cov-report=term-missing -q tests
```

Build Python artifacts:

```bash
uv build .
```

Check build artifacts:

```bash
uvx twine check dist/*
```

Build standalone release artifacts:

```bash
bash scripts/build-eve-client-release.sh
```

## License

Apache-2.0. See [LICENSE](/Users/kozman/Repos/github.com/sherifkozman/eve/packages/client/LICENSE).
