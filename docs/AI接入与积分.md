# AI 接入与积分（开发与运维）

该文档记录服务端实现约定，不作为玩家计价说明；客户端只展示余额、使用门槛及不足提示。

## 通用组件

| 文件 | 职责 |
| --- | --- |
| `server/app/ai_service.py` | 付费 AI 游戏注册、开局检查、账号级串行锁、Agent Plan JSON 调用、统一模型和 low 思考参数、20 秒网络超时、最多 1024 输出 token、不自动重试 |
| `server/app/ai_pricing.py` | 全部积分精度、单价和开局门槛的唯一来源 |
| `server/app/ai_credits.py` | 整数余额、管理员覆盖余额、请求登记、实际 token 结算、幂等扣费及未知用量标记 |
| `server/app/ai_credit_schema.py` | AI 使用流水与管理员调整流水的幂等建表 |
| `server/app/undercover_agent.py` | 谁是卧底系统提示和私有玩家视图输入 |
| `server/app/undercover_bots.py` | 谁是卧底阶段调度、房间和阶段有效性检查、既有动作入口与提示 |

模型默认 `deepseek-v4.1-flash-economy`，Chat Completions 基址为 `https://ark.cn-beijing.volces.com/api/plan/v3`，`reasoning_effort=low`。密钥仅从后端进程环境变量 `ARK_AGENT_API_KEY` 读取，不写入配置模板、源码、数据库、日志、Web bundle 或 APK。服务端须在正确环境下通过现有运维脚本启动；本功能不修改 systemd 环境；后端代码需用 `scripts/restart.sh backend` 加载。运维脚本的默认 TERM 宽限期为 35 秒，覆盖在途 AI 结算窗口；APK 单独构建和发布。

## 余额单位与结算

积分采用最初金额单位的 100 倍。每百万 token 的积分为：缓存命中输入 4、未命中输入 200、输出 800。每个 token 对应 0.000004 / 0.0002 / 0.0008 积分。

一个积分对应 250,000 个整数单位，单 token 扣费单位依次为 1 / 50 / 200。数据库 `users.ai_credit_units` 使用 INTEGER，默认 0；余额对外转换为积分数字，UI 使用 `Math.floor`，小数余额继续累计，负的小额余额也会显示为负整数。存储粒度为 0.000004 积分（六位小数），管理员设置同样遵守该粒度。

`usage.prompt_tokens` 为总输入，`usage.prompt_tokens_details.cached_tokens` 为其中命中部分（省略或 null 时为 0），未命中输入是两者之差。`usage.completion_tokens` 为总输出，包含计入其中的推理 token，不重复相加。扣费公式只在后端计算；实际缓存命中数不能通过提示长度估算。

`ai_service.require_game_start(room)` 对 `ALWAYS_AI_GAMES` 中的游戏（海龟汤）始终要求房主至少 100 积分；对 `PAID_BOT_GAMES` 中且已添加人机的房间也要求至少 100；不限制纯真人和本地算法人机。`call_json` 每次真正发请求前重新检查支付人余额，低于 0 禁用，等于 0 仍可调用。使用同一账号的多房间/多游戏请求共享锁，前一次结算后下一次才检查余额。

请求登记和扣费各用 SQLite `BEGIN IMMEDIATE` 事务。流水 `operation_id` 唯一，`settle_call` 幂等；扣费通过 `balance=balance-cost` 更新，不用先读取再覆盖。管理员覆盖余额与结算也串行写库；执行期间在途调用返回时仍从新余额扣除实际消耗。

## 新游戏接入

1. 可选人机游戏在 `ai_service.PAID_BOT_GAMES` 注册并接入 add_bot / remove_bot；必须调用 AI 的游戏在 `ALWAYS_AI_GAMES` 注册，不依赖机器人席位。现有通用开局路径已执行统一积分检查。
2. 开局保存本局支付人（当前使用 `game_state.ai_owner_id`），续局重新绑定当时房主。不得直接把临时换房主变成新支付人。
3. 玩家 Agent 只接收和真人一致的裁剪视图；主持 Agent 可按明确角色权限读取本题固定真相，绝不能把它透传回客户端。所有 Agent 只拿角色所需的上下文，不能交入无关引擎状态或其他人的秘密。玩家文字视作数据，不执行其中的提示指令。
4. 调用 `await ai_service.call_json(messages, owner_id, operation_id, is_current)`。operation_id 由游戏、局号、阶段与 bot ID 确定；发送前持久化已尝试标记，重连和热重启不能重新生成请求 ID 规避幂等。
5. `is_current` 在等待账号锁后、真正调用前检查房间/局/阶段和真人在线情况；调用后再次检查，丢弃过期答案。调用已经消耗的 token 仍结算。
6. 返回的动作通过现有房间动作入口验证；不能由 Agent 直接改引擎。由游戏决定失败/不足时的超时行为并通知支付人。引擎不导入通用服务，不访问网络和余额。
7. 增补隐私、阶段失效、断线暂停、未知 usage、并发扣费、热重启去重、正常结算与本地人机免扣费测试。

## 使用与调整流水

- `ai_usage` 保存 operation_id、支付人、模型、状态、缓存命中/未命中输入、输出和成本整数单位，不保存词、提示词、模型答案或凭证。
- 正常为 `pending → settled`。调用超时、HTTP 错误、usage 缺失或格式异常记 `unknown`；进程突然中断可能保留 `pending`。两者不自动重试、不估算扣费，需运维以提供方记录核对。已结算但回答非法的请求保持 `settled`。
- 提供方已接受请求但连接中断时，没有可靠 usage 就无法自动精确结算。不要把未知流水理解为调用免费。核对后可在后台调整余额；人工调整的前后余额与操作者均记录在 `ai_credit_adjustments`。
- 管理员通过 `POST /api/admin/users/{id}/ai-credits` 设置绝对余额，可操作自己；没有充值、签到、奖励或用户自增积分接口。
- 正常停机先禁止新 AI 动作并等待在途请求结算（最多 25 秒），再保存对局计时；超时取消保留未知用量流水。
- 当前部署单后端进程，账号锁位于该事件循环。未来扩成多进程前，需把在途请求互斥迁移为数据库租约或集中队列，不能仅靠每进程锁。

验证命令见 [测试矩阵](测试矩阵.md)。真实接口冒烟使用临时数据库，仅请求测试词，不改现有用户余额。

海龟汤主持与玩家 Agent 分开，具体协议、公共题库授权和账号排重见 [海龟汤设计](游戏设计/海龟汤/设计.md)。添加付费人机前也检查 100 积分门槛，失败提示固定为「AI积分不足」。

海龟汤进度判定附带在原有主持 JSON 中，固定三档提示由服务器选择，不产生额外付费调用。谁是卧底的简短策略计划与公开动作同次生成，仅动作进入规则校验和广播，策略计划不会发送给玩家。
