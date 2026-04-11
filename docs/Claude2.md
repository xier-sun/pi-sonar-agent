当前agent基于claudecodesdk开发，有没有 S3776这个规则以及编译失败的，有没有通用的方法论可以给codex让他实现

有。这两个问题背后有一个通用的方法论，不只适用于 S3776 和 build
  失败，而是适用于所有"修复需要跨符号/跨文件传播"的场景。

  核心方法论：Analyze-Before-Edit + Incremental Verify

  当前架构的根本问题是：

  生成修复 → 事后验证 → 失败重试 → 再失败 → 放弃

  应该改成：

  分析影响面 → 注入约束 → 生成受约束的修复 → 每步验证 → 完成

  区别不在于多了什么门禁，而在于把验证知识前移到生成阶段。

  给 Codex 的实现方案

  整体思路是在 ClaudeFixAgent.fix_issue() 的 prompt 构造和 tool 编排上做改造，不需要改 SDK 本身。

  方案一：Impact Analysis 前置（解决 build 失败）

  在模型开始 Edit 之前，强制插入一个"影响面分析"阶段。这个不是靠 prompt 说教，而是靠 tool 编排强制执行。

  # src/core/impact_analyzer.py

  class ImpactAnalyzer:
      """在 edit 前分析符号的引用图，生成 propagation contract"""

      def analyze(self, file_path: str, symbol_name: str, repo_root: str) -> ImpactReport:
          """
          用 grep 扫描符号的所有引用点，返回结构化报告。
          这个方法在 agent 调用 Edit 之前执行，结果注入 prompt。
          """
          references = self._grep_symbol(symbol_name, repo_root)

          return ImpactReport(
              symbol=symbol_name,
              definition_file=file_path,
              reference_files=references,          # 所有引用该符号的文件:行号
              interface_declarations=self._find_interface_decls(symbol_name, repo_root),
              override_chain=self._find_overrides(symbol_name, repo_root),
              total_sites=len(references),
          )

      def _grep_symbol(self, symbol: str, root: str) -> list[Reference]:
          # rg --no-heading -n "symbol_name" --type cs {root}
          # 解析输出为 [Reference(file, line, context)]
          ...

      def _find_interface_decls(self, symbol: str, root: str) -> list[str]:
          # 在 .cs 文件中搜索 interface 定义里包含该符号的声明
          ...

      def _find_overrides(self, symbol: str, root: str) -> list[str]:
          # 搜索 override/virtual 链
          ...

  关键：把 ImpactReport 注入到 system prompt 里，让模型在生成方案时就知道要改哪些地方。

  # src/agent/claude_agent.py — fix_issue 改造

  async def fix_issue(self, issue, context):
      # === 新增：如果 repair_plan 包含 signature_change ===
      if repair_plan.requires_signature_change:
          impact = ImpactAnalyzer().analyze(
              file_path=issue.file_path,
              symbol_name=repair_plan.target_symbol,
              repo_root=context.repo_root,
          )
          # 注入 prompt
          prompt_sections.append(self._format_impact_constraint(impact))

      # 原有流程继续...
      result = await self.runtime.run(messages, tools)

  def _format_impact_constraint(self, impact: ImpactReport) -> str:
      return f"""
  ## 签名变更传播约束（硬性要求）

  你计划修改的符号 `{impact.symbol}` 存在以下引用，全部必须同步更新：

  定义位置：{impact.definition_file}
  接口声明：{impact.interface_declarations}
  Override 链：{impact.override_chain}
  调用点（共 {impact.total_sites} 处）：
  {self._format_references(impact.reference_files)}

  规则：
  1. 如果你重命名该符号，上述所有位置必须在同一次修复中全部更新
  2. 如果传播范围超过 5 个文件，放弃重命名，改用不改签名的策略
  3. 每修改一个文件后，用 Bash grep 确认旧名称不再出现
  """

  方案二：Constraint-First Prompt（解决 quality gate 自我击穿）

  当前的问题是 quality gate 规则写在验证层，模型生成代码时不知道或不够重视。解决方法是把 quality gate
  规则转化为生成约束，而不只是验证规则。

  # src/core/constraint_compiler.py

  class ConstraintCompiler:
      """把 quality gate 规则编译成模型可执行的生成约束"""

      def compile_for_rule(self, sonar_rule: str, quality_gates: list[QualityRule]) -> str:
          """
          针对特定 sonar rule 的修复场景，
          把可能被触发的 quality gate 编译成 prompt 约束。
          """
          if sonar_rule == "csharpsquid:S3776":
              return self._compile_s3776_constraints(quality_gates)
          # 其他规则...
          return self._compile_generic_constraints(quality_gates)

      def _compile_s3776_constraints(self, gates: list) -> str:
          constraints = []

          # 从 quality gate 规则反推生成约束
          if self._has_gate("async_requires_await", gates):
              constraints.append(
                  "禁止创建标记为 async 但不包含 await 的方法。"
                  "如果提取的逻辑块不包含 await 调用，必须用同步 private 方法。"
              )

          if self._has_gate("public_xml_docs", gates):
              constraints.append(
                  "禁止引入新的 public/protected 成员。"
                  "所有提取的 helper 方法必须是 private。"
              )

          if self._has_gate("async_signature", gates):
              constraints.append(
                  "如果创建 async 方法，方法名必须以 Async 结尾。"
              )

          return "\n".join(f"- {c}" for c in constraints)

  在 prompt 里的呈现方式：

  ## 修复约束（违反任何一条将导致本次修复被拒绝）

  - 禁止创建标记为 async 但不包含 await 的方法。如果提取的逻辑块不包含 await 调用，必须用同步 private 方法。
  - 禁止引入新的 public/protected 成员。所有提取的 helper 方法必须是 private。
  - 如果创建 async 方法，方法名必须以 Async 结尾。
  - 不要改变原方法的 public 签名。

  方案三：Incremental Verify Loop（解决 no_change 和大方法 Edit 失败）

  这是最通用的一个改造。当前是"模型做完所有 edit → 一次性验证"，改成"每个 edit 后立即验证"。

  在 Claude Code SDK 里，这可以通过自定义 tool handler 实现：

  # src/core/verified_edit.py

  class VerifiedEditTool:
      """
      包装原生 Edit tool，在每次 edit 后自动执行验证。
      作为 custom tool 注册到 Claude SDK。
      """

      def __init__(self, build_gate, quality_gate, impact_report=None):
          self.build_gate = build_gate
          self.quality_gate = quality_gate
          self.impact = impact_report
          self.edit_history = []

      async def execute(self, file_path: str, old_string: str, new_string: str) -> dict:
          # 1. 执行 edit
          result = apply_edit(file_path, old_string, new_string)

          if not result.success:
              # Edit 失败时，提供精确的失败上下文
              return {
                  "success": False,
                  "error": "old_string not found",
                  "hint": self._suggest_fix(file_path, old_string),
                  # 给模型看附近的实际内容，帮它修正 old_string
                  "nearby_content": self._get_nearby_lines(file_path, old_string),
              }

          self.edit_history.append(result)

          # 2. 如果有 impact report，检查传播完整性
          if self.impact and result.changes_symbol:
              residual = self._check_old_symbol_residual()
              if residual:
                  return {
                      "success": True,
                      "warning": f"旧符号名仍在以下位置残留，必须继续更新：\n{residual}",
                      "propagation_remaining": len(residual),
                  }

          # 3. 快速语法检查（不跑完整 build，只检查当前文件能否解析）
          syntax_ok = self._quick_syntax_check(file_path)
          if not syntax_ok:
              return {
                  "success": True,
                  "warning": "当前文件存在语法错误，请检查修改是否完整",
                  "syntax_errors": syntax_ok.errors,
              }

          return {"success": True}

      def _suggest_fix(self, file_path, old_string):
          """当 old_string 匹配失败时，用模糊匹配找到最接近的代码段"""
          content = read_file(file_path)
          # 用 difflib.get_close_matches 或简单的行级匹配
          # 返回实际文件中最接近 old_string 的片段
          ...

      def _check_old_symbol_residual(self):
          """grep 检查旧符号名是否还残留"""
          ...

  方案四：Strategy Degradation（通用降级策略）

  这是最重要的通用方法论。当复杂策略失败时，自动降级到更简单的策略，而不是用同样的策略 retry 3 次。