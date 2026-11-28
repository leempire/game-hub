# 海龟汤公开题库来源

检索与收录：2026-11-28。全部题目来自下列网上已有公开项目；GameHub 未生成或自编题面和汤底。三个批次共 **23 道不同汤底**，运行时统一随机抽取。

| 批次 | 数量 | 来源 | 许可 |
| --- | --- | --- | --- |
| 01 经典公开题 | 14 | [JQW2006/turtle-soup 公开题库](https://github.com/JQW2006/turtle-soup/blob/main/backend/app/data/puzzles.json) | [MIT](licenses/JQW2006-MIT.txt) |
| 02 公开轻推理 | 6 | [EasonZhu1997/AIUI-TurtleSoup 服务器题库](https://github.com/EasonZhu1997/AIUI-TurtleSoup/blob/main/server/puzzles.mjs) | [MIT](licenses/EasonZhu1997-MIT.txt) |
| 03 公开日常题 | 3 | [AZHi-xinxin/haiguitang-coop 示例题](https://github.com/AZHi-xinxin/haiguitang-coop/blob/main/samples/sample_soups.json)，作者 AZHi-xinxin | [CC BY 4.0 内容许可声明](licenses/AZHi-CC-BY-4.0.md) · [协议](https://creativecommons.org/licenses/by/4.0/) |

整理说明：保留原有题面和汤底，转换为统一 JSON 格式；从来源列明的关键点中选择必要事实，日常题的关键点做了合并精简。经典题来源同时有中英文版本，合并为一份中文题，不把翻译当成新汤底。示例题「备用钥匙」「雨伞」因题面与答案存在矛盾未收录。另一个候选项目 AISoupGameWeb 明确声明其附带题库版权归属不明，因此未采用该批内容、未沿用该项目的 MIT 标注。

`family` 是稳定的汤底身份，用于跨房间、跨译本排重；以后修改标题和措辞不能改 family，新增同底变体也必须复用 family。`id` 是条目编号。`source` 保留作者/项目、原始文件链接与许可链接，终局和个人战绩提供来源署名。公开分享仅展示来源项目主页和许可链接，不带题库文件直链或汤底，兼顾署名和避免即时剧透。

本目录只供后端读取，不挂载静态 HTTP 路径，不作为客户端依赖。不得将题库移入 Expo assets 或复制进客户端 JS。

`hints` 为 GameHub 依据已有固定汤底整理的三档辅助提示（从方向到更具体的线索），不是新增题目或改编汤底。完整阶梯只在服务端存储，游戏中按推理进展逐档公开，绝不直接发送模型生成的自由提示。
