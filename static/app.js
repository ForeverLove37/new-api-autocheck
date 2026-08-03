const state = {
  token: localStorage.getItem("autocheck.token") || "",
  authRequired: true,
  currentView: "overview",
  summary: null,
  accounts: [],
  proxies: [],
  config: null,
  timezones: [],
  logs: [],
  selectedAccounts: new Set(),
};

const content = document.getElementById("content");
const authShell = document.getElementById("auth-shell");
const appShell = document.getElementById("app-shell");
const modal = document.getElementById("modal");

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  if (state.token) {
    headers.set("Authorization", `Bearer ${state.token}`);
  }
  const response = await fetch(path, { ...options, headers });
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json") ? await response.json() : null;
  if (!response.ok) {
    if (response.status === 401 && state.authRequired) {
      showAuthentication();
    }
    throw new Error(data?.detail || `Request failed (${response.status})`);
  }
  return data;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatDate(value) {
  if (!value) return "Not yet";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  const timeZone = state.config?.schedule_timezone || "UTC";
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZone,
      timeZoneName: "short",
    }).format(date);
  } catch {
    return date.toLocaleString(undefined, { timeZone: "UTC", timeZoneName: "short" });
  }
}

function formatSchedule(account) {
  if (!account.schedule_enabled) return '<span class="muted">Off</span>';
  const hour = String(account.schedule_hour).padStart(2, "0");
  const minute = String(account.schedule_minute).padStart(2, "0");
  const jitter = account.schedule_jitter_minutes
    ? ` + 0-${account.schedule_jitter_minutes} min`
    : "";
  const nextRun = account.enabled ? formatDate(account.next_scheduled_at) : "Account disabled";
  const timeZone = state.config?.schedule_timezone || "UTC";
  return `<div class="cell-primary">${hour}:${minute}${escapeHtml(jitter)}</div><div class="cell-secondary">${escapeHtml(timeZone)} | ${escapeHtml(nextRun)}</div>`;
}

function statusBadge(status, fallback = "Not run") {
  if (status === "success") return '<span class="status status-success">Success</span>';
  if (status === "failed") return '<span class="status status-failure">Failed</span>';
  if (status === "enabled") return '<span class="status status-success">Enabled</span>';
  if (status === "disabled") return '<span class="status status-muted">Disabled</span>';
  if (status === "stored") return '<span class="status status-success">Stored</span>';
  if (status === "missing") return '<span class="status status-warning">Missing</span>';
  return `<span class="status status-muted">${escapeHtml(fallback)}</span>`;
}

function toast(message, isError = false) {
  const region = document.getElementById("toast-region");
  const item = document.createElement("div");
  item.className = `toast${isError ? " is-error" : ""}`;
  item.textContent = message;
  region.append(item);
  window.setTimeout(() => item.remove(), 5_000);
}

function setButtonBusy(button, busy) {
  if (!button) return;
  if (busy) {
    button.dataset.originalText = button.textContent;
    button.disabled = true;
    button.textContent = "Working...";
  } else {
    button.disabled = false;
    button.textContent = button.dataset.originalText || button.textContent;
  }
}

function showAuthentication() {
  state.token = "";
  localStorage.removeItem("autocheck.token");
  appShell.hidden = true;
  authShell.hidden = false;
  document.getElementById("admin-password").focus();
}

function showApplication() {
  authShell.hidden = true;
  appShell.hidden = false;
}

async function loadData() {
  const [summary, accounts, proxies, config, timezones, logs] = await Promise.all([
    api("/api/summary"),
    api("/api/accounts"),
    api("/api/proxies"),
    api("/api/config"),
    api("/api/timezones"),
    api("/api/logs?limit=80"),
  ]);
  state.summary = summary;
  state.accounts = accounts;
  state.proxies = proxies;
  state.config = config;
  state.timezones = timezones;
  state.logs = logs;
  state.selectedAccounts = new Set(
    [...state.selectedAccounts].filter((id) => accounts.some((account) => account.id === id)),
  );
}

async function refreshAndRender() {
  try {
    await loadData();
    render();
  } catch (error) {
    toast(error.message, true);
  }
}

function render() {
  document.querySelectorAll(".nav-item").forEach((item) => {
    item.classList.toggle("is-active", item.dataset.view === state.currentView);
  });
  const renderers = {
    overview: renderOverview,
    accounts: renderAccounts,
    proxies: renderProxies,
    settings: renderSettings,
    activity: renderActivity,
  };
  content.innerHTML = renderers[state.currentView]();
  content.focus({ preventScroll: true });
}

function renderOverview() {
  const summary = state.summary || {};
  return `
    <section class="view">
      <header class="view-header">
        <div>
          <h1 class="view-title">Overview</h1>
          <p class="view-subtitle">${escapeHtml(state.config?.base_url || "No site configured")}</p>
        </div>
        <div class="toolbar">
          <button class="button button-quiet" data-action="balance-all" type="button">Check balances</button>
          <button class="button button-quiet" data-action="run-all-refresh" type="button">Refresh sessions and run</button>
          <button class="button button-primary" data-action="run-all" type="button">Run enabled accounts</button>
        </div>
      </header>
      <dl class="metric-grid">
        <div class="metric"><dt>Accounts</dt><dd>${summary.accounts_total || 0}</dd></div>
        <div class="metric"><dt>Enabled</dt><dd>${summary.accounts_enabled || 0}</dd></div>
        <div class="metric"><dt>Sessions</dt><dd>${summary.accounts_with_cookie || 0}</dd></div>
        <div class="metric"><dt>Recent failures</dt><dd>${summary.recent_failures || 0}</dd></div>
      </dl>
      <h2 class="section-title">Account status</h2>
      ${accountTable(state.accounts.slice(0, 8), { compact: true })}
    </section>`;
}

function renderAccounts() {
  const selectedCount = state.selectedAccounts.size;
  const allSelected = state.accounts.length > 0 && selectedCount === state.accounts.length;
  return `
    <section class="view">
      <header class="view-header">
        <div>
          <h1 class="view-title">Accounts</h1>
          <p class="view-subtitle">${state.accounts.length} configured</p>
        </div>
        <div class="toolbar">
          <button class="button button-quiet" data-action="import-legacy" type="button">Import legacy files</button>
          <button class="button button-primary" data-action="add-account" type="button">Add account</button>
        </div>
      </header>
      <div class="selection-toolbar">
        <div class="action-row">
          <strong>${selectedCount} selected</strong>
          <select id="bulk-proxy-select" aria-label="Proxy for selected accounts">
            <option value="">No proxy</option>
            ${proxyOptions()}
          </select>
          <button class="button button-quiet" data-action="assign-proxy" type="button" ${selectedCount ? "" : "disabled"}>Assign proxy</button>
        </div>
        <div class="action-row">
          <button class="button button-quiet" data-action="balance-selected" type="button" ${selectedCount ? "" : "disabled"}>Check balances</button>
          <button class="button button-quiet" data-action="run-selected-refresh" type="button" ${selectedCount ? "" : "disabled"}>Refresh and run selected</button>
          <button class="button button-primary" data-action="run-selected" type="button" ${selectedCount ? "" : "disabled"}>Run selected</button>
        </div>
      </div>
      ${accountTable(state.accounts, { selectable: true, allSelected })}
    </section>`;
}

function accountTable(accounts, options = {}) {
  if (!accounts.length) {
    return '<div class="data-panel"><div class="empty-state">No accounts have been added.</div></div>';
  }
  const selectionHeader = options.selectable
    ? `<th><input data-action="toggle-all-accounts" type="checkbox" aria-label="Select all accounts" ${options.allSelected ? "checked" : ""} /></th>`
    : "";
  const rows = accounts
    .map((account) => {
      const selection = options.selectable
        ? `<td><input data-action="toggle-account" data-id="${account.id}" type="checkbox" aria-label="Select ${escapeHtml(account.username)}" ${state.selectedAccounts.has(account.id) ? "checked" : ""} /></td>`
        : "";
      return `
        <tr>
          ${selection}
          <td>
            <div class="cell-primary">${escapeHtml(account.label || account.username)}</div>
            ${account.label ? `<div class="cell-secondary">${escapeHtml(account.username)}</div>` : ""}
          </td>
          <td>${account.enabled ? statusBadge("enabled") : statusBadge("disabled")}</td>
          <td>${account.has_cookie ? statusBadge("stored") : statusBadge("missing")}</td>
          <td>${account.proxy ? `<div class="cell-primary">${escapeHtml(account.proxy.name)}</div><div class="cell-secondary">${escapeHtml(account.proxy.scheme)}://${escapeHtml(account.proxy.host)}:${account.proxy.port}</div>` : '<span class="muted">Direct</span>'}</td>
          <td>${formatSchedule(account)}</td>
          <td><div>${statusBadge(account.last_checkin_status)}</div><div class="cell-secondary">${escapeHtml(formatDate(account.last_checkin_at))}</div></td>
          <td><div class="cell-primary">${escapeHtml(account.last_balance_display || "Not checked")}</div><div>${statusBadge(account.last_balance_status)}</div><div class="cell-secondary">${escapeHtml(formatDate(account.last_balance_at))}</div></td>
          <td>
            <div class="table-actions">
              <button class="button button-quiet" data-action="login-account" data-id="${account.id}" type="button">Login</button>
              <button class="button button-quiet" data-action="checkin-account" data-id="${account.id}" type="button">Check in</button>
              <button class="button button-quiet" data-action="balance-account" data-id="${account.id}" type="button">Balance</button>
              <button class="button button-quiet" data-action="edit-account" data-id="${account.id}" type="button">Edit</button>
              <button class="button button-danger" data-action="delete-account" data-id="${account.id}" type="button">Delete</button>
            </div>
          </td>
        </tr>`;
    })
    .join("");
  return `
    <div class="data-panel table-wrap">
      <table class="account-table">
        <thead><tr>${selectionHeader}<th>Account</th><th>State</th><th>Session</th><th>Proxy</th><th>Schedule</th><th>Last check-in</th><th>Balance</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
      </table>
    </div>`;
}

function renderProxies() {
  const rows = state.proxies
    .map(
      (proxy) => `
        <tr>
          <td><div class="cell-primary">${escapeHtml(proxy.name)}</div><div class="cell-secondary">${escapeHtml(proxy.scheme)}://${escapeHtml(proxy.host)}:${proxy.port}</div></td>
          <td>${proxy.has_auth ? statusBadge("stored", "") : '<span class="muted">None</span>'}</td>
          <td>${proxy.enabled ? statusBadge("enabled") : statusBadge("disabled")}</td>
          <td>${proxy.assigned_count}</td>
          <td>
            <div class="table-actions">
              <button class="button button-quiet" data-action="edit-proxy" data-id="${proxy.id}" type="button">Edit</button>
              <button class="button button-danger" data-action="delete-proxy" data-id="${proxy.id}" type="button">Delete</button>
            </div>
          </td>
        </tr>`,
    )
    .join("");
  return `
    <section class="view">
      <header class="view-header">
        <div><h1 class="view-title">Proxies</h1><p class="view-subtitle">HTTP, HTTPS, and SOCKS5 endpoints</p></div>
        <button class="button button-primary" data-action="add-proxy" type="button">Add proxy</button>
      </header>
      <div class="data-panel table-wrap">
        ${rows ? `<table><thead><tr><th>Proxy</th><th>Authentication</th><th>State</th><th>Accounts</th><th></th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty-state">No proxies have been added.</div>'}
      </div>
    </section>`;
}

function renderSettings() {
  const config = state.config;
  if (!config) return "";
  return `
    <section class="view">
      <header class="view-header">
        <div><h1 class="view-title">Site settings</h1><p class="view-subtitle">Target, scheduling, browser, and administrator settings</p></div>
      </header>
      <form class="stacked-form" data-form="settings">
        <section class="form-section">
          <h2>Target</h2>
          <div class="field-grid">
            <label class="field-span-full">Base URL<input name="base_url" required value="${escapeHtml(config.base_url)}" /></label>
            <label>Login path or URL<input name="login_path" required value="${escapeHtml(config.login_path)}" /></label>
            <label>Check-in path or URL<input name="checkin_path" required value="${escapeHtml(config.checkin_path)}" /></label>
            <label>Balance path or URL<input name="balance_path" required value="${escapeHtml(config.balance_path)}" /></label>
            <label>Status path or URL<input name="status_path" required value="${escapeHtml(config.status_path)}" /></label>
            <label>Referer path or URL<input name="referer_path" value="${escapeHtml(config.referer_path || "")}" /></label>
            <label>Request timeout (seconds)<input name="request_timeout_seconds" type="number" min="3" max="120" required value="${config.request_timeout_seconds}" /></label>
          </div>
        </section>
        <section class="form-section">
          <h2>Scheduling and records</h2>
          <div class="field-grid">
            <label>Global timezone<select name="schedule_timezone" required>${timezoneOptions(config.schedule_timezone)}</select></label>
          </div>
        </section>
        <section class="form-section">
          <h2>Login form</h2>
          <div class="field-grid">
            <label>Username selector<input name="username_selector" required value="${escapeHtml(config.username_selector)}" /></label>
            <label>Password selector<input name="password_selector" required value="${escapeHtml(config.password_selector)}" /></label>
            <label>Submit selector<input name="submit_selector" value="${escapeHtml(config.submit_selector || "")}" /></label>
            <label>Post-login path or URL<input name="post_login_path" value="${escapeHtml(config.post_login_path || "")}" /></label>
            <label class="field-span-full">Custom request headers (JSON)<textarea name="custom_headers" spellcheck="false">${escapeHtml(JSON.stringify(config.custom_headers, null, 2))}</textarea></label>
          </div>
        </section>
        <div class="action-row"><button class="button button-primary" type="submit">Save settings</button></div>
      </form>
      <form class="stacked-form settings-secondary" data-form="password-change">
        <section class="form-section">
          <h2>Administrator password</h2>
          <div class="field-grid">
            <label>Current password<input name="current_password" type="password" autocomplete="current-password" required /></label>
            <label>New password<input name="new_password" type="password" autocomplete="new-password" minlength="12" required /></label>
            <label>Confirm new password<input name="confirm_password" type="password" autocomplete="new-password" minlength="12" required /></label>
          </div>
        </section>
        <div class="action-row"><button class="button button-primary" type="submit">Change password</button></div>
      </form>
    </section>`;
}

function renderActivity() {
  const rows = state.logs
    .map(
      (log) => `
        <tr>
          <td class="nowrap">${escapeHtml(formatDate(log.created_at))}</td>
          <td>${escapeHtml(log.account_username || "Deleted account")}</td>
          <td>${escapeHtml(log.action)}</td>
          <td>${log.success ? statusBadge("success") : statusBadge("failed")}</td>
          <td>${log.status_code ?? "-"}</td>
          <td>${escapeHtml(log.message)}</td>
        </tr>`,
    )
    .join("");
  return `
    <section class="view">
      <header class="view-header">
        <div><h1 class="view-title">Activity</h1><p class="view-subtitle">Latest login, check-in, and balance attempts</p></div>
      </header>
      <div class="data-panel table-wrap">
        ${rows ? `<table><thead><tr><th>Time</th><th>Account</th><th>Action</th><th>Result</th><th>HTTP</th><th>Message</th></tr></thead><tbody>${rows}</tbody></table>` : '<div class="empty-state">No activity has been recorded.</div>'}
      </div>
    </section>`;
}

function proxyOptions(selectedId = null) {
  return state.proxies
    .map(
      (proxy) => `<option value="${proxy.id}" ${Number(selectedId) === proxy.id ? "selected" : ""}>${escapeHtml(proxy.name)} (${escapeHtml(proxy.scheme)}://${escapeHtml(proxy.host)}:${proxy.port})</option>`,
    )
    .join("");
}

function timezoneOptions(selectedTimezone) {
  const timezones = state.timezones.includes(selectedTimezone)
    ? state.timezones
    : [selectedTimezone, ...state.timezones];
  return timezones
    .map((timezone) => `<option value="${escapeHtml(timezone)}" ${timezone === selectedTimezone ? "selected" : ""}>${escapeHtml(timezone)}</option>`)
    .join("");
}

function openModal(title, formHtml, submitLabel) {
  modal.innerHTML = `
    <div class="modal-content">
      <header class="modal-header"><h2>${escapeHtml(title)}</h2><button class="icon-close" data-action="close-modal" aria-label="Close" type="button">&times;</button></header>
      <div class="modal-body">${formHtml}</div>
      <footer class="modal-footer"><button class="button button-quiet" data-action="close-modal" type="button">Cancel</button><button class="button button-primary" form="modal-data-form" type="submit">${escapeHtml(submitLabel)}</button></footer>
    </div>`;
  modal.showModal();
}

function closeModal() {
  if (modal.open) modal.close();
  modal.innerHTML = "";
}

function openAccountModal(account = null) {
  const isEdit = Boolean(account);
  openModal(
    isEdit ? "Edit account" : "Add account",
    `
      <form id="modal-data-form" class="modal-form" data-form="account" data-id="${account?.id || ""}">
        <div class="field-grid">
          <label>Label<input name="label" value="${escapeHtml(account?.label || "")}" /></label>
          <label>Username<input name="username" required value="${escapeHtml(account?.username || "")}" /></label>
          <label>Password<input name="password" type="password" autocomplete="new-password" /></label>
          <label>User ID<input name="user_id" value="${escapeHtml(account?.user_id || "")}" /></label>
          <label class="field-span-full">Session cookie<textarea name="cookie" spellcheck="false"></textarea></label>
          <label>Proxy<select name="proxy_id"><option value="">No proxy</option>${proxyOptions(account?.proxy?.id)}</select></label>
          <label class="check-label"><input name="enabled" type="checkbox" ${account?.enabled ?? true ? "checked" : ""} />Enabled</label>
          ${isEdit ? '<label class="check-label field-span-full"><input name="clear_cookie" type="checkbox" />Clear stored session cookie</label>' : ""}
          <h3 class="modal-section-title field-span-full">Daily schedule</h3>
          <label class="check-label field-span-full"><input name="schedule_enabled" type="checkbox" ${account?.schedule_enabled ? "checked" : ""} />Enable automatic check-in</label>
          <label>Hour<input name="schedule_hour" type="number" min="0" max="23" required value="${account?.schedule_hour ?? 8}" /></label>
          <label>Minute<input name="schedule_minute" type="number" min="0" max="59" required value="${account?.schedule_minute ?? 0}" /></label>
          <label>Random delay (minutes)<input name="schedule_jitter_minutes" type="number" min="0" max="720" required value="${account?.schedule_jitter_minutes ?? 0}" /></label>
        </div>
      </form>`,
    isEdit ? "Save account" : "Create account",
  );
}

function openProxyModal(proxy = null) {
  const isEdit = Boolean(proxy);
  openModal(
    isEdit ? "Edit proxy" : "Add proxy",
    `
      <form id="modal-data-form" class="modal-form" data-form="proxy" data-id="${proxy?.id || ""}">
        <div class="field-grid">
          <label>Name<input name="name" required value="${escapeHtml(proxy?.name || "")}" /></label>
          <label>Protocol<select name="scheme"><option value="http" ${proxy?.scheme === "http" ? "selected" : ""}>HTTP</option><option value="https" ${proxy?.scheme === "https" ? "selected" : ""}>HTTPS</option><option value="socks5" ${proxy?.scheme === "socks5" ? "selected" : ""}>SOCKS5</option></select></label>
          <label>Host<input name="host" required value="${escapeHtml(proxy?.host || "")}" /></label>
          <label>Port<input name="port" type="number" min="1" max="65535" required value="${proxy?.port || ""}" /></label>
          <label>Username<input name="username" autocomplete="off" /></label>
          <label>Password<input name="password" type="password" autocomplete="new-password" /></label>
          <label class="check-label"><input name="enabled" type="checkbox" ${proxy?.enabled ?? true ? "checked" : ""} />Enabled</label>
          ${isEdit ? '<label class="check-label"><input name="clear_auth" type="checkbox" />Clear proxy authentication</label>' : ""}
        </div>
      </form>`,
    isEdit ? "Save proxy" : "Create proxy",
  );
}

async function runBatch(accountIds, refreshCookies, button) {
  if (accountIds && !accountIds.length) {
    toast("Select at least one account.", true);
    return;
  }
  setButtonBusy(button, true);
  try {
    const result = await api("/api/checkins/run", {
      method: "POST",
      body: JSON.stringify({ account_ids: accountIds, enabled_accounts_only: !accountIds, refresh_cookies: refreshCookies }),
    });
    const successCount = result.results.filter((item) => item.success).length;
    toast(`${successCount} of ${result.results.length} check-ins succeeded.`);
    await refreshAndRender();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setButtonBusy(button, false);
  }
}

async function runBalanceBatch(accountIds, button) {
  if (accountIds && !accountIds.length) {
    toast("Select at least one account.", true);
    return;
  }
  setButtonBusy(button, true);
  try {
    const result = await api("/api/balances/run", {
      method: "POST",
      body: JSON.stringify({ account_ids: accountIds, enabled_accounts_only: !accountIds, refresh_cookies: false }),
    });
    const successCount = result.results.filter((item) => item.success).length;
    toast(`${successCount} of ${result.results.length} balance checks succeeded.`);
    await refreshAndRender();
  } catch (error) {
    toast(error.message, true);
  } finally {
    setButtonBusy(button, false);
  }
}

async function handleAction(action, button) {
  const id = Number(button.dataset.id);
  switch (action) {
    case "close-modal":
      closeModal();
      return;
    case "refresh":
      setButtonBusy(button, true);
      await refreshAndRender();
      setButtonBusy(button, false);
      return;
    case "add-account":
      openAccountModal();
      return;
    case "edit-account":
      openAccountModal(state.accounts.find((account) => account.id === id));
      return;
    case "delete-account":
      if (window.confirm("Delete this account and its stored session?")) {
        await api(`/api/accounts/${id}`, { method: "DELETE" });
        toast("Account deleted.");
        await refreshAndRender();
      }
      return;
    case "login-account":
      setButtonBusy(button, true);
      try {
        const result = await api(`/api/accounts/${id}/login`, { method: "POST" });
        toast(result.message, !result.success);
        await refreshAndRender();
      } finally {
        setButtonBusy(button, false);
      }
      return;
    case "checkin-account":
      setButtonBusy(button, true);
      try {
        const result = await api(`/api/accounts/${id}/checkin`, { method: "POST" });
        toast(result.message, !result.success);
        await refreshAndRender();
      } finally {
        setButtonBusy(button, false);
      }
      return;
    case "balance-account":
      setButtonBusy(button, true);
      try {
        const result = await api(`/api/accounts/${id}/balance`, { method: "POST" });
        toast(result.message, !result.success);
        await refreshAndRender();
      } finally {
        setButtonBusy(button, false);
      }
      return;
    case "balance-all":
      await runBalanceBatch(null, button);
      return;
    case "balance-selected":
      await runBalanceBatch([...state.selectedAccounts], button);
      return;
    case "run-all":
      await runBatch(null, false, button);
      return;
    case "run-all-refresh":
      await runBatch(null, true, button);
      return;
    case "run-selected":
      await runBatch([...state.selectedAccounts], false, button);
      return;
    case "run-selected-refresh":
      await runBatch([...state.selectedAccounts], true, button);
      return;
    case "assign-proxy": {
      const value = document.getElementById("bulk-proxy-select").value;
      await api("/api/proxies/assignments", {
        method: "PATCH",
        body: JSON.stringify({ account_ids: [...state.selectedAccounts], proxy_id: value ? Number(value) : null }),
      });
      toast("Proxy assignment updated.");
      await refreshAndRender();
      return;
    }
    case "import-legacy": {
      setButtonBusy(button, true);
      try {
        const result = await api("/api/import/legacy", { method: "POST", body: JSON.stringify({}) });
        toast(`Imported ${result.accounts_created} accounts and ${result.proxies_created} proxies.`);
        await refreshAndRender();
      } finally {
        setButtonBusy(button, false);
      }
      return;
    }
    case "add-proxy":
      openProxyModal();
      return;
    case "edit-proxy":
      openProxyModal(state.proxies.find((proxy) => proxy.id === id));
      return;
    case "delete-proxy":
      if (window.confirm("Delete this proxy? Assigned accounts will use a direct connection.")) {
        await api(`/api/proxies/${id}`, { method: "DELETE" });
        toast("Proxy deleted.");
        await refreshAndRender();
      }
      return;
    default:
      return;
  }
}

document.addEventListener("click", async (event) => {
  const navItem = event.target.closest("[data-view]");
  if (navItem) {
    state.currentView = navItem.dataset.view;
    render();
    return;
  }
  const button = event.target.closest("[data-action]");
  if (!button || ["toggle-account", "toggle-all-accounts"].includes(button.dataset.action)) return;
  try {
    await handleAction(button.dataset.action, button);
  } catch (error) {
    toast(error.message, true);
  }
});

document.addEventListener("change", (event) => {
  const target = event.target;
  if (target.matches('[data-action="toggle-account"]')) {
    const id = Number(target.dataset.id);
    if (target.checked) state.selectedAccounts.add(id);
    else state.selectedAccounts.delete(id);
    render();
  }
  if (target.matches('[data-action="toggle-all-accounts"]')) {
    state.selectedAccounts = target.checked ? new Set(state.accounts.map((account) => account.id)) : new Set();
    render();
  }
});

document.addEventListener("submit", async (event) => {
  const form = event.target;
  if (form.id === "login-form") {
    event.preventDefault();
    const errorElement = document.getElementById("login-error");
    errorElement.textContent = "";
    const button = form.querySelector("button[type=submit]");
    setButtonBusy(button, true);
    try {
      const result = await api("/api/auth/login", {
        method: "POST",
        body: JSON.stringify({ password: form.password.value }),
      });
      state.token = result.access_token;
      localStorage.setItem("autocheck.token", state.token);
      showApplication();
      await refreshAndRender();
      form.reset();
    } catch (error) {
      errorElement.textContent = error.message;
    } finally {
      setButtonBusy(button, false);
    }
    return;
  }

  if (!form.dataset.form) return;
  event.preventDefault();
  const submitButton = form.querySelector('button[type="submit"]') || modal.querySelector("button[type=submit][form]");
  setButtonBusy(submitButton, true);
  try {
    if (form.dataset.form === "account") await submitAccount(form);
    if (form.dataset.form === "proxy") await submitProxy(form);
    if (form.dataset.form === "settings") await submitSettings(form);
    if (form.dataset.form === "password-change") await submitPasswordChange(form);
  } catch (error) {
    toast(error.message, true);
  } finally {
    setButtonBusy(submitButton, false);
  }
});

async function submitAccount(form) {
  const isEdit = Boolean(form.dataset.id);
  const values = new FormData(form);
  const password = values.get("password").trim();
  const cookie = values.get("cookie").trim();
  const payload = {
    label: values.get("label").trim() || null,
    username: values.get("username").trim(),
    user_id: values.get("user_id").trim() || null,
    proxy_id: values.get("proxy_id") ? Number(values.get("proxy_id")) : null,
    enabled: form.enabled.checked,
    schedule_enabled: form.schedule_enabled.checked,
    schedule_hour: Number(values.get("schedule_hour")),
    schedule_minute: Number(values.get("schedule_minute")),
    schedule_jitter_minutes: Number(values.get("schedule_jitter_minutes")),
  };
  if (!isEdit || password) payload.password = password || null;
  if (!isEdit || cookie) payload.cookie = cookie || null;
  if (isEdit && form.clear_cookie.checked) payload.cookie = null;
  const path = isEdit ? `/api/accounts/${form.dataset.id}` : "/api/accounts";
  await api(path, { method: isEdit ? "PATCH" : "POST", body: JSON.stringify(payload) });
  closeModal();
  toast(isEdit ? "Account updated." : "Account created.");
  await refreshAndRender();
}

async function submitProxy(form) {
  const isEdit = Boolean(form.dataset.id);
  const values = new FormData(form);
  const username = values.get("username").trim();
  const password = values.get("password").trim();
  const payload = {
    name: values.get("name").trim(),
    scheme: values.get("scheme"),
    host: values.get("host").trim(),
    port: Number(values.get("port")),
    enabled: form.enabled.checked,
  };
  if (!isEdit || username) payload.username = username || null;
  if (!isEdit || password) payload.password = password || null;
  if (isEdit && form.clear_auth.checked) {
    payload.username = null;
    payload.password = null;
  }
  const path = isEdit ? `/api/proxies/${form.dataset.id}` : "/api/proxies";
  await api(path, { method: isEdit ? "PATCH" : "POST", body: JSON.stringify(payload) });
  closeModal();
  toast(isEdit ? "Proxy updated." : "Proxy created.");
  await refreshAndRender();
}

async function submitSettings(form) {
  const values = new FormData(form);
  let headers;
  try {
    headers = JSON.parse(values.get("custom_headers") || "{}");
  } catch {
    throw new Error("Custom request headers must be valid JSON.");
  }
  if (typeof headers !== "object" || Array.isArray(headers) || headers === null) {
    throw new Error("Custom request headers must be a JSON object.");
  }
  const payload = {
    base_url: values.get("base_url").trim(),
    login_path: values.get("login_path").trim(),
    checkin_path: values.get("checkin_path").trim(),
    balance_path: values.get("balance_path").trim(),
    status_path: values.get("status_path").trim(),
    referer_path: values.get("referer_path").trim() || null,
    username_selector: values.get("username_selector").trim(),
    password_selector: values.get("password_selector").trim(),
    submit_selector: values.get("submit_selector").trim() || null,
    post_login_path: values.get("post_login_path").trim() || null,
    custom_headers: headers,
    schedule_enabled: state.config.schedule_enabled,
    schedule_hour: state.config.schedule_hour,
    schedule_minute: state.config.schedule_minute,
    schedule_timezone: values.get("schedule_timezone"),
    request_timeout_seconds: Number(values.get("request_timeout_seconds")),
  };
  await api("/api/config", { method: "PUT", body: JSON.stringify(payload) });
  toast("Site settings saved.");
  await refreshAndRender();
}

async function submitPasswordChange(form) {
  const values = new FormData(form);
  const newPassword = values.get("new_password");
  if (newPassword !== values.get("confirm_password")) {
    throw new Error("New password confirmation does not match.");
  }
  const result = await api("/api/auth/password", {
    method: "PUT",
    body: JSON.stringify({
      current_password: values.get("current_password"),
      new_password: newPassword,
    }),
  });
  state.token = result.access_token;
  localStorage.setItem("autocheck.token", state.token);
  form.reset();
  toast("Administrator password changed.");
}

document.getElementById("logout-button").addEventListener("click", showAuthentication);
modal.addEventListener("click", (event) => {
  if (event.target === modal) closeModal();
});
modal.addEventListener("close", () => {
  modal.innerHTML = "";
});

async function bootstrap() {
  try {
    const status = await api("/api/auth/status");
    state.authRequired = status.auth_required;
    if (state.authRequired && !state.token) {
      showAuthentication();
      return;
    }
    showApplication();
    await refreshAndRender();
  } catch (error) {
    if (state.authRequired) showAuthentication();
    else toast(error.message, true);
  }
}

bootstrap();
