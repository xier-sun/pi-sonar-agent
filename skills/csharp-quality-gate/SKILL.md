---
title: C# 质量门禁
description: 当需要生成、修改或审查任何 C# 代码时，统一提供严格的编码规范、异步标准、质量门禁与审查要点，并限制修改范围不要无关扩散。
---

## Fix
- 只在当前 issue 必须触达的代码范围内执行规范；不要为了补齐无关旧代码而扩大修改范围。
- 不要顺手补 XML 注释、中文注释、sealed、DI 或命名统一化，除非当前 issue 或当前 patch 已经真实触达这些点。
- 命名约定必须保持稳定：类名/方法名使用 PascalCase，私有字段使用 _camelCase，局部变量使用 camelCase。
- 异步方法必须使用 async/await，方法名以 Async 结尾，返回 Task/Task<T>；严禁 async void（事件处理器除外）。
- 异步方法内部如果没有 await，应改为同步方法或直接返回 Task；不要留下 async 无 await、半截 Async 改名或假异步。
- 依赖注入优先通过构造函数注入接口；如果当前仓库/当前类型采用 IDependency 自动注册模式，新增 DI 服务时保持一致。
- LINQ 优先使用方法语法；涉及数据库异步访问时优先异步版本，例如 ToListAsync()；不要在当前 patch 引入 query syntax。
- 新增业务注释优先使用专业中文；财务与核心业务术语保持专业中文表达，例如罚息、应收账款、账务、呆滞库存、订单取消。
- 当前 patch 触达公开类、公开方法、公开属性、公开实体时，必须补齐完整 XML 文档注释；不要为了当前 issue 去补无关公开成员。
- 对 S3776 等复杂度问题，优先在目标方法体内做最小重写、提前返回、条件扁平化；不要顺手做整段架构重构。
- 保持类型、签名和现有架构约定稳定；不要为了绕过类型问题引入 dynamic、宽泛 object 参数或不必要的新 DTO。
- 优先删除本轮引入的冗余局部变量、无用 using 和死代码；新增纯辅助方法如果不依赖实例状态，优先 static；新增无继承意图的 concrete class 优先 sealed。

## Review
- 只审查当前 patch 是否值得进入编译，不做修复设计，不扩大发散范围。
- 重点看当前 issue 是否真正改到目标方法，而不是只移动变量、改调用点或做无关整理。
- 重点看是否引入明显的语法、类型、签名、async、DI、LINQ 语法、作用域或命名风险。
- 审查当前 patch 触达的公开成员是否补齐 XML 文档，异步方法是否真正 await，新增 helper/class 是否符合 static/sealed 倾向。
- 审查新增业务注释是否为专业中文，财务类术语是否仍然保持准确中文表达。
- 对 S3776，请基于当前代码判断是否已实质降低复杂度；不要要求 fix agent 额外提供复杂度数值证明或完整方法说明。
- S3776 最终是否满足 <=30 由编译后的 post-check 再确认；review 阶段只拦明显跑偏或明显硬风险。
- decision=retry 时，constraints 只给 1-3 条最小可执行约束；不得要求同步重构相似 sibling 方法。

## Main
- 只判断当前 patch 是否值得进入编译，不重做 review，也不设计修法。
- 只有当 patch 已改到当前 issue 目标方法、没有明显语法/类型/async/签名/DI 硬风险时，才允许 compile。
- 不要因为 XML 注释、sealed、static、中文注释等非当前 issue 必要项而拒绝进入编译。
- 不要因为无关旧代码缺注释、历史命名风格不统一等仓库存量问题拒绝当前 patch；只看当前触达范围。
- 对当前 patch 已触达的公开成员、异步方法、DI 接口注入、LINQ 语法、static/sealed 倾向和中文业务注释，要按门禁判断是否存在明确 blocker。
- 如果 review 已 approve，而 main 看不到新的明确 blocker，优先进入编译而不是继续空转。
- decision=retry 时，constraints 只保留进入下一轮前最关键的 1-3 条约束。
