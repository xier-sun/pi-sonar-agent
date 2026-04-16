# pi-sonar-agent 实施清单与 Bash 工具能力评估

更新时间：2026-04-16

输入依据：

- `logs/runs/batch_20260415114404.log`
- `logs/issue_artifacts/BI/20260415114404-01/*`
- `src/core/run_coordinator.py`
- `src/agent/claude_agent.py`
- `src/core/issue_planner.py`
- `src/core/fix_verifier.py`
- `src/core/quality_gate_verifier.py`
- `src/core/issue_prompt.py`
- `src/core/tool_surface.py`
- `src/core/policy.py`
- `src/core/diff_reviewer.py`
- `data/rule_profiles.json`
- `docs/agent-hardening-implementation-plan-20260410.md`
- `docs/AGENT_REFACTOR_PLAN.md`

## 1. 目标

本清单用于把当前 `pi-sonar-agent` 从“外层链路完整、内层修复不稳定”的状态，推进到“规则路由清晰、仓库能力可识别、失败可切换策略、PR 后可回查 Sonar 关闭状态”的工程化状态。

重点不是继续调 prompt，而是把以下能力落成代码：

1. 仓库能力指纹
2. 动态引擎路由
3. Sonar MCP 结构化接入
4. 失败指纹驱动的策略切换
5. 语义预检前移
6. PR 后 Sonar 闭环验证

## 1.1 实施状态

- `已完成` 文档状态对齐
  - 已新增实施状态区
  - 已明确标注 `P0-4` 暂不实施
- `已完成` Bash 新增文件能力
  - 已把 `rule profile -> EditContract -> Bash policy -> diff reviewer` 链路打通
  - 已支持按声明目录受控创建新文件
  - 已保持默认 Bash 仍为只读诊断工具
- `已完成` `P0-1` 仓库能力指纹
  - 已新增 `repo_capability` 检测模块
  - 已在 clone 后生成 `.pi-sonar-agent-runtime/repo_capability.json`
  - 已把关键兼容性信息注入 `EditContract` / prompt guidance
  - 已完成 `quality_gate_verifier` 级别的语言特性硬门禁
  - 已在 layered verification 下实现 build 前拦截不兼容语法
- `已完成` `P0-2` 动态引擎路由
  - 已新增 `engine_router` 运行时路由层
  - 已把 `S107` 改为 Roslyn 不可用时直接 skip
  - 已把 `engine_routing_decision` 写入 `FixResult` / artifact
  - 已移除 `S107` 的 agent fallback 配置
- `已完成` `P0-3` 官方 Sonar MCP 只读接入
  - 已新增 `mcp_servers` / `sonar_mcp_client` 配置装配层
  - 已把 `mcp_servers` 透传到 Claude SDK request/options
  - 已支持只读工具过滤与 `mcp_servers/mcp_tools_count/mcp_mode` 元数据输出
  - 已消除运行时静态 `MCP_FIX_TOOLS=[]` 的硬编码依赖
- `已完成` `P1-1` 失败指纹驱动策略切换
  - 已新增 `failure_fingerprint` 结构化提取模块
  - 已把失败指纹写入 `RetryContext`
  - 已支持连续同类失败指纹计数
  - 已让 planner 基于重复指纹切换更保守 archetype，并在第 3 次前直接停止无意义重试
  - 已补充 `tool_input_invalid_burst` / `turn_exhausted_after_partial_patch` / `lang_feature_incompatible` 指纹
  - 已补齐 `semantic_precheck -> failure_fingerprint -> retry_feedback -> planner` 闭环
  - 已让 `anonymous_type_helper_boundary` 触发保签名/原方法内收口策略，并在重复命中时直接 skip
- `已完成` `P1-2` changed-file 语义预检
  - 已新增 `semantic_precheck` 轻量语义预检层
  - 已把预检插入 `boundary -> semantic_precheck -> propagation -> quality_gate -> build`
  - 已支持在 build 前拦截不兼容语法、空 async、匿名类型跨 helper 边界、明显不完整的签名传播
  - 已把 `semantic_precheck_result` 写入 verifier / FixResult / artifact
  - 已新增 `repair_plan` 与 patch 漂移硬门禁，阻止 `requires_new_type=false` 时偷偷新增 `class/record/struct`
- `已完成` `P1-3` prompt 瘦身
  - 已把 system/user prompt 预算收口到 `6k / 8k` 目标
  - 已把长门禁、repair plan、预取上下文外置到参考文件
  - 已新增 `prompt_budget_report.json` 工件与预算回归测试
  - 已把“无显式 build tool 时不展示真实构建命令”接入 prompt builder，避免再诱导模型在 Bash 中自行 build
- `已完成` `P1-4` 收紧 `S3776` archetype
  - 已让 `S3776` 默认回到单文件 `private_helper_extract / guard_clause_flatten / local_block_reorder`
  - 已把 public/interface/controller 传播自动降级为保签名修法
  - 已保留受控 internal 传播的白名单路径
- `已完成` `P1-5` 工具真相源统一
  - 已新增统一 `build_visible_toolset(...)`
  - 已把 prompt、runtime allowlist、artifact/log 快照对齐到同一份工具视图
  - 已把 `visible_toolset` 写入 `FixResult` / artifact
- `已完成` `P2-1` Roslyn-routed `S107` 安全子集闭环
  - 已新增可编译的 `fix_engine/AgentFixEngine.csproj`
  - 已保留 `S107` 的 Roslyn 安全分析路径，稳定输出 `canFixSafely / safetyFlags`
  - 已新增 deterministic `S107` 参数对象补丁生成器，并接入 `resolved_engine == "roslyn"` 主链路
  - 已把 Roslyn 产出的安全候选真正落到 patch + reviewer + build verification，而不再停在结构化 skip
- `已完成` `P2-2` 实施级测试矩阵
  - 已新增 `tests/fixtures/repos/*` 固定仓库夹具
  - 已补 fixture-based repo capability / S107 Roslyn / public-surface regression smoke tests
  - 已形成跨模块 150 条 issue-fix 相关回归基线
- `已完成` `P2-3` 一等 `CreateFile` 能力
  - 已把 `Write` 接成正式的 create-file-only 能力，并纳入 runtime allowlist / prompt / reviewer 审计链
  - 已支持 `Write(create_file_under=...)` 目录白名单
  - 已明确保持 `Write` 仅用于创建尚不存在的新文件，已有文件修改继续走 `Edit/MultiEdit`
- `暂不实施` `P0-4` PR 后 Sonar 闭环验证
  - 按当前决策先挂起，不进入本轮实施范围
- `已完成` 2026-04-16 六条收敛护栏补强
  - `1/6` max-turn patch salvage：`Reached maximum number of turns` 且已落盘 patch 时不再直接 skip，而是进入 verifier salvage 链
  - `2/6` 无效 Edit 连击熔断：连续无效 `Edit/MultiEdit/Write` 会提前终止 attempt，避免白白烧光 turn
  - `3/6` patch-vs-plan 语义预检：patch 不能违背 `repair_plan` 的 `requires_new_type / requires_signature_change`
  - `4/6` prompt/build/Bash 对齐：无显式 build tool 时，prompt 与 retry feedback 都明确交由外层流程执行构建验证
  - `5/6` 新失败指纹与降级：planner 已消费 `tool_input_invalid_burst / turn_exhausted_after_partial_patch / lang_feature_incompatible`
  - `6/6` 精确测试回归：已补运行时、prompt、planner、semantic precheck、retry context、salvage 的自动化覆盖
- `已完成` 2026-04-16 误拦与空转收口
  - 已把 `anonymous_type_helper_boundary` 从“同文件新增 helper + 出现匿名类型就硬拦”收紧为“仅在新增 helper 内部真实新增匿名对象构造时硬拦”
  - 已补 attempt-02 类回归：匿名类型留在原方法内、helper 仅承载简单同步逻辑时，`semantic_precheck` 不再误杀可编译 patch
  - 已把 `tool_input_invalid` 的失败明细稳定化为结构化 key，例如 `tool_input_invalid:missing:old_string`
  - 已让外层 retry 在连续两次命中同一类无效写工具输入且无有效落盘时提前止损，不再跨多轮空转
  - 已清洗 workspace rules 中的绝对根目录提示，把 `C:\...` 路径替换为 `<workspace-root>`，并追加“只使用仓库相对路径”的运行时说明
  - 已完成定向和跨模块验证：`67 passed`、`14 passed`、`156 passed`
  - `pytest -q` 全量在当前机器上触发 .NET Core CLR OOM，当前未标记为全量通过
- `已完成` 2026-04-16 `S3776` 类型形状失败闭环
  - 已在 planner 中把 `helper_extraction_type_break / nullable_type_mismatch / anonymous_type_leak / anonymous_type_helper_boundary` 视为同类类型形状失败
  - 已在第 2 次同类失败后自动移除 `helper_extract` capability，并把 `repair_shape` 收敛为 `method_rewrite_in_place`
  - 已把 `S3776` 的重复类型形状失败自动 skip 阈值从第 2 次放宽到第 3 次，确保至少给一次“无 helper 的方法内收口”真实机会
  - 已新增 `dynamic_helper_signature_boundary` 语义预检，专门拦截新增 helper 把匿名/nullable-heavy 状态退化成 `dynamic` 合同的 patch
  - 已把该语义 finding 映射回 `helper_extraction_type_break` 失败指纹，确保 retry feedback 与 planner 降级继续闭环
  - 已使用真实 `a22e301e-b1e0-4a1f-9bec-712001f1a11a` 场景参数回放验证：`allowed_capabilities=('method_rewrite',)`、`repair_shape=method_rewrite_in_place`、`skip_reason=''`

本轮已完成验证：

- `.venv\Scripts\python.exe -m pytest tests/test_runtime_layers.py -k "bash or file_creation" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_issue_guardrails.py -k "file_creation or cap or S107 or contract_review" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_repo_capability.py -q`
- `.venv\Scripts\python.exe -m pytest tests/test_repo_capability.py tests/test_issue_guardrails.py -q`
- `.venv\Scripts\python.exe -m pytest tests/test_fix_verifier.py -k "language_feature or repo_capability or quality_gate_verifier" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_engine_router.py tests/test_claude_agent.py -k "engine_router or engine_routing or no_longer_skips_previously_policy_managed_rule" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_artifact_writer.py -q`
- `.venv\Scripts\python.exe -m pytest tests/test_mcp_server_config.py tests/test_runtime_layers.py -k "mcp or claude_adapter_build_request_handles_third_party_provider" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_claude_agent.py -k "mcp_runtime or builtin_tool_policy_allows_editing_tools_without_bash or claude_fix_tool_policy_allows_finish_and_harmless_shell" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_failure_fingerprint.py tests/test_issue_planner.py -k "fingerprint or repeated_failure or type_shape" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_issue_retry.py -k "fingerprint or strategy" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_semantic_precheck.py tests/test_attempt_scheduler.py -q`
- `.venv\Scripts\python.exe -m pytest tests/test_fix_verifier.py -k "semantic_precheck or repo_capability_gate" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_issue_prompt_size.py tests/test_artifact_writer.py tests/test_claude_agent.py -k "prompt or build_user_prompt or externalizes_reference" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_issue_planner.py -k "s3776 or propagation or archetype" -q`
- `.venv\Scripts\python.exe -m pytest tests/test_runtime_layers.py tests/test_claude_agent.py tests/test_artifact_writer.py -k "visible_toolset or mcp_runtime or build_user_prompt or tool_policy or artifact_writer" -q`
- `dotnet build fix_engine\AgentFixEngine.csproj -c Release`
- `.venv\Scripts\python.exe -m pytest tests/test_roslyn_engine.py tests/test_fixture_matrix.py -q`
- `.venv\Scripts\python.exe -m pytest tests/test_repo_capability.py tests/test_engine_router.py tests/test_mcp_server_config.py tests/test_failure_fingerprint.py tests/test_semantic_precheck.py tests/test_issue_prompt_size.py tests/test_issue_planner.py tests/test_runtime_layers.py tests/test_claude_agent.py tests/test_artifact_writer.py tests/test_roslyn_engine.py tests/test_fixture_matrix.py -q`
- `.venv\Scripts\python.exe -m pytest tests/test_runtime_layers.py tests/test_issue_guardrails.py tests/test_claude_agent.py tests/test_roslyn_engine.py tests/test_fixture_matrix.py tests/test_s107_parameter_object.py -q`
- `.venv\Scripts\python.exe -m pytest tests/test_s107_parameter_object.py -q`
- `.venv\Scripts\python.exe -m py_compile ...`
- `.venv\Scripts\python.exe -m pytest tests/test_issue_prompt_size.py tests/test_runtime_layers.py tests/test_semantic_precheck.py tests/test_failure_fingerprint.py tests/test_issue_planner.py tests/test_issue_retry.py tests/test_claude_agent.py -q`
- `.venv\Scripts\python.exe -m pytest -q` -> `323 passed`
- `.venv\Scripts\python.exe -m pytest tests/test_issue_planner.py tests/test_semantic_precheck.py tests/test_failure_fingerprint.py tests/test_issue_retry.py -q` -> `76 passed`

---

## 2. 当前结论

### 2.1 当前主问题

本轮失败不是单一模型能力问题，而是以下几个工程问题叠加：

1. `S107` 的策略默认鼓励 `record` / 参数对象，但目标仓库实际为 `netcoreapp3.1`，语言能力不支持。
2. `S3776` 的复杂度修复没有类型保持约束，helper extraction 容易引入 nullable、匿名类型、`dynamic` 失配。
3. Planner 对部分复杂度问题会触发签名传播，导致本来是 file-scope 的修复膨胀成 solution-scope。
4. 重试能带回失败日志，但不会根据失败类型切换 archetype。
5. MCP 设计意图和运行时现实不一致，日志里 agent 实际还是 `mcp_servers: []`。
6. Prompt 已做外置，但复杂 issue 的 system/user prompt 仍然偏重。
7. PR 创建之后没有真正回查 Sonar issue 是否已关闭。

### 2.2 当前 Bash 能力结论

结论先行：

- **当前项目已支持 Bash 受控新增文件能力。**
- **当前实现不是无限制写文件，而是按规则和路径白名单受控放开。**

当前边界：

1. 只有规则 profile 显式声明 `create_file` 时，planner 才会下沉为 `EditContract.allow_file_creation=True`
2. 只有命中 `EditContract.allowed_new_file_roots` 的目录才允许通过 `Write` 或受控 Bash 创建
3. `Write` 只允许创建当前还不存在的新文件，不允许重写已有文件
4. `DiffReviewer` 只对白名单目录内的新文件放行
5. 仍然禁止：
   - 删除文件
   - 覆盖已有文件
   - move / copy / rename
   - 通过 shell 直接改写已有源码

后续方向：

1. 保持默认 Bash 仍偏向只读诊断工具
2. 若后续仍需要更强粒度，再把当前 `Write(create_file_under=...)` 再下钻成更细的独立 `CreateFile` 工具名

### 2.3 当前代码中的一个已修复不一致

当前 `data/rule_profiles.json` 中仍有部分规则声明了 `create_file`：

- `csharpsquid:S107`
- `csharpsquid:S6960`

本轮对齐后，自动修复运行时：

1. 已把 `Write` 暴露为正式的一等 create-file-only issue-fix tool
2. 仍保留受控 Bash 在声明目录内创建新文件的兜底能力
3. `diff_reviewer` 继续对白名单目录外的 `file_created` 做硬拦截

因此，这部分配置现在已和运行时真实能力对齐，不再是“profile 声明了 `create_file`，但 agent 实际没有正式 create-file tool”的错位状态。

---

## 3. 实施优先级

### P0 先止血

1. 仓库能力指纹
2. 动态引擎路由
3. 官方 Sonar MCP 接入
4. PR 后 Sonar 闭环

### P1 再提效

1. 失败指纹驱动策略切换
2. changed-file 语义预检
3. prompt 瘦身
4. `S3776` archetype 收紧
5. 工具真相源统一

### P2 最后补基础设施

1. Roslyn fix engine 正式交付
2. 实施级测试矩阵
3. 可选的 `CreateFile` 一等能力

---

## 4. P0 详细实施清单

### P0-1 仓库能力指纹

当前状态：`已完成`

目标：

- 在 edit 前识别目标仓库的 .NET / C# 能力边界
- 阻止 `record` / `init` / `required` 这类不兼容修复

新增文件：

- `src/core/repo_capability.py`
- `tests/test_repo_capability.py`

需要修改：

- `src/core/run_coordinator.py`
- `src/core/issue_planner.py`
- `src/core/issue_prompt.py`
- `src/core/quality_gate_verifier.py`

建议数据结构：

```python
@dataclass(frozen=True)
class RepoCapabilityProfile:
    target_frameworks: tuple[str, ...]
    lang_version: str
    nullable: str
    implicit_usings: str
    supports_record: bool
    supports_init_only: bool
    supports_required: bool
    supports_file_scoped_namespace: bool
    supports_global_using: bool
    repo_family: str
    evidence_files: tuple[str, ...]
```

实施步骤：

1. 扫描 `*.csproj`、`Directory.Build.props`、`Directory.Build.targets`、`global.json`
2. 生成统一的 `RepoCapabilityProfile`
3. 在 clone 完仓库后立即检测，并写入：
   - `.pi-sonar-agent-runtime/repo_capability.json`
4. 在 planner 和 prompt 中注入能力摘要
5. 在 `quality_gate_verifier.py` 增加 `language_feature_compatibility` 硬门禁预检

验收标准：

1. `netcoreapp3.1` 仓库自动判定不支持 `record/init`
2. `S107` 不再生成 `record` / `init`
3. prompt 中会明确说明语言能力边界
4. 对 `net6.0` 或显式 `LangVersion=9+` 仓库可正确放开

---

### P0-2 动态引擎路由

当前状态：`已完成`

目标：

- 不再让高风险规则盲目掉入通用 agent fallback
- 把“能修 / 不能修 / 需要 Roslyn”作为显式决策

新增文件：

- `src/core/engine_router.py`
- `tests/test_engine_router.py`

需要修改：

- `src/core/run_coordinator.py`
- `src/core/issue_planner.py`
- `src/fixers/roslyn.py`
- `data/rule_profiles.json`

建议数据结构：

```python
@dataclass(frozen=True)
class EngineRoutingDecision:
    primary_engine: str
    resolved_engine: str
    fallback_allowed: bool
    fallback_reason: str
    skip_reason: str
    requires_roslyn: bool
    capability_blockers: tuple[str, ...]
```

实施步骤：

1. 引入路由层，不再只靠 `rule_profiles.json` 的静态 `primary_engine`
2. `S107` 改为：
   - Roslyn 可用则走 Roslyn
   - Roslyn 不可用则 skip
   - 暂不回退到 agent
3. `S3776` 继续用 agent，但限制默认 archetype
4. 日志和工件中写入 `engine_routing_decision`

验收标准：

1. Roslyn 缺失时，`S107` 不再进入 attempt
2. 跳过原因对外可见
3. issue summary 能明确区分：
   - 模型失败
   - 引擎不可用
   - 策略跳过

---

### P0-3 官方 Sonar MCP 只读接入

当前状态：`已完成`

目标：

- 让 Sonar 结构化上下文真正进 agent
- 用 MCP 替代一部分大 prompt 文本灌输

新增文件：

- `src/core/mcp_servers.py`
- `src/core/sonar_mcp_client.py`
- `tests/test_mcp_server_config.py`

需要修改：

- `src/agent/claude_agent.py`
- `src/core/tool_surface.py`
- `src/core/registry.py`

实施步骤：

1. 引入官方 Sonar MCP server 配置装配
2. 支持 `stdio` 或 `http` 模式
3. 默认启用只读模式
4. 为 workspace checkout 提供挂载路径
5. `claude_agent.py` 中把 `MCP_FIX_TOOLS` 改成动态加载结果
6. 日志中输出：
   - `mcp_servers`
   - `mcp_tools_count`
   - `mcp_mode`

注意：

- 不要把 git / push / PR 动作挂给 MCP
- MCP 只负责 Sonar 结构化上下文

验收标准：

1. 运行日志不再是 `mcp_servers: []`
2. agent 可调用 Sonar issue / rule / analysis 类只读工具
3. 主 prompt 长度下降

参考：

- `https://github.com/SonarSource/sonarqube-mcp-server`

---

### P0-4 PR 后 Sonar 闭环验证

当前状态：`暂不实施`

目标：

- PR 创建后，不再把“已发 PR”误当作“已修复”

说明：

- 根据当前实现决策，本项先挂起，不进入本轮代码实施范围。
- 文档保留设计和验收标准，后续若恢复实施，再按此章节继续推进。

新增文件：

- `src/core/post_pr_verifier.py`
- `tests/test_post_pr_verifier.py`

需要修改：

- `src/core/run_coordinator.py`
- `src/integrations/sonar.py`
- `src/integrations/ado.py`

建议能力：

```python
@dataclass(frozen=True)
class PostPRVerificationResult:
    pr_created: bool
    pipeline_completed: bool
    pipeline_succeeded: bool
    sonar_analysis_detected: bool
    issue_keys_closed: tuple[str, ...]
    issue_keys_remaining: tuple[str, ...]
    verification_status: str
    details: str
```

实施步骤：

1. PR 创建后等待 ADO pipeline 完成
2. 轮询 Sonar PR analysis 或 branch analysis
3. 回查本次 `issue_key` 是否关闭
4. 在 PR 描述、通知、最终运行摘要中体现真实关闭率

验收标准：

1. 最终结果能区分：
   - PR 已创建但 Sonar 未分析
   - Sonar 已分析且 issue 已关闭
   - Sonar 已分析但 issue 仍残留

---

## 5. P1 详细实施清单

### P1-1 失败指纹驱动策略切换

当前状态：`已完成`

目标：

- retry 不再只是重复尝试
- 根据失败类型切换 archetype 和约束

新增文件：

- `src/core/failure_fingerprint.py`
- `tests/test_failure_fingerprint.py`

需要修改：

- `src/core/retry_context.py`
- `src/core/issue_planner.py`
- `src/core/repair_plan.py`
- `src/core/fix_verifier.py`

建议失败指纹：

- `lang_feature_incompatible`
- `async_without_await`
- `signature_propagation_incomplete`
- `nullable_type_mismatch`
- `anonymous_type_leak`
- `helper_extraction_type_break`
- `public_surface_drift`
- `turn_exhausted_no_progress`

实施步骤：

1. 从 build log / quality gate / review gate 中提取结构化 fingerprint
2. 在 planner 中引入“失败 -> 策略切换表”
3. 连续两次相同指纹时强制切换 archetype
4. 连续三次相同指纹时 skip 并给结构化原因
5. 把 `semantic_precheck` blocker 同步写入 `RetryContext`，避免被 quality gate 文案覆盖
6. 当 `anonymous_type_helper_boundary` 命中时，下一轮强制切到 `signature_preserving_refactor -> expression_simplification`

验收标准：

1. 第二轮 attempt 会显式带上“禁止重复某类修法”
2. 同类问题不会连续三轮原样重复
3. `semantic_precheck` 失败会优先以语义阻塞原因进入下一轮 prompt，而不是被 quality gate 叙事盖掉

---

### P1-2 changed-file 语义预检

当前状态：`已完成`

目标：

- 在 full build 前就发现明显不兼容 patch

新增文件：

- `src/core/semantic_precheck.py`
- `tests/test_semantic_precheck.py`

需要修改：

- `src/core/fix_verifier.py`
- `src/core/attempt_scheduler.py`
- `src/core/quality_gate_verifier.py`

预检项建议：

1. 禁用语法特性
2. async 方法无 await
3. helper 参数/返回类型不匹配
4. 匿名类型跨方法边界泄露
5. public rename 传播未完成
6. nullable 泛型不兼容

建议顺序：

1. boundary
2. semantic_precheck
3. propagation
4. quality_gate
5. fast_compile
6. full_build

验收标准：

1. `record/init` 类问题在 full build 前就能拦截
2. 常见 helper extraction 失配能提前发现

---

### P1-3 prompt 瘦身

当前状态：`已完成`

目标：

- 从“大段说明书”切换到“短任务 + 外部引用”

新增文件：

- `tests/test_issue_prompt_size.py`

需要修改：

- `src/core/issue_prompt.py`
- `src/core/resource_loader.py`

实施步骤：

1. system prompt 控制在 6k chars 以内
2. user prompt 控制在 8k chars 左右
3. 质量门禁、repair plan、长规则说明默认外置
4. 保留：
   - issue facts
   - repo capability
   - edit contract
   - retry delta

新增工件建议：

- `prompt_budget_report.json`

已实现：

1. `IssuePromptBuilder` 已拆成预算化构建结果，支持 system/user prompt 长度统计
2. 复杂约束已自动外置到 `.pi-sonar-agent-runtime/sonar_fix_reference.md`
3. `ArtifactWriter` 已写出 `prompt_budget_report.json`
4. 已补 `tests/test_issue_prompt_size.py` 与 prompt/artifact 回归

验收标准：

1. 复杂 issue 的 system/user prompt 明显下降
2. 外置内容仍可按需读取

---

### P1-4 收紧 `S3776` archetype

当前状态：`已完成`

目标：

- 复杂度问题默认不再优先改公开签名

需要修改：

- `data/rule_profiles.json`
- `src/core/repair_plan.py`
- `src/core/issue_planner.py`

实施步骤：

1. `S3776` 默认 archetype 改成：
   - `guard_clause_flatten`
   - `private_helper_extract`
   - `local_block_reorder`
2. 只有在 propagation 闭包明确时才允许签名变化
3. 若目标是 public/interface/controller，则默认拒绝签名变化

已实现：

1. 默认 `S3776` 首轮已切到 `private_helper_extract -> guard_clause_flatten -> local_block_reorder`
2. public/interface/controller 传播已自动降级为保签名修法
3. 仅保留受控 internal 传播白名单
4. 已补 planner 回归覆盖默认单文件、public/interface 降级、internal 受控传播三类场景

验收标准：

1. `S3776` 首轮改动通常保持在单文件
2. interface/controller 被意外带上的比例下降

---

### P1-5 工具真相源统一

当前状态：`已完成`

目标：

- 模型看到的工具集合和运行时允许的工具集合完全一致

需要修改：

- `src/agent/claude_agent.py`
- `src/core/tool_surface.py`
- `src/core/registry.py`

建议：

- 新增统一 `build_visible_toolset(...)`
- 输出：
  - visible tools
  - hidden tools
  - disabled reasons

已实现：

1. 已在 `src/core/registry.py` 新增统一 `build_visible_toolset(...)`
2. `ClaudeFixAgent` 已把该快照同时用于 prompt、runtime allowlist 与 metadata
3. `visible_toolset` 已写入 artifact，便于事后审计

验收标准：

1. prompt
2. runtime allowlist
3. 日志输出

三者一致，不再分叉。

---

## 6. P2 详细实施清单

### P2-1 正式交付 Roslyn-routed `S107` 安全子集

当前状态：`已完成`

目标：

- 为结构性规则提供稳定的一等引擎

新增目录：

- `fix_engine/`
- `fix_engine/AgentFixEngine.csproj`
- `fix_engine/Rules/...`

第一批建议规则：

1. `S107`
2. `S1172`
3. `S4136`

实施要求：

1. 先做“可修 / 不可修”判定稳定
2. 再做补丁生成
3. `S107` 首版只支持 C# 8 安全参数对象
4. interface / override / public API 高风险场景直接返回 `cannot_fix_safely`

已实现：

1. 已新增可编译的 `fix_engine/AgentFixEngine.csproj`
2. 已交付 `S107` 首版安全分析器，稳定输出 `canFixSafely / safetyFlags`
3. 已新增 deterministic `S107` 参数对象补丁生成器，仅覆盖 `private/internal`、普通块体方法、C# 8 安全子集
4. `ClaudeFixAgent` 已把 Roslyn 安全候选接成 `patch -> reviewer -> semantic precheck -> build verification` 的完整闭环
5. public/interface/controller/override 等高风险场景仍保持结构化拒绝，不会回退到自由 agent 重构

验收标准：

1. `S107` 可稳定识别风险
2. 安全子集 case 已能自动产出参数对象补丁并通过本地 build 验证
3. 参数对象补丁不再依赖 agent 自由生成核心结构

---

### P2-2 补实施级测试矩阵

当前状态：`已完成`

目标：

- 避免后续每次改 planner/verifier 都靠跑批量碰运气

新增测试建议：

- `tests/test_repo_capability.py`
- `tests/test_engine_router.py`
- `tests/test_failure_fingerprint.py`
- `tests/test_semantic_precheck.py`
- `tests/test_issue_prompt_size.py`
- `tests/test_post_pr_verifier.py`

fixture 建议：

1. C# 8 老仓库
2. C# 10 新仓库
3. interface 传播高风险仓库
4. `record/init` 不兼容样例
5. async helper 无 await 样例
6. 匿名类型 helper 提取失败样例

已实现：

1. 已新增 `tests/fixtures/repos/netcoreapp31_legacy`
2. 已新增 `tests/fixtures/repos/s107_internal_candidate`
3. 已新增 `tests/fixtures/repos/s107_public_interface_risk`
4. 已补 fixture-based smoke tests 覆盖 repo capability、Roslyn S107、安全降级

验收标准：

1. P0 / P1 每项至少有 1 个自动化回归
2. issue 级小样本可以稳定回放

---

### P2-3 一等 `CreateFile` 能力

当前状态：`已完成`

目标：

- 仅为少数真正需要新文件的规则提供正式、可审计的新增文件能力
- **不是给 Bash 放开文件创建**

适用前提：

1. 规则本身天然需要新文件
2. reviewer、contract、quality gate 都能支持白名单创建
3. 只在少数规则开启

候选规则：

- `csharpsquid:S6960`
- 个别需要参数对象独立文件的 `S107`

新增文件建议：

- `src/core/create_file_policy.py`
- `tests/test_create_file_policy.py`

需要修改：

- `src/core/registry.py`
- `src/core/editor_policy.py`
- `src/core/policy.py`
- `src/core/diff_reviewer.py`
- `src/core/issue_contract.py`
- `src/core/issue_planner.py`

建议设计：

1. 已用 Claude 内建 `Write` 工具承接正式 create-file-only 能力
2. `EditContract` 已具备：
   - `allow_file_creation`
   - `allowed_new_file_roots`
3. `ToolPolicy` 只有在：
   - `Write(create_file_under=...)`
   - 且目标文件当前尚不存在
   时才允许 `Write`
4. `diff_reviewer` 只有在：
   - `allow_file_creation=True`
   - 且路径命中白名单
   时才允许 `file_created`
5. `Bash` 仍保留受控兜底，但已有文件修改仍禁止走 shell

验收标准：

1. 新文件能力可审计、可回放、可白名单控制
2. `Write` 只允许新建文件，不允许重写已有文件
3. 只对特定规则放开

---

## 7. 关于 Bash 是否要增加“新增文件”功能的最终建议

### 当前结论

当前阶段已按需求实施：

- **Bash 已支持受控“新增文件”能力。**
- **`Write` 已作为正式 create-file-only 工具接入 issue-fix runtime。**

但边界是：

1. 只有规则 profile 显式声明允许 `create_file` 时才会放开
2. 只有命中 `EditContract.allowed_new_file_roots` 的目录才允许创建
3. `Write` 只允许创建尚不存在的新文件
4. Bash 仅允许 bash-compatible 创建路径
5. 仍然禁止：
   - 删除文件
   - 覆盖已有文件
   - move / copy / rename
   - 通过 shell 直接改写已有源码

### 当前实现方式

本轮没有把新增文件塞成“无限制 Bash 写文件”，而是做成了受控能力：

1. `rule_profiles.json` 中声明了 `create_file` 的规则，会在 planner 中下沉为 `EditContract.allow_file_creation=True`
2. planner 会根据目标文件目录推导 `allowed_new_file_roots`
3. `ToolPolicy` 会对这些声明目录放行 `Write(create_file_under=...)`，且要求目标文件当前不存在
4. `ToolPolicy` 也会对同目录范围内的受控 Bash 新建文件命令放行
5. `DiffReviewer` 只对白名单目录内的新文件放行

### 后续方向

如果后续发现当前 `Write(create_file_under=...)` 语义仍然不够细，再继续把它拆成独立的 `CreateFile` 工具名；当前功能层面已经完成 `P2-3`。

---

## 8. 推荐排期

第 1 周：

1. `P0-1`
2. `P0-2`
3. `P0-3`

第 2 周：

1. `P0-4`
2. `P1-1`
3. `P1-2`

第 3 周：

1. `P1-3`
2. `P1-4`
3. `P1-5`

第 4 周及以后：

1. `P2-1`
2. `P2-2`
3. `P2-3`

---

## 9. 完成定义

每项任务完成必须满足：

1. 有代码实现
2. 有日志或工件可观测
3. 有至少 1 个自动化测试
4. 有一条 issue 级或批量回归结果
5. 最终 `issue_summary.json` 或运行摘要能体现该能力是否生效
