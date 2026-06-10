"""Type definitions for complaint classifier."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TypedDict, NotRequired


# ---------- 输入模型 ----------

@dataclass(frozen=True)
class ComplaintRow:
    """单条投诉工单的所有字段"""

    row_index: int
    工单号: str
    投诉主题: str
    号码: str
    受理时间: datetime | None
    投诉内容: str
    工单状态: str
    受理渠道: str
    归档意见: str
    归档时间: datetime | None
    联系电话: str
    客户星级: str
    受理部门: str
    受理员工: str
    客户归属地: str
    业务地市: str


# ---------- 分类输出模型 ----------

class ClassifyResult(TypedDict):
    """LLM 分类返回结果"""

    row_index: int
    primary_category: str
    secondary_category: str
    confidence: str  # high | medium | low
    reasoning: str
    model: str
    token_usage: int


# ---------- 配置模型 ----------

class LLMConfig(TypedDict):
    """LLM API 连接配置"""

    provider: str
    model: str
    api_key_env: str
    base_url: NotRequired[str]
    temperature: float
    max_tokens: int


class ClassifyConfig(TypedDict):
    """分类体系配置"""

    categories: dict[str, list[str]]
    prompt_template_path: str
    few_shot_examples: list[dict[str, str]]
    output_schema_version: str


class RuntimeConfig(TypedDict):
    """运行时配置"""

    concurrency: int
    rate_limit_rpm: int
    retry_max_attempts: int
    retry_backoff_base: float
    checkpoint_interval: int
    archive_column_name: str
    mask_phone: bool


class AppConfig(TypedDict):
    """完整应用配置"""

    llm: LLMConfig
    classify: ClassifyConfig
    runtime: RuntimeConfig


# ---------- 缓存模型 ----------

class CacheEntry(TypedDict):
    """缓存记录"""

    text_hash: str
    result: ClassifyResult


# ---------- 检查点模型 ----------

class CheckpointData(TypedDict):
    """断点续跑检查点"""

    processed_count: int
    completed_results: list[ClassifyResult]
    last_row_index: int
    timestamp: str


# ---------- 汇总模型 ----------

class SummaryData(TypedDict):
    """汇总数据"""

    category_stats: list[dict[str, str | int | float]]
    cross_tabs: list[dict[str, str | dict[str, dict[str, int]]]]


class CategoryStat(TypedDict):
    """单分类统计"""

    primary_category: str
    secondary_category: str
    count: int
    percentage: float


class CrossTabResult(TypedDict):
    """交叉统计结果"""

    row_dim: str
    col_dim: str
    matrix: dict[str, dict[str, int]]
    row_totals: dict[str, int]
    col_totals: dict[str, int]


# ---------- 自定义异常 ----------

class ClassificationError(Exception):
    """分类过程基础异常"""


class LLMCallError(ClassificationError):
    """LLM API 调用失败"""

    def __init__(self, status_code: int, response_body: str) -> None:
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(f"LLM call failed [{status_code}]: {response_body}")


class LLMRateLimitError(LLMCallError):
    """LLM API 速率限制（429）"""


class ParseError(ClassificationError):
    """LLM 响应解析失败"""


class ConfigError(Exception):
    """配置错误"""


class ColumnNotFoundError(ConfigError):
    """目标列未在 Excel 表头中找到"""
