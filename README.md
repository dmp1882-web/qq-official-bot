# QQ 官方机器人（群聊版）

基于 QQ 开放平台官方 SDK（botpy）的群聊机器人：点歌发语音、摇一摇随机视频配 AI 毒舌点评、
AI 聊天带记忆、Minecraft 资料查询、基岩版查服等。单文件主程序，开箱可跑；
所有密钥走环境变量或本地 key 文件，仓库内不含任何真实凭据。

## 功能

| 功能 | 说明 | 额外依赖 |
|---|---|---|
| 点歌 | 网易云 → B站 → Meting 三级取歌链路，语音以 SILK 直传发送 | 网易云 Cookie、ffmpeg |
| 摇一摇 | 随机抽 B 站视频（支持「摇一摇 3」/「摇一摇 三」多抽、合并一条发送），AI 看封面图写毒舌点评（≤40 字），多抽附终极总结 | DeepSeek（含视觉模型 deepseek-flash） |
| AI 聊天 | @机器人对话，带群记忆（最近 10 轮），毒舌人设 | DeepSeek |
| 搜一下 / 查 wiki | 联网搜索 + AI 总结 | 百度 API Key |
| MC 查询 | 查合成 / 查生物 / 查附魔 / 查药水 / 查村民 / 换算 | 无 |
| 查服 | 查询基岩版服务器在线状态 | Node.js + bedrock-protocol |
| 绑定 / 点赞 | 绑定 QQ 号后可通过 NapCat 给本人点赞 | NapCat（OneBot HTTP） |
| 查玩家 | Xbox 玩家查询 | 需自建中转服务（见下） |

## 快速开始

```bash
git clone <this-repo> && cd qq-official-bot
pip install -r requirements.txt
# 查服功能需要（可选）：
npm install
# 系统依赖：ffmpeg（点歌转码用）

cp .env.example .env   # 或者在本目录放同名 key 文件，见下
# 填好 QQ_APPID / QQ_APP_SECRET / DeepSeek Key 后：
python3 bot.py
```

配置有两种等效方式，环境变量优先：

1. **环境变量**：按 `.env.example` 设置（自行用 export / systemd EnvironmentFile 注入）。
2. **key 文件**：在本目录放 `qq_appid.txt`、`qq_app_secret.txt`、`deepseek_key.txt` 等纯文本文件，内容即密钥本身。这些文件名已写进 `.gitignore`，不会被误提交。

在 QQ 开放平台把机器人拉进群、开通群聊权限即可使用；指令面板可用平台接口
`PUT https://api.sgroup.qq.com/v2/panels/<panel_id>` 维护（scope=group，条目说明文字有长度限制，别写太长）。

## 可选功能说明

- **点赞**：依赖 [NapCat](https://napcat.icu) 的 OneBot HTTP API。配置 `NAPCAT_API` 与 `NAPCAT_TOKEN` 后可用；未配置时点赞命令会报错但不影响其他功能。用户需先发送「绑定 <QQ号>」把群 OpenID 与 QQ 号对应起来（映射存本地 `openid_qq_map.json`，已 gitignore）。
- **查玩家**：需要一个能访问 Xbox Live 的中转服务（本仓库不含中转实现），把中转地址配到 `XBL_RELAY_URL`、共享密钥放 `xbl_relay_secret.txt`。接口约定：`GET {XBL_RELAY_URL}/v2/search/<gamertag>`，请求头 `X-Relay-Secret: <secret>`。
- **查服**：把目标服务器配到 `MC_SERVER_HOST` / `MC_SERVER_PORT`（基岩版），机器人通过 `mc_ping.js` 查询。

## 已知坑（都是实测踩出来的）

- **点歌语音**：早期版本让 QQ 回连本机 HTTP 端口下载 SILK，公网被云安全组挡住永远失败（报错 40093007 / 40034032）。现已改走 `file_data` 直传；`PUBLIC_HOST` 只是直传失败时的旧回退，可不配。
- **网易云 403**：部分机房 IP 被 `music.126.net` 的 CDN 拒绝下载（与会员状态无关）。链路已内置回退：网易云直链失败 → B 站同名音频（带评分挑选：过滤合集/AI/翻唱等、比对时长）→ Meting 代理。Meting 是匿名接口，VIP 歌曲经它可能只有试听片段。
- **B 站 412 风控**：搜索接口会间歇返回 HTTP 412，摇一摇与 B 站取歌都已内置多次重试；风控严重时段成功率会下降，这是平台侧限制。
- **DeepSeek 视觉空返回**：`deepseek-flash` 看图点评会间歇返回空正文（非额度问题）。摇一摇点评已做成「视觉 / 文字并行竞速」：视觉 25 秒内出结果就用它，否则用文字版点评兜底，不会让群里干等。
- **QQ 被动回复时限**：群消息的被动回复有时间窗口，长耗时功能（多抽摇一摇）请控制在 5 个以内。

## 免责声明

- 本项目与腾讯、网易云音乐、哔哩哔哩、DeepSeek 均无隶属关系；相关接口为非官方调用方式，使用者需自行遵守各平台服务条款。
- 点歌内容版权归原权利方所有，本项目仅做技术演示，请勿用于商业或侵权用途。
- 使用 NapCat 等非官方协议工具存在账号风控风险，自行评估。

## License

MIT
