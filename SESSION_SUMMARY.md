# 会话总结 —— 投诉归档意见AI分类工具

> 最后更新：2026-06-11 | 总计会话轮次：多轮（涵盖需求→设计→开发→迭代）

---

## 一、项目概述

利用大语言模型对运营商投诉工单的"归档意见"和"投诉内容"进行语义分类，输出带分类标签的 Excel 和汇总统计。

**核心数据**：`广义投诉流量202605.xlsx`（18992 行，15 列）

**运行命令**：
```powershell
cd "C:\Users\wen\Desktop\移动工作\工作\分类投诉ai分析"

# 校验配置
python -m complaint_classifier check-config

# 测试（指定条数）
python -m complaint_classifier test 广义投诉流量202605.xlsx --sample 5

# 全量运行
python -m complaint_classifier run 广义投诉流量202605.xlsx
```

> 注意：系统 Python 路径为 `C:\Python\python_3.12.0\python.exe`，已通过 `pip install -e .` 安装项目包，如果重装系统需重新执行。

---

## 二、当前配置（config.yaml）

| 参数 | 值 | 说明 |
|------|-----|------|
| provider | `qwen` | 阿里云 DashScope 兼容模式 |
| model | `qwen-max` | 千问最强模型 |
| base_url | `https://dashscope.aliyuncs.com/compatible-mode/v1` | API 端点 |
| concurrency | `6` | 并发线程数 |
| temperature | `0.0` | 确保分类一致性 |
| max_tokens | `300` | 分类 JSON 输出足够 |
| retry_max_attempts | `3` | 指数退避重试 |
| checkpoint_interval | `500` | 每 500 条自动保存断点 |

---

## 三、分类体系

**14 个一级分类，42 个二级分类**，定义在 `config.yaml` 的 `classify.categories`：

| 一级分类 | 二级分类数 | 关键子项 |
|----------|-----------|---------|
| 流量问题 | 8 | 流量超套扣费、流量提醒不到位、套内流量扣减争议、定向流量问题等 |
| 套餐变更问题 | 5 | 降档或变更受阻、合约解约、特定套餐无法办理等 |
| 营销宣传问题 | 3 | 虚假宣传、条款告知不全、未经确认订购 |
| 增值业务问题 | 4 | 增值业务扣费、订购未告知、退订仍扣费等 |
| 优惠返还问题 | 3 | 优惠赠送问题、到期无法延续、返还不及时 |
| 主副卡或家庭卡问题 | 2 | 费用或资源争议、家庭低消共享问题 |
| 语音或短信问题 | 2 | 资源扣减、提醒问题 |
| 网络信号问题 | 3 | 信号弱、无法上网、漫游费用 |
| 宽带或电视问题 | 3 | 故障断网、装移机、设备问题 |
| 携号转网相关问题 | 3 | 挽留误导、携出后问题、合约争议 |
| 退订或取消问题 | 1 | 退订不成功 |
| 服务或流程问题 | 2 | 服务态度推诿、营销骚扰 |
| 查询或退费问题 | 2 | 退费查证、账单查询争议 |
| 其他 | 1 | 其他问题 |

---

## 四、关键决策记录

| 决策 | 方案 | 原因 |
|------|------|------|
| 技术栈 | Python 3.12 + openpyxl + openai SDK + tqdm + diskcache | 纯 Python 生态，无外部服务依赖 |
| LLM 调用方式 | 每条独立调用（无对话历史） | 解决 AI 上下文失忆问题 |
| 分类输入 | 投诉内容 + 归档意见 综合分析 | 仅靠归档意见无法区分"扣费"和"提醒不到位" |
| 并发策略 | ThreadPoolExecutor（I/O 密集型） | API 调用为网络等待，多线程有效 |
| 缓存策略 | SHA256(归档意见全文) → diskcache | 相同文本不重复调 API |
| 输出格式 | Excel 原表 + 新增分类列 + 汇总 Sheet | 保留原始数据便于复核 |
| Prompt 结构 | System（规则+示例）+ User（数据）分离 | 每次调用重新注入规则，防止漂移 |

---

## 五、文件清单

```
complaint-classifier/
├── pyproject.toml              # 项目配置 + 依赖
├── config.yaml                 # LLM + 分类体系 + 运行时配置
├── prompts/
│   └── default_classify.yaml   # System/User Prompt 模板
├── src/complaint_classifier/
│   ├── __init__.py             # 版本号 0.1.0
│   ├── __main__.py             # CLI 入口 (run/test/check-config)
│   ├── models.py               # 数据模型 + 异常类（原 types.py，因重名已改）
│   ├── config.py               # 配置加载校验（VALID_PROVIDERS 含 qwen）
│   ├── reader.py               # Excel 读取 + 列检测
│   ├── classifier.py           # LLM 调用 + 重试 + Prompt 构建 + 并发
│   ├── cache.py                # diskcache 哈希缓存
│   ├── writer.py               # 结果回写（run 和 test 两种模式）
│   ├── summary.py              # 分类汇总 + 交叉统计
│   └── progress.py             # tqdm 进度 + 断点续跑
├── output/                     # 输出目录
│   ├── cache/
│   └── checkpoints/
├── logs/                       # 运行日志
├── tests/                      # 测试目录
├── RESEARCH.md                 # 需求调研
├── PRD.md                      # 产品需求文档
├── TECH_DESIGN.md              # 技术设计文档
└── AGENTS.md                   # Agent 行为规范
```

---

## 六、已解决的历史问题

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `types.py` 循环导入 | 与 Python 内置 `types` 模块重名 | 重命名为 `models.py` |
| 并发执行异常（40% 失败） | API 偶发返回 `None` content，直接下标访问崩溃 | `_extract_content()` 安全链式访问 + 空响应重试 |
| `provider: mass` 校验失败 | 不在 VALID_PROVIDERS 列表中 | 添加 `mass` 到允许列表 |
| `Qwen3.7-Max` 404 | DashScope 无此模型名 | 改为 `qwen-max` |
| `provider: qwen` 校验失败 | 未加入允许列表 | 添加 `qwen` 到 VALID_PROVIDERS |
| 缓存命中率仅 0.3% | 完整文本哈希，首次运行几乎无重复 | 正常现象，重复运行会提升至 15-30% |
| 输出缺少投诉内容列 | test 命令未包含 | writer.py 两个函数均已加入 |

---

## 七、待办事项

| 优先级 | 事项 | 状态 |
|--------|------|------|
| **P0** | 全量运行 18992 行（预计 ~2.6 小时 @6并发） | 待执行 |
| **P1** | 全量结果人工抽检复核，验证分类准确率 | 待执行 |
| **P1** | 根据全量结果微调分类体系或 Few-Shot 示例 | 待执行 |
| **P2** | `rate_limit_rpm` 参数实际生效（目前未限速） | 待实现 |
| **P2** | 支持 CSV 等其他输入格式 | 待实现 |
| **P2** | 本地 Ollama 模型支持（完全离线） | 待实现 |
| **P3** | Web UI 界面（FastAPI + React） | 待实现 |
| **P3** | 多文件批量处理 | 待实现 |

---

## 八、下次会话快速启动

```powershell
cd "C:\Users\wen\Desktop\移动工作\工作\分类投诉ai分析"
python -m complaint_classifier check-config     # 确认环境正常
python -m complaint_classifier test 广义投诉流量202605.xlsx --sample 10  # 快速验证
```

如需换 API Key 或模型，直接编辑 `config.yaml` 中 `llm` 部分即可，无需改代码。
