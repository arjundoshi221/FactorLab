"""Run the UDP monitor."""

import argparse
import asyncio

from factorlab.components.monitor.service import run
from factorlab.core.logging import configure_logging


def main(argv=None):
    parser = argparse.ArgumentParser(prog="factorlab-monitor")
    parser.add_argument("--registry", default="/app/monitor-services.yaml")
    parser.add_argument("--log-dir", default="/var/log/factorlab/monitor")
    parser.add_argument("--port", type=int, default=9125)
    args = parser.parse_args(argv)
    configure_logging(component="monitor", service="monitor")
    asyncio.run(run(args.registry, args.log_dir, args.port))
    return 0
