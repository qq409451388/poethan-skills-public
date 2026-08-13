(() => {
  const API_ROOT = "https://chat.deepseek.com/api/v0";
  const LIST_URL = `${API_ROOT}/chat_session/fetch_page?lte_cursor.pinned=false`;
  const DELETE_URL = `${API_ROOT}/chat_session/delete`;
  const HISTORY_URL = `${API_ROOT}/chat/history_messages`;
  const ROOT_ID = "dsm-root";
  const CHECKBOX_CLASS = "dsm-session-checkbox";

  const state = {
    sessions: [],
    selected: new Set(),
    editMode: false,
    busy: false,
    message: "",
    useFallbackList: false
  };

  function init() {
    if (document.getElementById(ROOT_ID)) return;
    document.documentElement.classList.add("dsm-ready");
    injectRoot();
    render();
    refreshSessions();

    const observer = new MutationObserver(() => {
      if (state.editMode) attachCheckboxesToPage();
    });
    observer.observe(document.body, { childList: true, subtree: true });
  }

  function injectRoot() {
    const root = document.createElement("div");
    root.id = ROOT_ID;
    root.setAttribute("aria-live", "polite");
    document.body.append(root);
  }

  function render() {
    const root = document.getElementById(ROOT_ID);
    if (!root) return;

    const selectedCount = state.selected.size;
    const statusText = state.message || `${state.sessions.length} 个会话，已选择 ${selectedCount} 个`;
    root.innerHTML = `
      <div class="dsm-toolbar ${state.editMode ? "dsm-toolbar-active" : ""}">
        <button type="button" class="dsm-btn dsm-primary" data-action="toggle-edit">
          ${state.editMode ? "完成" : "编辑"}
        </button>
        ${state.editMode ? `
          <button type="button" class="dsm-btn" data-action="refresh" ${state.busy ? "disabled" : ""}>刷新</button>
          <button type="button" class="dsm-btn" data-action="select-all" ${state.busy ? "disabled" : ""}>全选</button>
          <button type="button" class="dsm-btn" data-action="clear" ${!selectedCount || state.busy ? "disabled" : ""}>取消</button>
          <button type="button" class="dsm-btn" data-action="export" ${!selectedCount || state.busy ? "disabled" : ""}>导出 ${selectedCount || ""}</button>
          <button type="button" class="dsm-btn dsm-danger" data-action="delete" ${!selectedCount || state.busy ? "disabled" : ""}>删除 ${selectedCount || ""}</button>
        ` : ""}
      </div>
      ${state.editMode || state.busy || state.message ? `<div class="dsm-status">${escapeHtml(statusText)}</div>` : ""}
      ${state.editMode && state.useFallbackList ? renderFallbackList() : ""}
    `;

    root.querySelector("[data-action='toggle-edit']")?.addEventListener("click", toggleEdit);
    root.querySelector("[data-action='refresh']")?.addEventListener("click", refreshSessions);
    root.querySelector("[data-action='select-all']")?.addEventListener("click", selectAll);
    root.querySelector("[data-action='clear']")?.addEventListener("click", clearSelection);
    root.querySelector("[data-action='export']")?.addEventListener("click", exportSelected);
    root.querySelector("[data-action='delete']")?.addEventListener("click", deleteSelected);

    for (const input of root.querySelectorAll("input[data-session-id]")) {
      input.addEventListener("change", () => setSelected(input.dataset.sessionId, input.checked));
    }

    document.body.classList.toggle("dsm-edit-mode", state.editMode);
    if (state.editMode) attachCheckboxesToPage();
    else removePageCheckboxes();
  }

  function renderFallbackList() {
    const rows = state.sessions
      .map((session) => {
        const id = getSessionId(session);
        const title = getSessionTitle(session);
        const time = getSessionTime(session);
        return `
          <label class="dsm-row">
            <input type="checkbox" data-session-id="${escapeAttr(id)}" ${state.selected.has(id) ? "checked" : ""}>
            <span class="dsm-row-title">${escapeHtml(title)}</span>
            <span class="dsm-row-time">${escapeHtml(time)}</span>
          </label>
        `;
      })
      .join("");

    return `<div class="dsm-panel">${rows || "<div class='dsm-empty'>没有读取到会话</div>"}</div>`;
  }

  async function refreshSessions() {
    setBusy(true, "正在读取会话列表...");
    try {
      const sessions = await fetchAllSessions();
      state.sessions = uniqueById(sessions);
      state.selected = new Set([...state.selected].filter((id) => state.sessions.some((s) => getSessionId(s) === id)));
      state.message = state.editMode ? `已读取 ${state.sessions.length} 个会话` : "";
      render();
    } catch (error) {
      state.message = `读取失败：${getErrorMessage(error)}`;
      render();
    } finally {
      setBusy(false);
    }
  }

  async function fetchAllSessions() {
    const sessions = [];
    let url = LIST_URL;
    const seenUrls = new Set();

    for (let page = 0; page < 20 && url && !seenUrls.has(url); page += 1) {
      seenUrls.add(url);
      const payload = await requestJson(url);
      sessions.push(...extractSessions(payload));

      const cursor = extractNextCursor(payload);
      const hasMore = extractHasMore(payload);
      if (!cursor || hasMore === false) break;

      const next = new URL(LIST_URL);
      if (typeof cursor === "string") {
        next.searchParams.set("cursor", cursor);
      } else {
        for (const [key, value] of Object.entries(cursor)) {
          if (value !== null && value !== undefined && typeof value !== "object") {
            next.searchParams.set(key, String(value));
          }
        }
      }
      url = next.toString();
    }

    return sessions;
  }

  function attachCheckboxesToPage() {
    const matched = new Set();

    for (const session of state.sessions) {
      const id = getSessionId(session);
      if (!id) continue;
      const element = findSessionElement(session);
      if (!element) continue;
      matched.add(id);
      attachCheckbox(element, id);
    }

    const shouldUseFallbackList = matched.size < Math.max(1, Math.min(3, state.sessions.length));
    const fallbackListChanged = state.useFallbackList !== shouldUseFallbackList;
    state.useFallbackList = shouldUseFallbackList;
    syncPageCheckboxes();
    if (document.getElementById(ROOT_ID) && state.editMode) {
      document.getElementById(ROOT_ID).classList.toggle("dsm-has-panel", state.useFallbackList);
    }
    if (fallbackListChanged && state.editMode) queueMicrotask(render);
  }

  function findSessionElement(session) {
    const id = cssEscape(getSessionId(session));
    const title = normalizeText(getSessionTitle(session));
    const selectors = [
      `a[href*="${id}"]`,
      `[data-id="${id}"]`,
      `[data-session-id="${id}"]`,
      `[data-chat-session-id="${id}"]`
    ];

    for (const selector of selectors) {
      const element = document.querySelector(selector);
      if (isUsableSessionNode(element)) return closestSessionRow(element);
    }

    if (title) {
      for (const element of document.querySelectorAll("a, [role='button'], [class*='session'], [class*='chat']")) {
        if (!isUsableSessionNode(element)) continue;
        if (normalizeText(element.textContent).includes(title)) return closestSessionRow(element);
      }
    }

    return null;
  }

  function attachCheckbox(row, id) {
    const existing = row.querySelector(`:scope > .${CHECKBOX_CLASS}`);
    if (existing) {
      existing.checked = state.selected.has(id);
      return;
    }

    row.classList.add("dsm-page-row");
    row.style.setProperty("--dsm-original-padding-left", getComputedStyle(row).paddingLeft);
    row.style.setProperty("--dsm-original-padding-right", getComputedStyle(row).paddingRight);
    row.dataset.dsmSessionId = id;
    const input = document.createElement("input");
    input.type = "checkbox";
    input.className = CHECKBOX_CLASS;
    input.dataset.sessionId = id;
    input.tabIndex = -1;
    input.checked = state.selected.has(id);
    input.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
    });
    row.addEventListener("click", handlePageRowClick, true);
    row.prepend(input);
  }

  function removePageCheckboxes() {
    for (const input of document.querySelectorAll(`.${CHECKBOX_CLASS}`)) {
      const row = input.closest(".dsm-page-row");
      input.remove();
      row?.classList.remove("dsm-page-row");
      row?.style.removeProperty("--dsm-original-padding-left");
      row?.style.removeProperty("--dsm-original-padding-right");
      row?.removeEventListener("click", handlePageRowClick, true);
      if (row) delete row.dataset.dsmSessionId;
    }
  }

  function handlePageRowClick(event) {
    if (!state.editMode) return;
    const row = event.currentTarget;
    const id = row?.dataset?.dsmSessionId;
    if (!id) return;
    event.preventDefault();
    event.stopPropagation();
    event.stopImmediatePropagation();
    setSelected(id, !state.selected.has(id));
  }

  function syncPageCheckboxes() {
    for (const input of document.querySelectorAll(`.${CHECKBOX_CLASS}`)) {
      input.checked = state.selected.has(input.dataset.sessionId);
    }
  }

  function toggleEdit() {
    state.editMode = !state.editMode;
    state.message = state.editMode ? "选择要处理的会话" : "";
    render();
  }

  function selectAll() {
    state.selected = new Set(state.sessions.map(getSessionId).filter(Boolean));
    state.message = `已全选 ${state.selected.size} 个会话`;
    render();
  }

  function clearSelection() {
    state.selected.clear();
    state.message = "已取消选择";
    render();
  }

  function setSelected(id, checked) {
    if (!id) return;
    if (checked) state.selected.add(id);
    else state.selected.delete(id);
    state.message = `已选择 ${state.selected.size} 个会话`;
    render();
  }

  async function exportSelected() {
    const selected = getSelectedSessions();
    if (!selected.length) return;

    setBusy(true, `正在导出 ${selected.length} 个会话...`);
    try {
      const exported = [];
      for (let index = 0; index < selected.length; index += 1) {
        const session = selected[index];
        state.message = `正在导出 ${index + 1}/${selected.length}：${getSessionTitle(session)}`;
        render();
        const id = getSessionId(session);
        exported.push({
          id,
          title: getSessionTitle(session),
          session,
          history: await fetchHistory(id)
        });
      }

      const data = {
        source: "chat.deepseek.com",
        exported_at: new Date().toISOString(),
        count: exported.length,
        conversations: exported
      };

      downloadExportFiles(data);
      state.message = `已导出 ${exported.length} 个会话：JSON 和 HTML`;
    } catch (error) {
      state.message = `导出失败：${getErrorMessage(error)}`;
    } finally {
      setBusy(false);
    }
  }

  async function deleteSelected() {
    const selected = getSelectedSessions();
    if (!selected.length) return;

    const ok = window.confirm(`确定删除 ${selected.length} 个 DeepSeek 会话？删除后不可恢复。`);
    if (!ok) return;

    setBusy(true, `正在删除 ${selected.length} 个会话...`);
    const failed = [];
    const deletedIds = [];
    for (let index = 0; index < selected.length; index += 1) {
      const session = selected[index];
      const id = getSessionId(session);
      state.message = `正在删除 ${index + 1}/${selected.length}：${getSessionTitle(session)}`;
      render();
      try {
        await requestJson(DELETE_URL, {
          method: "POST",
          body: JSON.stringify({ chat_session_id: id })
        });
        deletedIds.push(id);
        removeSessionsLocally([id]);
      } catch (error) {
        failed.push(`${getSessionTitle(session)}：${getErrorMessage(error)}`);
      }
    }

    state.message = failed.length ? `已删除 ${deletedIds.length} 个，${failed.length} 个失败` : `已删除 ${deletedIds.length} 个会话`;
    setBusy(false);
    render();
    if (failed.length) console.warn("[DeepSeek Session Manager] delete failures", failed);
  }

  function removeSessionsLocally(ids) {
    const idSet = new Set(ids);
    state.sessions = state.sessions.filter((session) => !idSet.has(getSessionId(session)));
    for (const id of idSet) {
      state.selected.delete(id);
      removePageSessionRow(id);
    }
  }

  function removePageSessionRow(id) {
    const escapedId = cssEscape(id);
    const checkbox = document.querySelector(`.${CHECKBOX_CLASS}[data-session-id="${escapedId}"]`);
    const row = checkbox?.closest(".dsm-page-row") || findSessionElement({ chat_session_id: id });
    row?.remove();
  }

  async function fetchHistory(id) {
    const url = new URL(HISTORY_URL);
    url.searchParams.set("chat_session_id", id);
    return requestJson(url.toString());
  }

  async function requestJson(url, options = {}) {
    const authHeaders = getAuthHeaders();
    const response = await fetch(url, {
      credentials: "include",
      headers: {
        "accept": "application/json",
        "content-type": "application/json",
        "x-app-version": "2.0.0",
        "x-client-locale": navigator.language?.toLowerCase().startsWith("zh") ? "zh_CN" : "en_US",
        "x-client-platform": "web",
        "x-client-timezone-offset": String(-new Date().getTimezoneOffset() * 60),
        "x-client-version": "2.0.0",
        ...authHeaders,
        ...options.headers
      },
      ...options
    });

    const text = await response.text();
    const payload = text ? JSON.parse(text) : null;
    if (!response.ok || isApiError(payload)) {
      const apiMessage = payload?.msg || payload?.message || payload?.error;
      if (/missing token/i.test(String(apiMessage))) {
        throw new Error("Missing Token：没有从 DeepSeek 页面存储中读取到登录 token，请在 chat.deepseek.com 重新登录后刷新页面");
      }
      throw new Error(apiMessage || `${response.status} ${response.statusText}`);
    }
    return payload;
  }

  function getAuthHeaders() {
    const token = getDeepSeekToken();
    if (!token) return {};
    return {
      "authorization": token.toLowerCase().startsWith("bearer ") ? token : `Bearer ${token}`
    };
  }

  function getDeepSeekToken() {
    const candidates = [
      readStorageValue("userToken"),
      readStorageValue("accessToken"),
      readStorageValue("token"),
      readStorageValue("authToken"),
      findTokenInStorage(localStorage),
      findTokenInStorage(sessionStorage)
    ];

    return candidates.map(normalizeToken).find(Boolean) || "";
  }

  function readStorageValue(key) {
    return localStorage.getItem(key) || sessionStorage.getItem(key) || "";
  }

  function findTokenInStorage(storage) {
    for (let index = 0; index < storage.length; index += 1) {
      const key = storage.key(index);
      if (!key) continue;
      const lowerKey = key.toLowerCase();
      const value = storage.getItem(key) || "";
      if (lowerKey.includes("token") || lowerKey.includes("auth")) {
        const token = normalizeToken(value);
        if (token) return token;
      }
    }
    return "";
  }

  function normalizeToken(value) {
    if (!value || typeof value !== "string") return "";
    const trimmed = value.trim();
    if (!trimmed) return "";

    if (trimmed.startsWith("{") || trimmed.startsWith("[") || trimmed.startsWith('"')) {
      try {
        const parsed = JSON.parse(trimmed);
        return typeof parsed === "string" ? normalizeToken(parsed) : extractTokenFromObject(parsed);
      } catch {
        return "";
      }
    }

    const bearer = trimmed.match(/Bearer\s+([A-Za-z0-9._~+/=-]+)/i);
    if (bearer) return bearer[1];

    if (/^[A-Za-z0-9._~+/=-]{20,}$/.test(trimmed)) return trimmed;
    return "";
  }

  function extractTokenFromObject(value) {
    if (!value || typeof value !== "object") return "";
    if (Array.isArray(value)) {
      for (const item of value) {
        const token = extractTokenFromObject(item);
        if (token) return token;
      }
      return "";
    }

    const likelyKeys = ["value", "userToken", "accessToken", "token", "authToken", "authorization"];
    for (const key of likelyKeys) {
      const token = normalizeToken(value[key]);
      if (token) return token;
    }

    for (const [key, childValue] of Object.entries(value)) {
      if (!/token|auth/i.test(key)) continue;
      const token = typeof childValue === "string" ? normalizeToken(childValue) : extractTokenFromObject(childValue);
      if (token) return token;
    }

    return "";
  }

  function isApiError(payload) {
    if (!payload || typeof payload !== "object") return false;
    if (payload.code === 0 || payload.code === 200) return false;
    return typeof payload.code === "number" && payload.code !== 0;
  }

  function extractSessions(payload) {
    const arrays = [];
    walk(payload, (value) => {
      if (!Array.isArray(value)) return;
      const sessionLike = value.filter((item) => item && typeof item === "object" && getSessionId(item));
      if (sessionLike.length) arrays.push(sessionLike);
    });
    return arrays.sort((a, b) => b.length - a.length)[0] || [];
  }

  function extractNextCursor(payload) {
    let cursor = null;
    walk(payload, (value, key) => {
      if (cursor || !value) return;
      if (/next.*cursor|cursor/i.test(String(key)) && (typeof value === "string" || typeof value === "object")) {
        cursor = value;
      }
    });
    return cursor;
  }

  function extractHasMore(payload) {
    let result;
    walk(payload, (value, key) => {
      if (result !== undefined) return;
      if (/has.*more|has_more|hasMore/i.test(String(key)) && typeof value === "boolean") result = value;
    });
    return result;
  }

  function walk(value, visitor, key = "") {
    visitor(value, key);
    if (!value || typeof value !== "object") return;
    if (Array.isArray(value)) {
      for (const item of value) walk(item, visitor, key);
      return;
    }
    for (const [childKey, childValue] of Object.entries(value)) walk(childValue, visitor, childKey);
  }

  function uniqueById(sessions) {
    const map = new Map();
    for (const session of sessions) {
      const id = getSessionId(session);
      if (id && !map.has(id)) map.set(id, session);
    }
    return [...map.values()];
  }

  function getSelectedSessions() {
    return state.sessions.filter((session) => state.selected.has(getSessionId(session)));
  }

  function getSessionId(session) {
    return session?.chat_session_id || session?.id || session?.session_id || session?.uuid || "";
  }

  function getSessionTitle(session) {
    return session?.title || session?.topic || session?.name || session?.summary || getSessionId(session) || "未命名会话";
  }

  function getSessionTime(session) {
    const value = session?.updated_at || session?.inserted_at || session?.created_at || session?.update_time || session?.create_time;
    if (!value) return "";
    const date = typeof value === "number" ? new Date(value > 9999999999 ? value : value * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function closestSessionRow(element) {
    return element.closest("a, [role='button'], li, [class*='session'], [class*='chat']") || element;
  }

  function isUsableSessionNode(element) {
    return element instanceof HTMLElement && !element.closest(`#${ROOT_ID}`) && element.offsetParent !== null;
  }

  function setBusy(busy, message = "") {
    state.busy = busy;
    if (message) state.message = message;
    render();
  }

  function downloadExportFiles(data) {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    downloadFile(`deepseek-conversations-${stamp}.json`, JSON.stringify(data, null, 2), "application/json;charset=utf-8");
    downloadFile(`deepseek-conversations-${stamp}.html`, buildExportHtml(data), "text/html;charset=utf-8");
  }

  function downloadFile(filename, text, type) {
    const blob = new Blob([text], { type });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  function buildExportHtml(data) {
    const list = data.conversations
      .map((conversation, index) => renderConversationListItem(conversation, index))
      .join("\n");
    const conversations = data.conversations
      .map((conversation, index) => renderConversationHtml(conversation, index))
      .join("\n");

    return `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DeepSeek 会话导出</title>
  <style>
    :root { color-scheme: light; --sidebar: #f7f7f8; --panel: #ffffff; --line: #e6e8ee; --text: #202123; --muted: #6b7280; --user: #dbeafe; --assistant: #ffffff; --accent: #4d6bfe; }
    * { box-sizing: border-box; }
    html { scroll-behavior: smooth; }
    body { margin: 0; background: var(--panel); color: var(--text); font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; line-height: 1.68; }
    .app { display: grid; grid-template-columns: 320px minmax(0, 1fr); min-height: 100vh; }
    aside { position: sticky; top: 0; height: 100vh; overflow: auto; border-right: 1px solid var(--line); background: var(--sidebar); padding: 14px 10px; }
    header { padding: 8px 8px 14px; }
    h1 { margin: 0; font-size: 18px; font-weight: 650; }
    .meta { margin-top: 5px; color: var(--muted); font-size: 12px; }
    .session-link { display: block; margin: 4px 0; padding: 9px 10px; border-radius: 8px; color: #343541; text-decoration: none; }
    .session-link:hover { background: #ececf1; }
    .session-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13px; font-weight: 560; }
    .session-json { max-height: 92px; margin-top: 5px; overflow: hidden; color: var(--muted); font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; font-size: 11px; line-height: 1.45; white-space: pre-wrap; }
    main { min-width: 0; background: var(--panel); }
    article { min-height: 100vh; border-bottom: 1px solid var(--line); }
    h2 { position: sticky; top: 0; z-index: 1; margin: 0; padding: 15px 28px; border-bottom: 1px solid var(--line); background: rgba(255,255,255,.92); backdrop-filter: blur(10px); font-size: 16px; font-weight: 650; }
    .messages { width: min(860px, calc(100vw - 380px)); margin: 0 auto; padding: 24px 18px 42px; }
    .message { display: flex; margin: 0 0 22px; }
    .bubble { max-width: min(720px, 86%); padding: 12px 15px; border-radius: 12px; white-space: pre-wrap; overflow-wrap: anywhere; font-size: 15px; }
    .message.user { justify-content: flex-end; }
    .message.user .bubble { background: var(--user); border-top-right-radius: 4px; }
    .message.assistant, .message.system, .message.tool, .message.message { justify-content: flex-start; }
    .message.assistant .bubble, .message.system .bubble, .message.tool .bubble, .message.message .bubble { background: var(--assistant); border: 1px solid transparent; border-top-left-radius: 4px; }
    .role { margin-bottom: 5px; color: var(--accent); font-size: 12px; font-weight: 700; }
    .time { margin-top: 8px; color: var(--muted); font-size: 12px; }
    .think { margin: 0 0 10px; border: 1px solid var(--line); border-radius: 8px; background: #f7f7f8; color: #4b5563; }
    .think summary { padding: 8px 10px; color: #6b7280; font-size: 12px; }
    .think-content { padding: 0 10px 10px; white-space: pre-wrap; font-size: 13px; }
    .raw-json { width: min(860px, calc(100vw - 380px)); margin: 0 auto 32px; border: 1px solid var(--line); border-radius: 8px; background: #fafafa; }
    .raw-json > summary { cursor: pointer; padding: 10px 14px; color: var(--muted); font-size: 13px; }
    .raw-json pre { margin: 0; padding: 0 14px 14px; overflow: auto; color: #374151; font-size: 12px; line-height: 1.5; }
    @media (max-width: 860px) {
      .app { grid-template-columns: 1fr; }
      aside { position: relative; height: auto; max-height: 42vh; border-right: 0; border-bottom: 1px solid var(--line); }
      .messages, .raw-json { width: min(100%, 720px); }
      h2 { padding: 14px 18px; }
    }
  </style>
</head>
<body>
  <div class="app">
    <aside>
      <header>
        <h1>DeepSeek 会话导出</h1>
        <div class="meta">导出时间：${escapeHtml(new Date(data.exported_at).toLocaleString())}<br>会话数：${data.count}</div>
      </header>
      <nav>${list || "<div class='session-link'>没有会话</div>"}</nav>
    </aside>
    <main>${conversations || "<article><h2>没有会话</h2></article>"}</main>
  </div>
  <script type="application/json" id="deepseek-export-data">${escapeScriptJson(data)}</script>
</body>
</html>`;
  }

  function renderConversationListItem(conversation, index) {
    const id = conversation.id || `session-${index + 1}`;
    const preview = JSON.stringify(getConversationListJson(conversation), null, 2);
    return `<a class="session-link" href="#${escapeAttr(toDomId(id))}">
  <div class="session-title">${escapeHtml(conversation.title || conversation.id || "未命名会话")}</div>
  <div class="session-json">${escapeHtml(preview)}</div>
</a>`;
  }

  function getConversationListJson(conversation) {
    const chatSession = conversation.history?.data?.biz_data?.chat_session;
    return {
      id: conversation.id || chatSession?.id,
      title: conversation.title || chatSession?.title,
      messages: extractMessages(conversation.history).length,
      updated_at: chatSession?.updated_at || conversation.session?.updated_at || conversation.session?.update_time
    };
  }

  function renderConversationHtml(conversation, index) {
    const messages = extractMessages(conversation.history);
    const body = messages.length
      ? messages.map(renderMessageHtml).join("\n")
      : `<section class="message assistant"><div class="bubble">${escapeHtml(JSON.stringify(conversation.history, null, 2))}</div></section>`;

    return `<article id="${escapeAttr(toDomId(conversation.id || `session-${index + 1}`))}">
  <h2>${escapeHtml(conversation.title || conversation.id || "未命名会话")}</h2>
  <div class="messages">${body}</div>
  <details class="raw-json">
    <summary>原始 JSON</summary>
    <pre>${escapeHtml(JSON.stringify(conversation, null, 2))}</pre>
  </details>
</article>`;
  }

  function renderMessageHtml(message) {
    const role = getMessageRole(message);
    const content = getMessageContent(message);
    const time = getMessageTime(message);
    const thinking = message.thinking ? `<details class="think">
      <summary>思考过程</summary>
      <div class="think-content">${escapeHtml(message.thinking)}</div>
    </details>` : "";
    return `<section class="message ${escapeAttr(toClassName(role))}">
  <div class="bubble">
    <div class="role">${escapeHtml(role)}</div>
    ${thinking}
    <div>${escapeHtml(content || JSON.stringify(message, null, 2))}</div>
    ${time ? `<div class="time">${escapeHtml(time)}</div>` : ""}
  </div>
</section>`;
  }

  function extractMessages(history) {
    const deepSeekMessages = extractDeepSeekMessages(history);
    if (deepSeekMessages.length) return deepSeekMessages;

    const messages = [];
    walk(history, (value) => {
      if (!value || typeof value !== "object" || Array.isArray(value)) return;
      if (getMessageContent(value) && /user|assistant|system|tool|ai|human/i.test(getMessageRole(value))) {
        messages.push(value);
      }
    });
    return uniqueMessageObjects(messages);
  }

  function extractDeepSeekMessages(history) {
    const chatMessages = getDeepSeekChatMessages(history);
    if (!chatMessages.length) return [];

    return chatMessages
      .slice()
      .sort((a, b) => Number(a.message_id || 0) - Number(b.message_id || 0))
      .map((message) => {
        const fragments = Array.isArray(message.fragments) ? message.fragments : [];
        const response = fragments.filter((fragment) => fragment.type === "RESPONSE").map((fragment) => fragment.content || "").join("\n\n");
        const request = fragments.filter((fragment) => fragment.type === "REQUEST").map((fragment) => fragment.content || "").join("\n\n");
        const thinking = fragments.filter((fragment) => fragment.type === "THINK").map((fragment) => fragment.content || "").join("\n\n");
        const fallback = fragments
          .filter((fragment) => fragment.type !== "TIP" && fragment.type !== "THINK")
          .map((fragment) => fragment.content || "")
          .filter(Boolean)
          .join("\n\n");

        return {
          id: message.message_id,
          role: normalizeDeepSeekRole(message.role),
          content: response || request || fallback,
          thinking,
          inserted_at: message.inserted_at,
          raw: message
        };
      })
      .filter((message) => message.content || message.thinking);
  }

  function getDeepSeekChatMessages(history) {
    const direct = history?.data?.biz_data?.chat_messages;
    if (Array.isArray(direct)) return direct;

    let result = [];
    walk(history, (value, key) => {
      if (result.length || key !== "chat_messages" || !Array.isArray(value)) return;
      if (value.some((item) => item && typeof item === "object" && Array.isArray(item.fragments))) {
        result = value;
      }
    });
    return result;
  }

  function normalizeDeepSeekRole(role) {
    if (/^USER$/i.test(String(role))) return "user";
    if (/^ASSISTANT$/i.test(String(role))) return "assistant";
    return String(role || "message").toLowerCase();
  }

  function uniqueMessageObjects(messages) {
    const seen = new Set();
    return messages.filter((message) => {
      const key = message.id || message.message_id || `${getMessageRole(message)}:${getMessageContent(message)}:${getMessageTime(message)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }

  function getMessageRole(message) {
    const role = message?.role || message?.sender || message?.from || message?.type || "";
    if (/user|human/i.test(role)) return "user";
    if (/assistant|ai|bot|model/i.test(role)) return "assistant";
    if (/system/i.test(role)) return "system";
    if (/tool/i.test(role)) return "tool";
    return String(role || "message").toLowerCase();
  }

  function getMessageContent(message) {
    const content = message?.content || message?.text || message?.message || message?.answer || message?.prompt;
    if (typeof content === "string") return content;
    if (Array.isArray(content)) {
      return content.map((item) => typeof item === "string" ? item : item?.text || item?.content || "").filter(Boolean).join("\n");
    }
    if (content && typeof content === "object") return content.text || content.content || "";
    return "";
  }

  function getMessageTime(message) {
    const value = message?.created_at || message?.updated_at || message?.inserted_at || message?.time || message?.timestamp;
    if (!value) return "";
    const date = typeof value === "number" ? new Date(value > 9999999999 ? value : value * 1000) : new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
  }

  function escapeScriptJson(data) {
    return JSON.stringify(data).replace(/</g, "\\u003c").replace(/>/g, "\\u003e").replace(/&/g, "\\u0026");
  }

  function toClassName(value) {
    return String(value || "message").toLowerCase().replace(/[^a-z0-9_-]+/g, "-");
  }

  function toDomId(value) {
    const normalized = String(value || "session").toLowerCase().replace(/[^a-z0-9_-]+/g, "-").replace(/^-+|-+$/g, "");
    return normalized || "session";
  }

  function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, (char) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#039;"
    })[char]);
  }

  function escapeAttr(value) {
    return escapeHtml(value);
  }

  function normalizeText(value) {
    return String(value || "").replace(/\s+/g, " ").trim();
  }

  function cssEscape(value) {
    if (window.CSS?.escape) return CSS.escape(value);
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function getErrorMessage(error) {
    return error instanceof Error ? error.message : String(error);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init, { once: true });
  } else {
    init();
  }
})();
