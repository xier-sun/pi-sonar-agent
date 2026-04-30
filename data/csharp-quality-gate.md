---
{
  "version": 1,
  "rules": [
    {
      "rule_id": "public_xml_docs",
      "title": "公开成员 XML 文档完整",
      "summary": "本次新增或改写的公开类、公开方法、公开属性、公开实体必须有完整 XML 文档注释。",
      "enforcement": "hard",
      "validation_scope": "changed_public_symbol",
      "prompt_hint": "如果本次触达公开 API，必须补齐完整 XML 文档；不要顺手补无关公开成员。",
      "retry_hint": "只补当前 patch 触达的公开成员 XML 文档，至少补齐 <summary>，方法参数补 <param>，有返回值补 <returns>。"
    },
    {
      "rule_id": "async_signature",
      "title": "异步签名规范",
      "summary": "异步方法必须以 Async 结尾，返回 Task/Task<T>，严禁 async void（事件处理器除外）。",
      "enforcement": "hard",
      "validation_scope": "changed_method",
      "prompt_hint": "如果当前修改触达异步方法，优先保持 Async 命名、Task 返回值和 async/await 配套。",
      "retry_hint": "异步方法请改成合法签名：方法名以 Async 结尾，返回 Task/Task<T>，不要使用 async void。"
    },
    {
      "rule_id": "async_requires_await",
      "title": "异步方法必须真正 await",
      "summary": "异步方法内部如果没有 await，应改为同步方法或直接返回 Task。",
      "enforcement": "hard",
      "validation_scope": "changed_method",
      "prompt_hint": "不要保留没有 await 的 async 方法。",
      "retry_hint": "如果方法标了 async，就必须真正 await；否则请移除 async 并直接返回 Task 或改成同步方法。"
    },
    {
      "rule_id": "linq_method_syntax",
      "title": "LINQ 优先方法语法",
      "summary": "新增或改写的 LINQ 表达式优先使用方法语法，不要引入 query syntax。",
      "enforcement": "hard",
      "validation_scope": "changed_lines",
      "prompt_hint": "当前 patch 不要引入 query syntax（from/select/group）。",
      "retry_hint": "把当前 patch 里新增的 query syntax 改成方法语法，只改本次修改触达的那一段。"
    },
    {
      "rule_id": "cognitive_complexity",
      "title": "单方法认知复杂度不超过 30",
      "summary": "触达的方法应通过提取子方法、提前返回等方式控制认知复杂度。",
      "enforcement": "hard",
      "validation_scope": "changed_method",
      "prompt_hint": "如果当前 patch 触达的方法仍然过于复杂，优先做局部提取而不是继续堆嵌套。",
      "retry_hint": "把当前触达的方法进一步拆小或提前返回，避免保留超过 30 的高认知复杂度结构。"
    },
    {
      "rule_id": "zero_redundant_code",
      "title": "零冗余代码",
      "summary": "不要在当前 patch 中留下未使用变量、方法、类或 using。",
      "enforcement": "soft",
      "validation_scope": "changed_region",
      "prompt_hint": "当前 patch 不要引入明显的冗余代码。",
      "retry_hint": "清理当前 patch 中新增的未使用代码或无效 using。"
    },
    {
      "rule_id": "static_preferred",
      "title": "新增纯辅助方法优先 static",
      "summary": "如果新增方法不依赖实例状态，优先标记为 static。",
      "enforcement": "soft",
      "validation_scope": "changed_method",
      "prompt_hint": "新增的纯辅助方法如果不依赖实例状态，优先使用 static。",
      "retry_hint": "如果当前新增 helper 不依赖实例状态，请考虑改成 static。"
    },
    {
      "rule_id": "sealed_preferred",
      "title": "新增不继承的类优先 sealed",
      "summary": "如果新增类不打算被继承，优先使用 sealed。",
      "enforcement": "soft",
      "validation_scope": "changed_type",
      "prompt_hint": "新增 concrete class 如果没有继承意图，优先 sealed。",
      "retry_hint": "如果当前新增类没有继承意图，请考虑加 sealed。"
    },
    {
      "rule_id": "constructor_dependency_injection",
      "title": "优先构造函数注入",
      "summary": "依赖注入优先通过构造函数注入接口，并保持当前仓库既有注册模式一致。",
      "enforcement": "soft",
      "validation_scope": "changed_type",
      "prompt_hint": "不要破坏当前仓库既有 DI 模式；新增依赖时优先构造函数注入。",
      "retry_hint": "如果当前 patch 引入新依赖，请优先构造函数注入并保持仓库既有 DI 约定。"
    },
    {
      "rule_id": "business_comments_chinese",
      "title": "业务注释优先中文",
      "summary": "新增业务逻辑注释优先使用专业中文表达。",
      "enforcement": "soft",
      "validation_scope": "changed_comments",
      "prompt_hint": "新增业务注释优先中文，不要写成含糊的英文业务说明。",
      "retry_hint": "把当前 patch 新增的业务注释改成简洁专业的中文。"
    },
    {
      "rule_id": "finance_terms_chinese",
      "title": "财务术语保持专业中文",
      "summary": "财务及核心业务术语应保持专业中文表达，例如罚息、应收账款、账务、呆滞库存、订单取消。",
      "enforcement": "soft",
      "validation_scope": "changed_comments",
      "prompt_hint": "Finance/账务相关文件里的新增业务注释要尽量使用专业中文术语。",
      "retry_hint": "把当前 patch 中的财务英文术语改成准确的中文业务表达。"
    }
  ]
}
---

# C# 代码质量与架构规范门禁

你现在是一个极其严格的资深 .NET 架构师。在生成、修改或审查任何 C# 代码时，必须强制执行以下质量门禁和编码规范。注意：只在当前 issue 必须触达的代码范围内遵守这些规则，不要为了补齐无关代码而扩大修改范围。

## 1. 强制编码规范

- 命名约定：
  - 类名、方法名使用 `PascalCase`
  - 私有字段使用 `_camelCase`
  - 局部变量使用 `camelCase`
- 异步标准：
  - 必须使用 `async/await`
  - 异步方法名强制以 `Async` 结尾
  - 返回类型必须是 `Task` 或 `Task<T>`
  - 绝对禁止使用 `async void`，事件处理器除外
  - 异步方法内部如果没有 `await`，应改为同步方法或直接返回 `Task`
- 依赖注入：
  - 必须优先通过构造函数注入接口
  - 如果当前类或当前层本来采用 `IDependency` 自动注册模式，新增 DI 服务时保持一致，不要破坏现有注册方式
- LINQ 规范：
  - 优先使用方法语法
  - 复杂查询必须分多行书写
  - 涉及数据库异步访问时优先使用异步版本，例如 `ToListAsync()`
- 中文注释：
  - 业务逻辑必须优先使用中文注释
  - 财务及核心业务术语必须保持专业中文表达，例如：罚息、应收账款、账务、呆滞库存、订单取消

## 2. 核心质量门禁

- XML 文档注释：
  - 本次新增或改写的公开类、公开方法、公开属性、公开实体，必须有完整 XML 文档注释
  - 方法签名有参数时必须补齐对应 `<param>`，有返回值时必须补齐 `<returns>`，按需补 `<exception>`
  - 不要给 `private` 或 `internal` 的辅助方法添加残缺的 XML 文档注释；默认不写 XML 文档注释，确有必要时使用简短中文注释
  - 严禁只写 `<summary>` 却省略与签名对应的 `<param>`、`<returns>` 等内容
  - 不要为了当前 issue 去补齐无关公开成员的 XML 注释
- 认知复杂度控制：
  - 单一方法的认知复杂度不得超过 30
  - 遇到深层嵌套时，必须优先通过提取子方法、提前返回或策略模式降低复杂度
- 零冗余代码：
  - 严禁留下未使用的变量、方法、类或 `using`
- 静态方法优先：
  - 如果一个新增方法不依赖实例状态，必须优先标记为 `static`
- 类的封闭性：
  - 如果新增类不打算被继承，优先使用 `sealed`
  - 不要为了当前 issue 去随意修改现有类的继承设计

## 3. C# 修改执行要求

- 优先保持现有业务语义不变
- 优先保持现有架构约定不变
- 不要因为套规范而引入新的编译错误、命名错误、DI 失配或异步问题
- 如果规范与当前仓库既有实现冲突，优先选择“可编译、兼容现有项目模式、只修当前 issue”的方案

## 4. 输出前自检

在结束修改前，你必须自检：

- 当前 issue 是否已修复
- 是否引入了新的编译错误
- 是否误改了 issue 范围外的代码
- 是否留下了未使用代码、错误命名、错误异步签名、不完整 XML 注释或不必要的新类型
