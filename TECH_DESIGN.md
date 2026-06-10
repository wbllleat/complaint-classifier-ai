# 投诉归档意见AI分类工具 —— 技术设计文档（TECH_DESIGN.md）

> 版本：v1.0 | 日期：2026-06-09 | 对应 PRD v1.0 MVP 阶段

---

## 1. 架构总览

### 1.1 系统架构图

`
┌──────────────────────────────────────────────────────────┐
│                   CLI 入口 (__main__.py)                  │
│         commands: run | test | check-config               │
└───────────────────────┬──────────────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────────────┐
│                  Pipeline 编排器 (run 命令)               │
│   read_excel -> classify_batch -> write_results          │
│          -> generate_summary -> write_summary            │
└───┬────────────┬──────────────┬──────────────┬───────────┘
    │            │              │              │
┌───▼────┐ ┌─────▼─────┐ ┌──────▼──────┐ ┌─────▼──────┐
│ reader │ │ classifier│ │   writer    │ │  summary   │
│  .py   │ │    .py    │ │    .py      │ │   .py      │
│        │ │           │ │             │ │            │
│openpyxl│ │ openai SDK│ │  openpyxl   │ │ collections│
│read    │ │ prompt    │ │  write      │ │ pivot      │
│detect  │ │ JSON mode │ │  new cols   │ │ stats      │
└────────┘ └─────┬─────┘ └─────────────┘ └────────────┘
                 │
           ┌─────▼─────┐     ┌──────────────┐
           │  cache.py │     │  progress.py │
           │  SQLite   │     │  tracker     │
           │  hash->   │     │  checkpoint  │
           │  result   │     └──────────────┘
           └───────────┘

┌──────────────────────────────────────────────────────────┐
│                     types.py (共享层)                     │
│  ComplaintRow | ClassifyResult | AppConfig | CacheEntry  │
└──────────────────────────────────────────────────────────┘
`

### 1.2 设计原则

| 原则 | 实现方式 |
|------|----------|
| **函数式优先** | 所有核心逻辑为纯函数，仅 cache.py 有状态封装 |
| **严格类型** | 所有函数参数/返回值声明类型，通过 mypy strict 检查 |
| **显式错误** | 自定义异常类，禁止静默吞错 |
| **无默认参数** | 所有函数参数必须显式传入 |
| **单轮独立调用** | 每条归档意见独立构造完整 Prompt 发送给 LLM，无对话历史依赖 |

### 1.3 技术选型

| 依赖 | 版本 | 用途 |
|------|------|------|
| Python | >=3.11 | 运行时 |
| openpyxl | >=3.1 | Excel 读写 |
| DeepSeek API | >=1.0 | LLM API 调用 |
| PyYAML | >=6.0 | 配置文件解析 |
| tqdm | >=4.66 | 终端进度条 |
| diskcache | >=5.6 | 轻量缓存（MVP 备选） |

---
## 2. 数据模型与类型定义

### 2.1 核心类型一览

所有类型定义集中于 src/complaint_classifier/types.py，遵循无默认值、严格类型原则。

#### 输入模型：ComplaintRow

dataclass(frozen=True)，表示单条投诉工单的所有字段。

| 字段 | 类型 | 说明 |
|------|------|------|
| row_index | int | 原始行号（1-based，不含表头） |
| 工单号 | str | 工单唯一标识 |
| 投诉主题 | str | 投诉分类路径 |
| 号码 | str | 客户手机号 |
| 受理时间 | datetime \| None | 工单受理时间 |
| 投诉内容 | str | 客户原始投诉描述 |
| 工单状态 | str | 当前工单状态 |
| 受理渠道 | str | 如 10086热线、中国移动APP |
| 归档意见 | str | **核心分类字段** |
| 归档时间 | datetime \| None | 归档时间 |
| 联系电话 | str | 客户联系电话 |
| 客户星级 | str | 如一星、全球通（白金） |
| 受理部门 | str | 受理部门编码/名称 |
| 受理员工 | str | 受理员工姓名/工号 |
| 客户归属地 | str | 客户归属城市 |
| 业务地市 | str | 业务发生城市 |

#### 输出模型：ClassifyResult

TypedDict，表示单条分类结果。

| 字段 | 类型 | 说明 |
|------|------|------|
| row_index | int | 对应 ComplaintRow.row_index |
| primary_category | str | 一级分类标签 |
| secondary_category | str | 二级分类标签 |
| confidence | str | high \| medium \| low |
| reasoning | str | LLM 给出的分类理由 |
| model | str | 使用的模型名 |
| token_usage | int | 本次调用消耗 token 数 |

#### 配置模型层级

| 类型 | 说明 |
|------|------|
| LLMConfig | API 连接配置（provider, model, api_key_env, temperature, max_tokens） |
| ClassifyConfig | 分类体系（categories 树, prompt 模板路径, few_shot_examples） |
| RuntimeConfig | 运行时参数（concurrency, rate_limit_rpm, retry, checkpoint_interval） |
| AppConfig | 顶层配置，聚合以上三者 |

#### 缓存模型

| 类型 | 字段 | 说明 |
|------|------|------|
| CacheEntry | text_hash: str, result: ClassifyResult | 缓存记录，key 为 SHA256(归档意见文本) |

#### 检查点模型

| 类型 | 字段 | 说明 |
|------|------|------|
| CheckpointData | processed_count: int, completed_results: list[ClassifyResult], last_row_index: int, timestamp: str | 断点续跑数据 |

#### 汇总模型

| 类型 | 字段 | 说明 |
|------|------|------|
| CategoryStat | primary_category, secondary_category, count: int, percentage: float | 单一分类统计 |
| CrossTabResult | row_dim, col_dim, matrix: dict[str, dict[str, int]], row_totals, col_totals | 交叉统计结果 |

#### 自定义异常层级

| 异常类 | 父类 | 触发条件 |
|--------|------|----------|
| ClassificationError | Exception | 分类过程基础异常 |
| LLMCallError | ClassificationError | LLM API 调用失败（含 status_code, response_body） |
| LLMRateLimitError | LLMCallError | 429 速率限制 |
| ParseError | ClassificationError | LLM 响应 JSON 解析失败 |
| ConfigError | Exception | 配置错误 |
| ColumnNotFoundError | ConfigError | 目标列未在 Excel 表头中找到 |

### 2.2 数据流

    Excel 文件 (.xlsx)
        |
        v
    reader.read_excel(path, sheet_name)
        |
        +--> tuple[list[str], list[ComplaintRow]]
        |    headers = [工单号, 投诉主题, ..., 业务地市]
        |    rows = [ComplaintRow(...), ...]  (len = 18992)
                |
                v
    classifier.classify_batch(rows, config, cache, progress)
        |
        |  for each row:
        |    hash = SHA256(row.归档意见)
        |    cached = cache.lookup(hash)
        |    if cached:
        |        result = cached
        |    else:
        |        prompt = build_prompt(row, config.classify)
        |        response = call_llm(prompt, config.llm)
        |        result = parse_response(response)
        |        cache.store(hash, result)
        |    progress.update()
        |
        +--> list[ClassifyResult]  (len = 18992)
                |
                v
    writer.write_results(input_path, output_path, rows, results)
        |
        +--> output_classified.xlsx
             原15列 + 一级分类 + 二级分类 + 置信度 + 分类理由
                |
                v
    summary.generate_summary(rows, results)
        |
        +--> list[CategoryStat]
        +--> output_summary.xlsx （分类统计 + 交叉分析）
---

## 3. 模块详细设计

### 3.1 config.py —— 配置加载与校验

**职责**：加载 config.yaml，校验完整性，解包为 AppConfig。

| 函数 | 签名 | 说明 |
|------|------|------|
| load_config | (path: str) -> AppConfig | 从 YAML 文件加载并校验 |
| validate_config | (config: AppConfig) -> None | 校验必填字段、类型、分类树完整性 |
| get_api_key | (config: LLMConfig) -> str | 从环境变量读取 API Key |

**校验规则**：llm.provider 必须在 [openai, azure, ollama, deepseek] 中；temperature 在 [0.0, 1.0]；max_tokens >= 100；categories 不能为空；prompt_template_path 文件存在；concurrency 在 1-10；rate_limit_rpm > 0

### 3.2 reader.py —— Excel 读取与解析

**职责**：读取 .xlsx 文件，检测归档意见列，解析为 ComplaintRow 列表。

| 函数 | 签名 | 说明 |
|------|------|------|
| read_excel | (path: str, sheet_name: str or None) -> tuple[list[str], list[ComplaintRow]] | 读取工作表，返回表头和行数据 |
| detect_archive_column | (headers: list[str], target_name: str) -> int | 匹配归档意见列的索引 |
| get_sheet_names | (path: str) -> list[str] | 获取所有工作表名称 |

**实现要点**：1) 使用 openpyxl.load_workbook(data_only=True) 避免公式残留；2) 首行=表头，第二行起=数据；3) detect_archive_column 模糊匹配（精确 -> 包含）；4) None 转空字符串；5) openpyxl 自动解析 datetime；6) row_index 从 2 开始

### 3.3 classifier.py —— LLM 分类核心

**职责**：构造 Prompt、调用 LLM API、解析结果。

| 函数 | 签名 | 说明 |
|------|------|------|
| build_prompt | (row, config) -> tuple[str, str] | 返回 (system_prompt, user_prompt) |
| call_llm | (system_prompt, user_prompt, llm_config) -> str | 调用 LLM，返回 JSON 字符串 |
| parse_response | (response, row_index, model, token_usage) -> ClassifyResult | 解析 JSON |
| classify_single | (row, app_config, cache) -> ClassifyResult | 单行分类（含缓存逻辑） |
| classify_batch | (rows, app_config, progress) -> list[ClassifyResult] | 批量分类 |

**API 调用策略**：
- 使用 DeepSeek SDK https://api.deepseek.com
- response_format=json_object 强制 JSON 输出
- 每次调用独立、自包含，不携带对话历史
- temperature=0.0 确保分类一致性

**错误处理流程**：429 -> 等待 Retry-After -> 重试(最多3次) -> raise LLMRateLimitError；5xx -> 指数退避(1s,2s,4s) -> 重试(最多3次) -> raise LLMCallError；4xx(非429) -> 不重试 -> raise LLMCallError

### 3.4 cache.py —— 结果缓存

**职责**：基于归档意见文本哈希的去重缓存，避免重复 API 调用。

| 类/函数 | 说明 |
|---------|------|
| Cache(db_path) | 初始化 SQLite 连接 |
| Cache.lookup(text_hash) -> ClassifyResult or None | 查询缓存 |
| Cache.store(text_hash, result) | 写入缓存 |
| compute_hash(text) -> str | SHA256 哈希 |

**SQLite 表结构**：cache(text_hash TEXT PK, primary_category, secondary_category, confidence, reasoning, model, token_usage, created_at)

**设计要点**：哈希基于完整归档意见文本(去空白)；DB 存储在 output/cache/ 目录；WAL 模式提升并发读；ClassifyResult 序列化为 JSON 存入

### 3.5 writer.py —— 结果回写

**职责**：在原 Excel 基础上新增分类列，保留原有格式和数据。

| 函数 | 说明 |
|------|------|
| write_results(input_path, output_path, rows, results) -> str | 回写并返回输出路径 |

**实现要点**：load_workbook 加载原文件；表头末尾追加 一级分类/二级分类/置信度/分类理由 列；按 row_index 对齐写入；保持原格式/样式；低置信度行黄色 PatternFill；输入输出不同文件

### 3.6 summary.py —— 汇总统计

| 函数 | 说明 |
|------|------|
| generate_summary(rows, results) -> SummaryData | 生成完整汇总 |
| category_stats(results) -> list[CategoryStat] | 按分类统计数量和占比 |
| cross_tabulate(rows, results, row_dim, col_dim) -> CrossTabResult | 交叉统计 |
| write_summary(summary, output_path) -> str | 写入汇总 Excel |

**输出 Sheet**：分类汇总(一级二级计数/占比)、渠道×分类、星级×分类、城市×分类 四个 Sheet

### 3.7 progress.py —— 进度跟踪

| 类/函数 | 说明 |
|---------|------|
| ProgressTracker(total, checkpoint_interval, checkpoint_dir) | 基于 tqdm 的进度跟踪 |
| ProgressTracker.update(count) | 更新进度 |
| save_checkpoint(results, last_index) | 每 N 条自动保存 JSON 检查点 |
| load_checkpoint(checkpoint_dir) -> CheckpointData or None | 加载最新检查点 |

**设计要点**：基于 tqdm；默认每500条保存检查点；检查点文件 checkpoint_{timestamp}.json；启动时自动检测并提示续跑

---

## 4. CLI 接口设计

### 4.1 命令结构

    python -m complaint_classifier run input.xlsx

| 命令 | 说明 | MVP |
|------|------|-----|
| run input.xlsx | 执行完整分类流程 | P0 |
| test input.xlsx --sample 10 | 小样本测试验证 Prompt | P1 |
| check-config | 校验 config.yaml | P1 |
| resume | 从检查点续跑 | P1 |
| cache-stats | 查看缓存统计 | P2 |

### 4.2 run 命令参数

| 参数 | 类型 | 必需 | 说明 |
|------|------|------|------|
| input | str (位置参数) | 是 | 输入 Excel 文件路径 |
| --config | str | 否 | 配置文件路径，默认 ./config.yaml |
| --output | str | 否 | 输出目录，默认 ./output/ |
| --sheet | str | 否 | 工作表名，默认第一个 Sheet |
| --no-cache | flag | 否 | 禁用缓存 |
| --no-resume | flag | 否 | 禁用断点续跑 |
| --dry-run | flag | 否 | 仅预览不执行 |

### 4.3 输出约定

| 输出 | 路径 |
|------|------|
| 分类结果 | output/{filename}_classified.xlsx |
| 汇总统计 | output/{filename}_summary.xlsx |
| 日志 | logs/run_{timestamp}.log |
| 检查点 | output/checkpoints/ |
| 缓存 | output/cache/cache.db |

---

## 5. Prompt 工程设计

### 5.1 设计目标

解决 RESEARCH.md 中提出的核心问题：AI 上下文失忆（分类标准漂移）。方案：每条分类请求完全独立、自包含。

### 5.2 Prompt 模板结构

模板文件存储于 prompts/default_classify.yaml，分为 system_prompt 和 user_prompt_template 两部分：

**system_prompt 包含**：
- 角色设定：电信投诉归档意见分类专家
- 分类体系：{categories_tree} 占位符，运行时注入 YAML 中的分类树
- 输出格式约束：严格的 JSON Schema，要求输出 primary_category、secondary_category、confidence、reasoning 四个字段
- Few-Shot 示例：{few_shot_examples} 占位符，运行时注入 3-5 个标准分类样本

**user_prompt_template 包含**：
- 从归档意见中提取的字段：【问题概述】【查证情况】【处理方案】【业务源头是否解决】【联系客户情况】
- 每个字段对应 {placeholder}，运行时由 build_prompt 函数填充

### 5.3 Few-Shot 示例设计原则

- 每个示例 = text(归档意见片段) + primary(一级分类) + secondary(二级分类) + reasoning(分类理由)
- 覆盖各类典型场景：费用争议(流量超套)、网络质量(信号差)、业务办理(套餐变更)
- 示例存储在 config.yaml 的 classify.few_shot_examples 中，可随时增删
- 每次 API 调用都携带全部 Few-Shot 示例（不依赖历史）

### 5.4 归档意见预处理

build_prompt 函数在构造 user_prompt 前执行预处理：

1. 正则提取：re.findall 按【】标记提取各字段
2. 截断：查证情况/处理方案 超过 300 字时截断并加 (已截断) 标记
3. 脱敏：如果 config.runtime.mask_phone=true，手机号替换为 138****1234
4. 空值：未提取到的字段填入 (未填写)

### 5.5 LLM 输出解析

parse_response 函数处理流程：

1. json.loads 解析 LLM 返回的 JSON 字符串
2. 校验 primary_category 在配置的分类标签树中存在
3. 校验 secondary_category 在其一级分类的子项中存在
4. 校验 confidence 在 [high, medium, low] 中
5. 校验失败时标记 confidence=low, reasoning=解析失败:{原因}，不抛异常
---

## 6. 缓存设计详解

### 6.1 设计动机

投诉工单中存在大量相似或相同的归档意见模板文本（如：直接解释+联系不上客户），缓存可避免对相同文本重复调用 API。

### 6.2 哈希计算

compute_hash 函数实现：
1. 输入：归档意见完整文本
2. 预处理：strip() 去除首尾空白
3. 哈希：hashlib.sha256(preprocessed.encode(utf-8)).hexdigest()
4. 返回：64 位十六进制字符串

### 6.3 SQLite 实现细节

| 特性 | 选择 |
|------|------|
| 存储引擎 | SQLite（磁盘） |
| 写入模式 | WAL (Write-Ahead Logging) |
| 主键 | text_hash (SHA256) |
| 并发策略 | sqlite3 内置锁 + 重试 |
| 索引 | created_at 时间索引 |
| 路径 | output/cache/cache.db |

### 6.4 缓存读写流程

classify_single 函数内部：

    text_hash = compute_hash(row.归档意见)
    cached = cache.lookup(text_hash)
    if cached is not None:
        return cached  # 缓存命中，跳过 API 调用
    result = call_llm(...)
    cache.store(text_hash, result)
    return result

### 6.5 缓存预期效果

基于实际数据分析，归档意见存在显著重复：
- 处理方案相同率约 35%（直接解释、联系不上等模板）
- 查证情况相似率约 25%
- 预估整体缓存命中率：15%-30%
- 首次运行无缓存，第二次运行命中率最高
---

## 7. 配置 Schema 设计

### 7.1 config.yaml 完整结构

**（1） ===== LLM 连接配置 =====**

```markdown
llm:
  provider: openai          # openai / azure / ollama / deepseek
  model: gpt-4o-mini
  api_key_env: OPENAI_API_KEY
  base_url: null            # 可选，自定义端点
  temperature: 0.0          # 分类任务建议 0
  max_tokens: 300
```

**（2） ===== 分类体系配置 =====**

```markdown
classify:
  categories:
    费用争议:
      - 流量超套扣费
      - 增值业务扣费
      - 套餐费用不符
      - 账单异常
      - 主副卡费用
    服务质量:
      - 解释不到位
      - 未及时联系客户
      - 处理方案不合理
      - 服务态度问题
    业务办理:
      - 套餐变更争议
      - 合约和协议争议
      - 办理流程问题
      - 营销宣传争议
    网络质量:
      - 信号覆盖问题
      - 网速不达标
      - 断网或掉线
    其他:
      - 无法分类
  prompt_template_path: prompts/default_classify.yaml
  output_schema_version: v1.0
  few_shot_examples:
    - text: (归档意见片段)
      primary: 费用争议
      secondary: 流量超套扣费
      reasoning: (分类理由)
```

**（3） ===== 运行时配置 =====**

```markdown
runtime:
  concurrency: 3            # 并发 API 调用数 (1-10)
  rate_limit_rpm: 500       # 每分钟最大请求数
  retry_max_attempts: 3     # 最大重试次数
  retry_backoff_base: 1.0   # 退避基数(秒)
  checkpoint_interval: 500  # 每 N 条保存检查点
  archive_column_name: 归档意见
  mask_phone: false         # 是否脱敏手机号
```

### 7.2 校验规则清单

| 字段 | 校验 | 错误信息 |
|------|------|----------|
| llm.provider | 枚举值检查 | provider 必须为 openai/azure/ollama/deepseek |
| llm.temperature | 0 <= x <= 1 | temperature 必须在 [0, 1] 区间 |
| llm.max_tokens | x >= 100 | max_tokens 至少为 100 |
| classify.categories | 非空 dict | 分类体系不能为空 |
| classify.categories.* | 每个 key 至少 1 子项 | 一级分类缺少二级分类 |
| classify.prompt_template_path | 文件存在检查 | Prompt 模板文件不存在 |
| runtime.concurrency | 1 <= x <= 10 | concurrency 必须在 1-10 |
| runtime.rate_limit_rpm | x > 0 | rate_limit_rpm 必须为正数 |
| runtime.checkpoint_interval | x > 0 | checkpoint_interval 必须为正数 |
| 环境变量 | api_key_env 有值 | 环境变量未设置或为空 |
---

## 8. 错误处理策略

### 8.1 异常层级

    Exception
    +-- ClassificationError          (分类过程基础异常)
    |   +-- LLMCallError             (API 调用失败, 含 status_code, body)
    |   |   +-- LLMRateLimitError    (429 限流)
    |   +-- ParseError              (LLM 返回 JSON 解析失败)
    +-- ConfigError                  (配置错误)
        +-- ColumnNotFoundError      (归档意见列不存在)

### 8.2 API 调用重试策略

| 条件 | 行为 | 参数 |
|------|------|------|
| 429 Rate Limit | 等待 Retry-After 秒后重试 | 最多 3 次 |
| 5xx Server Error | 指数退避 1s, 2s, 4s | 最多 3 次 |
| 4xx Client Error (非429) | 不重试，直接抛出 | - |
| 网络超时/连接错误 | 指数退避重试 | 最多 3 次 |
| JSON 解析失败 | 不重试（非网络问题） | 标记低置信度 |

### 8.3 重试实现核心逻辑

`python
def call_llm_with_retry(system_prompt, user_prompt, llm_config):
    client = OpenAI(api_key=get_api_key(llm_config))
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=llm_config.model,
                messages=[
                    {role: system, content: system_prompt},
                    {role: user, content: user_prompt},
                ],
                temperature=llm_config.temperature,
                max_tokens=llm_config.max_tokens,
                response_format={type: json_object},
            )
            return response.choices[0].message.content
        except RateLimitError as e:
            if attempt == MAX_RETRIES:
                raise LLMRateLimitError(str(e))
            wait = float(e.response.headers.get(Retry-After, BACKOFF_BASE * (2 ** (attempt - 1))))
            time.sleep(wait)
        except (APIError, APITimeoutError) as e:
            if attempt == MAX_RETRIES:
                raise LLMCallError(getattr(e, status_code, 0), str(e))
            wait = BACKOFF_BASE * (2 ** (attempt - 1))
            time.sleep(wait)
`

### 8.4 日志记录规范

使用 Python logging 模块，结构化日志格式：

- 正确：logger.error(msg, extra={status_code: ..., model: ..., row_index: ...})
- 错误：logger.error(fmsg with string interpolation)  // 禁止字符串拼接

日志文件路径：logs/run_{YYYYMMDD_HHMMSS}.log，同时输出到终端 stderr。

---

## 9. 测试策略

### 9.1 核心原则

遵循 AGENTS.md：优先集成测试和端到端测试，避免仅为覆盖率添加单测，避免 mock。

### 9.2 测试分层

| 层级 | 测试内容 | 工具 | MVP |
|------|----------|------|-----|
| 冒烟测试 | 端到端：读取文件 - 分类 10 条 - 写入输出 | pytest | P0 |
| 集成测试 | reader + classifier + writer 模块集成 | pytest | P1 |
| 单元测试 | 纯数据转换函数 (compute_hash, parse_response, detect_column) | pytest | P2 |

### 9.3 冒烟测试用例 (6 个)

1. 正常流程：分类投诉例子.xlsx + config.yaml -> 生成 _classified.xlsx 和 _summary.xlsx
2. 空归档意见：含空值行 -> 空值行置信度=low，不影响其他行
3. 缓存命中：重复运行相同文件 -> 第二次运行缓存命中率大于0
4. 配置错误：缺少 api_key_env -> 抛出 ConfigError
5. 列名不匹配：归档意见列名为 处理意见 -> 抛出 ColumnNotFoundError
6. 断点续跑：Ctrl+C 中断后 resume -> 从检查点继续，结果完整

### 9.4 测试数据

- 分类投诉例子.xlsx（5条真实数据）作为冒烟测试数据集
- 广义投诉流量202605.xlsx（18992条）作为性能基准数据
- tests/fixtures/ 目录存放测试 fixture

---

## 10. 项目初始化步骤

### 10.1 pyproject.toml 关键配置

    [build-system]
    requires = [setuptools]
    build-backend = setuptools.build_meta
    
    [project]
    name = complaint-classifier
    version = 0.1.0
    requires-python = >=3.11
    dependencies = [openpyxl, openai, pyyaml, tqdm, diskcache]
    
    [project.optional-dependencies]
    dev = [pytest, mypy]
    
    [tool.mypy]
    strict = true

### 10.2 初始化命令

    mkdir -p src/complaint_classifier tests prompts output/cache output/checkpoints logs
    touch src/complaint_classifier/__init__.py
    cp config.example.yaml config.yaml
    export OPENAI_API_KEY=sk-xxxx
    pip install -e .
    python -m complaint_classifier run 分类投诉例子.xlsx

---

> 本文档与 RESEARCH.md 和 PRD.md 配套，三者关系：
> RESEARCH.md（需求调研）-> PRD.md（产品需求）-> TECH_DESIGN.md（技术设计）
