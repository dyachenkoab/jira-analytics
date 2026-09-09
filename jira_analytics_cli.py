#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

from jira_analytics import Config, DEFAULT_CONFIG, DEFAULT_ENV, check_sources, collect_period_result, ensure_user_files, env_values, load_config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", nargs="*", help="check, dump, задача, ссылка или JQL")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", default="raw_dump.json", help="Output file for dump command")
    parser.add_argument("--raw", action="store_true", help="Write unsanitized raw dump")
    ns = parser.parse_args(argv)

    ensure_user_files()
    config_path = Path(ns.config)
    command = ns.command[1:] if ns.command[:1] == ["check"] else ns.command
    config = Config() if command and not config_path.exists() else load_config(config_path)
    env = env_values(DEFAULT_ENV)

    if command and command[0] == "dump":
        print("Collecting raw data...")
        collect_period_result(env, config, dump_path=ns.output, raw=ns.raw)
        label = "Raw data" if ns.raw else "Sanitized data"
        print(f"{label} dumped to {ns.output}")
        return 0

    return check_sources(env, config, command)


if __name__ == "__main__":
    sys.exit(main())
