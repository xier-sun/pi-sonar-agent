---
{
  "version": 1,
  "common_prompt_guidance": [
    "优先用低风险重构降复杂度：先提取纯布尔条件、纯值计算、嵌套三元解析和 early-return 守卫；这些足够达标时不得升级到完整代码块提取或新类型引入。",
    "当前修复如果不涉及方法提取，也必须遵守类型、安全、生命周期与兼容性边界；不要为了“过规则”偷偷改变运行时语义。 "
  ],
  "refactor_safety_constraints": [
    "默认不要把匿名类型提取成新 helper 的显式参数、返回值或字段；不得用 dynamic 或 object 兜底承接匿名类型、nullable-heavy 状态或 LINQ 投影。",
    "Expression<Func<...>>、Func<...>、IQueryable<T>、IEnumerable<T> 的语义不能随意互换；凡是依赖查询翻译的链路默认保持 inline，或保持 IQueryable/Expression 语义。",
    "提取后的方法必须保持闭包变量、using/IDisposable 生命周期、async/await、ref/out、struct/ref struct 语义等价；ref struct 不得跨 await/迭代器边界，也不得被 lambda 或本地函数捕获。",
    "泛型约束、var 推断、命名元组元素、默认接口方法、序列化约束等编译器/框架上下文不能在提取后丢失。",
    "如必须用具名类型替换匿名类型，先确认仓库语言能力和最小改动范围；只要低风险选项足够，就不得引入 record/class/struct/DTO。"
  ],
  "families": {
    "complexity": {
      "title": "Complexity",
      "prompt_guidance": [
        "复杂度类问题先做方法内收口，只有在原方法实在无法收敛时才提取少量 private helper。",
        "新增 helper 默认保持 private 且同步；不要用 dynamic、匿名类型跨 helper 传值。"
      ]
    },
    "signature": {
      "title": "Signature",
      "prompt_guidance": [
        "签名类问题优先最小改动；公开面、接口实现、override 默认保守。",
        "只有在传播闭包明确时才联动调用点和接口，否则先避免破坏外部契约。"
      ]
    },
    "cleanup": {
      "title": "Cleanup",
      "prompt_guidance": [
        "删除型规则只做最小清理，不顺手补文档、改 async 命名或做结构性重构。",
        "删除当前 issue 目标后，如果相邻 helper 连带变成未使用，可以一并收口；不要继续扩大范围。"
      ]
    },
    "expression": {
      "title": "Expression",
      "prompt_guidance": [
        "表达式类问题优先原地改写成更清晰的局部变量或语句块，不要为了语法问题新增公开结构。",
        "最小化 diff，保持原有业务语义和执行顺序。"
      ]
    }
  }
}
---

# Sonar Fix Playbook

这份手册服务于 `headless simple_loop` 模式，目标不是把 patch 修成“完美模板”，而是帮助模型和审查者围绕当前 issue 做最小、可编译、可继续闭环的修复。

## C# Refactor Safety Boundary

适用范围：

- 所有 C# 规则通用
- 尤其适用于 `S3776`、`S1200`、`S1541` 以及任何“通过提取代码/拆 helper/改 LINQ 链/引入新类型”来降低复杂度或消除重复的修复

如果低风险手段已经足够达标，就不要升级到高风险抽取。优先级固定为：

1. 提取纯布尔条件 `private static bool Pred(T x)`
2. 提取纯值计算 `private static TResult Calc(T1 a, T2 b)`
3. 提取嵌套三元解析 `private static TResult Resolve(...)`
4. 提取 early-return 守卫 `private static bool ShouldSkip(...)`
5. 提取完整代码块或整条 LINQ 链
6. 引入新具名类型替换匿名类型

只要 `1-4` 足够，不得使用 `5-6`。

### 一、类型传递禁区

1. 默认不要把匿名类型提取成新 helper 的显式方法边界。
`new { A = x, B = y }` 产生的类型没有适合写进新 helper 显式签名的稳定名字。
虽然可以通过泛型推断、`dynamic` 或 `object` 临时绕过去，但这些做法要么依赖脆弱推断，要么丢失类型安全；重构时默认不要依赖。

- 替代：提取纯条件/纯计算，让输入输出都保持具名类型
- 替代：如果必须跨方法传递，先评估是否真的值得引入具名类型，以及这是否超出最小改动范围

2. `Expression<Func<...>>` 与 `Func<...>` 不可互换。
如果原始调用点在 `IQueryable`/EF Core 链中，新方法必须保持 `Expression` 语义，否则会失去 SQL 翻译。

3. `IQueryable<T>` 与 `IEnumerable<T>` 在依赖查询翻译的链路里不能随意互换。
提取方法时，参数和返回值必须保持原有查询语义，不能把服务端过滤偷偷退化成客户端求值；只有在已经明确物化到内存后，才按 `IEnumerable<T>` 语义继续收口。

### 二、生命周期与作用域

4. 闭包变量捕获必须显式化。
提取含 lambda/本地函数的逻辑时，所有捕获变量都要重新检查是否应该变成参数，以及提取后它们是否还会在原位置被修改。

5. `using` / `IDisposable` 生命周期不能变。
提取到新方法时，资源应作为参数传入，而不是在 helper 中重新创建或延长作用域。

6. `async/await` 传播规则不能破。
不得把需要 `await` 的调用变成 fire-and-forget；不得把 `async` 方法改成不返回 `Task/ValueTask`；不得在 `async` 方法里引入 `ref/out` 参数。

### 三、语言特性陷阱

7. `ref struct` 不能装箱、不能当类字段，也不能跨 `await`/迭代器边界或被 lambda/本地函数捕获。
例如 `Span<T>`、`ReadOnlySpan<T>` 等提取时都要重新检查这一层约束；不要因为“能编译”就假设它们可以安全穿过异步或闭包边界。

8. 值类型语义不能偷偷改变。
`struct` 按值传递会复制；如果提取后需要修改原值，必须显式 `ref`。

9. 可空值类型拆箱要保持可空语义。
不要把 `decimal?`、`int?`、`DateTime?` 等在 helper 提取时收窄成非可空类型。

10. 默认接口方法与显式接口实现不能丢失接口转换。
提取调用点时，若原逻辑依赖接口视角，必须保持这一点。

### 四、编译器推断依赖

11. 泛型类型推断和约束不能丢。
提取后仍要保留 `where T : ...` 这类约束，不能为了省事改成 `object`/`dynamic`。

12. `var` 依赖上下文推断。
新方法签名、局部返回值和中间状态需要显式具名类型，不能把原上下文里的 `var` 幻觉带过去。

13. 命名元组元素名不是独立的运行时成员。
编译器会保留名称相关元数据，但运行时底层仍是 `Item1`/`Item2` 这类成员；不要依赖元组元素名做重载区分，也不要在反射/序列化场景把它们当成稳定契约成员；需要稳定契约时优先使用具名类型。

### 五、框架特定规则

14. EF Core 跟踪实体不得跨线程/跨上下文乱传。
提取到并行代码前必须确认是否需要 `AsNoTracking` 或 detach；同步/异步 `DbContext` 调用也不能混搭。

15. DI 生命周期不能漂移。
`Scoped` 服务不能被提取到 `Singleton` 生命周期的类中，也不要在 helper/new class 里手动 `new()` 出本该由容器管理的服务。

16. 序列化兼容性要先确认。
如果提取引入 DTO/record/class，必须确认当前项目的序列化器、公共 setter、`init-only`、默认构造函数等兼容性。

### 六、最终决策规则

- 只要当前 issue 可以通过原方法内收口、局部变量、guard clause、纯条件/纯计算提取完成，就不得升级到完整代码块抽取。
- 只要当前 issue 不需要跨方法传递复杂状态，就不得引入 `dynamic`、匿名类型泄漏或新具名类型。
- 只要当前 issue 不需要扩大公开契约，就不得顺手改 public/protected、接口、override、DI 生命周期、序列化模型。

## Complexity

- 适用规则：`S3776`
- 优先级：原方法体内收口 > guard clause / 条件合并 > private helper > 其它
- 禁止项：
  - 把匿名类型、nullable-heavy 状态或 `dynamic` 直接搬到新增 helper 边界
  - 为了降复杂度顺手改 public/protected 签名
  - 在没有完整传播闭包时改接口、controller、override

## Signature

- 适用规则：`S107`、`S1172`
- 优先级：最小签名收敛 > 保持公开面稳定 > 在闭包明确时再传播
- 复杂 `S107` 优先读取 `docs/s107-fix-guide.md`，再决定参数如何按职责收进 context
- 禁止项：
  - 为压参数个数顺手重写整段业务逻辑
  - 没有完整传播闭包就扩散到公开 API、接口实现、override
  - 为了快速通过规则引入无关 public DTO、property 或新的公开 helper

## Cleanup

- 适用规则：`S1481`、`S1144`
- 优先级：只修当前目标 > 最小删除/最小使用 > 不扩散
- 禁止项：
  - 顺手清理同方法或同文件更远处的其它项
  - 借删除型修复做 async 改名、文档补齐、风格大改
  - 引入新的 helper、类型或公开面

## Expression

- 适用规则：`S3358`
- 优先级：原表达式附近改写 > 局部变量 / if-else / 语句 lambda > 其它
- 禁止项：
  - 仅仅把嵌套三元换个位置继续保留
  - 为表达式规则新增类级 public helper
  - 为了风格问题引入跨文件传播或大范围改动

## 通用闭环原则

- 每轮 patch 后先看 build，再看轻量 issue 校验，再看新阻塞。
- 本地无法可靠判断时允许 `UNKNOWN`，不要因为判不准就陷入无意义循环。
- 只有真正影响闭环的硬问题才作为 blocker：
  - 编译错误
  - 仓库语言特性不兼容
  - async 合同破坏
  - 公开签名漂移
  - helper 类型形状破坏
  - 越界范围变更
