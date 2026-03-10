# Eve Client

Local installer and integration CLI for connecting developer tools to the hosted Eve memory service.

## Install

Preferred:

```bash
uv tool install eve-client
```

Alternative:

```bash
pipx install eve-client
```

Release helper:

```bash
bash packages/client/scripts/install-eve-client.sh
```

The helper verifies the installed `eve` executable and prints its resolved path when possible.
In headless mode it fails closed if a different `eve` binary is ahead of the installed one on `PATH`.

Local repo validation:

```bash
EVE_CLIENT_SOURCE=packages/client bash packages/client/scripts/install-eve-client.sh
```

If you need to overwrite an existing local `eve` tool during testing:

```bash
EVE_CLIENT_SOURCE=packages/client EVE_CLIENT_INSTALL_FLAGS="--force" \
  bash packages/client/scripts/install-eve-client.sh
```

If you intentionally need to continue while another `eve` binary is ahead of the installed one on `PATH`:

```bash
EVE_CLIENT_ALLOW_SHADOWED_BINARY=1 bash packages/client/scripts/install-eve-client.sh
```

Precedence:
- default non-interactive behavior fails closed on a shadowed binary
- `EVE_CLIENT_ALLOW_SHADOWED_BINARY=1` explicitly allows continuing
- `EVE_CLIENT_FAIL_ON_SHADOWED_BINARY=1` always wins and forces failure

Run:

```bash
eve --help
```

## What it does

- detects supported local tools
- shows an explicit install plan before mutating anything
- writes Eve-managed MCP config and companion files
- stores local credentials through keyring-first storage
- verifies, repairs, rolls back, and uninstalls Eve-managed changes
- removes Eve-owned tool config entries and companion files on uninstall while refusing to delete user-modified Eve-owned files

## Supported rollout

- Claude Code
- Gemini CLI
- Codex CLI

Notes:

- Claude Code and Gemini CLI are currently supported on the API-key path.
- Gemini package installs:
  - MCP config
  - package-managed hooks in `~/.gemini/settings.json`
  - a companion `GEMINI.md` at global or project scope
- Codex is supported through the Eve-owned OAuth + bearer runtime path.
- OAuth browser flow exists, but it remains experimental and is not the primary packaged path yet.
- Claude Desktop is instructional-only for hosted Eve and is not auto-configured locally.

## Common commands

```bash
eve quickstart
eve install --dry-run
eve install --apply --yes --tool claude-code --api-key <eve-key>
eve status
eve doctor
eve verify
eve repair --tool gemini-cli --apply --yes
eve uninstall --tool claude-code --yes
eve auth login --tool codex-cli --api-key <eve-key>
```

Recommended first run:

```bash
eve quickstart
eve connect --tool claude-code
eve verify --tool claude-code
```

Non-interactive / CI usage:

```bash
eve auth login --tool claude-code --api-key <eve-key>
eve install --tool claude-code --apply --yes
eve verify --tool claude-code
```

Use `eve install --all --apply --yes` only when you intentionally want to connect every detected supported tool in one pass.

Experimental hosted OAuth flow:

```bash
eve auth login --auth-mode oauth
eve auth login --tool claude-desktop --auth-mode oauth
eve connect --tool claude-desktop --auth-mode oauth
```

This opens the hosted Eve connection flow in the browser instead of storing a local API key.
It is not the primary supported installer path yet.

Custom hosted UI endpoints are blocked by default. For local/staging validation, set:

```bash
export EVE_UI_BASE_URL=http://localhost:3300
export EVE_ALLOW_CUSTOM_UI_BASE_URL=1
```

If a custom UI base URL is configured without `EVE_ALLOW_CUSTOM_UI_BASE_URL=1`, OAuth/connect flows fail instead of silently using it.

## Codex support

Codex uses the hosted MCP path through Eve-owned OAuth plus a bearer token injected at runtime.

Typical flow:

```bash
eve auth login --tool codex-cli --auth-mode oauth
eve install --tool codex-cli --auth-mode oauth --apply --yes
eve verify --tool codex-cli --auth-mode oauth
```

For one-off execution through Eve-managed auth:

```bash
eve run --tool codex-cli -- exec "Use Eve memory to search for my latest preference."
```

Native Codex MCP login is not the supported path for Eve.

## Safety model

- no silent shell profile mutation
- no undocumented hooks
- no overwrite of user-authored instruction files
- backup and manifest tracking before mutation
- explicit rollback and uninstall support
- uninstall removes Eve-managed credentials and tool entries, and warns loudly when user-modified Eve files must be reviewed manually

## Development

Run tests:

```bash
uv run --package eve-client pytest --cov=packages/client/eve_client --cov-report=term-missing -q packages/client/tests
```

Build artifacts:

```bash
uv build packages/client
```

Check release artifacts:

```bash
uvx twine check dist/eve_client-*.tar.gz dist/eve_client-*.whl
```

Build standalone release artifacts:

```bash
bash packages/client/scripts/build-eve-client-release.sh
```

Export to a standalone repository:

```bash
bash packages/client/scripts/export-eve-client-standalone.sh /path/to/eve-client
```
