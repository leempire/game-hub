# 我是卧底实施计划

**目标**　在「多人桌游」中加入支持 4–8 名真人的文字推理游戏，完成入房、私下看词、轮流发线索、投票与续开。

**架构**　纯引擎裁决规则和投影玩家视图。房间层管理阶段期限、持久化、退房和战绩。客户端复用现有房间 WebSocket 和对局组件。

**技术**　Python、pytest、FastAPI、SQLite、Expo、React Native、TypeScript、Playwright。测试在本机 WSL2 执行。

**设计**　[`设计.md`](../../游戏设计/我是卧底/设计.md)。实现使用 `dev/wqqxxx`。

## 共用接口

- 游戏键 `undercover`，路由 `Undercover`，深链 `undercover/:code`，游戏目录分类 `table`。4–6 人一卧底，7–8 人两卧底。发言 45 秒，投票或重投 30 秒，8 轮仍无胜负判和。
- `create_game(player_ids, word_pair, match_id, rng)` 产生状态；`apply_clue(gs, uid, text, phase_id)`、`apply_vote(gs, uid, target_id, phase_id)` 成功返回 `None`，拒绝返回中文错误。`expire(gs)` 执行本阶段超时，`depart(gs, uid, reason, *, settle=True)` 处理离开，`resolve_departures(gs)` 处理延后的离开裁决。
- 视图 `view_for(gs, uid)` 含 `match_id`、`revision`、`phase_id`、`phase` (`clue|vote|runoff|finished`)、`round`、`remaining_ms`、`speaker_id`、`player_ids`、`alive`、`turn_order`、`clues` (`{round,user_id,text}`，跳过为 null)、`eliminations` (`{round,user_id,role,reason}`)、`tallies` (`{round,runoff,votes:[{user_id,count}]}`)、`candidate_ids`、`votes_cast`、`your_vote`、`your_word`、`can_clue`。`sync`、`game_start`、`undercover_state` 共用该结构。
- 客户端动作 `undercover_clue {match_id,phase_id,text}`、`undercover_vote {match_id,phase_id,target_id}`。`game_over {game:'undercover',match_id,winner,reason,summary,forfeited}` 中 `summary` 是 `snapshot(gs)`，含 `game`、`round`、`winner` (`civilian|undercover|draw|aborted`)、`reason`、`roles:[{user_id,role}]`、`word_pair:{civilian,undercover}`、`eliminations` 和汇总 `tallies`。`forfeited` 为离场用户 ID 列表。历史使用相同摘要，匿名分享只给裁剪后的整体结果。

## 实施与验收

- [x] **纯引擎及词库**　先在 `server/test/test_undercover.py` 验证人数分配、私密视图、线索校验和阶段版本、重投、超时、弃权、终局。新增 `server/app/game/undercover.py`，使用自编词对和可注入随机源。单跑引擎测试。
- [x] **房间、定时、WebSocket 与战绩**　先在 `server/test/test_ws_undercover.py` 验证四人开局、逐人投影、重连、并发兼容和战绩。接 `room_manager.py`、`room_timing.py`、`ws.py`、`routers/rooms.py`、`database.py` 和 `sharing.py`。测试旧阶段动作、定时到期、热重启、票目标退房及重复结算。
- [x] **客户端**　实现 `UndercoverScreen`，接游戏目录、图标、帮助、房间人数、消息、三处路由、返回和战绩。通过四个真实浏览器会话验收入房、看词、发言、锁票、出局后只读、刷新、结算和续开。
- [x] **回归与文档**　更新使用指南、接口、设计索引和测试矩阵。在 WSL2 跑后端 pytest、前端 lint 和类型检查、相关 E2E、视觉审计，报告真机覆盖范围。

## 验收记录

- 本机 WSL2 的专项后端 24 项、前端 136 项、TypeScript、四人 Chromium 联机和视觉审计 44 项通过。
- 后端两次全量复测均为 1976 通过。两次各出现一项不同的既有观战收尾用例波动。改密断开观战连接的清理断言在原分支也能复现。
- 新增前端文件 lint 通过。全仓 lint 与原分支同为 235 个错误、107 个警告。Android 真机验收待执行。
