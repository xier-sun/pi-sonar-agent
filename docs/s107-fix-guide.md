# S107 Fix Guide

这份指南专门服务于 `csharpsquid:S107`。目标不是做“漂亮重构”，而是在最小风险下把目标方法最终签名参数数收敛到 `<=7`，并保持调用链可编译。

## 何时优先读取这份指南

出现以下任一信号时，不要直接开始零碎替换，先读完整份指南再动手：

- 目标方法当前参数明显多于 `9` 个
- 调用点不止一个，或者存在跨 service/controller 的签名传播
- 参数里混有多组 `batch/preloaded/context/dictionary/calculation state`
- 前一轮已经做过一些参数合并，但最终签名仍然是 `8` 或 `9`
- 你已经准备做第 2 次以上的小步 Edit

## 硬完成条件

只有同时满足以下条件，这次 `S107` 才算真的修好：

- 目标方法最终签名顶层参数总数 `<=7`
- 所有调用点都已同步更新
- 改完后重新读取目标方法声明，并重新计数
- 没有靠 `dynamic/object`、无关 public DTO、无关 helper 扩散来硬凑过关

`8` 或 `9` 个参数依然失败。方向正确但未达阈值，不算完成。

## 推荐工作流

1. 先读 3 处，再决定收敛方案
   - 目标方法声明
   - 全部调用点
   - 当前文件或相邻代码里已有的 context/preloaded 类型
2. 先按“职责”分组参数，不要按“顺手能合并的两个参数”分组
3. 如果目标方法是 `private/internal` 且调用点可控，优先引入同文件 `private sealed` context type
4. 尽量一次性完成签名、方法体和调用点改造，不要靠一串零碎 `replace_all` 慢慢挪
5. 结束前重新读取签名并数顶层参数；若仍 `>7`，继续改，不要提交半成品

## 优先采用的收敛模式

### 1. 核心输入保留显式参数，外围状态收进 context

适合目标方法本身有明确主语义参数，而其余参数只是辅助状态的场景。

优先保留：

- 当前主实体，例如 `order`, `saleOrder`, `item`
- 当前核心服务或 `unitOfWork`
- 当前主时间参数，例如 `calculationDate`, `generationStartDate`

优先收进 context：

- 各类预加载字典、batch 数据、lookup map
- 成组出现的规则、费率、配置项
- 同一生命周期下的累计值、计算状态、事件跟踪状态

### 2. 按生命周期拆成多个小 context，而不是做一个巨型 DTO

当参数天然分成几组时，优先按职责拆组，而不是把所有参数都塞进一个新类型。

常见分组方式：

- `Request/Input Context`
- `Preloaded/Batch Context`
- `Calculation State Context`
- `Penalty/Rule Context`
- `Period/Event Context`

目标不是“只剩 1 个参数”，而是“顶层保持 4 到 7 个清晰参数”。

### 3. 复用已有 context/preloaded 类型

如果调用点附近已经有 `preloadedData`、`ParallelPreloadedData` 或类似上下文，不要重复造一套几乎相同的新 DTO。优先：

- 直接复用已有类型
- 在已有类型上增加最小必要字段
- 新建一个只包剩余局部状态的私有 context

## 明确不推荐的修法

- 只合并 2 个参数，最后还剩 `8/9` 个参数
- 新建无关 public DTO、public property 或跨文件大重构
- 为了省事改业务逻辑顺序、删除分支、偷改语义
- 只改方法体，不改调用点
- 把 tuple、局部变量、中间包装当作“参数已经减少”
- 连续多次小步 Edit，每次只挪 1 到 2 个参数，最后把 turns 耗尽

## 复杂案例模板

以下不是唯一答案，但适合作为高参数方法的收敛模板。

### 模板 A: `ProcessSingleOrderInternal` 类场景

适合“少数核心参数 + 大量预加载字典/批量状态”的方法。

保留显式参数：

- `unitOfWork`
- 核心 service
- 当前 `order`
- `calculationDate`
- `generationStartDate`

收进 `ProcessSingleOrderContext`：

- `customerTypes`
- `penaltyRules`
- 各类 `batch/preloaded` 字典
- `remarkDic` 及同类 lookup 状态

### 模板 B: `ProcessOverduePeriodWithEventTracking` 类场景

适合“一边是区间/事件状态，一边是订单/单据数据，再加规则配置”的方法。

建议拆成三组：

- `PeriodStateContext`
  - `periodStart`
  - `periodEnd`
  - `currentEvent`
  - `previousOverdueAmount`
  - `generationStartDate`
- `SaleOrderCalcContext`
  - `saleOrderForCalc`
  - `payTerm`
  - `delivery`
  - `return`
  - `receipt`
  - `refund`
  - `clearRecon`
  - `saleOrderPlugin`
- `PenaltyCalcContext`
  - `rule`
  - `rateTiers`
  - `slp`
  - `isInterRecon`

### 模板 C: `ProcessAcceptanceAndQualityAssurance` 类场景

适合“前几项是主输入，尾部是一串累计值/计算状态”的方法。

保留显式参数：

- 主实体
- 支付条款/基础对象
- 当前集合输入
- 少数必须 `ref` 的累计结果

收进私有 `AcceptQaCalcContext`：

- `accumulateDays`
- `accumulatedClearRecon`
- `accumulatedClearReconFc`
- `calculationDate`

## 最终检查清单

提交前逐项确认：

- 我现在读到的目标方法签名参数数是多少
- 是否已经 `<=7`
- 是否所有调用点都同步更新
- 是否复用了已有 context/preloaded 类型
- 是否避免了无关 public DTO 和大范围扩散
- 如果这是 `private/internal` 方法，我是否优先用了同文件私有 context

只要其中任何一项答案是否定的，就不要结束本轮。
