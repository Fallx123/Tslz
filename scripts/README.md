# Scripts

> Entry points and utility scripts.

---

## Main Entry Points

| Script | Purpose | Usage |
|--------|---------|-------|
| `run_dashboard.py` | Launch Reflex dashboard | `python -m scripts.run_dashboard` |
| `run_daemon.py` | Launch background agent | `python -m scripts.run_daemon` |
| `run_webhook.py` | Launch TradingView webhook server | `python -m scripts.run_webhook` |

---

## Usage

```bash
# Run dashboard (development)
python -m scripts.run_dashboard

# Run daemon (background agent)
python -m scripts.run_daemon

# Run TradingView webhook server
python -m scripts.run_webhook

# Or use the Makefile
make dashboard
make daemon
```

---

## Adding Scripts

Keep scripts minimal — logic belongs in `src/hynous/`.

```python
# scripts/my_script.py

"""Brief description of what this script does."""

from hynous.core import load_config
from hynous.something import do_thing

def main():
    config = load_config()
    do_thing(config)

if __name__ == "__main__":
    main()
```
