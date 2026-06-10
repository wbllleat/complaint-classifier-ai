"""LLM classification with retry, prompt building, and response parsing."""

import json
import logging
import re
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import yaml
from openai import APIError, APITimeoutError, OpenAI, RateLimitError

from .cache import Cache, compute_hash
from .config import get_api_key
from .models import (
    AppConfig,
    ClassifyResult,
    LLMCallError,
    LLMRateLimitError,
    ParseError,
    ComplaintRow,
)

logger = logging.getLogger(__name__)


def build_prompt(row: ComplaintRow, config: AppConfig) -> tuple[str, str]:
    """Build system and user prompts for a single row."""
    template_path = config["classify"]["prompt_template_path"]
    with open(template_path, "r", encoding="utf-8") as f:
        template = yaml.safe_load(f)

    categories = config["classify"]["categories"]
    cat_lines: list[str] = []
    for primary, secondary_list in categories.items():
        cat_lines.append(f"- {primary}: {', '.join(secondary_list)}")
    categories_text = "\n".join(cat_lines)

    few_shot = config["classify"].get("few_shot_examples", [])
    few_shot_lines: list[str] = []
    for idx, example in enumerate(few_shot, start=1):
        few_shot_lines.append(f"示例 {idx}:")
        few_shot_lines.append(f"  归档意见: {example.get('text', '')}")
        few_shot_lines.append(f"  一级分类: {example.get('primary', '')}")
        few_shot_lines.append(f"  二级分类: {example.get('secondary', '')}")
        few_shot_lines.append(f"  理由: {example.get('reasoning', '')}")
    few_shot_text = "\n".join(few_shot_lines)

    system_prompt = template.get("system_prompt", "").format(
        categories_tree=categories_text,
        few_shot_examples=few_shot_text,
    )

    opinion = _preprocess_opinion(row.归档意见, config["runtime"].get("mask_phone", False))
    user_prompt = template.get("user_prompt_template", "").format(
        problem_summary=opinion.get("problem_summary", ""),
        investigation=opinion.get("investigation", ""),
        resolution=opinion.get("resolution", ""),
        is_resolved=opinion.get("is_resolved", ""),
        contact_info=opinion.get("contact_info", ""),
    )

    return system_prompt, user_prompt


def _create_client(llm_config: dict[str, Any]) -> OpenAI:
    """Create a new OpenAI client instance (thread-safe)."""
    api_key = get_api_key(llm_config)
    base_url = llm_config.get("base_url")
    if base_url:
        return OpenAI(api_key=api_key, base_url=base_url)
    return OpenAI(api_key=api_key)


def _extract_content(response: Any) -> str:
    """Safely extract content from LLM response, handling edge cases.

    Some API implementations may return None for choices, empty lists,
    or messages without content under concurrent load.
    """
    try:
        choices = getattr(response, "choices", None)
        if choices is None or len(choices) == 0:
            return "{}"
        choice = choices[0]
        message = getattr(choice, "message", None)
        if message is None:
            return "{}"
        content = getattr(message, "content", None)
        if content is None:
            return "{}"
        return str(content)
    except Exception:
        return "{}"


def call_llm(
    system_prompt: str, user_prompt: str, llm_config: dict[str, Any]
) -> tuple[str, int]:
    """Call LLM API with retry logic (thread-safe).

    Retries on: RateLimitError, APIError, APITimeoutError,
    and empty/malformed responses.
    """
    client = _create_client(llm_config)
    max_retries = 3
    backoff_base = 1.0

    for attempt in range(1, max_retries + 1):
        try:
            response = client.chat.completions.create(
                model=llm_config["model"],
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                temperature=llm_config.get("temperature", 0.0),
                max_tokens=llm_config.get("max_tokens", 300),
                response_format={"type": "json_object"},
            )
            content = _extract_content(response)
            token_usage = getattr(getattr(response, "usage", None), "total_tokens", 0) or 0

            # Retry if content is empty JSON (likely a malformed response)
            if content.strip() in ("", "{}") and attempt < max_retries:
                logger.warning(
                    "Empty response on attempt %d/%d, retrying...",
                    attempt,
                    max_retries,
                )
                time.sleep(backoff_base)
                continue

            return content, token_usage

        except RateLimitError:
            if attempt == max_retries:
                raise LLMRateLimitError(429, "Rate limit exceeded after retries")
            time.sleep(backoff_base * (2 ** (attempt - 1)))

        except (APIError, APITimeoutError) as e:
            if attempt == max_retries:
                status = getattr(e, "status_code", 0)
                body = getattr(e, "body", str(e))
                raise LLMCallError(status, str(body))
            time.sleep(backoff_base * (2 ** (attempt - 1)))

    raise LLMCallError(0, "Unexpected: all retries exhausted")


def parse_response(
    response: str,
    row_index: int,
    model: str,
    token_usage: int,
    valid_categories: dict[str, list[str]],
) -> ClassifyResult:
    """Parse LLM JSON response into ClassifyResult."""
    try:
        data = json.loads(response)
    except json.JSONDecodeError as e:
        return ClassifyResult(
            row_index=row_index,
            primary_category="其他",
            secondary_category="无法分类",
            confidence="low",
            reasoning=f"JSON解析失败: {e.msg}",
            model=model,
            token_usage=token_usage,
        )

    primary = data.get("primary_category", "其他")
    secondary = data.get("secondary_category", "无法分类")
    confidence = data.get("confidence", "low")
    reasoning = data.get("reasoning", "")

    if primary not in valid_categories:
        primary = "其他"
        secondary = "无法分类"
        confidence = "low"

    valid_secondaries = valid_categories.get(primary, [])
    if secondary not in valid_secondaries:
        secondary = valid_secondaries[0] if valid_secondaries else "无法分类"
        confidence = "low"

    if confidence not in {"high", "medium", "low"}:
        confidence = "low"

    return ClassifyResult(
        row_index=row_index,
        primary_category=primary,
        secondary_category=secondary,
        confidence=confidence,
        reasoning=str(reasoning)[:200],
        model=model,
        token_usage=token_usage,
    )


def classify_single(
    row: ComplaintRow, app_config: AppConfig, cache: Cache
) -> ClassifyResult:
    """Classify a single complaint row with caching (thread-safe)."""
    text_hash = compute_hash(row.归档意见)
    cached = cache.lookup(text_hash)
    if cached is not None:
        return cached

    system_prompt, user_prompt = build_prompt(row, app_config)
    response, token_usage = call_llm(system_prompt, user_prompt, app_config["llm"])

    result = parse_response(
        response,
        row.row_index,
        app_config["llm"]["model"],
        token_usage,
        app_config["classify"]["categories"],
    )

    cache.store(text_hash, result)
    return result


def classify_batch(
    rows: list[ComplaintRow],
    app_config: AppConfig,
    cache: Cache,
    concurrency: int,
    on_progress: Any | None = None,
    on_result: Any | None = None,
) -> list[ClassifyResult]:
    """Classify multiple rows concurrently."""
    total = len(rows)
    results: list[ClassifyResult | None] = [None] * total
    completed = 0

    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        future_to_idx: dict[Any, int] = {}
        for idx, row in enumerate(rows):
            future = executor.submit(_classify_one, row, app_config, cache)
            future_to_idx[future] = idx

        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                result = future.result()
                results[idx] = result
            except Exception:
                tb = traceback.format_exc()
                logger.error("Row %d failed:\n%s", rows[idx].row_index, tb)
                results[idx] = ClassifyResult(
                    row_index=rows[idx].row_index,
                    primary_category="其他",
                    secondary_category="无法分类",
                    confidence="low",
                    reasoning=f"执行异常: {_extract_error_type(tb)}",
                    model=app_config["llm"]["model"],
                    token_usage=0,
                )
            completed += 1
            if on_progress is not None:
                on_progress(completed, total)
            if on_result is not None and results[idx] is not None:
                on_result(results[idx])  # type: ignore[arg-type]

    return [r for r in results if r is not None]


def _extract_error_type(tb: str) -> str:
    """Extract the last exception type and message from a traceback."""
    lines = tb.strip().split("\n")
    for line in reversed(lines):
        line = line.strip()
        if line and not line.startswith("File ") and not line.startswith("^"):
            return line[:100]
    return "未知错误"


def _classify_one(
    row: ComplaintRow, app_config: AppConfig, cache: Cache
) -> ClassifyResult:
    """Wrapper for classify_single used by ThreadPoolExecutor."""
    return classify_single(row, app_config, cache)


def _preprocess_opinion(text: str, mask_phone: bool) -> dict[str, str]:
    """Extract structured fields from archive opinion text."""
    fields: dict[str, str] = {
        "problem_summary": "(未填写)",
        "investigation": "(未填写)",
        "resolution": "(未填写)",
        "is_resolved": "(未填写)",
        "contact_info": "(未填写)",
    }

    patterns = {
        "problem_summary": r"【问题概述】(.*?)(?=【|$)",
        "investigation": r"【查证情况】(.*?)(?=【|$)",
        "resolution": r"【处理方案】(.*?)(?=【|$)",
        "is_resolved": r"【业务源头问题是否解决】(.*?)(?=【|$)",
        "contact_info": r"【联系客户[^】]*】(.*?)(?=【|$)",
    }

    for key, pattern in patterns.items():
        match = re.search(pattern, text, re.DOTALL)
        if match:
            value = match.group(1).strip()
            if mask_phone:
                value = re.sub(r"1[3-9]\d{9}", "1**********", value)
            if len(value) > 300:
                value = value[:300] + "...(已截断)"
            fields[key] = value

    return fields
