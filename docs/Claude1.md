这份文档写得很扎实，问题定位也很清晰。我来逐一分析 7 个真实失败点，给出针对性的优化方案。

  核心诊断

  你的 agent 本质上卡在一个矛盾上：S3776（认知复杂度）的修复天然需要"拆方法 + 改签名 +
  跨文件传播"，但门禁体系是按"最小改动、不引入新债"设计的。 planner 想做大手术，contract 只给小刀，quality gate
  还在旁边挑毛病。

  按失败类型给方案

  1. Build 失败（A类，2个）— 跨签名传播做了一半

  问题根因：模型把 AutoPlugin 改成 AutoPluginAsync，但没有完整传播到所有 callsite 和接口定义。

  方案：引入 "propagation checklist" 机制

  在 RepairPlan 阶段：
  1. 如果 plan 包含 signature_change，强制生成 propagation_targets 列表
  2. 列表必须包含：接口定义、所有 callsite、override/virtual 链
  3. 在 FixVerifier 里增加一个 propagation_completeness_check：
     - 用 grep/roslyn 扫描旧签名是否还残留
     - 如果残留 → 直接判定 attempt 失败，不等 build
     - 把残留位置写入 RetryContext.lessons

  这比等 build 报 CS0103 再 retry 要快得多，而且给模型的 retry hint 更精确。

  2. Quality Gate 失败（B类，3个）— 修主问题引入新规范债

  这是最值得优化的一类。模型为了降复杂度，提取了 async helper，但：
  - helper 没有真实 await → 违反 async_requires_await
  - 新 public member 没加 XML docs → 违反 public_xml_docs
  - async 方法没加 Async 后缀 → 违反 async_signature

  方案：在 planner 阶段做 "quality gate 预算"

  # issue_planner.py 里，生成 RepairPlan 后增加一步
  def precheck_quality_gate_cost(repair_plan, quality_rules):
      risks = []
      if repair_plan.introduces_async_helper:
          if "async_requires_await" in quality_rules:
              risks.append("新 async helper 必须包含真实 await，否则改用同步 private method")
          if "async_signature" in quality_rules:
              risks.append("async 方法必须以 Async 结尾")
      if repair_plan.introduces_public_symbol:
          if "public_xml_docs" in quality_rules:
              risks.append("新 public 成员必须有 XML doc，建议改用 private")

      # 把 risks 注入 prompt 的 repair_plan 区域
      repair_plan.quality_gate_warnings = risks

  关键点：不是在事后验证时拦截，而是在 prompt 构造时就告诉模型 "如果你要提 async helper，必须满足这些条件，否则用同步
  private method"。

  更进一步，可以加一条硬规则到 prompt：

  ▎ 当修复 S3776 时，优先提取 private 同步方法。只有当原方法本身是 async 且提取的逻辑包含 await 调用时，才允许提取 async
   helper。

  3. Plan Conflict（C类，1个）— planner 知道要改签名，contract 不给

  这个最直接。当前 EditContract.allowed_capabilities 只有 method_rewrite + helper_extract，但 RepairPlan 判断需要
  signature_change。

  方案：capability 自动升级协议

  # issue_contract.py
  def reconcile_plan_and_contract(repair_plan, contract):
      needed = repair_plan.required_capabilities
      allowed = contract.allowed_capabilities

      gap = needed - allowed

      # 定义可自动升级的 capability 及其前置条件
      auto_upgradeable = {
          "signature_change": lambda: (
              repair_plan.has_propagation_targets
              and len(repair_plan.propagation_targets) <= 3  # 传播范围可控
          ),
          "multi_file_refactor": lambda: (
              len(repair_plan.affected_files) <= 2  # 最多跨2个文件
          ),
      }

      for cap in gap:
          if cap in auto_upgradeable and auto_upgradeable[cap]():
              contract.allowed_capabilities.add(cap)
              contract.auto_upgraded.append(cap)  # 记录审计
          else:
              return PlanConflict(f"需要 {cap} 但不满足自动升级条件")

  这样就不会出现 "系统已经知道修法，但不给修" 的死锁。同时通过传播范围限制来控制风险。

  4. No-change（D类，1个）— 大方法 Edit 落盘失败

  这是 String to replace not found in file 问题。1778 行的大方法，模型构造的 old_string 跟实际文件内容对不上。

  方案：分段 Edit 策略 + fallback 到行号定位

  # editor_policy.py — 针对大方法的 edit 策略
  def get_edit_strategy(method_line_count, repair_plan):
      if method_line_count > 80:
          return EditStrategy(
              mode="chunked",
              max_old_string_lines=20,      # 单次替换不超过20行
              require_anchor_context=True,   # old_string 必须包含前后各2行锚点
              fallback="line_range_replace", # 如果精确匹配失败，用行号范围
          )
      return EditStrategy(mode="standard")

  同时在 prompt 里对大方法场景加约束：

  ▎ 对于超过 80 行的方法，将重构拆成多个小 Edit 操作。每次 Edit 的 old_string 不超过 20
  行，并包含足够的上下文锚点（前后各 2 行不变的代码）。不要尝试一次替换整个方法体。

  5. S3776 专项 — "最小安全拆分模板"

  这是文档 6.3 第 5 点提到的，也是最根本的改进。当前大部分失败都集中在 S3776。

  方案：为 S3776 建立固定的拆分策略模板

  # data/rule_templates/S3776_repair_template.md

  ## S3776 修复策略（按优先级）

  ### 策略1：提取 private 同步方法（首选）
  - 适用：方法内有独立的逻辑块（if-else 分支、循环体、switch case）
  - 做法：把逻辑块提取为 private 方法，保持原签名不变
  - 约束：
    - 新方法必须是 private
    - 新方法不需要 XML doc
    - 如果原方法不是 async，新方法也不能是 async
    - 参数不超过 4 个，超过则用局部变量或 tuple

  ### 策略2：提取 private async 方法（仅当必要）
  - 适用：原方法是 async，且提取的逻辑块包含 await 调用
  - 约束：
    - 方法名必须以 Async 结尾
    - 方法体必须包含至少一个 await
    - 必须是 private

  ### 策略3：简化条件逻辑（不提取方法）
  - 适用：复杂度主要来自嵌套 if/else
  - 做法：early return、guard clause、合并条件

  ### 禁止：
  - 不要改变 public 方法签名
  - 不要引入新的 public 成员
  - 不要创建新文件
  - 不要改变方法的 async/sync 性质

  把这个模板在 IssuePlanner 里针对 S3776 自动加载，注入到 prompt 和 RepairPlan 的约束里。

  总结

  改动优先级：

  1. S3776 拆分模板 + quality gate 预算注入 prompt — 解决 B 类和 D 类，覆盖 4/7 个失败
  2. capability 自动升级协议 — 解决 C 类，消除 plan_conflict 死锁
  3. propagation completeness check — 解决 A 类，在 build 前就拦截不完整传播
  4. 大方法分段 Edit 策略 — 降低 String to replace not found 概率

  核心思路就一句话：把门禁的知识前移到 planner 和 prompt
  里，让模型在生成方案时就知道边界在哪，而不是生成完了再被门禁打回来反复 retry。