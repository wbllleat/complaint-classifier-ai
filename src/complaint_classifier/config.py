"""Configuration loading and validation."""

import os
from pathlib import Path

import yaml

from .models import AppConfig, ConfigError


VALID_PROVIDERS = {"openai", "azure", "ollama", "deepseek", "mass", "qwen"}

# Prefixes that indicate a direct API key (not an env var name)
DIRECT_KEY_PREFIXES = ("sk-", "fk-", "sf-")


def load_config(config_path: str) -> AppConfig:
    """Load and validate config from YAML file."""
    path = Path(config_path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ConfigError(f"Config file is empty: {config_path}")

    _validate_config(raw)
    return raw


def _validate_config(config: AppConfig) -> None:
    """Validate all required fields and their values."""
    llm = config.get("llm")
    if llm is None:
        raise ConfigError("Missing 'llm' section in config")
    if llm.get("provider") not in VALID_PROVIDERS:
        raise ConfigError(f"llm.provider must be one of {VALID_PROVIDERS}")
    if not (0.0 <= llm.get("temperature", 0) <= 1.0):
        raise ConfigError("llm.temperature must be in [0, 1]")
    if llm.get("max_tokens", 0) < 100:
        raise ConfigError("llm.max_tokens must be >= 100")
    api_key_env = llm.get("api_key_env", "")
    if not isinstance(api_key_env, str) or not api_key_env:
        raise ConfigError("llm.api_key_env is required")

    classify = config.get("classify")
    if classify is None:
        raise ConfigError("Missing 'classify' section in config")
    categories = classify.get("categories")
    if not isinstance(categories, dict) or len(categories) == 0:
        raise ConfigError("classify.categories must be a non-empty dict")
    for cat, subcats in categories.items():
        if not isinstance(subcats, list) or len(subcats) == 0:
            raise ConfigError(f"classify.categories.{cat} must have at least one sub-category")
    if not Path(classify.get("prompt_template_path", "")).exists():
        raise ConfigError(f"Prompt template not found: {classify.get('prompt_template_path')}")

    runtime = config.get("runtime")
    if runtime is None:
        raise ConfigError("Missing 'runtime' section in config")
    if not (1 <= runtime.get("concurrency", 1) <= 10):
        raise ConfigError("runtime.concurrency must be in [1, 10]")
    if runtime.get("rate_limit_rpm", 0) <= 0:
        raise ConfigError("runtime.rate_limit_rpm must be > 0")
    if runtime.get("checkpoint_interval", 0) <= 0:
        raise ConfigError("runtime.checkpoint_interval must be > 0")


def get_api_key(llm_config: dict) -> str:
    """Read API key from environment variable or use directly.

    If api_key_env looks like a direct key (starts with sk-/fk-/sf-
    or doesn't resolve from env), use it directly.
    Otherwise treat as environment variable name.

    Args:
        llm_config: LLM configuration dict with api_key_env field.

    Returns:
        API key string.

    Raises:
        ConfigError: If no key found.
    """
    value = llm_config["api_key_env"]

    # Direct key: matches known prefix patterns
    if value.startswith(DIRECT_KEY_PREFIXES):
        return value

    # Try environment variable first
    env_key = os.environ.get(value, "")
    if env_key:
        return env_key

    # If value has environment-variable-like name with underscores
    # but env not set, and value is long enough to be a key (> 20 chars),
    # treat it as a direct key
    if len(value) > 20:
        return value

    raise ConfigError(
        f"Environment variable '{value}' is not set and value doesn't look like a direct API key"
    )


def get_categories_flat(config: AppConfig) -> list[str]:
    """Get flat list of all valid secondary category labels."""
    result: list[str] = []
    for subcats in config["classify"]["categories"].values():
        result.extend(subcats)
    return result


def get_primary_categories(config: AppConfig) -> list[str]:
    """Get list of primary category labels."""
    return list(config["classify"]["categories"].keys())
