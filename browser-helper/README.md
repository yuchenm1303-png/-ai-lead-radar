# AI Lead Radar Browser Helper

Chrome / Edge Manifest V3 辅助工具。它不会后台抓取，也不会读取 Cookie；只有用户主动点击扩展按钮时，才读取当前可见页面的公开文本，并把候选内容带到 `https://smirel.com/radar/import.html` 复核后入库。

## 安装

1. 下载本仓库并解压。
2. Chrome/Edge 打开扩展管理页，开启“开发者模式”。
3. 选择“加载已解压的扩展程序”，指向 `browser-helper/`。
4. 正常登录并浏览小红书。打开笔记详情页后点击扩展，选择“捕获当前帖子”；在搜索结果页可选择“捕获当前页候选”。
5. Radar 会打开复核页，确认后才执行 prefilter → AI/规则判断 → score → dedupe → store。

## 边界

- 不绕 CAPTCHA、登录保护、风控或私有 API。
- 不保存 Cookie、localStorage、手机号、微信号等个人资料。
- 小红书 URL 会去掉查询参数，只保留公开笔记路径，避免保存临时追踪参数。
- 图片内文字目前不会在扩展本地 OCR；若正文主要存在图片中，应在 Radar 复核页补充必要文字。后续可接“用户主动截图 → 服务器端视觉模型 → 不持久化图片”的可选流程。
