const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const state = {
  server: "Doris 生产机",
  address: "doris · root@10.0.0.2:22",
  plugin: "doris",
  page: "server",
  stage: "select",
  running: false,
  runIntervals: [],
  aiReadyTimer: null
};

const pluginDefinitions = {
  doris: {
    name: "Doris 综合诊断", version: "0.3.0", id: "doris-diagnostic", mark: "D",
    fields: [
      { type: "select", label: "运行模式", id: "run-mode", value: "standard", options: [["quick","快速 · Quick"],["standard","标准 · Standard"],["deep","深度 · Deep"]], help: "标准模式包含 SQL、日志、线程采样与 Flink 状态。" },
      { type: "text", label: "数据库名称", value: "trade_warehouse" },
      { type: "text", label: "Doris 地址", value: "127.0.0.1:9030" },
      { type: "text", label: "管理用户", value: "root" },
      { type: "switch", label: "检查 Flink CDC", help: "读取作业状态和最近检查点", checked: true },
      { type: "switch", label: "采集错误日志", help: "最多读取最近 500 行", checked: true }
    ]
  },
  host: {
    name: "主机性能诊断", version: "1.0.0", id: "host-performance", mark: "H",
    fields: [
      { type: "select", label: "运行模式", id: "run-mode", value: "standard", options: [["quick","快速 · Quick"],["standard","标准 · Standard"]], help: "标准模式增加进程和磁盘采样。" },
      { type: "number", label: "负载告警阈值", value: "1.0" },
      { type: "number", label: "可用内存告警百分比", value: "10" },
      { type: "switch", label: "采集高占用进程", help: "显示 CPU 排名前 30 的进程", checked: true }
    ]
  },
  network: {
    name: "网络诊断", version: "1.0.0", id: "network-diagnostic", mark: "N",
    fields: [
      { type: "select", label: "运行模式", id: "run-mode", value: "standard", options: [["quick","快速 · Quick"],["standard","标准 · Standard"]] },
      { type: "text", label: "目标网卡", value: "自动识别" },
      { type: "number", label: "带宽告警阈值 Mbps", value: "80" },
      { type: "switch", label: "采集监听端口", help: "只读取端口与进程摘要", checked: true }
    ]
  }
};

const libraryData = {
  doris: { name: "Doris 综合诊断", description: "FE/BE、systemd、SQL、线程、日志与 Flink CDC 联动诊断。", id: "doris-diagnostic", version: "0.3.0", entrypoint: "run.sh", language: "python", modes: "quick, standard, deep", report: "report/report-schema.json · report/report-template.html", fields: [["DORIS_HOST","Doris 地址 · text"],["DORIS_DATABASE","数据库名称 · text"],["DORIS_PASSWORD","管理密码 · password"],["FLINK_ENABLED","检查 Flink CDC · boolean"]] },
  host: { name: "主机性能诊断", description: "采集 Linux 负载、CPU、内存和高占用进程。", id: "host-performance", version: "1.0.0", entrypoint: "run.sh", language: "python", modes: "quick, standard", report: "使用 App 通用报告", fields: [["LOAD_PER_CORE_WARNING","负载告警阈值 · integer"],["MEMORY_AVAILABLE_WARNING_PERCENT","可用内存告警 · integer"]] },
  network: { name: "网络诊断", description: "采样 Linux 网卡吞吐、连接摘要和监听端口。", id: "network-diagnostic", version: "1.0.0", entrypoint: "run.sh", language: "python", modes: "quick, standard", report: "使用 App 通用报告", fields: [["INTERFACE","目标网卡 · text"],["BANDWIDTH_WARNING_MBPS","带宽告警阈值 · integer"]] }
};

const resultData = {
  doris: {
    findings: `<article class="finding warning"><span>!</span><div><header><h3>BE 存在持续热线程</h3><i>警告</i></header><p>线程 <code>rs_normal / TID 17797</code> 连续 3 次采样的平均 CPU 为 98.4%。</p><aside><b>建议</b>结合当前查询和 perf 栈确认是否与 two-phase read 路径相关，再决定是否调整参数。</aside></div></article><article class="finding warning"><span>!</span><div><header><h3>Flink CDC 作业处于失败状态</h3><i>警告</i></header><p><code>trade-event-storage-to-doris</code> 当前状态为 FAILED，最近完成检查点仍可用。</p><aside><b>建议</b>先确认 Doris 后端恢复稳定，再从 chk-1890 恢复该作业。</aside></div></article><article class="finding success"><span>✓</span><div><header><h3>FE/BE 均由 systemd 正常托管</h3><i>正常</i></header><p>MainPID、实际 PID 和 cgroup 一致，最近 24 小时未发现重启循环。</p></div></article>`,
    raw: `===== SECTION: HOST =====\nhostname=VM-0-2-ubuntu\ncpu_cores=8\nmemory_total_mb=31207\nload1=3.22\n\n===== SECTION: DORIS_PROCESS =====\nfe_main_pid=1111845\nfe_actual_pids=1111845\nfe_managed_by_systemd=true\nbe_main_pid=1112895\nbe_actual_pids=1112895\nbe_managed_by_systemd=true\n\n===== SECTION: HOT_THREADS =====\ntid=17797\nname=rs_normal\ncpu_samples=98.2,99.1,97.8\navg=98.4\npersistent_hot=true\n\n===== SECTION: FLINK =====\njob_name=trade-event-storage-to-doris\njob_state=FAILED\nlatest_completed_checkpoint=chk-1890`,
    ai: "问题可能由前台读取负载与 CDC 恢复压力叠加引起"
  },
  host: {
    findings: `<article class="finding warning"><span>!</span><div><header><h3>系统负载持续高于核心数</h3><i>警告</i></header><p>load1/core 为 <code>1.42</code>，连续采样中没有明显回落。</p><aside><b>建议</b>先检查 CPU 排名前列的 Java 进程，再结合业务请求量确认是否需要扩容。</aside></div></article><article class="finding success"><span>✓</span><div><header><h3>内存与磁盘空间正常</h3><i>正常</i></header><p>可用内存 42%，主要数据盘使用率 63%，没有触发阈值。</p></div></article>`,
    raw: `===== SECTION: HOST =====\nhostname=trade-test-01\ncpu_cores=8\nload1=11.36\nload1_per_core=1.42\nmemory_available_percent=42.0\n\n===== SECTION: TOP_PROCESSES =====\npid=21904 cpu=386.2 command=java\npid=811 cpu=18.4 command=mysqld`,
    ai: "主要压力集中在单个 Java 服务，暂未发现内存或磁盘瓶颈"
  },
  network: {
    findings: `<article class="finding warning"><span>!</span><div><header><h3>eth0 出站带宽接近告警阈值</h3><i>警告</i></header><p>峰值吞吐为 <code>76.8 Mbps</code>，已达到当前 80 Mbps 阈值的 96%。</p><aside><b>建议</b>核对连接数最高的目标地址，并观察是否与批量同步任务时间重合。</aside></div></article><article class="finding success"><span>✓</span><div><header><h3>监听端口未发现异常变化</h3><i>正常</i></header><p>本次扫描结果与服务器保存的基线一致。</p></div></article>`,
    raw: `===== SECTION: NETWORK =====\ninterface=eth0\nrx_peak_mbps=24.1\ntx_peak_mbps=76.8\nwarning_mbps=80\n\n===== SECTION: CONNECTIONS =====\nestablished=184\ntime_wait=39\nlisten=17`,
    ai: "网络压力更像周期性数据同步，而不是连接泄漏"
  }
};

const defaultAILoading = `<div class="ai-scanner"><i></i></div><h3>AI 正在关联诊断证据</h3><p>模型正在分析采集事实、确定性检查和异常上下文。</p>`;

function showPage(page) {
  state.page = page;
  $$(".page").forEach(node => node.classList.toggle("active", node.id === `page-${page}`));
  const titles = { server: state.server, diagnostic: "新建诊断", plugins: "插件库", reports: "历史报告", settings: "设置" };
  $("#page-title").textContent = titles[page];
  $$(".nav-item[data-page]").forEach(node => node.classList.toggle("active", page !== "diagnostic" && node.dataset.page === page && (page !== "server" || node.dataset.server === state.server)));
  closeMobileNav();
}

function showStage(stage) {
  state.stage = stage;
  const order = ["select", "configure", "running", "result"];
  const index = order.indexOf(stage);
  $$(".stage").forEach(node => node.classList.toggle("active", node.id === `stage-${stage}`));
  $$(".rail-step").forEach((node, itemIndex) => {
    node.classList.toggle("active", itemIndex === index);
    node.classList.toggle("done", itemIndex < index);
  });
  const percent = [0, 33.33, 66.66, 100][index];
  $("#rail-progress").style.width = `${percent}%`;
  $("#rail-pulse").style.left = `${percent}%`;
  $(".diagnostic-rail").classList.toggle("running", stage === "running");
  $("#page-diagnostic").scrollTo({ top: 0, behavior: "smooth" });
}

function selectServer(button) {
  state.server = button.dataset.server;
  state.address = button.dataset.address;
  $("#server-name").textContent = state.server;
  $("#server-address").textContent = state.address;
  $("#diagnostic-server").textContent = state.server;
  $("#result-server").textContent = state.server;
  showPage("server");
}

function renderPluginFields() {
  const plugin = pluginDefinitions[state.plugin];
  $("#config-name").textContent = plugin.name;
  $("#config-version").textContent = `plugin.yaml · ${plugin.version}`;
  $(".manifest-chip .plugin-mark").textContent = plugin.mark;
  $("#plugin-fields").innerHTML = plugin.fields.map(field => {
    if (field.type === "switch") return `<label class="switch-row"><span><b>${field.label}</b><small>${field.help || ""}</small></span><input type="checkbox" ${field.checked ? "checked" : ""}><i></i></label>`;
    if (field.type === "select") return `<label><span>${field.label}</span><select id="${field.id || ""}">${field.options.map(option => `<option value="${option[0]}" ${option[0] === field.value ? "selected" : ""}>${option[1]}</option>`).join("")}</select>${field.help ? `<small class="field-help">${field.help}</small>` : ""}</label>`;
    return `<label><span>${field.label}</span><input type="${field.type}" value="${field.value}" required></label>`;
  }).join("");
  $("#execution-command").textContent = `/opt/poethan-sentinel/plugins/${plugin.id}/${plugin.version}/run.sh ${$("#run-mode")?.value || "standard"}`;
}

function selectedPlugin() {
  const checked = $('input[name="plugin"]:checked');
  state.plugin = checked?.value || "doris";
  $$(".plugin-option").forEach(node => node.classList.toggle("selected", node.dataset.plugin === state.plugin));
}

function beginDiagnostic() {
  clearRunTimers();
  showPage("diagnostic");
  showStage("select");
}

function clearRunTimers() {
  state.runIntervals.forEach(timer => clearTimeout(timer));
  state.runIntervals = [];
  clearTimeout(state.aiReadyTimer);
  state.running = false;
}

function beginRun() {
  const plugin = pluginDefinitions[state.plugin];
  const mode = $("#run-mode")?.selectedOptions[0]?.textContent.split(" · ")[0] || "标准";
  $("#run-title").textContent = `正在诊断 ${state.server}`;
  $("#run-plugin").textContent = plugin.name;
  $("#run-mode-label").textContent = `${mode}模式`;
  $("#result-mode").textContent = `${mode}模式`;
  $("#result-plugin").textContent = plugin.name;
  $("#run-timer").textContent = "00:00";
  $("#live-output").textContent = `[14:32:04] Connecting to ${state.address.split(" · ")[0]}...\n[14:32:04] SSH handshake accepted\n[14:32:04] Remote host: VM-0-2-ubuntu`;
  $("#output-lines").textContent = "3 行";
  $$("#run-steps li").forEach((node, index) => { node.className = index === 0 ? "current" : ""; $("time", node).textContent = "—"; });
  renderResultData(state.plugin);
  $("#ai-loading").innerHTML = defaultAILoading;
  $("#ai-loading").hidden = false;
  $("#ai-report").hidden = true;
  $("#ai-status").textContent = "生成中";
  $("#ai-status").classList.remove("ready");
  showStage("running");
  state.running = true;
  const started = Date.now();
  const timer = setInterval(() => {
    const seconds = Math.floor((Date.now() - started) / 1000);
    $("#run-timer").textContent = `00:${String(seconds).padStart(2,"0")}`;
  }, 250);
  state.runIntervals.push(timer);
  const lines = [
    "[14:32:05] Plugin cache hit: doris-diagnostic@0.3.0",
    "[14:32:06] Collecting HOST, DORIS_PROCESS, SYSTEMD...",
    "[14:32:07] Sampling BE hot threads (3/3)",
    "[14:32:08] Downloading result.tgz from /tmp/poethan-sentinel...",
    "[14:32:09] Report schema validated"
  ];
  $$("#run-steps li").forEach((node, index, nodes) => {
    const timeout = setTimeout(() => {
      nodes.forEach((item, itemIndex) => item.className = itemIndex < index ? "done" : itemIndex === index ? "current" : "");
      if (index > 0) $("time", nodes[index - 1]).textContent = `${index + 1}s`;
      $("#live-output").textContent += `\n${lines[index]}`;
      $("#output-lines").textContent = `${4 + index} 行`;
    }, 550 * index);
    state.runIntervals.push(timeout);
  });
  const finish = setTimeout(() => {
    clearInterval(timer);
    $$("#run-steps li").forEach((node, index) => { node.className = "done"; $("time", node).textContent = `${index + 2}s`; });
    state.running = false;
    showStage("result");
    if ($("#ai-enabled").checked) {
      state.aiReadyTimer = setTimeout(() => {
        $("#ai-loading").hidden = true;
        $("#ai-report").hidden = false;
        $("#ai-status").textContent = "已完成";
        $("#ai-status").classList.add("ready");
      }, 1800);
    } else {
      $("#ai-loading").innerHTML = "<h3>本次未启用 AI 分析</h3><p>原始输出和确定性诊断结论不受影响。</p>";
      $("#ai-status").textContent = "未启用";
    }
  }, 3100);
  state.runIntervals.push(finish);
}

function renderResultData(pluginKey) {
  const data = resultData[pluginKey];
  $("#tab-conclusion").innerHTML = data.findings;
  $("#raw-report").textContent = data.raw;
  $("#ai-report h3").textContent = data.ai;
}

function stopRun() {
  if (!state.running) return;
  clearRunTimers();
  showStage("configure");
  toast("检查已停止，配置和已收集输出仍然保留");
}

function renderLibrary(key) {
  $$(".library-item").forEach(node => node.classList.toggle("active", node.dataset.library === key));
  const target = $("#library-detail");
  if (key === "invalid") {
    target.innerHTML = `<header><div><span class="eyebrow">校验失败</span><h2>custom-check</h2><p>该目录不会出现在诊断插件选择列表中。</p></div><span class="plugin-mark error">!</span></header><div class="validation-error"><h3>plugin.yaml 缺少入口文件</h3><code>entrypoint: run.sh</code><p>清单声明了 run.sh，但插件根目录中不存在该文件。添加入口文件或修正 entrypoint 后重新扫描。</p><button class="button quiet">打开插件目录</button></div>`;
    return;
  }
  const item = libraryData[key];
  target.innerHTML = `<header><div><span class="eyebrow">plugin.yaml · 只读</span><h2>${item.name}</h2><p>${item.description}</p></div><button class="button quiet">打开插件目录</button></header><dl class="manifest-table"><dt>插件 ID</dt><dd>${item.id}</dd><dt>版本</dt><dd>${item.version}</dd><dt>入口文件</dt><dd>${item.entrypoint}</dd><dt>语言</dt><dd>${item.language}</dd><dt>运行模式</dt><dd>${item.modes}</dd><dt>报告资源</dt><dd>${item.report}</dd></dl><section class="field-list"><h3>配置表单字段</h3>${item.fields.map(field => `<div><b>${field[0]}</b><code>${field[1]}</code></div>`).join("")}</section>`;
}

function openModal(id) {
  $(`#${id}`).classList.add("open");
  document.body.style.overflow = "hidden";
  if (id === "server-modal") renderAuthFields();
}

function closeModal(modal) {
  modal.classList.remove("open");
  document.body.style.overflow = "";
}

function renderAuthFields() {
  const auth = $('input[name="auth"]:checked')?.value || "alias";
  const templates = {
    alias: `<label><span>SSH 别名</span><input id="server-target" value="doris" required></label>`,
    key: `<div class="auth-grid"><label><span>服务器地址</span><input id="server-target" value="10.0.0.2" required></label><label><span>用户名</span><input value="root"></label><label><span>端口</span><input type="number" value="22"></label><label><span>密钥文件</span><input value="~/.ssh/id_ed25519"></label></div>`,
    password: `<div class="auth-grid"><label><span>服务器地址</span><input id="server-target" value="10.0.0.2" required></label><label><span>用户名</span><input value="root"></label><label><span>端口</span><input type="number" value="22"></label><label><span>密码</span><input type="password" value="password"></label></div>`
  };
  $("#auth-fields").innerHTML = templates[auth];
  $("#save-server").disabled = true;
  $("#server-test").className = "connection-test";
  $("#server-test > span").textContent = "先测试连接，确认当前配置可以登录服务器。";
}

function testServer() {
  const target = $("#server-target")?.value.trim();
  if (!target) { toast("请先填写 SSH 别名或服务器地址", true); return; }
  const result = $("#server-test");
  $("#test-server").disabled = true;
  $("#server-test > span").textContent = `正在连接 ${target}…`;
  setTimeout(() => {
    result.classList.add("success");
    $("#server-test > span").textContent = `连接成功 · root@10.0.0.2 · 42 ms`;
    $("#test-server").textContent = "重新测试";
    $("#test-server").disabled = false;
    $("#save-server").disabled = false;
  }, 850);
}

async function validatePluginFiles(files) {
  const validation = $("#validation");
  validation.hidden = false;
  const manifestFile = files.find(file => file.name === "plugin.yaml");
  if (!manifestFile) {
    renderValidation("未知插件", [{ ok: false, text: "插件根目录缺少 plugin.yaml" }]);
    return false;
  }
  const text = await manifestFile.text();
  const read = key => {
    if (text.trim().startsWith("{")) { try { return JSON.parse(text)[key]; } catch { return null; } }
    return text.match(new RegExp(`^${key}:\\s*[\"']?([^\\n\"']+)`, "m"))?.[1]?.trim();
  };
  const id = read("id"), name = read("name"), version = read("version"), entrypoint = read("entrypoint");
  const paths = files.map(file => file.webkitRelativePath.split("/").slice(1).join("/"));
  const checks = [
    { ok: Boolean(id && name && version && entrypoint), text: id && name && version && entrypoint ? "清单包含 id、name、version、entrypoint" : "plugin.yaml 缺少必要字段" },
    { ok: /^[a-z0-9][a-z0-9.-]{1,63}$/.test(id || ""), text: "插件 ID 格式有效" },
    { ok: /^\d+\.\d+\.\d+/.test(version || ""), text: "版本使用语义化格式" },
    { ok: paths.includes(entrypoint), text: paths.includes(entrypoint) ? `找到入口文件 ${entrypoint}` : `入口文件 ${entrypoint || "未声明"} 不存在` }
  ];
  renderValidation(name || "未知插件", checks, version);
  return checks.every(check => check.ok);
}

function renderValidation(name, checks, version = "") {
  $("#validation").innerHTML = `<h3>${name}${version ? ` · ${version}` : ""}</h3>${checks.map(check => `<div class="check-row ${check.ok ? "" : "error"}"><i>${check.ok ? "✓" : "×"}</i><span>${check.text}</span></div>`).join("")}`;
  $("#confirm-import").disabled = !checks.every(check => check.ok);
}

function toast(message, isError = false) {
  const node = $("#toast");
  $("p", node).textContent = message;
  $("span", node).textContent = isError ? "!" : "✓";
  $("span", node).style.background = isError ? "var(--red)" : "var(--green)";
  node.classList.add("show");
  clearTimeout(node._timer);
  node._timer = setTimeout(() => node.classList.remove("show"), 2600);
}

function openReport() {
  showPage("diagnostic");
  showStage("result");
  clearTimeout(state.aiReadyTimer);
  $("#ai-loading").hidden = true;
  $("#ai-report").hidden = false;
  $("#ai-status").textContent = "已完成";
  $("#ai-status").classList.add("ready");
}

function closeMobileNav() { $("#sidebar").classList.remove("open"); $("#scrim").classList.remove("open"); }

$$('.nav-item[data-page="server"]').forEach(button => button.addEventListener("click", () => selectServer(button)));
$$('.nav-item[data-page]:not([data-page="server"])').forEach(button => button.addEventListener("click", () => showPage(button.dataset.page)));
$("#start-diagnostic").addEventListener("click", beginDiagnostic);
$("#leave-diagnostic").addEventListener("click", () => { if (state.running) stopRun(); showPage("server"); });
$("#to-configure").addEventListener("click", () => { selectedPlugin(); renderPluginFields(); showStage("configure"); });
$$('[data-back]').forEach(button => button.addEventListener("click", () => showStage(button.dataset.back)));
$("#start-run").addEventListener("click", event => { if ($("#diagnostic-form").reportValidity()) beginRun(); event.preventDefault(); });
$("#stop-run").addEventListener("click", stopRun);
$("#rerun").addEventListener("click", () => showStage("configure"));

$$('.plugin-option input').forEach(input => input.addEventListener("change", selectedPlugin));
$("#plugin-search").addEventListener("input", event => {
  const query = event.target.value.trim().toLowerCase();
  $$(".plugin-option").forEach(node => node.hidden = !node.textContent.toLowerCase().includes(query));
});

$$('.tabs [data-tab]').forEach(button => button.addEventListener("click", () => {
  $$(".tabs [data-tab]").forEach(item => item.classList.toggle("active", item === button));
  $$(".tab-panel").forEach(panel => panel.classList.toggle("active", panel.id === `tab-${button.dataset.tab}`));
}));
$$('[data-copy]').forEach(button => button.addEventListener("click", async () => { await navigator.clipboard.writeText($(`#${button.dataset.copy}`).textContent); toast("原始输出已复制"); }));
$$('[data-report]').forEach(button => button.addEventListener("click", openReport));

$$('.library-item').forEach(button => button.addEventListener("click", () => renderLibrary(button.dataset.library)));
$("#rescan").addEventListener("click", () => toast("扫描完成：3 个有效，1 个需要处理"));
renderLibrary("doris");

$$('[data-modal]').forEach(button => button.addEventListener("click", () => openModal(button.dataset.modal)));
$$('[data-close]').forEach(button => button.addEventListener("click", () => closeModal(button.closest(".modal"))));
$$('.modal').forEach(modal => modal.addEventListener("mousedown", event => { if (event.target === modal) closeModal(modal); }));
$$('input[name="auth"]').forEach(input => input.addEventListener("change", renderAuthFields));
$("#test-server").addEventListener("click", testServer);
$("#server-form").addEventListener("submit", event => {
  event.preventDefault();
  const name = $("#new-server-name").value.trim() || "新服务器";
  const target = $("#server-target")?.value.trim() || "new-server";
  const button = document.createElement("button");
  button.className = "nav-item";
  button.dataset.page = "server";
  button.dataset.server = name;
  button.dataset.address = `${target} · root@10.0.0.20:22`;
  const dot = document.createElement("i");
  dot.className = "dot online";
  const copy = document.createElement("span");
  const title = document.createElement("b");
  const subtitle = document.createElement("small");
  title.textContent = name;
  subtitle.textContent = `${target} · 10.0.0.20`;
  copy.append(title, subtitle);
  button.append(dot, copy);
  button.addEventListener("click", () => selectServer(button));
  $(".tools-heading").before(button);
  closeModal($("#server-modal"));
  selectServer(button);
  toast(`${name} 已保存`);
});

$("#plugin-folder").addEventListener("change", async event => { const valid = await validatePluginFiles([...event.target.files]); $("#confirm-import").disabled = !valid; });
$("#invalid-demo").addEventListener("click", () => { $("#validation").hidden = false; renderValidation("custom-check", [{ok:true,text:"找到 plugin.yaml"},{ok:true,text:"插件 ID 与版本有效"},{ok:false,text:"入口文件 run.sh 不存在"},{ok:false,text:"报告模板 report/template.html 不存在"}]); });
$("#confirm-import").addEventListener("click", () => { closeModal($("#plugin-modal")); toast("插件已导入并加入插件库"); });

$("#retest-server").addEventListener("click", event => { event.currentTarget.textContent = "正在测试…"; setTimeout(() => { event.currentTarget.textContent = "重新测试连接"; toast("连接成功 · 42 ms"); }, 700); });
$("#test-ai").addEventListener("click", event => { event.currentTarget.disabled = true; $("#ai-test-result").textContent = "正在等待模型响应…"; setTimeout(() => { event.currentTarget.disabled = false; $("#ai-test-result").textContent = "连接成功 · 模型回复 OK"; toast("AI 接口连接成功"); }, 950); });
$("#clear-cache").addEventListener("click", () => { $("#cache-size").textContent = "0 B"; toast("本机缓存已清空"); });

$("#mobile-menu").addEventListener("click", () => { $("#sidebar").classList.add("open"); $("#scrim").classList.add("open"); });
$("#scrim").addEventListener("click", closeMobileNav);
document.addEventListener("keydown", event => { if (event.key === "Escape") { const modal = $(".modal.open"); if (modal) closeModal(modal); else closeMobileNav(); } });

renderPluginFields();
