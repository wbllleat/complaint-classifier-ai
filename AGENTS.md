# AI代理指令（AGENTS.md）

> 本文档专门写给 Codex Agent 阅读，指导其在本项目中的行为规范。

## 项目概述

**投诉归档意见AI分类工具** 是一个本地运行的桌面脚本工具，利用大语言模型（LLM）对运营商投诉工单中的归档意见字段进行语义分析与自动分类。

- 输入：投诉工单 Excel（含归档意见列）
- 输出：原 Excel + 分类标签列 + 分类汇总统计表
- 数据量：约 18,000 行/月
- MVP 形态：命令行 Python 脚本
- 参考文档：RESEARCH.md（需求调研）、PRD.md（产品需求）、TECH_DESIGN.md（技术设计）

---

## 架构约束

- 项目结构必须遵循 TECH_DESIGN.md 第 5.4 节定义
- 所有类型定义集中于 src/complaint_classifier/types.py
- 模块职责严格分离：reader（读取）、classifier（分类）、cache（缓存）、writer（回写）、summary（汇总）、progress（进度）
- LLM 调用策略：每条归档意见独立调用，不依赖对话历史——这是解决 AI 上下文失忆问题的核心方案

---

## 代码风格

- 优先函数式编程，仅 cache.py 和 progress.py 可使用有状态类
- 编写纯函数：仅修改返回值，绝不修改输入参数或全局状态
- 遵循 DRY / KISS / YAGNI 原则
- 所有地方使用严格类型：函数返回、变量、集合
- 在编写新代码之前，先检查逻辑是否已经存在
- 所有导入位于文件顶部
- 禁止使用默认参数值——所有参数必须显式传入
- 为复杂数据结构创建 TypedDict 或 frozen dataclass 类型定义

---

## 错误处理

- 始终显式报错，禁止静默忽略
- 使用 types.py 中定义的特定异常类型：LLMCallError / LLMRateLimitError / ParseError / ConfigError / ColumnNotFoundError
- 禁止使用全能 except Exception 隐藏根因
- LLM API 调用使用指数退避重试（最多 3 次），429 等待 Retry-After
- 错误信息必须包含足够上下文：row_index、model、status_code
- 日志记录使用结构化字段 extra={...}，禁止 f-string 拼接消息

---

## 工具和依赖

- 使用 pyproject.toml 管理依赖，不在全局安装
- 依赖列表：openpyxl、openai、pyyaml、tqdm、diskcache
- 开发依赖：pytest、mypy（strict mode）
- 在需要时读取已安装依赖的源代码，而非猜测其行为
- 代码搜索优先使用 rg

---

## 测试要求

- 每个功能完成后进行手动冒烟测试
- 优先集成测试和端到端测试，验证真实行为
- 仅对纯数据转换函数使用单元测试（compute_hash, parse_response, detect_column）
- 禁止仅为覆盖率添加单元测试
- 在实际调用可行时避免 mock
- 测试数据使用项目中的 分类投诉例子.xlsx（5条）和 广义投诉流量202605.xlsx（18992条）
- 测试前确保 config.yaml 和环境变量已正确配置

---

## 文件与文档

- 代码是主要文档——使用清晰的命名、类型声明和 docstring
- 文档放在函数/类的 docstring 中，不在单独的文件中重复
- 三份设计文档各司其职：RESEARCH.md（调研）、PRD.md（需求）、TECH_DESIGN.md（实现）
- 新增设计决策需同步更新对应的设计文档

---

## 注意事项

- 保持代码简洁，先跑通核心链路再逐步完善
- 每完成一个模块立即手动测试，确保数据正确读写
- 数据不出本地，仅调用 LLM API 时发送归档意见文本
- API Key 从环境变量读取，绝不硬编码
- 输出文件不覆盖原始输入文件
- 处理大批量数据时关注缓存命中率，减少不必要的 API 调用
- 敏感字段（手机号）发送 LLM 前按配置决定是否脱敏
- 不要执行 git commit，保持 diff 清晰可审查
- 除非用户明确要求，否则不创建新分支
