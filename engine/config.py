"""Validated configuration layer.

SafetyConfig / ExecutionConfig / BrowserConfig / LoggingConfig, all from env
with strict validation. Invalid values raise (never silent unsafe fallback).
Production mode reduces verbosity ONLY — safety policy identical (asserted).

Environment variables (ORVEX_ prefix preferred; SFMCP_ accepted for backwards
compatibility — ORVEX_ takes precedence when both are set):

  ORVEX_MODE              dev|prod  (default: prod)
  ORVEX_LOG_LEVEL         DEBUG|INFO|WARNING|ERROR|CRITICAL  (default: INFO)
  ORVEX_MAX_STEPS         1-200  (default: 20)
  ORVEX_MAX_DURATION_S    1-600  (default: 30)
  ORVEX_MAX_LAUNCHES      0-50   (default: 5)
  ORVEX_MAX_WINDOWS       0-50   (default: 5)
  ORVEX_MAX_CLOSES        0-100  (default: 10)
  ORVEX_MAX_INPUTS        0-200  (default: 20)
  ORVEX_MAX_TRACKED       1-200  (default: 20)
  ORVEX_MAX_SESSIONS      1-16   (default: 4)
  ORVEX_IDLE_TTL_S        10-3600  (default: 180)
  ORVEX_MAX_SESSION_AGE_S 60-7200  (default: 900)
  ORVEX_MAX_NAV_S         5-120  (default: 20)
  ORVEX_APPROVAL_WAIT_S   1-600  (default: 120)
  ORVEX_USER_INTERVENTION PAUSE|TAKE_CONTROL|BLOCK|ALLOW  (default: PAUSE)
  ORVEX_ENABLE_SYSTEM     0|1  (default: 0)
  ORVEX_BLOCKLIST         semicolon-separated regex patterns to protect
  ORVEX_TEST_PREFIX       comma-separated test title prefixes
"""
from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(ValueError):
    pass


def _env(name: str, default: str = "") -> str:
    """Read ORVEX_<name> preferring over SFMCP_<name> (backwards compat)."""
    return (os.environ.get(f"ORVEX_{name}")
            or os.environ.get(f"SFMCP_{name}")
            or default)


def _int(name: str, default: int, lo: int, hi: int) -> int:
    raw = _env(name, str(default))
    try:
        v = int(raw)
    except ValueError:
        raise ConfigError(f"ORVEX_{name} must be int, got {raw!r}")
    if not (lo <= v <= hi):
        raise ConfigError(f"ORVEX_{name}={v} out of range [{lo},{hi}]")
    return v


def _float(name: str, default: float, lo: float, hi: float) -> float:
    raw = _env(name, str(default))
    try:
        v = float(raw)
    except ValueError:
        raise ConfigError(f"ORVEX_{name} must be float, got {raw!r}")
    if not (lo <= v <= hi):
        raise ConfigError(f"ORVEX_{name}={v} out of range [{lo},{hi}]")
    return v


@dataclass(frozen=True)
class SafetyConfig:
    max_steps: int = 20
    max_duration_s: float = 30.0
    max_process_launches: int = 5
    max_window_creations: int = 5
    max_window_closes: int = 10
    max_system_input_events: int = 20
    max_tracked_windows: int = 20


@dataclass(frozen=True)
class ExecutionConfig:
    mode: str = "prod"  # dev | prod (verbosity only)
    mcp_log_level: str = "INFO"


@dataclass(frozen=True)
class BrowserConfig:
    max_sessions: int = 4
    idle_ttl_s: float = 180.0
    max_session_age_s: float = 900.0
    max_navigation_s: float = 20.0


@dataclass(frozen=True)
class LoggingConfig:
    audit_jsonl: str = ""   # resolved at runtime in SafetyPolicy
    redact_text: bool = True


def load() -> tuple[SafetyConfig, ExecutionConfig, BrowserConfig, LoggingConfig]:
    mode = _env("MODE", "prod")
    if mode not in ("dev", "prod"):
        raise ConfigError(f"ORVEX_MODE must be dev|prod, got {mode!r}")
    log_level = _env("LOG_LEVEL", "INFO")
    if log_level not in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise ConfigError(f"ORVEX_LOG_LEVEL invalid: {log_level!r}")
    safety = SafetyConfig(
        max_steps=_int("MAX_STEPS", 20, 1, 200),
        max_duration_s=_float("MAX_DURATION_S", 30.0, 1.0, 600.0),
        max_process_launches=_int("MAX_LAUNCHES", 5, 0, 50),
        max_window_creations=_int("MAX_WINDOWS", 5, 0, 50),
        max_window_closes=_int("MAX_CLOSES", 10, 0, 100),
        max_system_input_events=_int("MAX_INPUTS", 20, 0, 200),
        max_tracked_windows=_int("MAX_TRACKED", 20, 1, 200),
    )
    browser = BrowserConfig(
        max_sessions=_int("MAX_SESSIONS", 4, 1, 16),
        idle_ttl_s=_float("IDLE_TTL_S", 180.0, 10.0, 3600.0),
        max_session_age_s=_float("MAX_SESSION_AGE_S", 900.0, 60.0, 7200.0),
        max_navigation_s=_float("MAX_NAV_S", 20.0, 5.0, 120.0),
    )
    return (safety, ExecutionConfig(mode=mode, mcp_log_level=log_level), browser,
            LoggingConfig())


def apply_to_budget(safety: SafetyConfig):
    from .safety import Budget

    return Budget(
        max_steps=safety.max_steps, max_duration_s=safety.max_duration_s,
        max_process_launches=safety.max_process_launches,
        max_window_creations=safety.max_window_creations,
        max_window_closes=safety.max_window_closes,
        max_system_input_events=safety.max_system_input_events,
        max_new_windows_per_task=safety.max_window_creations,
        max_total_tracked_windows=safety.max_tracked_windows,
        max_same_app_windows=safety.max_window_creations)
