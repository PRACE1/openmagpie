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

## Quickstart

```bash
magpie auth login            # browser device flow
magpie quickstart            # two questions, one working listener
```

`magpie quickstart` walks you from "signed in" to "watching real posts and getting them scored" in two prompts: which subreddits to watch and what to be notified about. Pick a backfill window at the prompt (default 24h) and the first `make dev-tick` will score real posts against your criteria immediately, no waiting for the scheduler.

## Commands

| Command | What it does |
|---|---|
| `magpie auth login` / `logout` / `status` | Device-flow sign-in; identity check |
| `magpie quickstart` | Interactive setup (see above) |
| `magpie feed create` / `list` / `get` / `edit` / `delete` | Curated source streams (the set a listener subscribes to) |
| `magpie listener create` / `list` / `get` / `edit` / `delete` | Listeners over feeds |
| `magpie listener rewind <id>` | Reset the judge cursor; re-judge items in the retention window (useful after refining instructions or lowering the threshold) |
| `magpie listener payload-sample <id>` | Preview what every configured notifier would emit for the next batch. Same code path delivery takes, just without the ship step |

Config lives at `~/.magpie/config.json`.
