# DeepSeek 会话管理器

这是一个可直接加载的 Chrome Manifest V3 扩展，用于在 `https://chat.deepseek.com/` 页面上批量选择、删除和导出历史会话。

## 安装

1. 打开 Chrome：`chrome://extensions/`
2. 打开右上角“开发者模式”
3. 点击“加载已解压的扩展程序”
4. 选择本目录：`deepseek-session-manager`
5. 打开或刷新 `https://chat.deepseek.com/`

## 使用

- 页面左上角会出现“编辑”按钮。
- 点击“编辑”后，可以选择会话。
- “全选”会选择当前接口读取到的会话。
- “导出”会逐个读取会话消息，并下载 JSON 文件和一个带样式的 HTML 阅读文件。
- “删除”会调用 DeepSeek 删除接口批量删除选中的会话，成功后会立即从页面列表移除。

## 说明

扩展使用当前 DeepSeek 网页登录态请求这些接口：

- `GET /api/v0/chat_session/fetch_page?lte_cursor.pinned=false`
- `POST /api/v0/chat_session/delete`
- `GET /api/v0/chat/history_messages?chat_session_id=...`

DeepSeek 网页接口还需要 `Authorization: Bearer <userToken>` 请求头。扩展会自动从 `localStorage` / `sessionStorage` 中读取 `userToken`、`accessToken`、`token` 等常见字段。

如果页面提示 `Missing Token`，通常是登录态过期或 token 字段尚未写入页面存储。请在 `chat.deepseek.com` 重新登录，然后刷新页面。

如果 DeepSeek 页面结构变化，扩展会显示自己的会话列表面板作为兜底，不依赖页面侧边栏 DOM 才能完成删除和导出。
