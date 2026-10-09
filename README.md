# 青禾报销助手 · Demo

> 助手是「制度检索窗口」+「本人单据状态的授权只读窗口」。它不生产规则，也不裁定事实。

## 运行

```bash
# 零第三方依赖，Python 3.10+ 标准库即可（本机 D:/python/python.exe 3.9.13 实测可用）
python run_demo.py     # 命令行演示，7 个场景
python server.py       # Web 演示 → http://127.0.0.1:8787
```

数据库会随 demo 自动重建，不需要额外初始化。

Web 版分五个页签：制度问答 / 我的单据 / 制度库 / 乔姐工作台 / 审计日志。
右上角可切换登录身份（张明·销售部华东 / 李静·研发部华南 / 王强·销售部华北）——
换成李静问同样的问题，华东补充规定就不会出现，那是 scope 过滤在起作用。

## 已实现的边界

| 约束 | 落地位置 | 验证场景 |
|---|---|---|
| 「有效」是双时间轴，不是静态文本 | `src/retrieval/search.py` 的 as_of 硬闸 | 场景 2 |
| 命中旧版必须提示现行版本 | `supersession_notice()` | 场景 2 |
| 元数据未确认不得用于回答 | SQL 里 `meta_confirmed_by IS NOT NULL` | 场景 6 |
| 助手提议、乔姐批准 | `src/ingest/queue.py` | 场景 6 |
| 越权在数据侧拦截，不靠提示词 | `query_my_tickets()` 无 applicant_id 参数 | 场景 5 |
| 只读，且只读状态 | 只有 `v_my_ticket` 视图，无金额/事由列 | 场景 4 |
| 状态是转述，不是判断 | `format_ticket_state()`，不预测结论 | 场景 4 |
| 该说不知道时不硬答 | `Answer.mode = answer / escalate / refuse` | 场景 3、5 |

## 演示里最能打的两条

1. **场景 2**：同样是「住宿标准多少」，问今天给 v3 的 500 元，问 7 月 12 日出差给 v2 的 400 元，并强制提示现行版本。
2. **场景 6**：同一个问题「打车能报吗」，在乔姐点确认前后答案不同 —— 变的是乔姐的决定，不是助手的判断。

## 对参考项目的改动

源码放在 `_reference/`，来自 GitHub 的 `Emmimal/temporal-rag` 与 `sudeep-daivajna/Enterprise-KB-Copilot`。
保留了它们的混合检索与评测思路，做了三处改动：

- **时效性从「重排加权」改成「硬闸」**。原实现给旧文档降权但仍留在候选上下文里，会被缝合出错答案；这里在 SQL 层按 `as_of` 直接过滤。
- **区分 conflict 与 overlay**。① 适用范围相同的两条规定内容打架 → 转交；② 通适规定 + 专项/限时规定叠加 → 照常回答但必须摆明关系。原实现混为一谈。
- **零外部依赖重写**。去掉 Chroma / sentence-transformers / Docker / API key，中文用字符 bigram + BM25 + 术语归一化，保证随时可跑。

## 尚未做的（P1/P2）

Web 界面、评测集跑分、`crossed` 转交单闭环、审计看板页面、LLM 表达层。
按《设计方案.md》的建议，这些应排在「内核可信」之后。
