# pi-sonar-agent 钉钉机器人手动触发实施方案

更新时间：2026-05-18

## 0. 实施状态

| 阶段 | 目标 | 当前状态 | 备注 |
| --- | --- | --- | --- |
| 阶段 A | 任务层落地（任务表 + JobStore + JobRunner + Worker） | 已完成 | 已落地 `run_jobs`、`dingtalk_command_records`、`job_store.py`、`job_runner.py`、`dingtalk_worker.py` |
| 阶段 B | 钉钉消息接入 | 已完成 | 已落地 `dingtalk_gateway.py`、`integrations/dingtalk_bot.py`、命令解析与原始消息落库 |
| 阶段 C | 确认卡片与确认回调 | 已完成 | 已落地确认卡片模板、确认/取消回调、幂等保护与阶段 C 回归测试 |
| 阶段 D | 结果回推 | 已完成 | 已落地开始/结束通知、Worker 通知链和非阻断保护 |
| 阶段 E | 权限、限流与取消 | 已完成 | 已落地白名单、频率限制、文本取消命令与取消权限校验 |
| 阶段 F | 真实钉钉 Stream 接入 | 已完成 | 已落地 Stream 模式接入、topic 注册、文本确认 fallback 与阶段 F 回归测试 |
| 阶段 G | 联调闭环与可验证性补齐 | 已完成 | 已补齐状态查询、重跑、运行入口与联调验收步骤，确保“发命令 -> 确认 -> 排队 -> 回推结果”可真实验证 |

### 阶段 A 细项状态

- [x] `run_jobs` 表落地
- [x] `dingtalk_command_records` 表落地
- [x] `src/core/job_store.py`
- [x] `src/core/job_runner.py`
- [x] `src/dingtalk_worker.py`
- [x] `src/pi_sonar_agent/*` 桥接模块补齐
- [x] 阶段 A 针对性测试补齐
- [x] 文档状态回写

### 阶段 B 细项状态

- [x] `src/integrations/dingtalk_bot.py`
- [x] `src/dingtalk_gateway.py`
- [x] `src/pi_sonar_agent/*` 桥接模块补齐
- [x] DingTalk 文本命令解析
- [x] `targets.json` 注册表解析与目标匹配
- [x] 原始消息落库到 `dingtalk_command_records`
- [x] 创建 `awaiting_confirmation` 任务
- [x] 重复消息去重
- [x] 阶段 B 针对性测试补齐
- [x] 文档状态回写

### 阶段 C 细项状态

- [x] 确认卡片模板
- [x] 确认/取消回调解析
- [x] `awaiting_confirmation -> queued` 状态流转
- [x] 取消任务状态流转
- [x] 幂等保护（重复确认/重复取消/终态保护）
- [x] 阶段 C 针对性测试补齐
- [x] 文档状态回写

### 阶段 D 细项状态

- [x] 开始执行消息模板
- [x] 完成/失败消息模板
- [x] Worker 开始节点通知
- [x] Worker 结束节点通知
- [x] 通知失败非阻断保护
- [x] 阶段 D 针对性测试补齐
- [x] 文档状态回写

### 阶段 E 细项状态

- [x] 触发用户白名单
- [x] 群白名单
- [x] 每用户活跃任务限制
- [x] 每用户时间窗口限流
- [x] 文本取消命令
- [x] 取消权限校验
- [x] 阶段 E 针对性测试补齐
- [x] 文档状态回写

### 阶段 F 细项状态

- [x] Stream 服务骨架
- [x] Chatbot topic 接入
- [x] 卡片回调 topic 接入
- [x] Stream 凭据配置
- [x] 启动入口与包桥接
- [x] 阶段 F 针对性测试补齐
- [x] 文档状态回写

### 阶段 G 细项状态

- [x] `查看任务 <job_id>` 状态查询命令
- [x] `查看我最近一次修复` 状态查询命令
- [x] `重跑任务 <job_id>` 命令
- [x] Worker / Gateway 启动入口补齐
- [x] 联调验收步骤文档化
- [x] 阶段 G 针对性测试补齐
- [x] 文档状态回写

适用范围：

- 当前 `pi-sonar-agent`
- 触发方式从“全自动批量执行”扩展为“钉钉机器人人工按需触发”
- 执行核心仍然复用现有 `RunCoordinator` / `batch_runner` / `main`
- 目标是“人工可控、按需执行、可审计、可回看、可逐步放量”

---

## 1. 目标

本方案的目标不是把 `pi-sonar-agent` 改造成一个在线聊天型大模型服务，而是在保持当前修复主链稳定的前提下，新增一层“人工触发入口”和“任务调度层”，让团队成员可以直接在钉钉中通过对话方式发起修复任务，并在钉钉中完成确认、查看状态和接收结果。

目标能力：

1. 用户可以在钉钉中用命令或半结构化对话发起修复请求
2. 机器人会回一张确认卡片，展示本次执行的关键参数
3. 用户确认后，任务进入后台执行，不阻塞钉钉对话
4. 执行过程中可以回推状态、进度、失败原因和最终结果
5. 最终结果包括：
   - 运行是否成功
   - 修复了多少 issue
   - 是否创建了 PR
   - PR 链接
   - 运行日志和摘要路径
6. 整个流程必须可审计、可幂等、可回看

非目标：

1. 不在第一阶段实现“机器人直接在线执行完整修复”
2. 不在第一阶段实现复杂自然语言理解
3. 不在第一阶段替换现有 CLI / batch 入口
4. 不在第一阶段引入复杂分布式消息队列

---

## 2. 总体方案

推荐采用下面这条主链：

1. 钉钉对话发起
2. 机器人解析用户请求
3. 机器人回确认卡片
4. 用户点击确认
5. 后台创建任务并排队
6. Worker 拉取任务并调用 `pi-sonar-agent`
7. 执行过程中的关键状态回推到钉钉
8. 执行结束后回推结果、PR 链接和日志位置

一句话概括：

`钉钉机器人负责“交互和确认”，后台 Worker 负责“真正执行”，pi-sonar-agent 负责“真正修复”。`

---

## 3. 架构分层

建议拆成 3 层：

### 3.1 对话接入层

职责：

- 接收钉钉机器人消息
- 解析命令
- 生成确认卡片
- 处理确认按钮回调
- 把执行状态和结果回推给钉钉

建议单独实现为一个轻量服务，例如：

- `services/dingtalk_gateway/`
- 或独立仓库 `pi-sonar-dingtalk-gateway`

第一阶段也可以放在当前仓库中，但建议与核心修复代码分层。

### 3.2 任务调度层

职责：

- 保存人工触发任务
- 管理任务状态
- 控制并发
- 提供幂等保护
- 允许后续扩展取消、重试、补发通知

这层不直接做修复逻辑，只负责“任务生命周期管理”。

### 3.3 修复执行层

职责：

- 复用现有 `pi-sonar-agent` 执行修复
- 继续使用当前：
  - `RunCoordinator`
  - `TargetConfig`
  - `batch_runner`
  - `main`
  - `state_store`
  - `artifact_writer`

原则：

- 不重写现有修复核心
- 不把钉钉协议混进修复主链

---

## 4. 推荐交互流程

### 4.1 发起

用户在钉钉里发消息，例如：

```text
修复 BI pengxiru@neware.com.cn
```

或：

```text
修复 BI pengxiru@neware.com.cn issue_keys=abc,def skip_issue_keys=xyz base_branch=develop
```

建议第一阶段采用“半结构化命令”而不是完全自由文本。

### 4.2 机器人解析

机器人解析出这些字段：

- `repository`
- `author`
- `base_branch`
- `issue_keys`
- `skip_issue_keys`
- `max_issues`
- `reviewer_email`（可选）

如果缺少核心字段，机器人直接提示补充，而不是创建任务。

### 4.3 回确认卡片

机器人回一张确认卡片，展示：

- 仓库
- 作者
- 基线分支
- issue_keys
- skip_issue_keys
- max_issues
- 触发人
- 预估执行模式：单目标 / 批量 / 是否跳过 build

卡片上至少提供 2 个操作：

- `确认执行`
- `取消`

可选：

- `仅生成配置`
- `查看上次同作者执行记录`

### 4.4 用户确认

用户点击 `确认执行` 后：

- 机器人服务写入一条任务
- 返回：
  - `任务已创建`
  - `任务编号`
  - `run_label`（如果已分配）

### 4.5 后台执行

后台 Worker 读取任务并执行：

1. 生成本次 `run_label`
2. 组装 `TargetConfig`
3. 调用现有修复入口
4. 持续更新任务状态
5. 必要时回推钉钉中间进度

### 4.6 结果回推

完成后机器人推送：

- 成功 / 失败 / 部分成功
- 修复 issue 数
- 跳过 issue 数
- 失败 issue 数
- PR 链接
- 日志路径
- 运行摘要路径

---

## 5. 对话命令格式建议

第一阶段建议只支持固定格式，避免歧义。

### 5.1 最小命令

```text
修复 <repository> <author>
```

示例：

```text
修复 BI pengxiru@neware.com.cn
```

### 5.2 带可选参数

```text
修复 <repository> <author> base_branch=<branch> issue_keys=<k1,k2> skip_issue_keys=<k3,k4> max_issues=<n>
```

示例：

```text
修复 BI pengxiru@neware.com.cn base_branch=develop issue_keys=abc,def skip_issue_keys=xyz max_issues=5
```

### 5.3 查询状态

```text
查看任务 <job_id>
```

### 5.4 查看最近结果

```text
查看我最近一次修复
```

### 5.5 重跑

```text
重跑任务 <job_id>
```

---

## 6. 任务状态机设计

建议任务状态至少包含：

- `pending`
- `awaiting_confirmation`
- `queued`
- `running`
- `succeeded`
- `partial`
- `failed`
- `cancelled`
- `timeout`

状态流转建议：

1. 收到用户请求后先创建 `awaiting_confirmation`
2. 用户确认后进入 `queued`
3. Worker 接手后进入 `running`
4. 执行结束后进入：
   - `succeeded`
   - `partial`
   - `failed`
   - `timeout`
5. 用户取消则进入 `cancelled`

---

## 7. 数据库设计建议

### 7.1 新增表 `run_jobs`

建议字段：

- `id`
- `job_id`
- `status`
- `trigger_source`
  - 固定值可为 `dingtalk_bot`
- `trigger_user_id`
- `trigger_user_name`
- `conversation_type`
  - `single_chat` / `group_chat`
- `conversation_id`
- `repository`
- `project_key`
- `author`
- `base_branch`
- `issue_keys_json`
- `skip_issue_keys_json`
- `max_issues`
- `reviewer_email`
- `target_payload_json`
- `confirmation_token`
- `confirmed_at`
- `queued_at`
- `started_at`
- `finished_at`
- `run_label`
- `result_status`
- `pr_url`
- `target_summary_path`
- `run_log_path`
- `error_message`
- `created_at`
- `updated_at`

说明：

- 这张表是“任务主表”
- 与现有的运行态表、PR 业务表并行存在
- 面向“触发和调度”

### 7.2 新增表 `dingtalk_command_records`

建议字段：

- `id`
- `job_id`
- `message_id`
- `sender_staff_id`
- `sender_nick`
- `raw_text`
- `parsed_command_json`
- `parse_status`
- `parse_error`
- `created_at`

说明：

- 保留原始指令和解析结果
- 便于审计和排障

### 7.3 后续可选表 `dingtalk_message_records`

建议字段：

- `id`
- `job_id`
- `message_type`
- `direction`
  - `inbound` / `outbound`
- `content_json`
- `send_status`
- `created_at`

说明：

- 用于保存确认卡片、状态回推、最终结果消息

---

## 8. 后台执行方案

### 8.1 方案选择

推荐第一阶段直接使用：

- 数据库任务表
- 单独 Worker 轮询

而不是：

- Redis 队列
- RabbitMQ
- Kafka

原因：

- 你现在项目已经有 MySQL
- 先用 DB 队列表最省事
- 后续若并发量变大再切消息队列

### 8.2 Worker 执行模式

Worker 进程循环：

1. 从 `run_jobs` 中拉取 `queued`
2. 抢占任务（更新为 `running`）
3. 根据 `target_payload_json` 组装 `TargetConfig`
4. 调用现有执行入口
5. 更新 `run_label`
6. 更新状态和结果
7. 触发钉钉消息回推

### 8.3 并发控制

第一阶段建议：

- 单 Worker
- 串行执行

原因：

- 修复任务耗时长
- 会拉代码、跑模型、跑构建、建 PR
- 串行最稳

第二阶段可支持：

- 按仓库串行
- 不同仓库并行

---

## 9. 与现有 pi-sonar-agent 的衔接方式

核心原则：

- 不改现有修复主链职责
- 新增一个“任务入口适配层”

建议新增模块：

### 9.1 `src/core/job_runner.py`

职责：

- 接收 `run_jobs` 记录
- 把任务参数转换为 `TargetConfig`
- 调用现有 `RunCoordinator`

### 9.2 `src/core/job_store.py`

职责：

- 对 `run_jobs` / `dingtalk_command_records` 做数据库读写

### 9.3 `src/integrations/dingtalk_bot.py`

职责：

- 封装钉钉消息收发
- 封装确认卡片发送
- 封装状态回推

### 9.4 `src/dingtalk_worker.py`

职责：

- 后台轮询任务
- 驱动 job 执行

### 9.5 `src/dingtalk_gateway.py`

职责：

- 提供钉钉接入入口
- 接收消息
- 解析命令
- 创建 `awaiting_confirmation` 任务
- 处理确认动作

---

## 10. 钉钉接入方式建议

推荐使用：

- `Stream Mode`

原因：

1. 不需要公网回调地址
2. 更适合内网部署
3. 更适合内部机器人
4. 官方推荐程度更高

不建议第一阶段用自定义机器人 + 简单 webhook 直接承接复杂交互，因为：

- 交互能力弱
- 确认卡片和回调能力不如应用机器人体系稳定

---

## 11. 确认卡片设计建议

卡片内容建议包括：

- 标题：`确认执行 Sonar 自动修复`
- 仓库
- 作者
- 基线分支
- issue_keys
- skip_issue_keys
- max_issues
- 触发人
- 生成时间

按钮建议：

- `确认执行`
- `取消`

点击确认后：

- 后端校验 `confirmation_token`
- 避免重复确认
- 任务状态从 `awaiting_confirmation` -> `queued`

---

## 12. 结果回推内容建议

### 12.1 开始执行

```text
任务已开始执行
任务编号：JOB-20260518-001
仓库：BI
作者：pengxiru@neware.com.cn
基线分支：develop
```

### 12.2 中间进度

建议只在关键节点推送，避免刷屏：

- 已拉取 Sonar issues
- 已进入第二轮增强修复
- 已创建 PR
- 中途异常，进入部分成果发布

### 12.3 完成结果

```text
任务执行完成
结果：partial
成功：6
跳过：2
失败：1
PR：https://...
日志：logs/runs/batch_xxx.log
摘要：logs/run-artifacts/.../run_summary.json
```

### 12.4 失败结果

```text
任务执行失败
结果：failed
原因：模型长时间不可用 / build 失败 / PR 创建失败
日志：...
```

---

## 13. 权限与安全控制

第一阶段就应该考虑这些限制：

### 13.1 触发权限

建议至少支持：

- 白名单用户
- 白名单群
- 或仓库级授权用户

### 13.2 参数限制

建议限制：

- 不允许随意指定任意仓库
- 不允许指定危险分支
- `base_branch` 只能在允许集合中

### 13.3 幂等与防重复

必须处理：

- 用户重复点确认
- 相同命令短时间重复提交
- 机器人重复回调

建议方式：

- `confirmation_token`
- `job_id`
- 任务创建时间窗去重

### 13.4 审计

必须能追踪：

- 谁发起的
- 何时发起
- 原始命令是什么
- 最终执行了什么 target 参数
- 最终结果是什么

---

## 14. 失败与降级方案

### 14.1 机器人解析失败

处理方式：

- 不创建执行任务
- 回消息提示用户改用标准格式

### 14.2 确认后执行失败

处理方式：

- `run_jobs.status = failed`
- 回推错误摘要
- 保留 `run_label`、日志、错误信息

### 14.3 Worker 崩溃

处理方式：

- 启动时扫 `running` 且长时间未更新的任务
- 标记为 `failed` 或 `timeout`

### 14.4 钉钉消息发送失败

处理方式：

- 不影响修复主流程
- 单独记录 `send_status`
- 后续可补偿重发

---

## 15. 分阶段实施计划

### 阶段 A：任务层落地（状态：已完成）

目标：

- 先不接钉钉
- 把任务表和 Worker 跑起来

交付：

- `run_jobs`
- `dingtalk_command_records`
- `job_store.py`
- `job_runner.py`
- `dingtalk_worker.py`（可先本地假触发）

验收：

- 手工插入一条 job
- Worker 能执行一次真实修复任务

### 阶段 B：钉钉消息接入（状态：已完成）

目标：

- 能从钉钉收到命令
- 能创建 `awaiting_confirmation`

交付：

- `dingtalk_gateway.py`
- 命令解析器
- 原始消息落库

验收：

- 钉钉发一条命令后，数据库中能看到待确认任务

### 阶段 C：确认卡片与确认回调（状态：已完成）

目标：

- 用户点击确认后任务进入 `queued`

交付：

- 确认卡片模板
- 回调处理
- 幂等保护

已完成实现：

- `src/integrations/dingtalk_bot.py` 新增确认卡片模板、卡片回调解析、确认结果回复模板
- `src/dingtalk_gateway.py` 新增确认/取消回调入口与幂等状态判断
- `src/core/job_store.py` 新增取消任务状态流转
- 本地 CLI 调试输出补充 `reply_card`
- 阶段 C 回归测试已通过

验收：

- 同一条任务只能确认一次

### 阶段 D：结果回推（状态：已完成）

目标：

- 执行结束后把结果回推到钉钉

交付：

- 开始/完成/失败消息模板
- PR 链接回推
- 日志路径回推

已完成实现：

- 新增 `src/core/dingtalk_job_notifier.py`，封装开始/完成通知模板
- `src/dingtalk_worker.py` 在任务开始和结束节点触发钉钉通知
- 钉钉通知失败时只告警，不阻断任务执行和状态落库
- 阶段 D 回归测试已通过

验收：

- 成功 / 失败 / partial 都能收到结果消息

### 阶段 E：权限、限流与取消（状态：已完成）

目标：

- 让生产使用更安全

交付：

- 用户白名单
- 群白名单
- 参数白名单
- 取消任务能力

已完成实现：

- 新增 `src/core/dingtalk_access_policy.py`，统一处理钉钉触发白名单、群白名单、活跃任务限制和时间窗口限流
- `src/dingtalk_gateway.py` 在创建修复任务前增加权限与限流校验
- `src/integrations/dingtalk_bot.py` 支持 `取消任务 <job_id>` 文本命令
- `src/core/job_store.py` / `src/core/db_client.py` 补充限流统计和按任务号取消能力
- `.env.example` 补充 DingTalk 白名单和限流配置说明
- 阶段 E 回归测试已通过

---

### 阶段 F：真实钉钉 Stream 接入（状态：已完成）

目标：

- 让钉钉机器人可以通过真实 Stream 通道接入现有手动触发闭环

交付：

- Stream 服务骨架
- Chatbot topic 注册
- 卡片回调 topic 注册
- Stream 凭据配置
- 启动入口

已完成实现：

- 新增 `src/dingtalk_stream_service.py`，注册 `/v1.0/im/bot/messages/get` 和 `/v1.0/card/instances/callback`
- 新增 `src/pi_sonar_agent/dingtalk_stream_service.py` 包桥接和 `pi-sonar-dingtalk-stream` 启动入口
- `pyproject.toml` 增加 `dingtalk-stream` 依赖和脚本入口
- `.env.example` / `src/core/project_env.py` 增加 `DINGTALK_STREAM_CLIENT_ID`、`DINGTALK_STREAM_CLIENT_SECRET`
- Stream 模式下新增文本确认 fallback：未展示互动卡片时，可直接发送 `确认任务 <job_id>` / `取消任务 <job_id>`
- 阶段 F 回归测试已通过

说明：

- 当前 Stream 接入已经能把“文本命令 -> 任务创建 -> 文本确认/取消 -> 后台执行 -> 结果通知”闭环跑通
- 互动卡片高级版的真实投放 API 仍可后续继续增强；在此之前，文本确认 fallback 足以支撑小范围联调和生产灰度

---

### 阶段 G：联调闭环与可验证性补齐（状态：已完成）

目标：

- 让真实联调时不仅能发起任务，还能查询任务状态、查询最近一次任务，并基于历史任务发起重跑

交付：

- `查看任务 <job_id>`
- `查看我最近一次修复`
- `重跑任务 <job_id>`
- `pi-sonar-dingtalk-worker`
- `pi-sonar-dingtalk-gateway`
- 真实联调验收步骤

已完成实现：

- `src/integrations/dingtalk_bot.py` 新增查看任务、查看最近一次修复、重跑任务命令解析和回复模板
- `src/dingtalk_gateway.py` 新增任务状态查询、最近任务查询、重跑任务创建逻辑
- `src/core/job_store.py` / `src/core/db_client.py` 新增“按触发人读取最近任务”和“从历史任务创建重跑任务”
- `pyproject.toml` 新增 `pi-sonar-dingtalk-worker`、`pi-sonar-dingtalk-gateway` 启动入口
- 阶段 G 回归测试已通过

验收：

- 在钉钉中可以完成：
  - `修复 ...`
  - `确认任务 <job_id>`
  - `查看任务 <job_id>`
  - `查看我最近一次修复`
  - `重跑任务 <job_id>`

---

## 16. 实施顺序建议

最推荐的顺序是：

1. 先做任务表和 Worker
2. 再做钉钉命令接入
3. 再做确认卡片
4. 再做结果回推
5. 最后做权限和取消
6. 再接真实钉钉 Stream 入口
7. 最后补齐联调验证和状态查询命令

原因：

- 先把“后台可执行”跑通，比先做 UI/对话层更稳
- Worker 一旦稳定，钉钉只是换一个触发入口

---

## 17. 验收标准

方案完成的最低验收标准：

1. 用户可在钉钉中发起修复请求
2. 机器人会回确认卡片
3. 用户确认后能创建后台任务
4. 后台任务能调用现有 `pi-sonar-agent` 完成一次真实执行
5. 执行结果能回推到钉钉
6. 数据库中能查到：
   - 原始命令
   - 解析参数
   - 任务状态
   - run_label
   - PR 链接
   - 日志路径
7. 当互动卡片不可用时，仍可通过 `确认任务 <job_id>` / `取消任务 <job_id>` 完成闭环
8. 用户可通过 `查看任务 <job_id>` / `查看我最近一次修复` 主动查询状态

---

## 18. 真实联调验收步骤

建议按下面顺序验证整条链路。

补充说明：

- 这一节保留“联调验收顺序”
- 如果你要在本机长期挂起 Worker / Stream，或者用 NSSM 注册为 Windows 服务，请直接参考 [RUNBOOK.md](./RUNBOOK.md) 的：
  - `5.5 钉钉手动触发，本地临时启动`
  - `5.6 钉钉手动触发，本地常驻启动（NSSM）`

### 18.1 环境准备

1. 配置 `.env`
   - `DB_*`
   - `DINGTALK_APPKEY`
   - `DINGTALK_APPSECRET`
   - `DINGTALK_AGENTID`
   - 如使用独立 Stream 凭据，再配置：
     - `DINGTALK_STREAM_CLIENT_ID`
     - `DINGTALK_STREAM_CLIENT_SECRET`
2. 在 `data/targets.json` 中准备至少一条可执行目标
3. 为目标配置 `dingtalk_userid`，这样开始/结束通知能直接私发给触发人

### 18.2 启动后台 Worker

```powershell
pi-sonar-dingtalk-worker
```

若只想消费一条任务做验证：

```powershell
pi-sonar-dingtalk-worker --run-once
```

### 18.3 启动真实 Stream 接入

```powershell
pi-sonar-dingtalk-stream --targets-file data/targets.json
```

### 18.4 在钉钉中发起修复

```text
修复 BI someone@example.com
```

或：

```text
修复 BI someone@example.com base_branch=develop issue_keys=issue-1,issue-2 skip_issue_keys=issue-3 max_issues=5
```

预期结果：

- 机器人返回“待确认”回复
- 回复中包含 `job_id`
- 数据库 `run_jobs` 中新增一条 `awaiting_confirmation`

### 18.5 确认执行

如果当前通道支持互动卡片，直接点卡片里的“确认执行”。

如果当前通道没有展示互动卡片，直接发送：

```text
确认任务 <job_id>
```

预期结果：

- 任务状态从 `awaiting_confirmation` 变为 `queued`
- Worker 拉起后状态变为 `running`
- 触发人收到“任务开始执行”通知

### 18.6 查询状态

```text
查看任务 <job_id>
```

或：

```text
查看我最近一次修复
```

预期结果：

- 能看到当前状态、run_label、PR 链接、日志、摘要等关键信息

### 18.7 等待执行结束

预期结果：

- 触发人收到完成通知
- 通知里包含：
  - 终态状态
  - 执行结果
  - run_label
  - PR 链接（如有）
  - 日志路径
  - 摘要路径

### 18.8 验证重跑

当任务已结束后，可发送：

```text
重跑任务 <job_id>
```

预期结果：

- 系统会创建一条新的 `awaiting_confirmation` 任务
- 新任务拥有新的 `job_id`
- 再次确认后能走完整闭环

---

## 19. 推荐结论

最终推荐落地方式：

- 钉钉机器人做“人工触发入口 + 确认 + 通知”
- `run_jobs` 做任务状态真相源
- 单 Worker 串行执行
- 现有 `pi-sonar-agent` 继续只负责修复主链

这条路线的优点是：

1. 风险最小
2. 与现有项目最兼容
3. 适合逐步上线
4. 可审计、可控、可回退

---

## 20. 后续实施约束

后面真正开始编码时，必须遵守下面这些约束：

1. 不重写现有 `RunCoordinator` 主修复逻辑
2. 不把钉钉协议细节侵入到核心修复链路中
3. 所有钉钉消息失败都不能阻断修复主流程
4. 所有任务状态必须先落库再回推消息
5. 确认动作必须幂等
6. 任务执行必须支持明确审计链路

这份文档作为后续实施的唯一方案基线。后续如果设计有调整，必须先更新本文件，再开始对应改造。
