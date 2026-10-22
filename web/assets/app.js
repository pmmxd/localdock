(() => {
  "use strict";

  const tokenMeta = document.querySelector('meta[name="locadock-token"]');
  const token = tokenMeta ? tokenMeta.content : "";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));

  const state = {
    services: [],
    summary: { total: 0, running: 0, stopped: 0 },
    manager: null,
    suggestedPort: 7000,
    search: "",
    loading: false,
    busy: new Set(),
    logServiceId: null,
    logContent: "",
    shuttingDown: false,
  };

  const elements = {
    serviceList: $("#service-list"),
    emptyState: $("#empty-state"),
    emptyTitle: $("#empty-title"),
    emptyCopy: $("#empty-copy"),
    search: $("#service-search"),
    serviceDialog: $("#service-dialog"),
    serviceForm: $("#service-form"),
    serviceTitle: $("#service-dialog-title"),
    serviceEyebrow: $("#service-dialog-eyebrow"),
    serviceName: $("#service-name"),
    serviceRoot: $("#service-root"),
    serviceEntry: $("#service-entry"),
    servicePort: $("#service-port"),
    servicePortHint: $("#port-hint"),
    autoAssignPort: $("#auto-assign-port"),
    serviceAutoOpen: $("#service-auto-open"),
    serviceBridgeEnabled: $("#service-bridge-enabled"),
    bridgeRootField: $("#bridge-root-field"),
    serviceBridgeRoot: $("#service-bridge-root"),
    writeSwitchRow: $("#write-switch-row"),
    serviceWriteEnabled: $("#service-write-enabled"),
    serviceWatchEnabled: $("#service-watch-enabled"),
    watchOptions: $("#watch-options"),
    serviceWatchRoot: $("#service-watch-root"),
    serviceWatchInterval: $("#service-watch-interval"),
    serviceAutoReload: $("#service-auto-reload"),
    serviceAutoCommand: $("#service-auto-command"),
    serviceCommandEnabled: $("#service-command-enabled"),
    commandOptions: $("#command-options"),
    serviceCommands: $("#service-commands"),
    formError: $("#form-error"),
    saveService: $("#save-service"),
    logsDialog: $("#logs-dialog"),
    logsTitle: $("#logs-title"),
    logContent: $("#log-content"),
    logsPath: $("#logs-path"),
    confirmDialog: $("#confirm-dialog"),
    confirmTitle: $("#confirm-title"),
    confirmMessage: $("#confirm-message"),
    confirmOk: $("#confirm-ok"),
    confirmCancel: $("#confirm-cancel"),
    toastStack: $("#toast-stack"),
    dataMenu: $("#data-menu"),
    dataMenuButton: $("#data-menu-button"),
    shutdownScreen: $("#shutdown-screen"),
  };

  const icon = (name) =>
    `<svg class="icon" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;

  function escapeHtml(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;")
      .replaceAll("'", "&#039;");
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) return "";
    return new Intl.DateTimeFormat("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    }).format(date);
  }

  async function api(path, options = {}) {
    const headers = new Headers(options.headers || {});
    headers.set("X-LocalDock-Token", token);
    let body = options.body;
    if (body && typeof body !== "string") {
      headers.set("Content-Type", "application/json");
      body = JSON.stringify(body);
    }
    const response = await fetch(path, { ...options, headers, body });
    let payload = null;
    try {
      payload = await response.json();
    } catch {
      payload = null;
    }
    if (!response.ok) {
      throw new Error(payload?.error || `请求失败 (${response.status})`);
    }
    return payload;
  }

  function showToast(message, type = "success") {
    const toast = document.createElement("div");
    toast.className = `toast${type === "error" ? " is-error" : ""}`;
    toast.textContent = message;
    elements.toastStack.append(toast);
    window.setTimeout(() => toast.remove(), 3800);
  }

  function setBusy(key, value) {
    if (value) state.busy.add(key);
    else state.busy.delete(key);
    renderServices();
  }

  function filteredServices() {
    const query = state.search.trim().toLocaleLowerCase();
    if (!query) return state.services;
    return state.services.filter((service) =>
      [service.name, service.root_path, String(service.port)]
        .join(" ")
        .toLocaleLowerCase()
        .includes(query),
    );
  }

  function renderSummary() {
    $("#metric-total").textContent = state.summary.total;
    $("#metric-running").textContent = state.summary.running;
    $("#metric-stopped").textContent = state.summary.stopped;
    $("#nav-count").textContent = state.summary.total;
    $("#list-summary").textContent = state.search
      ? `${filteredServices().length} / ${state.summary.total} 个站点`
      : `${state.summary.total} 个站点`;
    if (state.manager) {
      $("#manager-version").textContent = `v${state.manager.version} · 数据保存在本地`;
      $("#manager-version").title = state.manager.data_dir;
      try {
        $("#manager-address").textContent = new URL(state.manager.url).host;
      } catch {
        $("#manager-address").textContent = "127.0.0.1";
      }
    }
  }

  function actionButton(service, action, iconName, label, extraClass = "", disabled = false) {
    const busyKey = `${service.id}:${action}`;
    const busy = state.busy.has(busyKey);
    return `
      <button
        class="action-button ${extraClass}${busy ? " is-busy" : ""}"
        type="button"
        data-action="${action}"
        data-id="${service.id}"
        title="${escapeHtml(label)}"
        aria-label="${escapeHtml(label)}"
        ${disabled || busy ? "disabled" : ""}
      >
        ${icon(busy ? "rotate" : iconName)}
      </button>
    `;
  }

  function renderServices() {
    renderSummary();
    const services = filteredServices();
    elements.serviceList.innerHTML = services
      .map((service) => {
        const running = service.status === "running";
        const lastStarted = formatDate(service.last_started_at);
        return `
          <tr data-service-id="${service.id}">
            <td class="col-status">
              <span class="status-pill${running ? " is-running" : ""}">
                ${running ? "运行中" : "已停止"}
              </span>
            </td>
            <td class="col-site">
              <div class="site-cell">
                <div class="site-name">
                  <span title="${escapeHtml(service.name)}">${escapeHtml(service.name)}</span>
                  ${
                    service.write_enabled
                      ? '<span class="feature-badge">文件管理</span>'
                      : service.bridge_enabled
                        ? '<span class="feature-badge">本地打开</span>'
                        : ""
                  }
                  ${service.watch_enabled ? '<span class="feature-badge">实时监控</span>' : ""}
                  ${service.command_enabled ? '<span class="feature-badge">命令</span>' : ""}
                </div>
                <div class="site-path" title="${escapeHtml(service.root_path)}">
                  ${escapeHtml(service.root_path)}
                </div>
              </div>
            </td>
            <td class="col-address">
              ${
                running
                  ? `<a class="url-link" href="${escapeHtml(service.url)}" target="_blank" rel="noreferrer">${escapeHtml(service.url)}</a>`
                  : `<span class="url-muted">${escapeHtml(service.url)}</span>`
              }
            </td>
            <td class="col-port">
              <span class="port-badge" title="${lastStarted ? `上次启动 ${lastStarted}` : ""}">${service.port}</span>
            </td>
            <td class="col-actions">
              <div class="row-actions">
                ${actionButton(
                  service,
                  "toggle",
                  running ? "square" : "play",
                  running ? "停止站点" : "启动站点",
                  running ? "" : "is-start",
                )}
                ${actionButton(service, "restart", "rotate", "重启站点", "", !running)}
                ${actionButton(service, "refresh", "refresh", "刷新内容", "", !running)}
                ${actionButton(service, "open", "external", "打开站点", "", !running)}
                ${actionButton(service, "logs", "file", "查看日志")}
                ${actionButton(service, "edit", "pencil", "编辑站点")}
                ${actionButton(service, "delete", "trash", "删除站点", "is-danger")}
              </div>
            </td>
          </tr>
        `;
      })
      .join("");

    if (services.length > 0) {
      elements.emptyState.hidden = true;
      return;
    }
    elements.emptyState.hidden = false;
    if (state.services.length > 0 && state.search) {
      elements.emptyTitle.textContent = "没有匹配的站点";
      elements.emptyCopy.textContent = "换一个关键词再试试。";
      $("#empty-add").hidden = true;
    } else {
      elements.emptyTitle.textContent = "还没有站点";
      elements.emptyCopy.textContent = "添加一个 HTML 项目目录开始使用。";
      $("#empty-add").hidden = false;
    }
  }

  async function loadState({ quiet = false } = {}) {
    if (state.loading || state.shuttingDown) return;
    state.loading = true;
    try {
      const payload = await api("/api/state");
      state.services = payload.services;
      state.summary = payload.summary;
      state.suggestedPort = payload.suggested_port;
      state.manager = { ...payload.manager, version: payload.version };
      renderServices();
    } catch (error) {
      if (!quiet) showToast(error.message, "error");
    } finally {
      state.loading = false;
    }
  }

  function getService(serviceId) {
    return state.services.find((service) => service.id === serviceId);
  }

  async function runServiceAction(service, action) {
    const busyKey = `${service.id}:${action}`;
    setBusy(busyKey, true);
    try {
      if (action === "toggle") {
        const stopping = service.status === "running";
        await api(`/api/services/${service.id}/${stopping ? "stop" : "start"}`, {
          method: "POST",
        });
        showToast(stopping ? `“${service.name}”已停止` : `“${service.name}”已启动`);
      } else if (action === "restart") {
        await api(`/api/services/${service.id}/restart`, { method: "POST" });
        showToast(`“${service.name}”已重启`);
      } else if (action === "refresh") {
        await api(`/api/services/${service.id}/refresh-content`, {
          method: "POST",
        });
        showToast(`“${service.name}”已刷新`);
      } else if (action === "open") {
        await api(`/api/services/${service.id}/open`, { method: "POST" });
      }
      await loadState();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      state.busy.delete(busyKey);
      renderServices();
    }
  }

  function openServiceDialog(service = null) {
    elements.serviceForm.reset();
    elements.formError.hidden = true;
    elements.formError.textContent = "";
    elements.serviceDialog.dataset.editingId = service?.id || "";
    elements.serviceDialog.dataset.dismissMode = "x-only";
    elements.serviceDialog.setAttribute("closedby", "none");
    elements.serviceDialog.dataset.portTouched = "false";
    elements.serviceTitle.textContent = service ? "编辑站点" : "新增站点";
    elements.serviceEyebrow.textContent = service ? "EDIT SERVICE" : "NEW SERVICE";
    elements.saveService.textContent = service ? "保存修改" : "保存站点";
    elements.serviceName.value = service?.name || "";
    elements.serviceRoot.value = service?.root_path || "";
    elements.serviceEntry.value = service?.entry_file || "index.html";
    elements.servicePort.value = service?.port || state.suggestedPort;
    elements.servicePortHint.textContent = service
      ? "端口可手动修改，但不能与已有站点重复"
      : "正在确认唯一端口，默认从 7000 起分配";
    elements.serviceAutoOpen.checked = service ? Boolean(service.auto_open) : true;
    elements.serviceBridgeEnabled.checked = service
      ? Boolean(service.bridge_enabled)
      : false;
    elements.serviceBridgeRoot.value = service?.bridge_root || "";
    elements.serviceWriteEnabled.checked = service
      ? Boolean(service.write_enabled)
      : false;
    elements.serviceWatchEnabled.checked = service
      ? Boolean(service.watch_enabled)
      : false;
    elements.serviceWatchRoot.value = service?.watch_root || "";
    elements.serviceWatchInterval.value = service?.watch_interval || 5;
    elements.serviceAutoReload.checked = service
      ? Boolean(service.auto_reload)
      : false;
    elements.serviceAutoCommand.value = service?.auto_command_id || "";
    updateWatchOptions();
    elements.serviceCommandEnabled.checked = service
      ? Boolean(service.command_enabled)
      : false;
    elements.serviceCommands.value = formatCommandConfig(service?.commands_json);
    updateCommandOptions();
    updateBridgeRootField();
    elements.serviceDialog.showModal();
    window.setTimeout(() => elements.serviceName.focus(), 30);
    if (!service) refreshAutomaticPortPreview();
  }

  function closeDialog(dialog) {
    if (dialog?.open) dialog.close();
  }

  async function saveService(event) {
    event.preventDefault();
    const editingId = elements.serviceDialog.dataset.editingId;
    const payload = {
      name: elements.serviceName.value,
      root_path: elements.serviceRoot.value,
      entry_file: elements.serviceEntry.value,
      auto_open: elements.serviceAutoOpen.checked,
      bridge_enabled: elements.serviceBridgeEnabled.checked,
      bridge_root: elements.serviceBridgeRoot.value,
      write_enabled: elements.serviceWriteEnabled.checked,
      watch_enabled: elements.serviceWatchEnabled.checked,
      watch_root: elements.serviceWatchRoot.value,
      watch_interval: Number(elements.serviceWatchInterval.value),
      auto_reload: elements.serviceAutoReload.checked,
      auto_command_id: elements.serviceAutoCommand.value,
      command_enabled: elements.serviceCommandEnabled.checked,
      commands_json: elements.serviceCommands.value,
      port_mode:
        editingId || elements.serviceDialog.dataset.portTouched === "true"
          ? "manual"
          : "auto",
    };
    payload.port = Number(elements.servicePort.value);
    elements.formError.hidden = true;
    elements.saveService.disabled = true;
    elements.saveService.textContent = "保存中...";
    try {
      const result = await api(editingId ? `/api/services/${editingId}` : "/api/services", {
        method: editingId ? "PUT" : "POST",
        body: payload,
      });
      closeDialog(elements.serviceDialog);
      showToast(
        editingId ? "站点配置已更新" : `站点已添加，自动分配端口 ${result.service.port}`,
      );
      await loadState();
    } catch (error) {
      elements.formError.textContent = error.message;
      elements.formError.hidden = false;
    } finally {
      elements.saveService.disabled = false;
      elements.saveService.textContent = editingId ? "保存修改" : "保存站点";
    }
  }

  function confirmAction({ title, message, confirmLabel = "确认", danger = false }) {
    elements.confirmTitle.textContent = title;
    elements.confirmMessage.textContent = message;
    elements.confirmOk.textContent = confirmLabel;
    elements.confirmOk.className = `button ${danger ? "button-danger" : "button-primary"}`;
    elements.confirmDialog.showModal();
    return new Promise((resolve) => {
      const finish = (result) => {
        elements.confirmOk.removeEventListener("click", accept);
        elements.confirmCancel.removeEventListener("click", cancel);
        elements.confirmDialog.removeEventListener("cancel", cancel);
        closeDialog(elements.confirmDialog);
        resolve(result);
      };
      const accept = () => finish(true);
      const cancel = (event) => {
        event?.preventDefault();
        finish(false);
      };
      elements.confirmOk.addEventListener("click", accept);
      elements.confirmCancel.addEventListener("click", cancel);
      elements.confirmDialog.addEventListener("cancel", cancel);
    });
  }

  async function deleteService(service) {
    const confirmed = await confirmAction({
      title: "删除站点",
      message: `将删除“${service.name}”的配置。HTML 项目目录中的文件不会被删除。`,
      confirmLabel: "删除",
      danger: true,
    });
    if (!confirmed) return;
    setBusy(`${service.id}:delete`, true);
    try {
      await api(`/api/services/${service.id}`, { method: "DELETE" });
      showToast(`“${service.name}”已删除`);
      await loadState();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      state.busy.delete(`${service.id}:delete`);
      renderServices();
    }
  }

  async function openLogs(service) {
    state.logServiceId = service.id;
    elements.logsTitle.textContent = `${service.name} · 运行日志`;
    elements.logsPath.textContent = service.log_path;
    elements.logContent.textContent = "正在读取日志...";
    elements.logsDialog.showModal();
    await refreshLogs();
  }

  async function refreshLogs() {
    if (!state.logServiceId) return;
    elements.logContent.textContent = "正在读取日志...";
    try {
      const payload = await api(`/api/services/${state.logServiceId}/logs?tail=400`);
      state.logContent = payload.content || "暂无日志。";
      elements.logContent.textContent = state.logContent;
      elements.logsPath.textContent = payload.path;
      requestAnimationFrame(() => {
        elements.logContent.scrollTop = elements.logContent.scrollHeight;
      });
    } catch (error) {
      state.logContent = `读取失败：${error.message}`;
      elements.logContent.textContent = state.logContent;
    }
  }

  async function copyLogs() {
    if (!state.logContent) return;
    try {
      await navigator.clipboard.writeText(state.logContent);
      showToast("日志已复制");
    } catch {
      showToast("浏览器未允许读取剪贴板", "error");
    }
  }

  async function chooseFolder() {
    const button = $("#browse-folder");
    button.disabled = true;
    try {
      const payload = await api("/api/dialog/folder", { method: "POST" });
      if (payload.path) elements.serviceRoot.value = payload.path;
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function chooseBridgeFolder() {
    const button = $("#browse-bridge-root");
    button.disabled = true;
    try {
      const payload = await api("/api/dialog/folder", { method: "POST" });
      if (payload.path) elements.serviceBridgeRoot.value = payload.path;
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function chooseWatchFolder() {
    const button = $("#browse-watch-root");
    button.disabled = true;
    try {
      const payload = await api("/api/dialog/folder", { method: "POST" });
      if (payload.path) elements.serviceWatchRoot.value = payload.path;
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function updateBridgeRootField() {
    const enabled = elements.serviceBridgeEnabled.checked;
    elements.bridgeRootField.hidden = !enabled;
    elements.writeSwitchRow.hidden = !enabled;
    if (!enabled) elements.serviceWriteEnabled.checked = false;
    if (enabled && !elements.serviceBridgeRoot.value) {
      elements.serviceBridgeRoot.value = elements.serviceRoot.value;
    }
  }

  function updateWatchOptions() {
    const enabled = elements.serviceWatchEnabled.checked;
    elements.watchOptions.hidden = !enabled;
    if (!enabled) elements.serviceAutoReload.checked = false;
    if (enabled && !elements.serviceWatchRoot.value) {
      elements.serviceWatchRoot.value =
        elements.serviceBridgeRoot.value || elements.serviceRoot.value;
    }
  }

  function formatCommandConfig(value) {
    if (!value) return "";
    try {
      return JSON.stringify(JSON.parse(value), null, 2);
    } catch {
      return String(value);
    }
  }

  function updateCommandOptions() {
    elements.commandOptions.hidden = !elements.serviceCommandEnabled.checked;
  }

  async function assignAutomaticPort() {
    elements.autoAssignPort.disabled = true;
    try {
      const payload = await api("/api/suggested-port");
      state.suggestedPort = payload.port;
      elements.servicePort.value = payload.port;
      elements.serviceDialog.dataset.portTouched = "false";
      elements.servicePortHint.textContent = `已自动分配端口 ${payload.port}，可手动修改`;
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      elements.autoAssignPort.disabled = false;
    }
  }

  async function refreshAutomaticPortPreview() {
    try {
      const payload = await api("/api/suggested-port");
      const stillCreating = !elements.serviceDialog.dataset.editingId;
      const untouched = elements.serviceDialog.dataset.portTouched !== "true";
      if (elements.serviceDialog.open && stillCreating && untouched) {
        state.suggestedPort = payload.port;
        elements.servicePort.value = payload.port;
        elements.servicePortHint.textContent = `已自动分配端口 ${payload.port}，可手动修改`;
      }
    } catch {
      elements.servicePortHint.textContent =
        "自动分配暂不可用，保存时会再次检查端口";
    }
  }

  async function startAll() {
    const button = $("#start-all");
    button.disabled = true;
    try {
      const payload = await api("/api/start-all", { method: "POST" });
      if (payload.errors.length) {
        const names = payload.errors.map((item) => item.name).join("、");
        showToast(`部分站点未启动：${names}`, "error");
      } else if (payload.started.length) {
        showToast(`已启动 ${payload.started.length} 个站点`);
      } else {
        showToast("没有需要启动的站点");
      }
      await loadState();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function stopAll() {
    const confirmed = await confirmAction({
      title: "停止全部站点",
      message: "正在运行的本地站点将立即停止，管理器会继续运行。",
      confirmLabel: "全部停止",
    });
    if (!confirmed) return;
    const button = $("#stop-all");
    button.disabled = true;
    try {
      const payload = await api("/api/stop-all", { method: "POST" });
      showToast(payload.stopped ? `已停止 ${payload.stopped} 个站点` : "没有运行中的站点");
      await loadState();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function downloadFile(path, fallbackName) {
    try {
      const response = await fetch(path, {
        headers: { "X-LocalDock-Token": token },
      });
      if (!response.ok) {
        let message = "下载失败";
        try {
          const payload = await response.json();
          message = payload.error || message;
        } catch {
          // Keep the generic message for non-JSON failures.
        }
        throw new Error(message);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = fallbackName;
      document.body.append(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      showToast("文件已生成");
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  async function exitManager() {
    const confirmed = await confirmAction({
      title: "退出 LocalDock",
      message: "所有本地站点会被停止，管理端随后关闭。已保存的配置不会丢失。",
      confirmLabel: "停止并退出",
      danger: true,
    });
    if (!confirmed) return;
    try {
      await api("/api/shutdown", { method: "POST" });
      state.shuttingDown = true;
      elements.shutdownScreen.hidden = false;
    } catch (error) {
      showToast(error.message, "error");
    }
  }

  elements.serviceList.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const service = getService(button.dataset.id);
    if (!service) return;
    const action = button.dataset.action;
    if (
      action === "toggle" ||
      action === "restart" ||
      action === "refresh" ||
      action === "open"
    ) {
      runServiceAction(service, action);
    } else if (action === "logs") {
      openLogs(service);
    } else if (action === "edit") {
      openServiceDialog(service);
    } else if (action === "delete") {
      deleteService(service);
    }
  });

  elements.search.addEventListener("input", () => {
    state.search = elements.search.value;
    renderServices();
  });

  $("#add-service").addEventListener("click", () => openServiceDialog());
  $("#empty-add").addEventListener("click", () => openServiceDialog());
  $("#refresh-all").addEventListener("click", () => loadState());
  $("#start-all").addEventListener("click", startAll);
  $("#stop-all").addEventListener("click", stopAll);
  $("#browse-folder").addEventListener("click", chooseFolder);
  $("#browse-bridge-root").addEventListener("click", chooseBridgeFolder);
  $("#browse-watch-root").addEventListener("click", chooseWatchFolder);
  elements.serviceBridgeEnabled.addEventListener("change", updateBridgeRootField);
  elements.serviceWatchEnabled.addEventListener("change", updateWatchOptions);
  elements.serviceCommandEnabled.addEventListener("change", updateCommandOptions);
  elements.autoAssignPort.addEventListener("click", assignAutomaticPort);
  elements.servicePort.addEventListener("input", () => {
    if (!elements.serviceDialog.dataset.editingId) {
      elements.serviceDialog.dataset.portTouched = "true";
      elements.servicePortHint.textContent =
        "手动端口，保存时会检查是否与已有站点重复";
    }
  });
  $("#exit-manager").addEventListener("click", exitManager);
  $("#refresh-logs").addEventListener("click", refreshLogs);
  $("#copy-logs").addEventListener("click", copyLogs);
  elements.serviceForm.addEventListener("submit", saveService);

  $$("[data-close-dialog]").forEach((button) => {
    button.addEventListener("click", () => closeDialog(button.closest("dialog")));
  });

  $$(".modal").forEach((dialog) => {
    dialog.addEventListener("cancel", (event) => {
      if (dialog.dataset.dismissMode === "x-only") event.preventDefault();
    });
    dialog.addEventListener("click", (event) => {
      if (event.target !== dialog) return;
      if (dialog.dataset.dismissMode === "x-only") return;
      const bounds = dialog.getBoundingClientRect();
      const inDialog =
        event.clientX >= bounds.left &&
        event.clientX <= bounds.right &&
        event.clientY >= bounds.top &&
        event.clientY <= bounds.bottom;
      if (!inDialog && dialog !== elements.confirmDialog) closeDialog(dialog);
    });
  });

  elements.dataMenuButton.addEventListener("click", (event) => {
    event.stopPropagation();
    const opening = elements.dataMenu.hidden;
    elements.dataMenu.hidden = !opening;
    elements.dataMenuButton.setAttribute("aria-expanded", String(opening));
  });

  elements.dataMenu.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-download]");
    if (!button) return;
    elements.dataMenu.hidden = true;
    elements.dataMenuButton.setAttribute("aria-expanded", "false");
    downloadFile(button.dataset.download, button.dataset.filename);
  });

  document.addEventListener("click", (event) => {
    if (!elements.dataMenu.hidden && !event.target.closest(".menu-wrap")) {
      elements.dataMenu.hidden = true;
      elements.dataMenuButton.setAttribute("aria-expanded", "false");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.ctrlKey && event.key.toLocaleLowerCase() === "k") {
      event.preventDefault();
      elements.search.focus();
    }
  });

  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) loadState({ quiet: true });
  });

  window.setInterval(() => {
    if (!document.hidden) loadState({ quiet: true });
  }, 3000);

  loadState();
})();
