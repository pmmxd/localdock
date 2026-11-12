(() => {
  "use strict";

  const tokenMeta = document.querySelector('meta[name="locadock-token"]');
  const token = tokenMeta ? tokenMeta.content : "";
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => Array.from(root.querySelectorAll(selector));
  const COLLAPSED_GROUPS_KEY = "locadock-collapsed-groups";

  function loadCollapsedGroups() {
    try {
      const value = JSON.parse(localStorage.getItem(COLLAPSED_GROUPS_KEY) || "[]");
      return new Set(
        Array.isArray(value) ? value.filter((item) => typeof item === "string") : [],
      );
    } catch {
      return new Set();
    }
  }

  const state = {
    services: [],
    summary: { total: 0, running: 0, stopped: 0 },
    manager: null,
    suggestedPort: 7000,
    search: "",
    loading: false,
    busy: new Set(),
    collapsedGroups: loadCollapsedGroups(),
    selectedServices: new Set(),
    addGroupServiceId: null,
    renameGroupName: "",
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
    groupBatch: $("#group-batch"),
    selectionCount: $("#selection-count"),
    groupName: $("#group-name"),
    createGroup: $("#create-group"),
    groupTarget: $("#group-target"),
    addToGroup: $("#add-to-group"),
    removeGroup: $("#remove-group"),
    clearSelection: $("#clear-selection"),
    selectAllServices: $("#select-all-services"),
    addGroupDialog: $("#add-group-dialog"),
    addGroupForm: $("#add-group-form"),
    addGroupServiceName: $("#add-group-service-name"),
    addGroupTarget: $("#add-group-target"),
    addGroupError: $("#add-group-error"),
    addGroupSave: $("#add-group-save"),
    renameGroupDialog: $("#rename-group-dialog"),
    renameGroupForm: $("#rename-group-form"),
    renameGroupName: $("#rename-group-name"),
    renameGroupError: $("#rename-group-error"),
    saveGroupName: $("#save-group-name"),
    serviceDialog: $("#service-dialog"),
    serviceForm: $("#service-form"),
    serviceTitle: $("#service-dialog-title"),
    serviceEyebrow: $("#service-dialog-eyebrow"),
    serviceName: $("#service-name"),
    serviceType: $("#service-type"),
    serviceRoot: $("#service-root"),
    serviceEntry: $("#service-entry"),
    servicePort: $("#service-port"),
    servicePortHint: $("#port-hint"),
    autoAssignPort: $("#auto-assign-port"),
    serviceAutoOpen: $("#service-auto-open"),
    serviceBridgeEnabled: $("#service-bridge-enabled"),
    bridgeRootField: $("#bridge-root-field"),
    serviceBridgeRoot: $("#service-bridge-root"),
    externalAppField: $("#external-app-field"),
    serviceExternalApp: $("#service-external-app"),
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
    nodeOptions: $("#node-options"),
    nodeRuntimePath: $("#node-runtime-path"),
    nodeArgs: $("#node-args"),
    nodeCwd: $("#node-cwd"),
    nodeEnvFile: $("#node-env-file"),
    nodeHealthPath: $("#node-health-path"),
    nodeStartupTimeout: $("#node-startup-timeout"),
    nodeShutdownTimeout: $("#node-shutdown-timeout"),
    nodeRestartDelay: $("#node-restart-delay"),
    nodeAutoRestart: $("#node-auto-restart"),
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
      serviceMatchesSearch(service, query),
    );
  }

  function serviceGroupName(service) {
    return String(service.group_name || "").trim();
  }

  function serviceGroupKey(service) {
    return serviceGroupName(service).toLocaleLowerCase();
  }

  function existingGroupNames() {
    return [
      ...new Set(
        state.services
          .map((service) => serviceGroupName(service))
          .filter(Boolean),
      ),
    ].sort((left, right) => left.localeCompare(right, "zh-CN"));
  }

  function suggestedGroupName(services) {
    const names = services.map((service) => service.name.trim()).filter(Boolean);
    const tokenLists = names.map((name) => name.split(/\s+/));
    const common = [];
    for (let index = 0; index < (tokenLists[0]?.length || 0); index += 1) {
      const token = tokenLists[0][index];
      if (
        tokenLists.every(
          (tokens) => tokens[index]?.toLocaleLowerCase() === token.toLocaleLowerCase(),
        )
      ) {
        common.push(token);
      } else {
        break;
      }
    }
    const commonName = common.join(" ").trim();
    const baseName =
      commonName.length >= 3
        ? commonName
        : (names[0] || "新分组")
            .replace(/\s*(backend|frontend|tools?|server|client|web|api|service)\s*$/i, "")
            .replace(/(后端|前端|工具|服务)(（[^）]*）|\([^)]*\))?\s*$/, "")
            .trim() || "新分组";
    const used = new Set(
      state.services
        .map((service) => serviceGroupName(service))
        .filter(Boolean)
        .map((name) => name.toLocaleLowerCase()),
    );
    let candidate = baseName;
    let suffix = 2;
    while (used.has(candidate.toLocaleLowerCase())) {
      candidate = `${baseName} ${suffix}`;
      suffix += 1;
    }
    return candidate;
  }

  function serviceMatchesSearch(service, query) {
    return [service.name, service.root_path, String(service.port)]
      .join(" ")
      .toLocaleLowerCase()
      .includes(query);
  }

  function groupedEntries() {
    const query = state.search.trim().toLocaleLowerCase();
    const byGroup = new Map();
    for (const service of state.services) {
      const name = serviceGroupName(service);
      const key = serviceGroupKey(service);
      if (!key) continue;
      if (!byGroup.has(key)) byGroup.set(key, { name, services: [] });
      byGroup.get(key).services.push(service);
    }

    const entries = [];
    const emittedGroups = new Set();
    for (const service of state.services) {
      const key = serviceGroupKey(service);
      const group = byGroup.get(key);
      if (!group) {
        if (!query || serviceMatchesSearch(service, query)) {
          entries.push({ type: "service", service });
        }
        continue;
      }
      if (emittedGroups.has(key)) continue;
      emittedGroups.add(key);
      if (query && !group.services.some((item) => serviceMatchesSearch(item, query))) {
        continue;
      }
      const roots = [...new Set(group.services.map((item) => item.root_path))];
      entries.push({
        type: "group",
        key,
        label: group.name,
        rootPath: roots.length === 1 ? roots[0] : `${roots.length} 个目录`,
        services: group.services,
      });
    }
    return entries;
  }

  function visibleServiceCount() {
    return groupedEntries().reduce(
      (count, entry) => count + (entry.type === "group" ? entry.services.length : 1),
      0,
    );
  }

  function getGroupByKey(key) {
    const services = state.services.filter((service) => serviceGroupKey(service) === key);
    if (services.length === 0) return null;
    const roots = [...new Set(services.map((service) => service.root_path))];
    return {
      key,
      label: serviceGroupName(services[0]),
      rootPath: roots.length === 1 ? roots[0] : `${roots.length} 个目录`,
      services,
    };
  }

  function persistCollapsedGroups() {
    try {
      localStorage.setItem(
        COLLAPSED_GROUPS_KEY,
        JSON.stringify([...state.collapsedGroups]),
      );
    } catch {
      /* Storage can be unavailable when browser privacy settings are strict. */
    }
  }

  function updateGroupControls() {
    const count = state.selectedServices.size;
    const busy = state.busy.has("selection:group");
    const previousTarget = elements.groupTarget.value;
    const groups = existingGroupNames();
    elements.groupTarget.innerHTML = [
      '<option value="">选择已有分组</option>',
      ...groups.map(
        (name) =>
          `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`,
      ),
    ].join("");
    const matchingTarget = groups.find(
      (name) => name.toLocaleLowerCase() === previousTarget.toLocaleLowerCase(),
    );
    elements.groupTarget.value = matchingTarget || "";
    elements.groupBatch.hidden = count === 0;
    elements.selectionCount.textContent = `已选 ${count} 个服务`;
    elements.createGroup.disabled = busy || count < 2;
    elements.groupTarget.disabled = busy || groups.length === 0;
    elements.addToGroup.disabled = busy || count === 0 || !elements.groupTarget.value;
    elements.removeGroup.disabled = busy || count === 0;
    elements.clearSelection.disabled = busy || count === 0;
    elements.groupName.disabled = busy;
    updateSelectionIndicators();
  }

  function updateSelectionIndicators() {
    const visible = filteredServices();
    const selectedVisible = visible.filter((service) =>
      state.selectedServices.has(service.id),
    ).length;
    elements.selectAllServices.checked =
      visible.length > 0 && selectedVisible === visible.length;
    elements.selectAllServices.indeterminate =
      selectedVisible > 0 && selectedVisible < visible.length;

    for (const checkbox of $$("input[data-select-group]")) {
      let key = "";
      try {
        key = decodeURIComponent(checkbox.dataset.selectGroup || "");
      } catch {
        continue;
      }
      const members = state.services.filter(
        (service) => serviceGroupKey(service) === key,
      );
      const selected = members.filter((service) =>
        state.selectedServices.has(service.id),
      ).length;
      checkbox.checked = members.length > 0 && selected === members.length;
      checkbox.indeterminate = selected > 0 && selected < members.length;
    }
  }

  function clearSelectedServices() {
    state.selectedServices.clear();
    elements.groupName.value = "";
    updateGroupControls();
    renderServices();
  }

  function refreshSelectedGroupName() {
    if (state.selectedServices.size === 0) {
      elements.groupName.value = "";
      return;
    }
    if (elements.groupName.value.trim()) return;
    const selected = [...state.selectedServices]
      .map((serviceId) => getService(serviceId))
      .filter(Boolean);
    const names = [
      ...new Set(selected.map((service) => serviceGroupName(service)).filter(Boolean)),
    ];
    const allSameGroup =
      names.length === 1 &&
      selected.every((service) => serviceGroupName(service) === names[0]);
    elements.groupName.value = allSameGroup
      ? names[0]
      : suggestedGroupName(selected);
  }

  function renderSummary() {
    $("#metric-total").textContent = state.summary.total;
    $("#metric-running").textContent = state.summary.running;
    $("#metric-stopped").textContent = state.summary.stopped;
    $("#nav-count").textContent = state.summary.total;
    $("#list-summary").textContent = state.search
      ? `${visibleServiceCount()} / ${state.summary.total} 个站点`
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
    const groupKey = serviceGroupKey(service);
    const groupBusy =
      Boolean(groupKey) &&
      (state.busy.has(`group:${groupKey}:start-group`) ||
        state.busy.has(`group:${groupKey}:stop-group`) ||
        state.busy.has(`group:${groupKey}:rename-group`));
    return `
      <button
        class="action-button ${extraClass}${busy ? " is-busy" : ""}"
        type="button"
        data-action="${action}"
        data-id="${service.id}"
        title="${escapeHtml(label)}"
        aria-label="${escapeHtml(label)}"
        ${disabled || busy || groupBusy ? "disabled" : ""}
      >
        ${icon(busy ? "rotate" : iconName)}
      </button>
    `;
  }

  function groupActionButton(group, action, iconName, label, disabled = false) {
    const busyKey = `group:${group.key}:${action}`;
    const busy = state.busy.has(busyKey);
    const groupBusy =
      state.busy.has(`group:${group.key}:start-group`) ||
      state.busy.has(`group:${group.key}:stop-group`) ||
      state.busy.has(`group:${group.key}:rename-group`);
    return `
      <button
        class="group-action${busy ? " is-busy" : ""}"
        type="button"
        data-action="${action}"
        data-group="${escapeHtml(encodeURIComponent(group.key))}"
        title="${escapeHtml(label)}"
        aria-label="${escapeHtml(label)}"
        ${disabled || busy || groupBusy ? "disabled" : ""}
      >
        ${icon(busy ? "rotate" : iconName)}
        <span>${escapeHtml(label)}</span>
      </button>
    `;
  }

  function groupHeaderMarkup(group) {
    const running = group.services.filter((service) => service.status === "running").length;
    const selected = group.services.filter((service) =>
      state.selectedServices.has(service.id),
    ).length;
    const collapsed = state.collapsedGroups.has(group.key);
    return `
      <tr class="service-group-row" data-group-key="${escapeHtml(encodeURIComponent(group.key))}">
        <td colspan="5">
          <div class="group-toolbar">
            <input
              class="service-select group-select"
              type="checkbox"
              data-select-group="${escapeHtml(encodeURIComponent(group.key))}"
              aria-label="选择分组 ${escapeHtml(group.label)}"
              ${selected === group.services.length ? "checked" : ""}
            >
            <button
              class="group-toggle"
              type="button"
              data-action="toggle-group"
              data-group="${escapeHtml(encodeURIComponent(group.key))}"
              aria-expanded="${String(!collapsed)}"
              title="${collapsed ? "展开分组" : "折叠分组"}"
            >
              <span class="group-chevron" aria-hidden="true"></span>
              <span class="group-folder">${icon("folder")}</span>
              <span class="group-copy">
                <strong>${escapeHtml(group.label)}</strong>
                <small>${escapeHtml(group.rootPath)}</small>
              </span>
              <span class="group-summary">${group.services.length} 个服务 · ${running} 运行中</span>
            </button>
            <div class="group-actions">
              ${groupActionButton(group, "rename-group", "pencil", "重命名")}
              ${groupActionButton(group, "start-group", "play", "启动组", running === group.services.length)}
              ${groupActionButton(group, "stop-group", "square", "停止组", running === 0)}
            </div>
          </div>
        </td>
      </tr>
    `;
  }

  function serviceRowMarkup(service, options = {}) {
    const { grouped = false, hidden = false } = options;
    const running = service.status === "running";
    const backendService = service.service_type === "node";
    const lastStarted = formatDate(service.last_started_at);
    const selected = state.selectedServices.has(service.id);
    const ungrouped = !serviceGroupName(service);
    const hasGroups = existingGroupNames().length > 0;
    return `
      <tr class="${grouped ? "group-service-row" : ""}" data-service-id="${service.id}" ${hidden ? "hidden" : ""}>
        <td class="col-status">
          <div class="status-select">
            <input
              class="service-select"
              type="checkbox"
              data-select-id="${service.id}"
              aria-label="选择 ${escapeHtml(service.name)}"
              ${selected ? "checked" : ""}
            >
            <span class="status-pill${running ? " is-running" : ""}">
              ${running ? "运行中" : "已停止"}
            </span>
          </div>
        </td>
        <td class="col-site">
          <div class="site-cell">
            <div class="site-name">
              <span title="${escapeHtml(service.name)}">${escapeHtml(service.name)}</span>
              ${
                backendService
                  ? `<span class="feature-badge">${backendServiceLabel(service)}</span>`
                  : `${service.write_enabled ? '<span class="feature-badge">文件管理</span>' : service.bridge_enabled ? '<span class="feature-badge">本地打开</span>' : ""}${service.watch_enabled ? '<span class="feature-badge">实时监控</span>' : ""}${service.command_enabled ? '<span class="feature-badge">命令</span>' : ""}`
              }
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
            ${
              ungrouped
                ? actionButton(
                    service,
                    "add-group",
                    "folder-plus",
                    hasGroups ? "添加到分组" : "请先创建分组",
                    "",
                    !hasGroups,
                  )
                : ""
            }
            ${actionButton(service, "refresh", "refresh", "刷新内容", "", !running || backendService)}
            ${actionButton(service, "open", "external", "打开站点", "", !running)}
            ${actionButton(service, "logs", "file", "查看日志")}
            ${actionButton(service, "edit", "pencil", "编辑站点")}
            ${actionButton(service, "delete", "trash", "删除站点", "is-danger")}
          </div>
        </td>
      </tr>
    `;
  }

  function renderServices() {
    renderSummary();
    const services = filteredServices();
    elements.serviceList.innerHTML = groupedEntries()
      .map((entry) => {
        if (entry.type === "service") return serviceRowMarkup(entry.service);
        const collapsed = state.collapsedGroups.has(entry.key);
        return (
          groupHeaderMarkup(entry) +
          entry.services
            .map((service) => serviceRowMarkup(service, { grouped: true, hidden: collapsed }))
            .join("")
        );
      })
      .join("");
    updateGroupControls();

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
      elements.emptyCopy.textContent = "添加一个静态站点或后端服务开始使用。";
      $("#empty-add").hidden = false;
    }
  }

  async function loadState({ quiet = false } = {}) {
    if (state.loading || state.shuttingDown) return;
    state.loading = true;
    try {
      const payload = await api("/api/state");
      state.services = payload.services;
      const serviceIds = new Set(state.services.map((service) => service.id));
      for (const serviceId of state.selectedServices) {
        if (!serviceIds.has(serviceId)) state.selectedServices.delete(serviceId);
      }
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

  function toggleGroup(key) {
    if (state.collapsedGroups.has(key)) state.collapsedGroups.delete(key);
    else state.collapsedGroups.add(key);
    persistCollapsedGroups();
    renderServices();
  }

  async function runGroupAction(group, action) {
    const stopping = action === "stop-group";
    const busyKey = `group:${group.key}:${action}`;
    const targets = group.services.filter((service) =>
      stopping ? service.status === "running" : service.status !== "running",
    );
    if (targets.length === 0) {
      showToast(stopping ? "分组内没有运行中的服务" : "分组内服务均已启动");
      return;
    }

    setBusy(busyKey, true);
    try {
      const results = await Promise.allSettled(
        targets.map(async (service) => {
          await api(`/api/services/${service.id}/${stopping ? "stop" : "start"}`, {
            method: "POST",
          });
          return service.name;
        }),
      );
      const failed = results.filter((result) => result.status === "rejected");
      if (failed.length) {
        showToast(
          `分组“${group.label}”有 ${failed.length} 个服务操作失败：${failed
            .map((result) => result.reason?.message || "未知错误")
            .join("；")}`,
          "error",
        );
      } else {
        showToast(
          `分组“${group.label}”已${stopping ? "停止" : "启动"} ${targets.length} 个服务`,
        );
      }
      await loadState();
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      state.busy.delete(busyKey);
      renderServices();
    }
  }

  function openRenameGroupDialog(group) {
    state.renameGroupName = group.label;
    elements.renameGroupDialog.dataset.groupKey = group.key;
    elements.renameGroupName.value = group.label;
    elements.renameGroupError.hidden = true;
    elements.renameGroupError.textContent = "";
    elements.renameGroupDialog.showModal();
    window.setTimeout(() => {
      elements.renameGroupName.focus();
      elements.renameGroupName.select();
    }, 30);
  }

  function openAddGroupDialog(service) {
    state.addGroupServiceId = service.id;
    elements.addGroupServiceName.textContent = service.name;
    elements.addGroupError.hidden = true;
    elements.addGroupError.textContent = "";
    const groups = existingGroupNames();
    elements.addGroupTarget.innerHTML = [
      '<option value="">选择已有分组</option>',
      ...groups.map(
        (name) =>
          `<option value="${escapeHtml(name)}">${escapeHtml(name)}</option>`,
      ),
    ].join("");
    elements.addGroupSave.disabled = groups.length === 0;
    if (groups.length === 0) {
      elements.addGroupError.textContent = "还没有已有分组，请先勾选多个服务创建分组。";
      elements.addGroupError.hidden = false;
    }
    elements.addGroupDialog.showModal();
  }

  async function addServiceToGroup(event) {
    event.preventDefault();
    const service = getService(state.addGroupServiceId);
    const groupName = elements.addGroupTarget.value.trim();
    if (!service || !groupName) return;
    elements.addGroupSave.disabled = true;
    elements.addGroupError.hidden = true;
    try {
      await api("/api/services/group", {
        method: "POST",
        body: { ids: [service.id], group_name: groupName },
      });
      closeDialog(elements.addGroupDialog);
      await loadState();
      showToast(`“${service.name}”已添加到“${groupName}”`);
    } catch (error) {
      elements.addGroupError.textContent = error.message;
      elements.addGroupError.hidden = false;
    } finally {
      elements.addGroupSave.disabled = false;
    }
  }

  async function renameSelectedGroup(event) {
    event.preventDefault();
    const oldName = state.renameGroupName.trim();
    const newName = elements.renameGroupName.value.trim();
    if (!oldName || !newName) return;
    if (oldName === newName) {
      closeDialog(elements.renameGroupDialog);
      return;
    }
    elements.saveGroupName.disabled = true;
    elements.renameGroupError.hidden = true;
    try {
      await api("/api/groups/rename", {
        method: "POST",
        body: { old_name: oldName, new_name: newName },
      });
      const oldKey = oldName.toLocaleLowerCase();
      const newKey = newName.toLocaleLowerCase();
      if (state.collapsedGroups.delete(oldKey)) {
        state.collapsedGroups.add(newKey);
        persistCollapsedGroups();
      }
      closeDialog(elements.renameGroupDialog);
      await loadState();
      showToast(`分组已重命名为“${newName}”`);
    } catch (error) {
      elements.renameGroupError.textContent = error.message;
      elements.renameGroupError.hidden = false;
    } finally {
      elements.saveGroupName.disabled = false;
    }
  }

  async function saveSelectedGroup(groupName) {
    const ids = [...state.selectedServices];
    const targetExists =
      groupName.length > 0 &&
      state.services.some(
        (service) =>
          serviceGroupName(service).toLocaleLowerCase() === groupName.toLocaleLowerCase(),
      );
    if (groupName && ids.length < 2 && !targetExists) {
      showToast("创建分组至少需要选择两个服务", "error");
      return;
    }
    state.busy.add("selection:group");
    updateGroupControls();
    try {
      const result = await api("/api/services/group", {
        method: "POST",
        body: { ids, group_name: groupName },
      });
      state.selectedServices.clear();
      elements.groupName.value = "";
      await loadState();
      showToast(
        groupName
          ? `已将 ${result.updated} 个服务加入“${groupName}”`
          : `已将 ${result.updated} 个服务移出分组`,
      );
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      state.busy.delete("selection:group");
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
    elements.serviceDialog.dataset.watchConfigured = service?.watch_enabled
      ? "true"
      : "false";
    elements.serviceTitle.textContent = service ? "编辑站点" : "新增站点";
    elements.serviceEyebrow.textContent = service ? "EDIT SERVICE" : "NEW SERVICE";
    elements.saveService.textContent = service ? "保存修改" : "保存站点";
    elements.serviceName.value = service?.name || "";
    elements.serviceType.value = service?.service_type || "static";
    elements.serviceRoot.value = service?.root_path || "";
    elements.serviceEntry.value = service?.entry_file || "index.html";
    elements.servicePort.value = service?.port || state.suggestedPort;
    elements.servicePortHint.textContent = service
      ? "端口可手动修改，但不能与已有站点重复"
      : "正在确认唯一端口，默认从 7000 起分配";
    elements.serviceAutoOpen.checked = service ? Boolean(service.auto_open) : true;
    elements.serviceBridgeEnabled.checked = service
      ? Boolean(service.bridge_enabled)
      : true;
    elements.serviceBridgeRoot.value = service?.bridge_root || "";
    elements.serviceExternalApp.value = service?.external_app_path || "";
    elements.serviceWriteEnabled.checked = service
      ? Boolean(service.write_enabled)
      : true;
    elements.serviceWatchEnabled.checked = service
      ? Boolean(service.watch_enabled)
      : true;
    elements.serviceWatchRoot.value = service?.watch_root || "";
    elements.serviceWatchInterval.value = service?.watch_interval || 10;
    elements.serviceAutoReload.checked = service
      ? Boolean(service.auto_reload)
      : true;
    elements.serviceAutoCommand.value = service?.auto_command_id || "";
    updateWatchOptions();
    elements.serviceCommandEnabled.checked = service
      ? Boolean(service.command_enabled)
      : false;
    elements.serviceCommands.value = formatCommandConfig(service?.commands_json);
    const backend = parseBackendConfig(service?.backend_json);
    elements.nodeRuntimePath.value = backend.runtime_path || "";
    elements.nodeArgs.value = Array.isArray(backend.args)
      ? backend.args.join("\n")
      : "dist/server/index.js";
    elements.nodeCwd.value = backend.cwd || "";
    elements.nodeEnvFile.value = backend.env_file || "";
    elements.nodeHealthPath.value = backend.health_path || "/api/health";
    elements.nodeStartupTimeout.value = backend.startup_timeout || 30;
    elements.nodeShutdownTimeout.value = backend.shutdown_timeout || 5;
    elements.nodeRestartDelay.value = backend.restart_delay || 2;
    elements.nodeAutoRestart.checked = Boolean(backend.auto_restart);
    updateCommandOptions();
    updateBridgeRootField();
    updateServiceTypeVisibility();
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
      service_type: elements.serviceType.value,
      root_path: elements.serviceRoot.value,
      entry_file: elements.serviceEntry.value,
      auto_open: elements.serviceAutoOpen.checked,
      bridge_enabled: elements.serviceBridgeEnabled.checked,
      bridge_root: elements.serviceBridgeRoot.value,
      external_app_path: elements.serviceExternalApp.value,
      write_enabled: elements.serviceWriteEnabled.checked,
      watch_enabled: elements.serviceWatchEnabled.checked,
      watch_root: elements.serviceWatchRoot.value,
      watch_interval: Number(elements.serviceWatchInterval.value),
      auto_reload: elements.serviceAutoReload.checked,
      auto_command_id: elements.serviceAutoCommand.value,
      command_enabled: elements.serviceCommandEnabled.checked,
      commands_json: elements.serviceCommands.value,
      backend_json: JSON.stringify({
        runtime_path: elements.nodeRuntimePath.value.trim(),
        args: elements.nodeArgs.value
          .split(/\r?\n/)
          .map((value) => value.trim())
          .filter(Boolean),
        cwd: elements.nodeCwd.value.trim(),
        env_file: elements.nodeEnvFile.value.trim(),
        health_path: elements.nodeHealthPath.value.trim() || "/api/health",
        startup_timeout: Number(elements.nodeStartupTimeout.value),
        shutdown_timeout: Number(elements.nodeShutdownTimeout.value),
        restart_delay: Number(elements.nodeRestartDelay.value),
        auto_restart: elements.nodeAutoRestart.checked,
      }),
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

  async function chooseExternalApp() {
    const button = $("#browse-external-app");
    button.disabled = true;
    try {
      const payload = await api("/api/dialog/file", { method: "POST" });
      if (payload.path) elements.serviceExternalApp.value = payload.path;
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  async function chooseNodeRuntime() {
    const button = $("#browse-node-runtime");
    button.disabled = true;
    try {
      const payload = await api("/api/dialog/file", { method: "POST" });
      if (payload.path) elements.nodeRuntimePath.value = payload.path;
    } catch (error) {
      showToast(error.message, "error");
    } finally {
      button.disabled = false;
    }
  }

  function updateBridgeRootField() {
    const enabled = elements.serviceBridgeEnabled.checked;
    elements.bridgeRootField.hidden = !enabled;
    elements.externalAppField.hidden = !enabled;
    elements.writeSwitchRow.hidden = !enabled;
    if (!enabled) {
      elements.serviceWriteEnabled.checked = false;
      elements.serviceExternalApp.value = "";
    }
    if (enabled && !elements.serviceBridgeRoot.value) {
      elements.serviceBridgeRoot.value = elements.serviceRoot.value;
    }
  }

  function updateWatchOptions() {
    const enabled = elements.serviceWatchEnabled.checked;
    elements.watchOptions.hidden = !enabled;
    if (!enabled) elements.serviceAutoReload.checked = false;
    if (enabled && elements.serviceDialog.dataset.watchConfigured !== "true") {
      elements.serviceAutoReload.checked = true;
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

  function parseBackendConfig(value) {
    if (!value) return {};
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch {
      return {};
    }
  }

  function backendServiceLabel(service) {
    const runtime = String(parseBackendConfig(service?.backend_json).runtime_path || "").toLowerCase();
    if (runtime.includes("python")) return "Python 后端";
    if (runtime.includes("node")) return "Node 后端";
    return "后端进程";
  }

  function updateServiceTypeVisibility() {
    const backendService = elements.serviceType.value === "node";
    $$('.static-only').forEach((element) => {
      element.hidden = backendService;
    });
    elements.serviceEntry.disabled = backendService;
    elements.serviceEntry.required = !backendService;
    elements.nodeOptions.hidden = !backendService;
    if (!backendService) {
      updateBridgeRootField();
      updateWatchOptions();
      updateCommandOptions();
    } else {
      elements.servicePortHint.textContent = "此端口将作为后端服务监听端口";
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
    const checkbox = event.target.closest("input[data-select-id]");
    if (checkbox) {
      if (checkbox.checked) state.selectedServices.add(checkbox.dataset.selectId);
      else state.selectedServices.delete(checkbox.dataset.selectId);
      refreshSelectedGroupName();
      updateGroupControls();
      return;
    }

    const groupCheckbox = event.target.closest("input[data-select-group]");
    if (groupCheckbox) {
      let key = "";
      try {
        key = decodeURIComponent(groupCheckbox.dataset.selectGroup || "");
      } catch {
        return;
      }
      for (const service of state.services) {
        if (serviceGroupKey(service) !== key) continue;
        if (groupCheckbox.checked) state.selectedServices.add(service.id);
        else state.selectedServices.delete(service.id);
      }
      refreshSelectedGroupName();
      updateGroupControls();
      return;
    }
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const action = button.dataset.action;
    if (
      action === "toggle-group" ||
      action === "start-group" ||
      action === "stop-group" ||
      action === "rename-group"
    ) {
      let groupKey = "";
      try {
        groupKey = decodeURIComponent(button.dataset.group || "");
      } catch {
        return;
      }
      if (action === "toggle-group") {
        toggleGroup(groupKey);
        return;
      }
      const group = getGroupByKey(groupKey);
      if (group && action === "rename-group") {
        openRenameGroupDialog(group);
        return;
      }
      if (group) runGroupAction(group, action);
      return;
    }

    const service = getService(button.dataset.id);
    if (!service) return;
    if (
      action === "toggle" ||
      action === "restart" ||
      action === "refresh" ||
      action === "open"
    ) {
      runServiceAction(service, action);
    } else if (action === "add-group") {
      openAddGroupDialog(service);
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

  elements.groupName.addEventListener("input", updateGroupControls);
  elements.createGroup.addEventListener("click", () => {
    const selected = [...state.selectedServices]
      .map((serviceId) => getService(serviceId))
      .filter(Boolean);
    const groupName =
      elements.groupName.value.trim() || suggestedGroupName(selected);
    saveSelectedGroup(groupName);
  });
  elements.groupTarget.addEventListener("change", updateGroupControls);
  elements.addToGroup.addEventListener("click", () => {
    const groupName = elements.groupTarget.value;
    if (groupName) saveSelectedGroup(groupName);
  });
  elements.removeGroup.addEventListener("click", () => {
    saveSelectedGroup("");
  });
  elements.clearSelection.addEventListener("click", clearSelectedServices);
  elements.selectAllServices.addEventListener("change", () => {
    const visible = filteredServices();
    for (const service of visible) {
      if (elements.selectAllServices.checked) state.selectedServices.add(service.id);
      else state.selectedServices.delete(service.id);
    }
    refreshSelectedGroupName();
    updateGroupControls();
    renderServices();
  });
  elements.addGroupForm.addEventListener("submit", addServiceToGroup);
  elements.renameGroupForm.addEventListener("submit", renameSelectedGroup);

  $("#add-service").addEventListener("click", () => openServiceDialog());
  $("#empty-add").addEventListener("click", () => openServiceDialog());
  $("#refresh-all").addEventListener("click", () => loadState());
  $("#start-all").addEventListener("click", startAll);
  $("#stop-all").addEventListener("click", stopAll);
  $("#browse-folder").addEventListener("click", chooseFolder);
  $("#browse-bridge-root").addEventListener("click", chooseBridgeFolder);
  $("#browse-watch-root").addEventListener("click", chooseWatchFolder);
  $("#browse-external-app").addEventListener("click", chooseExternalApp);
  $("#browse-node-runtime").addEventListener("click", chooseNodeRuntime);
  elements.serviceType.addEventListener("change", updateServiceTypeVisibility);
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
