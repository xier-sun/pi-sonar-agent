自动修复 SonarQube issues

## 运行概览
- 作者: liyinglin@neware.com.cn
- 基线分支: develop
- 最终构建: 通过
- 成功: 21
- 跳过: 19
- 失败: 0
- 构建命令: dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"
- 解决方案: OpenAuth.Core/OpenAuth.Core.WebApi.sln
- 测试命令: None

## 审阅提示
- 本 PR 只包含最终构建验证通过的修复。
- 被跳过或失败的 issue 已自动回滚，不包含在当前提交中。
- 建议优先审阅这些文件: OpenAuth.Core/OpenAuth.App/HumanResource/Employees/JobAbilityApp.cs, OpenAuth.Core/OpenAuth.App/HumanResource/Interfaces/IJobAbilityApp.cs, OpenAuth.Core/OpenAuth.WebApi/Controllers/HumanResource/Employee/JobAbilityController.cs, OpenAuth.Core/OpenAuth.App/Finance/FinanceAfterSaleBonusApplicationApp.cs, OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs, OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs, OpenAuth.Core/OpenAuth.App/Finance/Interfaces/IFinanceHanlerApp.cs, OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs, OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs, OpenAuth.Core/OpenAuth.App/Finance/Interfaces/IPenaltyCalculationService.cs, OpenAuth.Core/OpenAuth.App/Finance/PenaltyCalculationService.cs, OpenAuth.Core/OpenAuth.App/Jobs/SlpPenalty/PenaltyCalculationJob.cs, OpenAuth.Core/OpenAuth.WebApi/Controllers/Finance/PenaltyController.cs, OpenAuth.Core/OpenAuth.App/Finance/Request/SalesInvoiceReq.cs, OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotImportService.cs, OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs, OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs

## 已修复 Issues
1. csharpsquid:S3776
   - Issue Key: 3cc92b32-9fe1-440a-a8b5-173c53007ac8
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/Employees/JobAbilityApp.cs:1170
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 50 to the 30 allowed.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/Employees/JobAbilityApp.cs, OpenAuth.Core/OpenAuth.App/HumanResource/Interfaces/IJobAbilityApp.cs, OpenAuth.Core/OpenAuth.WebApi/Controllers/HumanResource/Employee/JobAbilityController.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 6 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=6
   - 边界审计: drift score=2 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/HumanResource/Interfaces/IJobAbilityApp.cs 主区域外变更: 88；OpenAuth.Core/OpenAuth.WebApi/Controllers/HumanResource/Employee/JobAbilityController.cs 主区域外变更: 188, 189, 190
2. csharpsquid:S1481
   - Issue Key: 9891d18d-be6f-4b50-a233-95f73d451c9a
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/FinanceAfterSaleBonusApplicationApp.cs:2373
   - Sonar 问题: Remove the unused local variable 'deliveryDocEntrys'.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/FinanceAfterSaleBonusApplicationApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | No post-edit changed lines were available for quality-gate review.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/FinanceAfterSaleBonusApplicationApp.cs 主区域外变更: 2374
3. csharpsquid:S1481
   - Issue Key: ea6ad3ea-665d-4e2f-9a3d-cc84d3f9f1c4
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs:2223
   - Sonar 问题: Remove the unused local variable 'slpDict'.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
4. csharpsquid:S125
   - Issue Key: 35c291f7-fcf3-4485-a230-412e295cf88e
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs:2227
   - Sonar 问题: Remove this commented out code.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | No post-edit changed lines were available for quality-gate review.
5. csharpsquid:S1144
   - Issue Key: 1e604e34-4ff3-456f-a21b-cd9e5ebb556c
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs:2393
   - Sonar 问题: Remove the unused private method 'AddReceiptsAsync'.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | No post-edit changed lines were available for quality-gate review.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/FinanceHomeApp.cs 主区域外变更: 2389, 2412, 2438, 2476
6. csharpsquid:S3776
   - Issue Key: 0fb3e377-0955-41cb-8cae-729a28012d3c
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs:171
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 31 to the 30 allowed.
   - 尝试次数: 2
   - 处理结果: 经过 2 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs, OpenAuth.Core/OpenAuth.App/Finance/Interfaces/IFinanceHanlerApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 2 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=2
   - 边界审计: drift score=2 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/FinanceHanlerApp.cs 主区域外变更: 80；OpenAuth.Core/OpenAuth.App/Finance/Interfaces/IFinanceHanlerApp.cs 主区域外变更: 20
7. csharpsquid:S125
   - Issue Key: b795a0d6-cb47-4cf1-9bad-ea490a739052
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:62
   - Sonar 问题: Remove this commented out code.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
8. csharpsquid:S3358
   - Issue Key: 996dd759-d30a-4041-9cb5-0ccfbe463173
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:92
   - Sonar 问题: Extract this nested ternary operation into an independent statement.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
9. csharpsquid:S3358
   - Issue Key: eb636b43-4590-4664-8b69-40894b98b646
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:93
   - Sonar 问题: Extract this nested ternary operation into an independent statement.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs 主区域外变更: 89
10. csharpsquid:S125
   - Issue Key: bcdc9839-b1ff-4fa2-a5e0-7e3d6ce6b4a1
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:118
   - Sonar 问题: Remove this commented out code.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | No post-edit changed lines were available for quality-gate review.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs 主区域外变更: 127
11. csharpsquid:S6562
   - Issue Key: 3b716a5c-3460-4183-8d7e-820d2e11387f
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs:74
   - Sonar 问题: Provide the "DateTimeKind" when creating this object.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/UnTransportDailyNPercentProcessor.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
12. csharpsquid:S3776
   - Issue Key: ea97b5e2-bca2-42b2-b678-a91347eca09d
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/PenaltyCalculationService.cs:51
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 38 to the 30 allowed.
   - 尝试次数: 2
   - 处理结果: 经过 2 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/Interfaces/IPenaltyCalculationService.cs, OpenAuth.Core/OpenAuth.App/Finance/PenaltyCalculationService.cs, OpenAuth.Core/OpenAuth.App/Jobs/SlpPenalty/PenaltyCalculationJob.cs, OpenAuth.Core/OpenAuth.WebApi/Controllers/Finance/PenaltyController.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
   - 边界审计: drift score=2 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/Interfaces/IPenaltyCalculationService.cs 主区域外变更: 17；OpenAuth.Core/OpenAuth.App/Jobs/SlpPenalty/PenaltyCalculationJob.cs 主区域外变更: 34
13. external_roslyn:CS1591
   - Issue Key: 91ed2ea4-a21a-4f0b-8ff3-957435fb0a86
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/Request/SalesInvoiceReq.cs:263
   - Sonar 问题: 缺少对公共可见类型或成员“SalesInvocieExportReq”的 XML 注释
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/Request/SalesInvoiceReq.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 2 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=2
14. csharpsquid:S3776
   - Issue Key: dcf25c55-d4c8-425e-a0ee-840a500b4806
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1127
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 55 to the 30 allowed.
   - 尝试次数: 3
   - 处理结果: 经过 3 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotImportService.cs, OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 9 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=9
   - 边界审计: drift score=2 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotImportService.cs 主区域外变更: 140；OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs 主区域外变更: 271
15. external_roslyn:CS1591
   - Issue Key: 20621c0b-e1ba-4aa6-92f3-a8ad7c144f3c
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:467
   - Sonar 问题: 缺少对公共可见类型或成员“HistoricalOverdueImportService.GetBatchRefundHistoryInternal(IUnitWork, List<int>)”的 XML 注释
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 2 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=2
16. csharpsquid:S3776
   - Issue Key: fd483c64-1218-485f-9343-30ef2a2b530f
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:962
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 55 to the 30 allowed.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 3 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=3
17. csharpsquid:S3776
   - Issue Key: 56dee597-da49-46c5-9b12-62df7ba3ab97
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1504
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 32 to the 30 allowed.
   - 尝试次数: 2
   - 处理结果: 经过 2 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs 主区域外变更: 1480
18. csharpsquid:S3776
   - Issue Key: e536a516-dac6-4cc1-b3df-301e21acdc94
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1778
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 184 to the 30 allowed.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 2 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=2
19. csharpsquid:S3459
   - Issue Key: cb88142f-fc41-45bc-9a2d-6a094c7d9af6
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:942
   - Sonar 问题: Remove unassigned auto-property 'RemarkDic', or set its value.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs 主区域外变更: 876
20. csharpsquid:S1144
   - Issue Key: ec828433-949d-42c5-80e7-503b3c385ea5
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:943
   - Sonar 问题: Remove the unused private property 'BatchSaleOrderRemark'.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | No post-edit changed lines were available for quality-gate review.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs 主区域外变更: 951
21. csharpsquid:S3459
   - Issue Key: 1ae6ad1e-ceaf-4278-9248-10e33b961c7d
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs:436
   - Sonar 问题: Remove unassigned auto-property 'Rate', or set its value.
   - 尝试次数: 2
   - 处理结果: 经过 2 次尝试后，已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 8 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=8
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs 主区域外变更: 437

## 已跳过 Issues
1. csharpsquid:S3776
   - Issue Key: a22e301e-b1e0-4a1f-9bec-712001f1a11a
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReturnUnRelNewOrderProcessor.cs:41
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 34 to the 30 allowed.
   - 尝试次数: 5
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: retry | Quality gate rejected the patch with 1 hard violation(s).
   - 规范细项: hard failures=1, soft findings=5
   - 跳过原因: C# quality gate verification failed after 5 attempt(s)
   - 重试日志: logs\issue_attempts\BI_a22e301e-b1e0-4a1f-9bec-712001f1a11a_20260411212107-01.log
2. csharpsquid:S3776
   - Issue Key: c8c4ea2e-fd35-40f6-a859-dc9ed80884e1
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotApp.cs:195
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 32 to the 30 allowed.
   - 尝试次数: 5
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | All active quality gates passed for this attempt.
   - 跳过原因: Forbidden tool usage polluted the issue attempt after 5 attempt(s)
   - 重试日志: logs\issue_attempts\BI_c8c4ea2e-fd35-40f6-a859-dc9ed80884e1_20260411212107-01.log
3. csharpsquid:S107
   - Issue Key: e32e70ac-6c4d-484b-8eb2-78f115e2a23b
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:2622
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_e32e70ac-6c4d-484b-8eb2-78f115e2a23b_20260411212107-01.log
4. csharpsquid:S107
   - Issue Key: e5f957f4-1ab3-45d8-a7d0-21b9a4ecefcd
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:2648
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_e5f957f4-1ab3-45d8-a7d0-21b9a4ecefcd_20260411212107-01.log
5. csharpsquid:S107
   - Issue Key: f2f04149-8a3d-4915-a52a-f0098f141d5a
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/DailyOverdueSnapshotImportService.cs:188
   - Sonar 问题: Method has 11 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_f2f04149-8a3d-4915-a52a-f0098f141d5a_20260411212107-01.log
6. csharpsquid:S3776
   - Issue Key: e0df305b-afab-45c6-bd45-641d1ca20b66
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalAfterBillOverdueImportService.cs:457
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 67 to the 30 allowed.
   - 尝试次数: 5
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | All active quality gates passed for this attempt.
   - 跳过原因: Agent completed without modifying any files after 5 attempt(s)
   - 重试日志: logs\issue_attempts\BI_e0df305b-afab-45c6-bd45-641d1ca20b66_20260411212107-01.log
7. csharpsquid:S3776
   - Issue Key: c2c11128-8890-4742-9c7b-cf2ffb04071a
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/Reimburse/Services/ReimburseCommonService.cs:880
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 46 to the 30 allowed.
   - 尝试次数: 5
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | All active quality gates passed for this attempt.
   - 跳过原因: Agent completed without modifying any files after 5 attempt(s)
   - 重试日志: logs\issue_attempts\BI_c2c11128-8890-4742-9c7b-cf2ffb04071a_20260411212107-01.log
8. csharpsquid:S107
   - Issue Key: c9376b94-63fd-4229-88e9-54dd45bddaae
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs:43
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_c9376b94-63fd-4229-88e9-54dd45bddaae_20260411212107-01.log
9. csharpsquid:S107
   - Issue Key: 91335541-7084-45c2-a0ba-0ac021ce489d
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs:137
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_91335541-7084-45c2-a0ba-0ac021ce489d_20260411212107-01.log
10. csharpsquid:S107
   - Issue Key: a805790d-f60d-4121-b50e-9df270aa71db
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:507
   - Sonar 问题: Method has 14 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_a805790d-f60d-4121-b50e-9df270aa71db_20260411212107-01.log
11. csharpsquid:S107
   - Issue Key: b7a40bd3-56f4-4edc-b0df-3ff389a5014f
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:554
   - Sonar 问题: Method has 16 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_b7a40bd3-56f4-4edc-b0df-3ff389a5014f_20260411212107-01.log
12. csharpsquid:S107
   - Issue Key: d48b8eef-2eb6-4206-b6df-289bb4929256
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1504
   - Sonar 问题: Method has 15 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_d48b8eef-2eb6-4206-b6df-289bb4929256_20260411212107-01.log
13. csharpsquid:S107
   - Issue Key: 609d2d1d-9dbc-4bc2-9900-87f9b9d45cc7
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:1778
   - Sonar 问题: Method has 17 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_609d2d1d-9dbc-4bc2-9900-87f9b9d45cc7_20260411212107-01.log
14. csharpsquid:S107
   - Issue Key: ac012e8f-bd39-4bc9-8e92-f81c464aae5d
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:2270
   - Sonar 问题: Method has 18 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_ac012e8f-bd39-4bc9-8e92-f81c464aae5d_20260411212107-01.log
15. csharpsquid:S6960
   - Issue Key: 27eed645-6b54-4c88-aeca-707ba20429d3
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.WebApi/Controllers/Finance/HistoricalImportController.cs:24
   - Sonar 问题: This controller has multiple responsibilities and could be split into 4 smaller controllers.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S6960 默认跳过：Controller 职责拆分属于架构调整，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_27eed645-6b54-4c88-aeca-707ba20429d3_20260411212107-01.log
16. csharpsquid:S107
   - Issue Key: a6cc9ceb-a570-4768-8cca-8ff680911d13
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs:804
   - Sonar 问题: Method has 10 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_a6cc9ceb-a570-4768-8cca-8ff680911d13_20260411212107-01.log
17. csharpsquid:S107
   - Issue Key: bbd7c135-ef72-4de4-bc0f-b7f36e008062
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueCalculationService.cs:869
   - Sonar 问题: Method has 10 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 该 issue 按规则策略默认跳过，建议人工处理，未纳入本 PR。
   - 规范校验: not_applicable | No active quality gates were declared for this attempt.
   - 跳过原因: 规则 csharpsquid:S107 默认跳过：方法参数过多通常需要跨调用点重构，建议人工处理。
   - 重试日志: logs\issue_attempts\BI_bbd7c135-ef72-4de4-bc0f-b7f36e008062_20260411212107-01.log
18. csharpsquid:S1144
   - Issue Key: bed2ec1c-8990-4b9f-aae6-4ae12fcc4fa6
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:942
   - Sonar 问题: Remove the unused private set accessor in property 'RemarkDic'.
   - 尝试次数: 5
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | All active quality gates passed for this attempt.
   - 跳过原因: Agent completed without modifying any files after 5 attempt(s)
   - 重试日志: logs\issue_attempts\BI_bed2ec1c-8990-4b9f-aae6-4ae12fcc4fa6_20260411212107-01.log
19. csharpsquid:S3776
   - Issue Key: 052c0359-2d63-4dc6-993e-fefa2c4faa83
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/HistoricalOverdueImportService.cs:2270
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 55 to the 30 allowed.
   - 尝试次数: 5
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | All active quality gates passed for this attempt.
   - 跳过原因: Agent completed without modifying any files after 5 attempt(s)
   - 重试日志: logs\issue_attempts\BI_052c0359-2d63-4dc6-993e-fefa2c4faa83_20260411212107-01.log

## 失败 Issues
- 无失败 issue
