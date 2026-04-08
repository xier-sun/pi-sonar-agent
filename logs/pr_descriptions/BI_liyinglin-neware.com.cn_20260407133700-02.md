自动修复 SonarQube issues

## 运行概览
- 作者: liyinglin@neware.com.cn
- 基线分支: develop
- 最终构建: 通过
- 成功: 18
- 跳过: 23
- 失败: 0
- 构建命令: dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"
- 解决方案: OpenAuth.Core/OpenAuth.Core.WebApi.sln
- 测试命令: None

## 审阅提示
- 本 PR 只包含最终构建验证通过的修复。
- 被跳过或失败的 issue 已自动回滚，不包含在当前提交中。
- 建议优先审阅这些文件: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs, OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs, OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs, OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs, OpenAuth.Core/OpenAuth.App/Finance/PenaltyCalculationService.cs, OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotApp.cs, OpenAuth.Core/OpenAuth.App/Finance/Request/SalesInvoiceReq.cs, OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs, OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs, OpenAuth.Core/OpenAuth.App/Finance/Reimburse/Services/ReimburseCommonService.cs

## 已修复 Issues
1. csharpsquid:S1481
   - Issue Key: ea6ad3ea-665d-4e2f-9a3d-cc84d3f9f1c4
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs:2224
   - Sonar 问题: Remove the unused local variable 'slpDict'.
   - 尝试次数: 3
   - 处理结果: 经过 3 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
2. csharpsquid:S1144
   - Issue Key: 1e604e34-4ff3-456f-a21b-cd9e5ebb556c
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs:2394
   - Sonar 问题: Remove the unused private method 'AddReceiptsAsync'.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
3. csharpsquid:S3776
   - Issue Key: 0fb3e377-0955-41cb-8cae-729a28012d3c
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs:171
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 31 to the 30 allowed.
   - 尝试次数: 2
   - 处理结果: 经过 2 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
4. csharpsquid:S125
   - Issue Key: b795a0d6-cb47-4cf1-9bad-ea490a739052
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:62
   - Sonar 问题: Remove this commented out code.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
5. csharpsquid:S3358
   - Issue Key: 996dd759-d30a-4041-9cb5-0ccfbe463173
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:92
   - Sonar 问题: Extract this nested ternary operation into an independent statement.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
6. csharpsquid:S3358
   - Issue Key: eb636b43-4590-4664-8b69-40894b98b646
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:93
   - Sonar 问题: Extract this nested ternary operation into an independent statement.
   - 尝试次数: 2
   - 处理结果: 经过 2 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
7. csharpsquid:S6562
   - Issue Key: 3b716a5c-3460-4183-8d7e-820d2e11387f
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs:74
   - Sonar 问题: Provide the "DateTimeKind" when creating this object.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
8. csharpsquid:S3776
   - Issue Key: ea97b5e2-bca2-42b2-b678-a91347eca09d
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/PenaltyCalculationService.cs:51
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 38 to the 30 allowed.
   - 尝试次数: 2
   - 处理结果: 经过 2 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/PenaltyCalculationService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
9. csharpsquid:S3776
   - Issue Key: c8c4ea2e-fd35-40f6-a859-dc9ed80884e1
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotApp.cs:195
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 32 to the 30 allowed.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
10. external_roslyn:CS1591
   - Issue Key: 91ed2ea4-a21a-4f0b-8ff3-957435fb0a86
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/Request/SalesInvoiceReq.cs:263
   - Sonar 问题: 缺少对公共可见类型或成员“SalesInvocieExportReq”的 XML 注释
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/Request/SalesInvoiceReq.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
11. csharpsquid:S3776
   - Issue Key: dcf25c55-d4c8-425e-a0ee-840a500b4806
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1127
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 55 to the 30 allowed.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
12. external_roslyn:CS1591
   - Issue Key: 20621c0b-e1ba-4aa6-92f3-a8ad7c144f3c
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:467
   - Sonar 问题: 缺少对公共可见类型或成员“HistoricalOverdueImportService.GetBatchRefundHistoryInternal(IUnitWork, List<int>)”的 XML 注释
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
13. csharpsquid:S3776
   - Issue Key: e0df305b-afab-45c6-bd45-641d1ca20b66
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs:457
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 67 to the 30 allowed.
   - 尝试次数: 3
   - 处理结果: 经过 3 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
14. csharpsquid:S3776
   - Issue Key: c2c11128-8890-4742-9c7b-cf2ffb04071a
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/Reimburse/Services/ReimburseCommonService.cs:856
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 46 to the 30 allowed.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/Reimburse/Services/ReimburseCommonService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
15. csharpsquid:S3776
   - Issue Key: fd483c64-1218-485f-9343-30ef2a2b530f
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:962
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 55 to the 30 allowed.
   - 尝试次数: 2
   - 处理结果: 经过 2 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
16. csharpsquid:S3459
   - Issue Key: 1ae6ad1e-ceaf-4278-9248-10e33b961c7d
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs:436
   - Sonar 问题: Remove unassigned auto-property 'Rate', or set its value.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
17. csharpsquid:S3459
   - Issue Key: 1dafaa15-8579-4500-a094-9a7be8eeb098
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs:436
   - Sonar 问题: Remove unassigned auto-property 'Min', or set its value.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
18. csharpsquid:S3459
   - Issue Key: f08f2b1b-a660-4681-8f8e-047fefc21c7b
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs:436
   - Sonar 问题: Remove unassigned auto-property 'Max', or set its value.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。

## 已跳过 Issues
1. csharpsquid:S125
   - Issue Key: 35c291f7-fcf3-4485-a230-412e295cf88e
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs:2228
   - Sonar 问题: Remove this commented out code.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Issue changes exceeded allowed scope after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_35c291f7-fcf3-4485-a230-412e295cf88e_20260407133700-02.log
2. csharpsquid:S3776
   - Issue Key: a22e301e-b1e0-4a1f-9bec-712001f1a11a
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:41
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 34 to the 30 allowed.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Build verification failed after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_a22e301e-b1e0-4a1f-9bec-712001f1a11a_20260407133700-02.log
3. csharpsquid:S125
   - Issue Key: bcdc9839-b1ff-4fa2-a5e0-7e3d6ce6b4a1
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:118
   - Sonar 问题: Remove this commented out code.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Issue changes exceeded allowed scope after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_bcdc9839-b1ff-4fa2-a5e0-7e3d6ce6b4a1_20260407133700-02.log
4. csharpsquid:S107
   - Issue Key: e32e70ac-6c4d-484b-8eb2-78f115e2a23b
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:2622
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_e32e70ac-6c4d-484b-8eb2-78f115e2a23b_20260407133700-02.log
5. csharpsquid:S107
   - Issue Key: e5f957f4-1ab3-45d8-a7d0-21b9a4ecefcd
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:2648
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_e5f957f4-1ab3-45d8-a7d0-21b9a4ecefcd_20260407133700-02.log
6. csharpsquid:S107
   - Issue Key: f2f04149-8a3d-4915-a52a-f0098f141d5a
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotImportService.cs:188
   - Sonar 问题: Method has 11 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_f2f04149-8a3d-4915-a52a-f0098f141d5a_20260407133700-02.log
7. csharpsquid:S107
   - Issue Key: c9376b94-63fd-4229-88e9-54dd45bddaae
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs:43
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_c9376b94-63fd-4229-88e9-54dd45bddaae_20260407133700-02.log
8. csharpsquid:S107
   - Issue Key: 91335541-7084-45c2-a0ba-0ac021ce489d
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs:137
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_91335541-7084-45c2-a0ba-0ac021ce489d_20260407133700-02.log
9. csharpsquid:S107
   - Issue Key: a805790d-f60d-4121-b50e-9df270aa71db
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:507
   - Sonar 问题: Method has 14 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_a805790d-f60d-4121-b50e-9df270aa71db_20260407133700-02.log
10. csharpsquid:S107
   - Issue Key: b7a40bd3-56f4-4edc-b0df-3ff389a5014f
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:554
   - Sonar 问题: Method has 16 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_b7a40bd3-56f4-4edc-b0df-3ff389a5014f_20260407133700-02.log
11. csharpsquid:S3776
   - Issue Key: 56dee597-da49-46c5-9b12-62df7ba3ab97
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1504
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 32 to the 30 allowed.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Build verification failed after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_56dee597-da49-46c5-9b12-62df7ba3ab97_20260407133700-02.log
12. csharpsquid:S107
   - Issue Key: d48b8eef-2eb6-4206-b6df-289bb4929256
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1504
   - Sonar 问题: Method has 15 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_d48b8eef-2eb6-4206-b6df-289bb4929256_20260407133700-02.log
13. csharpsquid:S107
   - Issue Key: 609d2d1d-9dbc-4bc2-9900-87f9b9d45cc7
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1778
   - Sonar 问题: Method has 17 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_609d2d1d-9dbc-4bc2-9900-87f9b9d45cc7_20260407133700-02.log
14. csharpsquid:S3776
   - Issue Key: e536a516-dac6-4cc1-b3df-301e21acdc94
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1778
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 184 to the 30 allowed.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Build verification failed after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_e536a516-dac6-4cc1-b3df-301e21acdc94_20260407133700-02.log
15. csharpsquid:S107
   - Issue Key: ac012e8f-bd39-4bc9-8e92-f81c464aae5d
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:2270
   - Sonar 问题: Method has 18 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_ac012e8f-bd39-4bc9-8e92-f81c464aae5d_20260407133700-02.log
16. csharpsquid:S6960
   - Issue Key: 27eed645-6b54-4c88-aeca-707ba20429d3
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.WebApi/Controllers/Finance/HistoricalImportController.cs:24
   - Sonar 问题: This controller has multiple responsibilities and could be split into 4 smaller controllers.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S6960 默认跳过：Controller 职责拆分属于架构调整，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_27eed645-6b54-4c88-aeca-707ba20429d3_20260407133700-02.log
17. csharpsquid:S107
   - Issue Key: a6cc9ceb-a570-4768-8cca-8ff680911d13
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs:804
   - Sonar 问题: Method has 10 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_a6cc9ceb-a570-4768-8cca-8ff680911d13_20260407133700-02.log
18. csharpsquid:S107
   - Issue Key: bbd7c135-ef72-4de4-bc0f-b7f36e008062
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs:869
   - Sonar 问题: Method has 10 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_bbd7c135-ef72-4de4-bc0f-b7f36e008062_20260407133700-02.log
19. csharpsquid:S1144
   - Issue Key: bed2ec1c-8990-4b9f-aae6-4ae12fcc4fa6
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:942
   - Sonar 问题: Remove the unused private set accessor in property 'RemarkDic'.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Issue changes exceeded allowed scope after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_bed2ec1c-8990-4b9f-aae6-4ae12fcc4fa6_20260407133700-02.log
20. csharpsquid:S3459
   - Issue Key: cb88142f-fc41-45bc-9a2d-6a094c7d9af6
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:942
   - Sonar 问题: Remove unassigned auto-property 'RemarkDic', or set its value.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Issue changes exceeded allowed scope after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_cb88142f-fc41-45bc-9a2d-6a094c7d9af6_20260407133700-02.log
21. csharpsquid:S1144
   - Issue Key: ec828433-949d-42c5-80e7-503b3c385ea5
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:943
   - Sonar 问题: Remove the unused private property 'BatchSaleOrderRemark'.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Issue changes exceeded allowed scope after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_ec828433-949d-42c5-80e7-503b3c385ea5_20260407133700-02.log
22. csharpsquid:S3776
   - Issue Key: 052c0359-2d63-4dc6-993e-fefa2c4faa83
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:2270
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 55 to the 30 allowed.
   - 尝试次数: 3
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 跳过原因: Issue changes exceeded allowed scope after 3 attempt(s)
   - 重试日志: logs\issue_attempts\BI_052c0359-2d63-4dc6-993e-fefa2c4faa83_20260407133700-02.log
23. csharpsquid:S107
   - Issue Key: 545b2297-c21d-4d89-800a-842b0066489a
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalUnTransportImportService.cs:341
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_545b2297-c21d-4d89-800a-842b0066489a_20260407133700-02.log

## 失败 Issues
- 无失败 issue
