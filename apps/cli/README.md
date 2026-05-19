# magpie

The `magpie` CLI, talks to an OpenMagpie server.

## Install (editable, for dev)

```bash
uv sync                # creates .venv with deps from uv.lock
uv run magpie --help
```

To put `magpie` on your global `PATH`:

```bash
uv tool install --editable .
```

## Auth

```bash
magpie auth login            # browser device flow
magpie auth status           # show signed-in identity
magpie auth logout           # clear local config
```

Config lives at `~/.magpie/config.json` (mode 0600).
