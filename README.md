# Eve MCP Client

`eve-client` is the local installer and integration CLI for connecting supported AI tools to the hosted Eve memory service.

It detects local tools, configures MCP, installs Eve-managed prompt and hook files where supported, manages auth, and can verify, repair, or remove the integration later.

## Install

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
- stores auth locally using keyring-first storage
- verifies, repairs, rolls back, and uninstalls Eve-managed changes

## Supported Clients

### Claude Code

Supported today:
- MCP config
- `CLAUDE.md` companion file
- package-managed hooks

Primary auth path today:
- Eve API key

### Gemini CLI

Supported today:
- MCP config
- `GEMINI.md` companion file
- package-managed hooks

Primary auth path today:
- Eve API key

### Codex CLI

Supported today:
- MCP config
- Eve-owned OAuth login
- runtime bearer token injection

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
