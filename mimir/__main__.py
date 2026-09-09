"""Entry point: ``python -m mimir``."""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys

from . import __version__
from .gpu import GpuMonitor
from .history import HistoryStore
from .netcapture import create_attributor
from .platform import LOG_DIR, SETTINGS_FILE, is_admin
from .sampler import Sampler
from .settings import Settings


def setup_logging(level: str) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(fmt)
    root.addHandler(stream)
    try:
        fh = logging.handlers.RotatingFileHandler(LOG_DIR / "mimir.log", maxBytes=1_000_000, backupCount=3,
                                                  encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="mimir", description="Per-process resource monitor with history and peaks.")
    parser.add_argument("--interval", type=float, help="sampling interval in seconds (overrides settings)")
    parser.add_argument("--no-network", action="store_true", help="never start per-process network capture")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--shot", type=float, metavar="SECONDS",
                        help="debug: save a screenshot after SECONDS and exit")
    parser.add_argument("--shot-file", default="mimir_shot.png")
    parser.add_argument("--version", action="version", version=f"mimir {__version__}")
    args = parser.parse_args(argv)

    setup_logging(args.log_level)
    log = logging.getLogger("mimir")

    settings = Settings.load(SETTINGS_FILE)
    if args.interval:
        settings.sample_interval = args.interval
        settings.validate()
    if args.no_network:
        settings.network_capture = False

    store = HistoryStore(settings.max_snapshots)
    net = create_attributor()
    gpu = GpuMonitor()
    sampler = Sampler(settings, store, net, gpu)
    log.info("mimir %s starting (admin=%s, network=%s, gpu=%s)", __version__, is_admin(),
             "available" if net.available else net.reason, gpu.name or gpu.reason)

    from .ui.app import App     # imported late so --version/--help work without a display
    app = App(settings, store, sampler, net, gpu)
    relaunched = app.run(shot_after=args.shot, shot_file=args.shot_file)
    if relaunched:
        log.info("elevated instance requested; exiting this one")
    return 0


if __name__ == "__main__":
    sys.exit(main())
