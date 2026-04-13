自动修复 SonarQube issues

## 运行概览
- 作者: pengxiru@neware.com.cn
- 基线分支: develop
- 最终构建: 通过
- 成功: 27
- 跳过: 2
- 失败: 0
- 策略排除: 0
- 构建命令: dotnet build "OpenAuth.Core/OpenAuth.Core.WebApi.sln"
- 解决方案: OpenAuth.Core/OpenAuth.Core.WebApi.sln
- 测试命令: None

## 审阅提示
- 本 PR 只包含最终构建验证通过的修复。
- 被跳过或失败的 issue 已自动回滚，不包含在当前提交中。
- 建议优先审阅这些文件: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs, OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs, OpenAuth.Core/OpenAuth.App/HumanResource/HumanResourceApprovalApp.cs, OpenAuth.Core/OpenAuth.App/HumanResource/MisconductReportApp.cs, OpenAuth.Core/OpenAuth.App/HumanResource/UserRegister/UserRegisterDetail.cs, OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs, OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityOperationLogHelper.cs

## 已修复 Issues
1. csharpsquid:S1144
   - Issue Key: 23feccf9-71f5-4aa8-851f-768eeafa7b6c
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs:1037
   - Sonar 问题: Remove the unused private method 'CollectAllRelatedOrderIds'.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | No post-edit changed lines were available for quality-gate review.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs 主区域外变更: 1031, 1032, 1033, 1034, 1075
2. csharpsquid:S1144
   - Issue Key: 4b65e0d0-6a6c-4f9e-a168-917c82abeab6
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs:1082
   - Sonar 问题: Remove the unused private method 'SupplementMissingMainOrders'.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | No post-edit changed lines were available for quality-gate review.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs 主区域外变更: 1031, 1032, 1033, 1034, 1035, 1036, 1037, 1038
3. csharpsquid:S3776
   - Issue Key: 941f6101-03f0-4509-9ff2-6281f2e2028f
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs:1619
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 45 to the 30 allowed.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 6 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=6
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs 主区域外变更: 1566, 1595
4. csharpsquid:S107
   - Issue Key: b682bf4f-8fc8-4745-90f1-40ebaea7e4e2
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2108
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 4 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=4
5. csharpsquid:S107
   - Issue Key: c95aba2e-5991-4701-857d-5ce8226591d6
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2168
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 3 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=3
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs 主区域外变更: 2096
6. csharpsquid:S107
   - Issue Key: 0cb5b78f-8720-4a76-8548-909db494dc90
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2223
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs 主区域外变更: 1991, 1992, 1993
7. csharpsquid:S107
   - Issue Key: 633e14e3-dfbd-44e7-b5b4-a31379198224
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2261
   - Sonar 问题: Method has 8 parameters, which is greater than the 7 authorized.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs 主区域外变更: 2858, 2859, 2860
8. csharpsquid:S3776
   - Issue Key: 9a874d91-a7b6-4e39-a84e-da1c1722b7fe
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2341
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 38 to the 30 allowed.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 5 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=5
9. csharpsquid:S2955
   - Issue Key: c7a1d0fd-e02f-4d60-b5bc-d2e618df8ab2
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2595
   - Sonar 问题: Use a comparison to 'default(T)' instead or add a constraint to 'T' so that it can't be a value type.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs 主区域外变更: 2617
10. csharpsquid:S3776
   - Issue Key: c294734e-cfc1-461c-9e3c-1053f5d77bd0
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:3156
   - Sonar 问题: Refactor this method to reduce its Cognitive Complexity from 31 to the 30 allowed.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 2 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=2
11. external_roslyn:CS1573
   - Issue Key: e5d9736f-bdbc-4df1-bcce-48cffb9263fc
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2079
   - Sonar 问题: 参数“hasSubstantiveChange”在“SocialSecurityApp.ProcessSocialFundUpdate(SocialSecurityApp.DatabaseChangeLists, UpdateSocialFundRequest, SocialsecurityTypeEnums?, Dictionary<string, List<UserSocialsecurity>>, string, string, string, User, bool)”的 XML 注释中没有匹配的 param 标记(但其他参数有)
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
12. external_roslyn:CS1573
   - Issue Key: 32f65f79-c6a4-42d0-a582-c0e2ac420152
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2116
   - Sonar 问题: 参数“hasSubstantiveChange”在“SocialSecurityApp.ProcessHousingFundItem(SocialSecurityApp.DatabaseChangeLists, UpdateSocialFundRequest, List<UserSocialsecurity>, string, string, string, User, bool)”的 XML 注释中没有匹配的 param 标记(但其他参数有)
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
13. external_roslyn:CS1573
   - Issue Key: 836cb30c-ed56-4057-b529-d327730ceb14
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2176
   - Sonar 问题: 参数“hasSubstantiveChange”在“SocialSecurityApp.ProcessSocialSecurityItem(SocialSecurityApp.DatabaseChangeLists, UpdateSocialFundRequest, List<UserSocialsecurity>, string, string, string, User, bool)”的 XML 注释中没有匹配的 param 标记(但其他参数有)
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
14. external_roslyn:CS1573
   - Issue Key: df38858d-ca69-4a86-9cd1-867e6afca5d7
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs:2911
   - Sonar 问题: 参数“hasSubstantiveChange”在“SocialSecurityApp.ProcessSocialFundRefundUpdate(SocialSecurityApp.RefundDatabaseChangeLists, UpdateSocialReFundRequest, SocialsecurityTypeEnums?, Dictionary<string, List<UserSocialsecurity>>, string, string, string, User, bool)”的 XML 注释中没有匹配的 param 标记(但其他参数有)
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityApp.cs 主区域外变更: 2926
15. external_roslyn:CS1573
   - Issue Key: 7df7fe85-b963-4c91-b06c-850694223ea7
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/HumanResourceApprovalApp.cs:65
   - Sonar 问题: 参数“misconductReportApp”在“HumanResourceApprovalApp.HumanResourceApprovalApp(IUnitWork, IAuth, IServiceProvider, RevelanceManagerApp, IConfiguration, MisconductReportApp)”的 XML 注释中没有匹配的 param 标记(但其他参数有)
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/HumanResourceApprovalApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
16. csharpsquid:S6608
   - Issue Key: 26161bb4-4fea-4df6-a40f-71f65e3ffca5
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/MisconductReportApp.cs:1273
   - Sonar 问题: Indexing at Count-1 should be used instead of the "Enumerable" extension method "Last"
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/MisconductReportApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
17. external_roslyn:CS1570
   - Issue Key: 7f02ec41-dd9f-4377-9230-5b2621da0128
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/UserRegister/UserRegisterDetail.cs:575
   - Sonar 问题: XML 注释出现 XML 格式错误 --“在此位置不应为结束标记。”
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/UserRegister/UserRegisterDetail.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/HumanResource/UserRegister/UserRegisterDetail.cs 主区域外变更: 573
18. csharpsquid:S3267
   - Issue Key: 08f43ced-74ad-4213-94f6-e77a360944ef
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs:909
   - Sonar 问题: Loops should be simplified using the "Where" LINQ method
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
19. external_roslyn:CS1573
   - Issue Key: 5cbbccb2-2bac-42dc-8649-7a405d340471
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs:404
   - Sonar 问题: 参数“formFiles”在“UserContractApp.BatchApply(UserContractApplyReq, List<IFormFile>)”的 XML 注释中没有匹配的 param 标记(但其他参数有)
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
20. external_roslyn:CS1573
   - Issue Key: 59de4b8e-08b5-4973-9cf1-a608ad1b926c
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs:459
   - Sonar 问题: 参数“referenceFileLookup”在“UserContractApp.Apply(string, ContractSignType, UserContractUpdateReq, UserContract, Dictionary<string, Queue<IFormFile>>)”的 XML 注释中没有匹配的 param 标记(但其他参数有)
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
21. external_roslyn:CS1573
   - Issue Key: 18b0aa51-7688-4839-a1ea-0a9aa27704aa
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs:707
   - Sonar 问题: 参数“formFiles”在“UserContractApp.ReApply(UserContractUpdateReq, List<IFormFile>)”的 XML 注释中没有匹配的 param 标记(但其他参数有)
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
22. external_roslyn:CS1573
   - Issue Key: 61e71511-ff10-4dfd-8aa9-9911c4c3291e
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs:819
   - Sonar 问题: 参数“referenceFileLookup”在“UserContractApp.SyncReferenceAttachments(long, List<UserContractReferenceAttachmentReq>, Dictionary<string, Queue<IFormFile>>)”的 XML 注释中没有匹配的 param 标记(但其他参数有)
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/UserContractApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 1 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=1
23. csharpsquid:S1168
   - Issue Key: 3eec07db-a123-44d0-805d-2a152dbf5d8f
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityOperationLogHelper.cs:318
   - Sonar 问题: Return an empty collection instead of null.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityOperationLogHelper.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
24. csharpsquid:S1168
   - Issue Key: c85d1edc-3e94-4b95-95c1-d1ce5885882b
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityOperationLogHelper.cs:361
   - Sonar 问题: Return an empty collection instead of null.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityOperationLogHelper.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
25. csharpsquid:S3267
   - Issue Key: df26d73e-96b8-4313-88a9-2d1fd6db8543
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityOperationLogHelper.cs:364
   - Sonar 问题: Loops should be simplified using the "Where" LINQ method
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityOperationLogHelper.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
26. csharpsquid:S6580
   - Issue Key: 20da9e25-9ca9-4a1c-a3b6-87ba9d854db9
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityOperationLogHelper.cs:449
   - Sonar 问题: Use a format provider when parsing date and time.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityOperationLogHelper.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed.
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/HumanResource/SocialSecurityOperationLogHelper.cs 主区域外变更: 444
27. csharpsquid:S4136
   - Issue Key: 2bfba801-1e9f-4995-88dd-b2a8e5e244ca
   - 状态: FIXED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs:2121
   - Sonar 问题: All 'ApplySorting' method overloads should be adjacent.
   - 尝试次数: 1
   - 处理结果: 已完成修复，并通过该 issue 的本地构建验证。
   - 涉及文件: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs
   - 审阅建议: 重点确认上述文件中的修改确实只覆盖当前 Sonar 问题，且未引入额外逻辑变更。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | Hard quality gates passed. Recorded 6 soft reviewer finding(s).
   - 规范细项: hard failures=0, soft findings=6
   - 边界审计: drift score=1 | Patch stayed inside the filesystem boundary; extra drift was recorded for reviewer audit.
   - 漂移记录: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs 主区域外变更: 5151, 5152, 5153, 5154, 5155, 5156, 5157, 5158

## 已跳过 Issues
1. csharpsquid:S1066
   - Issue Key: f38e5fdc-9032-40d6-bf21-5520acdb172e
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.App/Finance/ReceiptApp.cs:5083
   - Sonar 问题: Merge this if statement with the enclosing one.
   - 尝试次数: 2
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: pass | All active quality gates passed for this attempt.
   - 跳过原因: Retry stopped early after 2 attempt(s): repeated `no_change` with unchanged strategy and diff.
   - 重试日志: logs\issue_attempts\BI_f38e5fdc-9032-40d6-bf21-5520acdb172e_20260413000736-01.log
2. csharpsquid:S6960
   - Issue Key: f00c0210-75dd-4924-b17e-9dd05c948a08
   - 状态: SKIPPED
   - 位置: OpenAuth.Core/OpenAuth.WebApi/Controllers/Finance/FinanceHomeController.cs:19
   - Sonar 问题: This controller has multiple responsibilities and could be split into 4 smaller controllers.
   - 尝试次数: 5
   - 处理结果: 达到最大重试次数后仍未通过构建校验，当前 issue 的改动已回滚，未纳入本 PR。
   - 启用规范门禁: public_xml_docs, async_signature, async_requires_await, linq_method_syntax, cognitive_complexity, zero_redundant_code, static_preferred, sealed_preferred, constructor_dependency_injection, business_comments_chinese, finance_terms_chinese
   - 规范校验: retry | Quality gate rejected the patch with 11 hard violation(s).
   - 规范细项: hard failures=2, soft findings=0
   - 跳过原因: C# quality gate verification failed after 5 attempt(s)
   - 重试日志: logs\issue_attempts\BI_f00c0210-75dd-4924-b17e-9dd05c948a08_20260413000736-01.log

## 失败 Issues
- 无失败 issue
