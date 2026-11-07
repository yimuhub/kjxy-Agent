# kjxy-Agent 商用交付就绪度审查报告

- 审查日期：2026-11-07。
- 代码基线：`4ffa1c0651442545f1fd5cf55843be1b2aa2c299`，v3.71.0。
- 目的：判断当前代码是否可以直接交付客户，而不是统计功能数量或沿用历史评分。
- 方法：按业务、权限、文件与本机能力、失败恢复、前端体验、发布与运维分域检查；对重要发现进行最小化复现；执行隔离质量验证。
- 方法参考：`risk-quality-reviewer`、`security-auditor-1.0.0`。本文使用风险矩阵，不用未经标定的百分比或综合分数掩盖阻断项。
- 边界：只审查和验证，不整改、不提交、不推送、不打包；不迁移或重启用户现有实例。安全复现只使用临时合成文件和内存数据库，不读取真实密钥，不执行真实键鼠动作。

## 1. 管理层结论

**当前不应按“拿去部署即可稳定商用”的标准放行。** 项目已有较完整的业务和工程底座，不需要推倒重做；主要距离是安全边界、收费权益一致性、失败可恢复性，以及能够重复证明交付质量的发布流程。

必须区分三个交付目标：

| 交付目标 | 当前判断 | 放行前提 |
| --- | --- | --- |
| 内部演示、限定人员验证 | 可以继续，但不能当作商业验收通过 | 使用合成数据、隔离实例；不开放高风险能力给不可信用户 |
| 单客户、单实例、有人维护的试点 | 有条件推进，不建议直接交出无人维护的安装包 | 先解决相关高优先级缺陷，完成客户核心流程验收和备份恢复演练 |
| 多客户共用服务、自助注册收费、标准化桌面发行 | 暂不放行 | 多租户宿主资源隔离、商业权益闭环、完整发布门禁及可核验的运维/签名/合规材料 |

“具备试点基础”不等于“当前版本已通过试点验收”。尤其不能用大量单元测试通过，替代真实桌面动作、权限负向场景、收费权益和生产恢复的验证。

本轮实测也印证了这一点：后端 2455 项测试通过，但同一 HEAD 上 ruff format、UI 规范检查失败，全量 E2E 有 23 条稳定失败和 16 条未跑（详见第 5 节）。现有门禁自身尚未全绿，是放行的第一道硬门槛。

## 2. 已经具备的基础

1. 模块化后端、Vue 管理端、桌面壳与迁移体系已经存在；这不是只有页面的原型。
2. 工具注册层具备输入 Schema、权限校验、高风险审批、审计与异步超时处理，见 [backend/app/modules/tools/registry.py:212](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/tools/registry.py#L212)。
3. 官方库使用发布者公钥进行 Ed25519 验签，不只是显示一个签名图标，见 [backend/app/modules/library/crypto.py:137](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/library/crypto.py#L137)。
4. 自定义 HTTP 工具有出网检查、DNS 固定和禁用环境代理，见 [backend/app/modules/tools/custom_runtime.py:80](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/tools/custom_runtime.py#L80)。这些防护值得保留，但不代表所有出网入口均已被本轮穷尽验证。
5. 指挥官调度器已有取消、预算、总时限和无可调度任务时的退出处理，见 [backend/app/modules/commander/service.py:1925](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/commander/service.py#L1925)。本轮没有依据将它整体称为“必然死循环”，但同步工具仍会影响这些时限的兑现。

## 3. 已确认的执行与文件安全问题

优先级定义：**P0** 为相应部署模式下的交付阻断；**P1** 为相关功能向客户开放前必须修复；**P2** 为可限定范围延期的改进。优先级不等于漏洞 CVSS 分数。

### S1 · P0（共享服务）工作区记录隔离不等于宿主文件隔离

- **事实**：允许根是进程级配置，默认服务器进程的用户主目录；创建工作区只判断路径处于这套全局根目录中，没有将根目录绑定到小院。不同小院可以各自创建一条工作区记录，指向同一个物理目录。
- **证据**：[backend/app/modules/workspace/service.py:92](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/workspace/service.py#L92)、[service.py:471](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/workspace/service.py#L471)、[backend/app/modules/workspace/router.py:68](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/workspace/router.py#L68)。
- **触发前提**：共享一个后端进程/宿主文件系统，调用者具备工作区管理与读取权限，目标目录处于全局允许根内且操作系统允许服务进程读取。不是匿名读取，也不是任意越过操作系统权限。
- **复现**：在同一临时根中，为合成小院 A、B 分别创建工作区，B 能读取该目录的合成文件。数据库的 `yard_id` 过滤正常，但无法隔离同一物理文件。
- **影响**：共享部署中，一个小院的管理员可接触未分配给该小院的宿主资料；具备相应插件授权和写权限时还存在跨边界写入风险。本机控制桥同样使用服务进程所在机器，不能误当作每位 Web 用户自己的电脑。
- **建议责任**：后端安全/部署负责人。
- **验收**：小院只能访问平台明确分配的目录/挂载；两个小院对同一宿主路径的独立注册必须被拒绝或映射到各自沙箱。桌面专用能力在共享服务中默认关闭，或绑定独立、可认证的设备代理。
- **状态/置信度**：已用真实服务函数和内存数据库复现，高；未对任何实际客户文件发起访问。

### S2 · P1 敏感路径限制可以被软链接和项目根选择绕过

- **事实**：敏感名称检查施加于用户输入的相对路径；解析软链接后只验证“仍在项目根内”，没有再次检查真实目标的敏感目录、名称或允许路径。项目根校验也不排除允许根下的敏感子目录。
- **证据**：[backend/app/modules/workspace/service.py:153](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/workspace/service.py#L153)、[service.py:176](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/workspace/service.py#L176)、[service.py:248](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/workspace/service.py#L248)、[service.py:539](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/workspace/service.py#L539)。
- **复现**：直接访问临时 `.env` 被拒绝；通过同目录 `alias.txt` 软链接访问同一个合成文件成功。另将临时 `.ssh` 目录注册为项目根后，可以读取其合成 `config` 文件。
- **影响**：产品声称的“敏感文件一律拒绝”不是可靠边界。软链接可能预先存在于导入的项目，不要求调用者已经具备创建软链接的接口。
- **建议责任**：后端安全负责人。
- **验收**：同时验证注册根、规范化输入路径、最终真实目标和目标相对于授权根的路径；补齐根内敏感链接、白名单外链接、敏感子目录根与链接切换测试。
- **状态/置信度**：合成文件复现，高。

### S3 · P1 同步工具不受注册表声明的超时控制

- **事实**：先直接执行 `tool.func(params, ctx)`，返回 awaitable 时才包 `asyncio.wait_for`。同步函数已经执行完毕后，超时控制才有机会发生；它也会占住调用所在的事件循环线程。
- **证据**：[backend/app/modules/tools/registry.py:386](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/tools/registry.py#L386)；生产同步工具实例见 [backend/app/modules/local_runtime/tool.py:32](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/local_runtime/tool.py#L32)、[backend/app/modules/workspace/tool.py:32](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/workspace/tool.py#L32)。
- **复现**：注册一个仅等待 60ms 的合成同步工具，声明时限 10ms；实际约 66ms 后仍返回成功，而非超时。
- **影响**：工具级时限不能作为整体时限保证；慢文件系统、系统工具或同步实现会延迟取消和其他并发任务。不能据此断言存在永久死循环，但“有 timeout 字段所以不会卡住”不成立。
- **建议责任**：Agent 执行引擎负责人。
- **验收**：同步工作不得阻塞主事件循环；明确超时后底层副作用是否仍继续，不能仅改成线程池就宣称可强制取消。以合成慢工具验证超时、心跳、取消与副作用结局记录。
- **状态/置信度**：最小复现，高。

### S4 · P1 macOS 按键脚本无法编译

- **事实**：按键脚本的单行 `if ... then ...` 后接独立 `else if`，不符合 AppleScript 语法。
- **证据**：[backend/app/modules/local_runtime/bridge_macos.py:43](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/local_runtime/bridge_macos.py#L43)、[bridge_macos.py:132](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/local_runtime/bridge_macos.py#L132)。
- **复现**：抽取现有 `_KEY_SCRIPT`，仅运行系统 `osacompile` 编译，不运行脚本；退出码 1：`Expected “end” or “end tell” but found “else if”. (-2741)`。
- **影响**：依赖 `press_key` 的回车、复制粘贴、组合键等桌面流程会失败，不能把“桥已就绪”理解为动作逐项可用。
- **建议责任**：桌面/本机能力负责人。
- **验收**：真实脚本编译通过；在隔离测试账户或专用应用中验证单键、多修饰键及失败后修饰键释放，不能仅 mock 系统调用。
- **状态/置信度**：系统编译器验证，高；未执行实际键鼠动作。

### S5 · P1 本机动作失败审计随事务回滚丢失

- **事实**：桥异常后，服务先在当前事务写失败审计，再抛出业务异常；路由捕获后对同一事务 rollback。
- **证据**：[backend/app/modules/local_runtime/service.py:329](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/local_runtime/service.py#L329)、[backend/app/modules/local_runtime/router.py:55](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/local_runtime/router.py#L55)、[backend/app/modules/audit/service.py:23](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/audit/service.py#L23)。
- **复现**：内存数据库中配置合成插件/授权，模拟桥失败，走实际路由函数；返回 `RUNTIME_ACTION_FAILED` 后，失败动作审计记录数为 0。
- **影响**：客户发生误操作、部分执行或设备权限失败时，无法仅靠这张审计表重建经过；与“所有执行留审计”的承诺不一致。
- **建议责任**：后端/审计负责人。
- **验收**：业务事务回滚后仍有持久的失败事件，包含请求关联、动作、操作者和结局；不保存剪贴板正文、截图或输入密钥。补充成功、失败、进程终止与审计存储失败的策略验证。
- **状态/置信度**：故障注入复现，高。

## 4. 业务、前端与发布审查

分域审查结论均经主审逐项读源码复核。标注口径：**静态确认**＝代码链路已核实但未做真实环境复现；**隔离复现**＝在临时目录/内存数据库上复现，非生产环境压测。

### 4.1 账号、收费与权益

#### B1 · P0（共享服务）挂接已有账号后可被院内管理员重置全局密码

- **事实**：`create_member` 按用户名查找全局账号，若该账号已存在且当前小院无成员关系，则直接建立成员关系，**没有本人确认/邀请接受环节**；挂接本身不改密码。但独立的 `reset_password` 只检查目标在**当前小院**的角色，随后修改的是**全局 User** 的密码哈希与 token 版本。
- **攻击链**：不可信小院管理员把受害者（在别的院是负责人）以 viewer 角色挂进自己小院 → 调用重置密码 → 受害者全局凭证被改、原院负责人身份仍保留 → 攻击者可用临时密码登录其原院负责人身份。
- **证据**：[backend/app/modules/yards/member_service.py:222](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/yards/member_service.py#L222)、[member_service.py:421](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/yards/member_service.py#L421)、[backend/app/modules/yards/permissions.py:187](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/yards/permissions.py#L187)（`yard_admin` 默认含 `members:manage` 与 `members:reset_password`）。
- **影响**：跨院账号接管。单客户单实例、管理员全部可信时不触发；共享服务或开放院管理员注册时为交付阻断。
- **验收**：挂接已有账号须本人确认；院内管理员不得重置跨院共享账号或平台账号的全局凭证；撤权/重置的令牌失效范围与身份模型一致。
- **状态/置信度**：静态确认攻击链完整（高）；分域代理另以内存服务级实验复现，未在真实 HTTP 部署重复攻击。

#### B2 · P1（收费场景）订单到账/兑换/取消缺乏并发幂等保护

- **事实**：`_mark_paid_and_grant` 注释声称幂等，但只检查当前 Session 对象的 `status`/`granted_at`，无状态 CAS、行锁或唯一抢占；兑换码使用无唯一约束（`code_id` 无 unique），订单终态迁移无并发防护。
- **证据**：[backend/app/modules/billing/service.py:192](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/billing/service.py#L192)、[service.py:219](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/billing/service.py#L219)、[service.py:317](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/billing/service.py#L317)、[backend/app/modules/billing/models.py:58](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/billing/models.py#L58)。
- **复现**：分域代理用双 Session 交错在内存库复现：一码两笔 paid；同一订单重复确认产生双倍 grant；取消与确认可互相覆盖。
- **影响**：并发确认/重试/双击可导致重复入账或终态错乱；这是真实资金权益一致性问题，不是界面小瑕疵。
- **验收**：状态迁移用条件更新（CAS）或行锁；兑换码与订单一对一唯一约束；入账与终态同一事务；并发兑换/确认/取消测试断言仅一个合法终态和一组额度。
- **状态/置信度**：隔离复现（高）；非真实 PostgreSQL 生产并发测试。

#### B3 · P1（收费场景）订阅到期后零上限策略被当作“无限制”跳过

- **事实**：订阅到账自动创建的策略基线 `limit_value=0`；额度引擎把 `effective_limit <= 0` 的策略直接 `continue`（视为不限制）。到期后 grant 过期被排除，零基线策略生效额度为 0，于是限制被**解除**而不是收紧。
- **触发前提**：该院没有其他仍有效的限制策略。
- **证据**：[backend/app/modules/billing/service.py:145](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/billing/service.py#L145)、[backend/app/modules/quota/engine.py:117](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/quota/engine.py#L117)、[engine.py:328](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/quota/engine.py#L328)。
- **影响**：付费到期反而获得无限制消费能力，与收费模型相反。
- **验收**：语义上区分“无限制”与“零余额”；到期后恢复明确的免费额度或拒绝服务，并有到期边界测试。
- **状态/置信度**：静态确认（高），分域代理内存复现预检通过。

#### B4 · P1（收费场景）一次性加油包跨自然月重复获得全额

- **事实**：加油包声明“一次性 100 万 Token、30 天有效”，但用量按自然月窗口计数，grant 只汇总仍有效的总额度，没有包级累计消费余额。月底耗尽后，次月仍在有效期内即可再次获得全额。
- **证据**：[backend/app/modules/billing/plans.py:71](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/billing/plans.py#L71)、[backend/app/modules/quota/engine.py:44](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/quota/engine.py#L44)。
- **影响**：标称一次性的权益实际可按自然月重复消费，收入与成本模型失真。
- **验收**：包级扣减跨月连续，有效期内累计消费不得超过购买量；补跨月边界测试。
- **状态/置信度**：静态确认（高），分域代理内存查询复现。

#### B5 · P1（收费场景）切换额度环境可使挂接模板策略的已购权益丢失（有条件）

- **事实**：环境切换删除该院全部 `source=template` 的策略并新建；`QuotaGrant.policy_id` 是普通字符串、无外键保护，挂在旧模板策略上的 grant 成为孤儿。**注意适用范围**：billing 自动新建的策略默认 `source=manual`，不受此删除影响；本缺陷成立于购买前已存在环境模板策略（默认模板含 tokens/month 策略）且 grant 挂到该模板策略的场景。
- **证据**：[backend/app/modules/quota/service.py:208](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/quota/service.py#L208)、[backend/app/modules/quota/models.py:109](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/quota/models.py#L109)、[backend/app/modules/quota/environments.py:26](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/quota/environments.py#L26)。
- **验收**：环境切换保留策略身份或以事务迁移权益；切换前后未到期购买余额守恒的自动化断言。
- **状态/置信度**：静态确认（高）；不是“所有购买后切环境必丢”。

#### B6 · P1（收费场景）持久化超管权限源不一致，无法完成支付闭环

- **事实**：登录时合并持久化 `platform_admin` 标志，刷新令牌路径遗漏该合并；且支付管理端点除 JWT 角色外还要求用户名在部署配置的超管名单内。持久化超管（不在名单）刷新后降权，发码/确认 403。
- **证据**：[backend/app/modules/auth/service.py:139](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/auth/service.py#L139)、[service.py:392](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/auth/service.py#L392)、[backend/app/core/deps.py:155](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/core/deps.py#L155)、[backend/app/modules/billing/router.py:125](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/billing/router.py#L125)。
- **验收**：统一实时超管权限源，初始化登录、刷新、发码、确认行为一致；撤权立即生效。
- **状态/置信度**：静态确认（高），分域代理内存复现 403。

### 4.2 前端可靠性

#### F1 · P1（共享终端）同页切换账号后本地聊天记录残留

- **事实**：登出只清认证态；聊天 store 按 scope 缓存复用，加载会话列表时把旧 active 会话当作“本地刚建未收录”保留，绕过清空 messages。A 退出、B 在同一页面登录后，可看到 A 的本地消息与会话标题。
- **证据**：[frontend/src/stores/auth.ts:159](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/frontend/src/stores/auth.ts#L159)、[frontend/src/stores/chat.ts:539](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/frontend/src/stores/chat.ts#L539)、[chat.ts:2136](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/frontend/src/stores/chat.ts#L2136)。
- **影响**：本地隐私泄露；不是后端越权（后端会话接口仍按账号鉴权）。
- **验收**：登出/切换账号清空全部聊天 store 与草稿附件；浏览器实测 A→B 无残留。
- **状态/置信度**：静态确认（高），待浏览器故障注入复核。

#### F2 · P1 插件回滚后回读失败，再次“回滚”可能反向恢复故障版

- **事实**：回滚成功后刷新台账；台账 GET 失败被静默吞掉（`skipErrorToast` + 空 catch 保留旧状态），界面仍显示旧版本。后端回滚是交换 installed/previous；用户据陈旧界面再次确认“回滚到 v1”，实际把 v2 交换回来。
- **证据**：[frontend/src/components/library/OfficialLibraryPanel.vue:782](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/frontend/src/components/library/OfficialLibraryPanel.vue#L782)、[OfficialLibraryPanel.vue:1011](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/frontend/src/components/library/OfficialLibraryPanel.vue#L1011)、[frontend/src/api/library.ts:292](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/frontend/src/api/library.ts#L292)、[backend/app/modules/library/service.py:1514](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/library/service.py#L1514)。
- **验收**：变更后台账回读失败必须显式提示并禁用再次变更直至回读成功；回滚目标版本以服务端当前状态为准。
- **状态/置信度**：静态确认（高）。

#### F3 · P1（收费场景）订单网络失败无反馈，结果不明仍可再次提交

- **事实**：创建订单 catch 为空；全局 toast 只覆盖有响应的错误，断网/超时无任何提示，按钮恢复可再次点击，也不回查订单状态。
- **证据**：[frontend/src/components/quota/BillingPanel.vue:436](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/frontend/src/components/quota/BillingPanel.vue#L436)、[frontend/src/api/client.ts:135](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/frontend/src/api/client.ts#L135)。
- **影响**：用户不知道订单是否已创建；与 B2 叠加时放大重复入账风险。不能据此断言已发生重复扣款。
- **验收**：无响应错误显式提示“结果未知”；提交前回查 pending 订单或前端幂等键。
- **状态/置信度**：已在隔离浏览器故障注入复现（高）：abort 订单 POST 后 0 toast、弹窗仍开，见第 5.3 节。

#### F4 · P1 SSE 失败时同文历史被误判为本轮发送成功

- **事实**：流式失败后回读消息，仅凭“存在同内容、不同 ID 的用户消息”判定已落库并返回成功，清掉草稿与附件。历史里若有同文（如“继续”），本轮实际未落库也被判成功。SSE POST 不自动重发（正确设计），失败走 fallback，但该判定使失败被掩盖。
- **证据**：[frontend/src/stores/chat.ts:1803](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/frontend/src/stores/chat.ts#L1803)、[frontend/src/utils/sse.ts:96](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/frontend/src/utils/sse.ts#L96)。
- **验收**：以本轮请求关联 ID/时间戳判定落库，而非内容相同；失败保留草稿并提示。
- **状态/置信度**：静态确认（高），待浏览器故障注入复核。

前端已有防护（不重复计为缺陷）：路由角色/权限守卫、axios 单飞续期、SSE GET 有限重连且 POST 不重发、SafeMarkdown 文本节点与 HTTP(S) 白名单、文档预览 iframe 沙箱。本轮未发现已证实的 XSS。

### 4.3 发布、部署与运维

#### O1 · P1 生产编排的 Redis 凭证链不完整

- **事实**：`docker-compose.prod.yaml` 的 redis 用 `--requirepass ${REDIS_PASSWORD:?...}` 启动，healthcheck 使用 `$$REDIS_PASSWORD`，但该服务没有 environment/env_file 把变量注入容器——compose 插值变量不等于容器环境变量；api 依赖 redis `service_healthy`，新部署存在启动阻断风险。另一套 `compose.yaml` + `compose.production.yaml` 中，scheduler 的 broker URL 在 base 里被 environment 固定为无密码地址，production 覆盖未补 scheduler。
- **证据**：[docker-compose.prod.yaml:35](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/docker-compose.prod.yaml#L35)、[compose.yaml:101](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/compose.yaml#L101)、[compose.production.yaml:57](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/compose.production.yaml#L57)。
- **验收**：干净环境完整启动生产编排并通过健康检查；CI 增加真实启动冒烟而非仅 `compose config`。
- **状态/置信度**：静态确认（高）；未实际运行 Docker 验证 NOAUTH 探针退出码。

#### O2 · P1 备份存在但恢复不能保证回到备份状态

- **事实**：备份是 plain `pg_dump`；恢复直接对**非空**库执行 psql，无 `ON_ERROR_STOP`、无单事务、不清空目标库；文件卷用 tar 覆盖但不删除备份后新增文件；健康检查通过即宣布“恢复完成”。原生部署 upgrade 脚本在 SQLite 服务未停时 cp 主库（未覆盖 WAL），迁移失败不触发回滚。
- **证据**：[deploy/scripts/backup.sh:13](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/deploy/scripts/backup.sh#L13)、[deploy/scripts/restore.sh:23](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/deploy/scripts/restore.sh#L23)、[deploy/native/server/upgrade.sh](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/deploy/native/server/upgrade.sh)。
- **验收**：恢复用新建/清空目标库 + 单事务 + `ON_ERROR_STOP`；文件恢复后差异校验；非空库恢复演练证明数据与备份一致；SQLite 备份用 backup API 或停写后含 WAL。
- **状态/置信度**：静态确认（高）；未对用户真实库演练。

#### O3 · P1（桌面发行）Tauri 密钥/令牌存在非安全随机数回退

- **事实**：`ensure_secret`、`ensure_bootstrap_token`、`ensure_instance_token` 依赖 `/dev/urandom`，失败时退化到 `DefaultHasher(时间+PID)` 重复填充 32 字节；Windows 无 `/dev/urandom`，普通平台必走回退；`ensure_secret` 的 Windows 备选 `\\.\PhysicalDrive0` 也不是安全随机源。
- **证据**：[client/desktop/src-tauri/src/lib.rs:182](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/client/desktop/src-tauri/src/lib.rs#L182)、[lib.rs:232](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/client/desktop/src-tauri/src/lib.rs#L232)、[lib.rs:264](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/client/desktop/src-tauri/src/lib.rs#L264)。
- **验收**：三处统一使用系统 CSPRNG（如 getrandom），失败拒绝启动；已生成的弱密钥有轮换路径。未读取任何实际 secret 文件。
- **状态/置信度**：静态确认（高）。

#### O4 · P1 发布链缺少质量门禁与依赖冻结

- **事实**：release 工作流 verify 只跑版本一致性检查，不依赖 CI/测试/E2E 结果；Python `pip install -e .` 不消费 `uv.lock`，桌面 `npm install` 而非 `npm ci`，Node 20 与前端 engines>=22 不一致。CI 有 ruff/mypy/pytest/迁移可逆/OpenAPI 漂移/前端构建，但无 Playwright 步骤，compose 只 config 不启动，Docker 只 build 不运行。Tauri updater 签名配置存在，不等于平台代码签名/公证完成。
- **证据**：[.github/workflows/release.yml](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/.github/workflows/release.yml)、[.github/workflows/ci.yml](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/.github/workflows/ci.yml)。
- **验收**：发布以全量门禁通过为前置；锁文件消费、Node 版本对齐；生产栈启动冒烟进 CI。
- **状态/置信度**：静态确认（高）；未查询线上分支/标签保护与 secret 实际状态，不能断言线上必然绕过所有保护。

#### O5 · P2 监控直方图双重累积，指标不可信

- **事实**：`observe_http` 内部已按累积桶计数（每个 `seconds <= bound` 的桶都 +1），`exposition` 又做一次前缀累加。本轮内存复现：单个 40ms 请求，有限桶输出 1..9 而 `+Inf` 为 1，违反 Prometheus histogram 语义，基于此的 P95/告警会失真。
- **证据**：[backend/app/core/context.py:33](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/core/context.py#L33)、[context.py:68](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/core/context.py#L68)。
- **验收**：桶计数与导出只累积一次；用已知样本断言导出文本符合 Prometheus 语义。
- **状态/置信度**：本轮内存复现（高）。

#### O6 · P2 部署形态能力不等价，容量证据不足

- **事实**：知识检索在 PostgreSQL/pgvector 走向量候选，SQLite 路径按 `retrieval_scan_limit`（默认 2000，可配置）截断扫描；Celery beat 定义了僵尸任务回收与 legacy 月清零，而内嵌调度启动列表未包含这两项（新 quota_counters 按自然月窗口，legacy YardQuota 月清零是另一套，影响范围需限定）。现有性能文档明确为本机 dev、SQLite+inline、单并发、n=10，locust/SSE100/PG 压测未执行。
- **证据**：[backend/app/modules/knowledge/service.py:569](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/modules/knowledge/service.py#L569)、[backend/app/core/config.py:130](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/core/config.py#L130)、[backend/app/workers/celery_app.py:22](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/workers/celery_app.py#L22)、[backend/app/main.py:56](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/backend/app/main.py#L56)、[docs/perf-baseline-2026-11-07.md](file:///Users/czmj/Documents/Hermes/kjxy-work/kjxy-Agent/docs/perf-baseline-2026-11-07.md)。
- **验收**：交付文档明确两种部署形态的能力差异；内嵌形态补齐等价维护任务或声明差异；生产 SLA 以目标拓扑压测为准。
- **状态/置信度**：静态确认（高）；不是“所有额度都不重置”。

## 5. 当前版本验证记录

已完成的安全最小复现全部基于本报告所列 HEAD，而非旧版本测试成绩：

| 验证 | 实际结果 | 结论边界 |
| --- | --- | --- |
| 同一宿主根注册给两个合成小院 | 两条记录均创建成功，第二小院能读同一合成文件 | 证实文件层未按小院分配根；不代表所有业务表越权 |
| 敏感文件直读/链接对照 | 直读拒绝、链接读取成功 | 证实规范化目标未复查敏感策略 |
| 敏感子目录作为项目根 | 注册并读取合成文件成功 | 证实项目根校验缺口 |
| 同步工具超时 | 声明 10ms，约 66ms 后成功 | 证实注册表时限不覆盖同步执行 |
| 本机动作失败审计 | 业务失败后持久记录数 0 | 证实该路由失败审计丢失 |
| macOS 按键脚本只编译 | 退出码 1，语法错误 -2741 | 不需要真实键鼠权限即可确认的功能缺陷 |
| 监控直方图单样本导出 | 40ms 请求有限桶输出 1..9、`+Inf`=1 | 证实双重累积，指标语义错误 |

### 5.1 全量质量门禁（隔离副本环境，非用户运行实例）

在与 HEAD 一致的 /tmp 副本上执行，日志保留于 `/tmp/kjxy-audit-4ffa1c0-TzgYsy/` 与 `/tmp/kjxy-commercial-validation-20261107/`：

| 门禁 | 退出码 | 结果 |
| --- | --- | --- |
| 后端 pytest 全量 | 0 | 2455 passed，6 warnings，12m08s |
| ruff check | 0 | 通过 |
| ruff format --check | **1** | `app/modules/workspace/service.py` 需重排（1/699 文件） |
| mypy | 0 | 通过 |
| 前端 lint / type-check | 0 / 0 | 通过 |
| 前端 unit | 0 | 70 pass / 0 fail |
| 前端 build | 0 | 通过（首次 rc=1 为沙箱写限制，换 configLoader 后成功，非产品问题） |
| UI 规范 / 弹窗检查 | **1 / 1** | 18 项违规：v-col-resize 缺失、7 处 el-dialog 缺 append-to-body（含 BillingPanel、HubView） |
| 版本一致性 | 0 | 3.71.0 九处一致 |

### 5.2 全量 E2E（8 shard，无版本过滤）

140 条用例：**97 通过 / 23 失败 / 3 flaky（重试通过，有 JSON 证据）/ 1 跳过 / 16 未跑**。shard1 因 chat-panel 用例挂起耗尽 480s globalTimeout，16 条 did-not-run。失败分类（明细见 `/tmp/kjxy-commercial-validation-20261107/e2e_failures.json`）：

- 品牌标题断言差异 ×2（期望含「阗策」，疑隔离环境品牌未配置）。
- 登录跳转落 /journal 与期望不符 ×5（疑行为变更或种子态，需逐条确认）。
- 元素缺失/数据依赖 ×10（含 /pet 公开页跳登录，需人工复核）。
- 60s 超时 ×4（当时 8 shard 与全量 pytest 并行，资源竞争为嫌疑，但无重试通过证据，不判 flaky）。
- 测试定位歧义 ×1、数量断言 ×1。

**结论边界**：23 条稳定失败未逐条闭环前，不能宣称“全量 E2E 通过”；16 条未跑用例是验证缺口，不是通过。

### 5.3 真实浏览器主路径与故障注入

自建 8001/5174 隔离服务 + SQLite backup API 副本库（未触碰用户 8000/5173/18700）：

- 登录、插件中心、Billing、ROI 四条主路径**全部通过，0 console error / 0 pageerror**。
- **F3 已升级为运行复现**：abort POST `/api/v1/billing/orders` 模拟断网后，0 toast、弹窗仍开、仅 console `net::ERR_FAILED`，与静态分析一致。证据：`repro-billing-network-error.{json,png}`。
- F1（账号切换残留）、F2（回滚台账）、F4（SSE 401 误成功）三条前端链本轮未做浏览器故障注入，维持静态确认等级。

### 5.4 验证环境处置

首个验证代理异常中断后，其遗留 pytest 与 8-shard Playwright 跑批经 manifest 确认基线同为 4ffa1c0，结果被收割而非重复执行；进程自然退出，8 个 shard 服务 cleanup 全部 cleaned=true。本轮自建服务已 SIGTERM、端口释放，临时配置已删除，用户运行实例未受影响。覆盖率未采集。

## 6. 未验证范围与商业前置条件

以下是需要补齐的证据，**不是已确认违法、不可用或已遭攻击**：

- 真实 PostgreSQL/Redis/Celery 生产拓扑下的并发、故障恢复和升级演练。
- 目标客户规模下的长稳、资源峰值、队列积压、模型延迟及真实费用核对；不能把开发机短时接口测试换算为生产 SLA。
- Windows/Linux/macOS 正式签名制品、干净机器安装、存量升级、更新失败和数据库回退配套流程。
- 外部模型、搜索、消息通道、知识导入与文档成品的真实端到端交付；本轮不调用付费模型或向外部发送客户数据。
- 农业建议的专家标注样本、引用准确率、无证据拒答、误诊/错误建议处理及人工复核责任。软件测试通过不等于农业结论正确。
- 数据处理协议、隐私告知与留存/删除规则、第三方模型的数据处理条款、素材和依赖许可证、客服及退款规则；如面向境内公众运营，应另行评估适用备案及生成式服务义务。
- 商户、证书、签名密钥、线上备份、告警值班等是否已在仓库外配置。本轮不读取秘密材料，也不以代码存在证明这些外部条件已经落实。

## 7. 放行方式与整改路线

不要用“再加几个功能”替代一次专门的商业化加固周期。先收紧交付范围、修复相关 P0/P1、冻结候选版本，再在与客户一致的环境验收。

### 7.1 整改阶段

工作量假设：2 名后端 + 1 名前端/桌面 + 0.5 名测试/运维，冻结新功能，不含外部审批、证书申请、备案等不可控等待。以下为条件估计，不是承诺工期。

| 阶段 | 内容 | 对应发现 | 条件估计 |
| --- | --- | --- | --- |
| 一 · 安全边界 | 工作区根按小院分配/沙箱映射；真实目标复查敏感策略；共享账号挂接确认与重置权限收口；同步工具移出事件循环并明确超时语义；失败审计独立持久化；macOS 按键脚本修复 | S1–S5、B1 | 1.5–2.5 周 |
| 二 · 收费权益闭环 | 订单/兑换/取消并发幂等与唯一约束；零上限语义；包级跨月守恒；环境切换权益迁移；超管权限源统一；前端订单失败反馈 | B2–B6、F3 | 1–2 周 |
| 三 · 交付可靠性 | 生产编排凭证链与启动冒烟；恢复/升级演练与脚本修复；Tauri CSPRNG；前端账号切换清场、回滚回读、SSE 落库判定；metrics 修复 | O1–O3、O5、F1、F2、F4 | 1–2 周 |
| 四 · 发布门禁与验收 | release 依赖全量门禁、锁文件与 Node 对齐、Playwright 进 CI；目标拓扑压测与长稳；客户核心流程验收；备份恢复演练留证 | O4、O6 及第 6 节前置条件 | 1–2 周，压测与外部材料另计 |

阶段一、二可部分并行。任一阶段完成后跑全量门禁与对应验收用例，不留“修了但没验”的项。

### 7.2 逐项放行判据

所有交付目标的共同前置：第 5 节门禁全绿——ruff format 与 UI 规范违规清零，23 条 E2E 稳定失败逐条闭环（修复或证实为环境/用例问题并留证），16 条未跑用例补跑完成。

- **共享服务放行**：S1、B1 修复并有负向测试；B2–B6 全部闭环；发布门禁（O4）生效；生产拓扑压测与恢复演练留证。缺一项不放行。
- **单客户试点放行**：与客户实际使用面相关的 P1 修复并验收（至少 S2–S5、F1–F4、O1–O3）；备份恢复在该客户环境演练成功；明确人工维护与升级责任人。
- **桌面标准发行放行**：O3 修复；三平台签名/公证制品在干净机器完成安装、升级、更新失败回退实测；S4 类系统桥缺陷逐项真机验证。
- **内部演示**：可继续，但使用合成数据与隔离实例，不开放高风险能力给不可信用户，不宣称商业验收通过。

### 7.3 本轮边界声明

本轮只审查与验证：未修改产品代码，未提交、未推送、未打包；未重启或迁移用户现有实例；未读取真实密钥、provider 配置正文或客户文件。报告本身是唯一新增仓库文件。
