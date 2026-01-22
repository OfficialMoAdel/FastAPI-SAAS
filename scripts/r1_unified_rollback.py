#!/usr/bin/env python
"""
R1-UNIFIED Rollback Script

Provides quick rollback capabilities for R1-UNIFIED optimizations.

Usage:
    uv run python scripts/r1_unified_rollback.py --feature event-bus
    uv run python scripts/r1_unified_rollback.py --all
    uv run python scripts/r1_unified_rollback.py --status
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("r1_unified_rollback")


class Feature(Enum):
    """R1-UNIFIED feature flags."""

    EVENT_BUS = "EVENT_BUS_USE_LUA_IDEMPOTENCY"
    SESSION_EVICTION = "SESSION_USE_PIPELINE_EVICTION"
    XFETCH = "XFETCH_USE_LUA_SCRIPT"
    RBAC_CACHE = "RBAC_USE_REDIS_CACHE"
    DLQ_BOUNDED = "DLQ_USE_BOUNDED_SIZE"
    PASSKEY_COLUMN = "PASSKEY_USE_COLUMN_SELECT"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER_USE_DOUBLE_CHECK"


FEATURE_ALIASES: dict[str, Feature] = {
    "event-bus": Feature.EVENT_BUS,
    "event_bus": Feature.EVENT_BUS,
    "idempotency": Feature.EVENT_BUS,
    "session": Feature.SESSION_EVICTION,
    "session-eviction": Feature.SESSION_EVICTION,
    "eviction": Feature.SESSION_EVICTION,
    "xfetch": Feature.XFETCH,
    "lua": Feature.XFETCH,
    "rbac": Feature.RBAC_CACHE,
    "rbac-cache": Feature.RBAC_CACHE,
    "cache": Feature.RBAC_CACHE,
    "dlq": Feature.DLQ_BOUNDED,
    "dlq-bounded": Feature.DLQ_BOUNDED,
    "passkey": Feature.PASSKEY_COLUMN,
    "passkey-column": Feature.PASSKEY_COLUMN,
    "circuit": Feature.CIRCUIT_BREAKER,
    "circuit-breaker": Feature.CIRCUIT_BREAKER,
    "toctou": Feature.CIRCUIT_BREAKER,
}


@dataclass
class RollbackAction:
    """Represents a rollback action."""

    feature: Feature
    previous_value: str | None
    new_value: str
    timestamp: datetime


def get_feature_status() -> dict[Feature, bool]:
    """Get current status of all feature flags."""
    status = {}
    for feature in Feature:
        env_value = os.environ.get(feature.value, "").lower()
        status[feature] = env_value in ("true", "1", "yes")
    return status


def print_status() -> None:
    """Print current feature flag status."""
    print("\n" + "=" * 60)
    print("R1-UNIFIED FEATURE FLAG STATUS")
    print("=" * 60)

    status = get_feature_status()

    for feature in Feature:
        enabled = status[feature]
        indicator = "[ON] " if enabled else "[OFF]"
        color = "\033[92m" if enabled else "\033[91m"
        reset = "\033[0m"
        print(f"{color}{indicator}{reset} {feature.name}: {feature.value}")

    print("=" * 60 + "\n")


def generate_env_file(features_to_disable: list[Feature]) -> str:
    """Generate .env content for rollback."""
    lines = [
        "# R1-UNIFIED Rollback Configuration",
        f"# Generated: {datetime.now(UTC).isoformat()}",
        "",
    ]

    status = get_feature_status()

    for feature in Feature:
        if feature in features_to_disable:
            lines.append(f"{feature.value}=false")
        else:
            # Keep current value
            current = "true" if status[feature] else "false"
            lines.append(f"{feature.value}={current}")

    return "\n".join(lines)


def rollback_feature(feature: Feature, dry_run: bool = False) -> RollbackAction:
    """Rollback a specific feature."""
    previous = os.environ.get(feature.value)
    new_value = "false"

    if not dry_run:
        os.environ[feature.value] = new_value
        logger.info(f"Disabled {feature.name}: {feature.value}=false")
    else:
        logger.info(f"[DRY RUN] Would disable {feature.name}: {feature.value}=false")

    return RollbackAction(
        feature=feature,
        previous_value=previous,
        new_value=new_value,
        timestamp=datetime.now(UTC),
    )


def rollback_all(dry_run: bool = False) -> list[RollbackAction]:
    """Rollback all features."""
    actions = []
    for feature in Feature:
        action = rollback_feature(feature, dry_run)
        actions.append(action)
    return actions


def parse_feature(feature_str: str) -> Feature:
    """Parse feature string to Feature enum."""
    feature_str = feature_str.lower().strip()

    if feature_str in FEATURE_ALIASES:
        return FEATURE_ALIASES[feature_str]

    # Try matching by enum name
    for feature in Feature:
        if feature.name.lower() == feature_str.replace("-", "_"):
            return feature

    raise ValueError(f"Unknown feature: {feature_str}")


def save_rollback_config(actions: list[RollbackAction], output_dir: Path) -> Path:
    """Save rollback configuration for later restore."""
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    config_path = output_dir / f"rollback_{timestamp}.env"

    lines = [
        "# R1-UNIFIED Rollback Configuration",
        f"# Generated: {datetime.now(UTC).isoformat()}",
        "# To restore, source this file or copy to .env",
        "",
    ]

    for action in actions:
        lines.append(f"# {action.feature.name}: {action.previous_value} -> {action.new_value}")
        lines.append(f"{action.feature.value}={action.new_value}")
        lines.append("")

    with open(config_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    return config_path


def main() -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="R1-UNIFIED Rollback Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  Rollback specific feature:
    uv run python scripts/r1_unified_rollback.py --feature event-bus
    uv run python scripts/r1_unified_rollback.py --feature rbac-cache

  Rollback all features:
    uv run python scripts/r1_unified_rollback.py --all

  Check current status:
    uv run python scripts/r1_unified_rollback.py --status

  Dry run (no changes):
    uv run python scripts/r1_unified_rollback.py --all --dry-run
        """,
    )

    parser.add_argument(
        "--feature",
        "-f",
        type=str,
        action="append",
        help="Feature to rollback (can be specified multiple times)",
    )
    parser.add_argument(
        "--all",
        "-a",
        action="store_true",
        help="Rollback all features",
    )
    parser.add_argument(
        "--status",
        "-s",
        action="store_true",
        help="Show current feature flag status",
    )
    parser.add_argument(
        "--dry-run",
        "-n",
        action="store_true",
        help="Show what would be done without making changes",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="rollback_configs",
        help="Directory to save rollback configuration",
    )
    parser.add_argument(
        "--generate-env",
        action="store_true",
        help="Generate .env file content for rollback",
    )

    args = parser.parse_args()

    # Show status
    if args.status:
        print_status()
        return 0

    # Generate env file
    if args.generate_env:
        features = []
        if args.all:
            features = list(Feature)
        elif args.feature:
            features = [parse_feature(f) for f in args.feature]

        env_content = generate_env_file(features)
        print(env_content)
        return 0

    # Rollback operations
    if not args.all and not args.feature:
        print("Error: Specify --feature, --all, or --status")
        parser.print_help()
        return 1

    actions = []

    if args.all:
        logger.info("Rolling back ALL R1-UNIFIED features")
        actions = rollback_all(args.dry_run)
    elif args.feature:
        for feature_str in args.feature:
            try:
                feature = parse_feature(feature_str)
                action = rollback_feature(feature, args.dry_run)
                actions.append(action)
            except ValueError as e:
                logger.error(str(e))
                return 1

    # Save configuration
    if actions and not args.dry_run:
        output_dir = Path(args.output_dir)
        config_path = save_rollback_config(actions, output_dir)
        logger.info(f"Rollback configuration saved to {config_path}")

    # Print summary
    print("\n" + "=" * 60)
    print("ROLLBACK SUMMARY")
    print("=" * 60)

    for action in actions:
        status = "[DRY RUN]" if args.dry_run else "[APPLIED]"
        print(f"{status} {action.feature.name}: {action.previous_value} -> {action.new_value}")

    print("=" * 60)

    if not args.dry_run:
        print("\nIMPORTANT: Restart the application for changes to take effect:")
        print("  systemctl restart fastapi-saas")
        print("  # or")
        print("  docker-compose restart api")

    return 0


if __name__ == "__main__":
    sys.exit(main())
