/* bacpro — BACnet commissioning console
 * Vanilla JS, no framework. Talks to the local Flask service.
 */
(function () {
  "use strict";

  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));
  const el = (tag, cls, html) => {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (html !== undefined) node.innerHTML = html;
    return node;
  };
  const esc = (value) =>
    String(value === null || value === undefined ? "" : value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  const icon = (name, cls) =>
    `<svg class="ico ${cls || ""}"><use href="#i-${name}"></use></svg>`;

  const OBJ_BADGE = {
    0: "AI", 1: "AO", 2: "AV", 3: "BI", 4: "BO", 5: "BV",
    8: "DEV", 13: "MSI", 14: "MSO", 19: "MSV",
  };
  const COMMANDABLE = new Set([1, 2, 4, 5, 14, 19]);
  const POLL_MS = 4000;

  const state = {
    transport: "ip",
    devices: [],
    selected: null,          // { address, instance, device }
    data: null,              // { objects, stats, loaded_at }
    view: "devices",
    filter: "all",
    search: "",
    point: null,             // selected object
    priority: null,          // 16-slot array
    live: true,
    loading: false,
    audit: [],
    status: null,
    history: new Map(),      // "type:instance" -> [numbers]
    bannerDismissed: false,
    lastUpdated: null,
    modbus: null,
    modbusTimer: null,
    bbmd: null,
    bbmdAddr: "",
    bbmdTable: null,
  };

  /* ------------------------------------------------------------------ api */
  async function api(path, options) {
    const res = await fetch(path, options);
    let body = null;
    try {
      body = await res.json();
    } catch (err) {
      body = null;
    }
    if (!res.ok) {
      const message = (body && (body.error || body.message)) || `HTTP ${res.status}`;
      const error = new Error(message);
      error.body = body;
      throw error;
    }
    return body;
  }

  const postJson = (path, payload) =>
    api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload || {}),
    });

  /* ---------------------------------------------------------------- toasts */
  let toastHost = null;
  function toast(message, kind) {
    if (!toastHost) {
      toastHost = el("div", "toasts");
      document.body.appendChild(toastHost);
    }
    const node = el("div", "toast" + (kind ? " " + kind : ""), esc(message));
    toastHost.appendChild(node);
    setTimeout(() => {
      node.style.opacity = "0";
      node.style.transform = "translateY(6px)";
      node.style.transition = "all .18s";
      setTimeout(() => node.remove(), 200);
    }, kind === "error" ? 5200 : 3200);
  }

  /* ----------------------------------------------------------------- clock */
  function tickClock() {
    const now = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    $("#clock").textContent =
      `${pad(now.getHours())}:${pad(now.getMinutes())}:${pad(now.getSeconds())}`;
  }

  /* ----------------------------------------------------------- transport UI */
  function setTransport(which) {
    state.transport = which;
    $$("#transportTabs button").forEach((btn) =>
      btn.classList.toggle("active", btn.dataset.transport === which)
    );
    $("#panelIp").hidden = which !== "ip";
    $("#panelMstp").hidden = which !== "mstp";
    const badge = $("#transportBadge");
    badge.textContent = which === "mstp" ? "MS/TP · RS-485" : "BACnet/IP";
    badge.classList.toggle("mstp", which === "mstp");
    $("#statusTransport").textContent =
      which === "mstp" ? "Transport: MS/TP RS-485" : "Transport: BACnet/IP";
    $("#statusPort").textContent = which === "mstp" ? "Serial" : "UDP 47808";
    if (which === "mstp") loadPorts();
  }

  async function loadPorts() {
    const select = $("#serialPort");
    try {
      const data = await api("/api/mstp/ports");
      const ports = data.ports || [];
      if (!ports.length) {
        select.innerHTML = '<option value="">No serial ports found</option>';
        return;
      }
      const current = select.value;
      select.innerHTML = ports
        .map((p) => {
          const label = p.description ? `${p.device} — ${p.description}` : p.device;
          return `<option value="${esc(p.device)}">${esc(label)}</option>`;
        })
        .join("");
      if (current && ports.some((p) => p.device === current)) select.value = current;
    } catch (err) {
      select.innerHTML = '<option value="">Port scan failed</option>';
    }
  }

  async function connectMstp() {
    const port = $("#serialPort").value;
    if (!port) {
      toast("Select a serial port first", "error");
      return;
    }
    const btn = $("#mstpBtn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Connecting…';
    $("#mstpStatus").innerHTML = '<span class="muted">Opening port…</span>';
    try {
      const data = await postJson("/api/mstp/connect", {
        port,
        baud: Number($("#baudSelect").value),
        mac: Number($("#macInput").value),
        max_master: Number($("#maxMasterInput").value),
      });
      renderMstpStatus(data);
      toast(`MS/TP master online on ${port}`, "info");
      setConnPill("ok", "MS/TP online");
    } catch (err) {
      $("#mstpStatus").innerHTML = `<span class="bad">${esc(err.message)}</span>`;
      toast(err.message, "error");
      setConnPill("bad", "MS/TP error");
    } finally {
      btn.disabled = false;
      btn.innerHTML = icon("cable") + " Connect to bus";
      refreshStatus();
    }
  }

  async function disconnectMstp() {
    try {
      await postJson("/api/mstp/disconnect");
      $("#mstpStatus").innerHTML = '<span class="muted">Not connected</span>';
      toast("MS/TP disconnected", "info");
      setConnPill("ok", "Ready");
    } catch (err) {
      toast(err.message, "error");
    }
    refreshStatus();
  }

  function renderMstpStatus(data) {
    const st = (data && data.status) || {};
    const rows = [
      `<span class="ok">Connected</span> · ${esc(st.port || $("#serialPort").value)}`,
      `MAC ${esc(st.mac)} · ${esc(st.baud)} baud · max ${esc(st.max_master)}`,
    ];
    if (st.stats) {
      const s = st.stats;
      rows.push(
        `tokens ${esc(s.tokens)} · frames ${esc(s.frames_sent)}/${esc(s.frames_received)}` +
          ` · crc ${esc(s.crc_errors)} · timeouts ${esc(s.timeouts)}`
      );
    }
    $("#mstpStatus").innerHTML = rows.join("<br>");
  }

  function setConnPill(kind, text) {
    const pill = $("#connPill");
    pill.classList.remove("warn", "bad");
    if (kind === "warn") pill.classList.add("warn");
    if (kind === "bad") pill.classList.add("bad");
    pill.querySelector("span").textContent = text;
  }

  /* --------------------------------------------------------------- discovery */
  async function discover() {
    const btn = $("#scanBtn");
    btn.disabled = true;
    btn.innerHTML = '<span class="spinner"></span> Scanning';
    setConnPill("warn", "Scanning");
    try {
      const params = new URLSearchParams({
        transport: state.transport,
        timeout: $("#timeoutSelect").value,
        targets: $("#targetsInput").value.trim(),
      });
      const data = await api("/api/discover?" + params.toString());
      state.devices = data.devices || [];
      state.selected = null;
      state.data = null;
      state.point = null;
      state.history.clear();
      closeDrawer();
      renderDevices();
      renderMain();
      state.lastUpdated = new Date();
      $("#statusUpdated").textContent =
        `Scanned ${data.elapsed_ms} ms · ${state.lastUpdated.toLocaleTimeString()}`;
      setConnPill("ok", `${state.devices.length} devices`);
      if (state.devices.length) {
        toast(`Found ${state.devices.length} device${state.devices.length === 1 ? "" : "s"}`, "info");
        selectDevice(state.devices[0]);
      } else {
        toast("No BACnet devices answered the Who-Is", "error");
      }
    } catch (err) {
      setConnPill("bad", "Scan failed");
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
      btn.innerHTML = icon("radar") + " Scan";
      refreshStatus();
    }
  }

  function deviceAddress(device) {
    const isMstp =
      device.transport === "mstp" ||
      (device.mac !== null && device.mac !== undefined && !device.port);
    return isMstp ? String(device.mac) : `${device.address}:${device.port || 47808}`;
  }

  /* ---------------------------------------------------------------- devices */
  function renderDevices() {
    const list = $("#deviceList");
    $("#deviceCount").textContent = state.devices.length;
    $("#statusDevices").textContent =
      `${state.devices.length} device${state.devices.length === 1 ? "" : "s"}`;
    if (!state.devices.length) {
      list.innerHTML =
        '<div style="padding:18px 10px;text-align:center;color:#8a9aa8;font-size:11.5px;line-height:1.6">' +
        "No devices yet.<br>Run a scan to populate the station.</div>";
      return;
    }
    list.innerHTML = "";
    state.devices.forEach((device) => {
      const active =
        state.selected && state.selected.instance === device.instance;
      const card = el("button", "device-card" + (active ? " active" : ""));
      card.type = "button";
      const sub =
        device.transport === "mstp"
          ? `MS/TP MAC ${device.mac}`
          : `${device.address}:${device.port}`;
      card.innerHTML =
        `<span class="device-icon">${icon("building")}</span>` +
        `<span><span class="device-name">${esc(device.name)}` +
        `<span class="online-dot"></span></span>` +
        `<span class="device-sub">${esc(device.vendor)} · ${esc(sub)}</span></span>` +
        `<span class="device-inst">#${esc(device.instance)}</span>`;
      card.addEventListener("click", () => selectDevice(device));
      list.appendChild(card);
    });
  }

  async function selectDevice(device) {
    state.selected = {
      address: deviceAddress(device),
      instance: device.instance,
      device,
    };
    state.point = null;
    state.priority = null;
    closeDrawer();
    renderDevices();
    $("#stationName").textContent = device.name || `Device ${device.instance}`;
    $("#topbarPath").textContent = `Device ${device.instance} · ${device.vendor || ""}`;
    await loadDevice(false);
  }

  async function loadDevice(refresh) {
    if (!state.selected) return;
    if (state.loading) return;
    state.loading = true;
    renderMain();
    try {
      const params = new URLSearchParams({
        address: state.selected.address,
        instance: state.selected.instance,
        live: "1",
        refresh: refresh ? "1" : "0",
      });
      const data = await api("/api/device?" + params.toString());
      state.data = data;
      recordHistory(data.objects || []);
      state.lastUpdated = new Date();
      $("#statusUpdated").textContent =
        `Updated ${data.loaded_at || state.lastUpdated.toLocaleTimeString()}`;
      renderMain();
      renderDevices();
      updateAlarmBanner();
      if (state.point) {
        const fresh = findObject(state.point.type, state.point.instance);
        if (fresh) {
          state.point = fresh;
          renderDrawer();
        }
      }
    } catch (err) {
      toast(err.message, "error");
      state.data = null;
      renderMain();
    } finally {
      state.loading = false;
    }
  }

  function findObject(type, instance) {
    if (!state.data) return null;
    return (state.data.objects || []).find(
      (o) => o.type === type && o.instance === instance
    );
  }

  function recordHistory(objects) {
    objects.forEach((o) => {
      if (typeof o.present_value !== "number") return;
      const key = `${o.type}:${o.instance}`;
      const arr = state.history.get(key) || [];
      arr.push(o.present_value);
      if (arr.length > 28) arr.shift();
      state.history.set(key, arr);
    });
  }

  /* ------------------------------------------------------------------- main */
  function renderMain() {
    const main = $("#main");
    if (state.view === "alarms") return renderAlarmsView();
    if (state.view === "commissioning") return renderAuditView();
    if (state.view === "network") return renderNetworkView();
    if (state.view === "modbus") return renderModbusView();

    if (!state.selected) return renderEmpty();
    if (state.loading && !state.data) {
      main.innerHTML =
        '<div class="empty"><div class="empty-mark">' + icon("radar") +
        '</div><h3>Loading device…</h3><p>Reading object list and present values over the wire.</p></div>';
      return;
    }
    if (!state.data) return renderEmpty();

    const device = state.selected.device;
    const stats = state.data.stats || {};
    const objects = state.data.objects || [];
    const points = objects.filter((o) => o.type !== 8);

    main.innerHTML = "";
    const crumb = el("div", "breadcrumb");
    crumb.innerHTML =
      `<b>${esc(device.name)}</b><span>/</span><span>Points</span>` +
      `<span class="grow"></span><span class="muted">${esc(objects.length)} objects</span>`;
    main.appendChild(crumb);

    const header = el("div", "device-header");
    header.innerHTML =
      `<div class="device-title"><h1>${esc(device.name)}</h1><div class="chips">` +
      `<span class="chip mono">Device ${esc(device.instance)}</span>` +
      `<span class="chip blue">${esc(device.vendor)}</span>` +
      `<span class="chip">${esc(device.model)}</span>` +
      `<span class="chip ${device.system_status === "Operational" ? "ok" : ""}">${esc(device.system_status)}</span>` +
      `<span class="chip mono">${esc(device.transport === "mstp" ? "MS/TP " + device.mac : device.address + ":" + device.port)}</span>` +
      `</div></div>` +
      `<div class="header-actions">` +
      `<button class="btn" id="refreshDevice">${icon("refresh")} Refresh</button>` +
      `<button class="btn" id="exportCsv">${icon("download")} CSV</button></div>`;
    main.appendChild(header);
    header.querySelector("#refreshDevice").addEventListener("click", () => loadDevice(true));
    header.querySelector("#exportCsv").addEventListener("click", exportCsv);

    const statsRow = el("div", "stats");
    statsRow.innerHTML =
      statTile("Objects", stats.objects || 0, "") +
      statTile("Alarms", stats.alarms || 0, stats.alarms ? "alarm" : "") +
      statTile("Faults", stats.faults || 0, stats.faults ? "fault" : "") +
      statTile("Overridden", stats.overrides || 0, stats.overrides ? "override" : "");
    main.appendChild(statsRow);

    const tabs = el("div", "tabs");
    tabs.innerHTML =
      '<button class="active" data-tab="points">Points</button>' +
      '<button data-tab="info">Device info</button>';
    main.appendChild(tabs);

    const panel = el("div");
    panel.id = "devicePanel";
    main.appendChild(panel);
    tabs.addEventListener("click", (event) => {
      const btn = event.target.closest("button[data-tab]");
      if (!btn) return;
      $$("button", tabs).forEach((b) => b.classList.toggle("active", b === btn));
      if (btn.dataset.tab === "points") renderPoints(panel, points);
      else renderDeviceInfo(panel, device);
    });
    renderPoints(panel, points);
  }

  function statTile(label, value, kind) {
    return (
      `<div class="stat ${kind || ""}"><div class="label">${esc(label)}</div>` +
      `<div class="value">${esc(value)}</div></div>`
    );
  }

  function renderEmpty() {
    const main = $("#main");
    main.innerHTML =
      '<div class="empty"><div class="empty-mark">' + icon("building") + "</div>" +
      "<h3>No device selected</h3>" +
      "<p>Run a Who-Is scan from the sidebar, or start the demo network to explore " +
      "the console with a simulated building. Every read and write goes through the " +
      "same BACnet services used on a live job.</p></div>";
  }

  /* ---------------------------------------------------------------- points */
  function pointGroup(type) {
    if (type === 0 || type === 1 || type === 2) return "analog";
    if (type === 3 || type === 4 || type === 5) return "binary";
    if (type === 13 || type === 14 || type === 19) return "multistate";
    return "other";
  }

  function filteredPoints(points) {
    const term = state.search.trim().toLowerCase();
    return points.filter((o) => {
      if (state.filter === "alarm" && o.status !== "Alarm") return false;
      if (state.filter === "fault" && o.status !== "Fault") return false;
      if (state.filter === "override" && o.status !== "Overridden") return false;
      if (state.filter === "commandable" && !COMMANDABLE.has(o.type)) return false;
      if (["analog", "binary", "multistate"].includes(state.filter) &&
          pointGroup(o.type) !== state.filter) return false;
      if (!term) return true;
      return (
        String(o.name).toLowerCase().includes(term) ||
        String(o.description || "").toLowerCase().includes(term) ||
        String(o.instance).includes(term) ||
        String(o.type_name || "").toLowerCase().includes(term)
      );
    });
  }

  function renderPoints(panel, points) {
    panel.innerHTML = "";
    const toolbar = el("div", "toolbar");
    const chips = [
      ["all", "All"],
      ["analog", "Analog"],
      ["binary", "Binary"],
      ["multistate", "Multistate"],
      ["alarm", "Alarms"],
      ["override", "Overridden"],
      ["commandable", "Commandable"],
    ];
    toolbar.innerHTML =
      '<div class="filters">' +
      chips
        .map(
          ([key, label]) =>
            `<button class="filter-chip${state.filter === key ? " active" : ""}" data-filter="${key}">${label}</button>`
        )
        .join("") +
      "</div>" +
      `<span class="live-pill${state.live ? "" : " paused"}" id="livePill"><i></i>${
        state.live ? "LIVE" : "PAUSED"
      }</span>` +
      '<div class="search-box">' + icon("search") +
      `<input id="pointSearch" placeholder="Search points" value="${esc(state.search)}"></div>`;
    panel.appendChild(toolbar);

    toolbar.addEventListener("click", (event) => {
      const chip = event.target.closest(".filter-chip");
      if (chip) {
        state.filter = chip.dataset.filter;
        renderPoints(panel, points);
        return;
      }
      if (event.target.closest("#livePill")) {
        state.live = !state.live;
        $("#statusPoll").textContent = state.live ? "Live polling 4 s" : "Live polling paused";
        renderPoints(panel, points);
      }
    });
    const search = toolbar.querySelector("#pointSearch");
    search.addEventListener("input", () => {
      state.search = search.value;
      const wrap = panel.querySelector(".table-wrap");
      if (wrap) wrap.replaceWith(buildTable(points));
    });

    panel.appendChild(buildTable(points));
  }

  function buildTable(points) {
    const rows = filteredPoints(points);
    const wrap = el("div", "table-wrap card");
    if (!rows.length) {
      wrap.innerHTML =
        '<div style="padding:26px;text-align:center;color:#8a9aa8">No points match this filter.</div>';
      return wrap;
    }
    const table = el("table", "points");
    table.innerHTML =
      "<thead><tr>" +
      "<th style='width:52px'>Type</th><th style='width:56px'>Inst</th>" +
      "<th>Point name</th><th style='width:96px;text-align:right'>Present value</th>" +
      "<th style='width:64px'>Units</th><th style='width:110px'>Trend</th>" +
      "<th style='width:104px'>Status</th></tr></thead>";
    const body = el("tbody");
    rows.forEach((o) => {
      const tr = el("tr");
      if (state.point && state.point.type === o.type && state.point.instance === o.instance) {
        tr.classList.add("selected");
      }
      const badge = OBJ_BADGE[o.type] || String(o.type);
      const objClass = OBJ_BADGE[o.type] ? `obj-${o.type}` : "obj-other";
      tr.innerHTML =
        `<td><span class="obj-badge ${objClass}">${esc(badge)}</span></td>` +
        `<td class="inst">${esc(o.instance)}</td>` +
        `<td><div class="point-name">${esc(o.name)}</div>` +
        (o.description ? `<div class="point-desc">${esc(o.description)}</div>` : "") +
        "</td>" +
        `<td class="pv">${esc(o.present_value_display)}</td>` +
        `<td class="units">${esc(o.units || "")}</td>` +
        `<td>${sparkline(o)}</td>` +
        `<td><span class="status-badge ${statusClass(o.status)}">${esc(o.status)}</span></td>`;
      tr.addEventListener("click", () => openDrawer(o));
      body.appendChild(tr);
    });
    table.appendChild(body);
    wrap.appendChild(table);
    const foot = el("div", "table-foot");
    foot.innerHTML =
      `<span>${rows.length} of ${points.length} points</span>` +
      "<span>Click a row to inspect &amp; command</span>";
    wrap.appendChild(foot);
    return wrap;
  }

  function statusClass(status) {
    switch (status) {
      case "Alarm": return "st-alarm";
      case "Fault": return "st-fault";
      case "Out of Service": return "st-oos";
      case "Overridden": return "st-override";
      case "Normal": return "st-normal";
      default: return "st-unknown";
    }
  }

  function sparkline(o) {
    const arr = state.history.get(`${o.type}:${o.instance}`);
    if (!arr || arr.length < 2) return '<span class="muted" style="font-size:10px">—</span>';
    const w = 96, h = 22, pad = 2;
    const min = Math.min(...arr), max = Math.max(...arr);
    const span = max - min || 1;
    const step = (w - pad * 2) / (arr.length - 1);
    const pts = arr.map((v, i) => {
      const x = pad + i * step;
      const y = h - pad - ((v - min) / span) * (h - pad * 2);
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    const cls = o.status === "Alarm" ? "alarm" : "";
    return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"><path class="${cls}" d="M${pts.join(" L")}"/></svg>`;
  }

  /* ------------------------------------------------------------ device info */
  function renderDeviceInfo(panel, device) {
    const rows = [
      ["Device instance", device.instance],
      ["Object name", device.name],
      ["Vendor", `${device.vendor}${device.vendor_id ? " (" + device.vendor_id + ")" : ""}`],
      ["Model", device.model],
      ["Firmware", device.firmware],
      ["Application", device.software],
      ["Location", device.location],
      ["System status", device.system_status],
      ["Protocol", `${device.protocol_version || "?"}.${device.protocol_revision || "?"}`],
      ["Max APDU", device.max_apdu],
      ["Segmentation", device.segmentation],
      ["Database rev", device.database_revision],
      ["Transport", device.transport === "mstp" ? "MS/TP" : "BACnet/IP"],
      ["Address", device.transport === "mstp" ? "MAC " + device.mac : `${device.address}:${device.port}`],
    ];
    panel.innerHTML = "";
    const grid = el("div", "info-grid");
    const card = el("div", "info-card");
    card.innerHTML = "<h3>Device identity</h3>";
    rows.forEach(([k, v]) => {
      const row = el("div", "info-row");
      row.innerHTML = `<dt>${esc(k)}</dt><dd>${esc(v === null || v === undefined || v === "" ? "—" : v)}</dd>`;
      card.appendChild(row);
    });
    grid.appendChild(card);

    const card2 = el("div", "info-card");
    card2.innerHTML = "<h3>Object inventory</h3>";
    const counts = {};
    (state.data.objects || []).forEach((o) => {
      counts[o.type_name] = (counts[o.type_name] || 0) + 1;
    });
    Object.keys(counts)
      .sort()
      .forEach((name) => {
        const row = el("div", "info-row");
        row.innerHTML = `<dt>${esc(name)}</dt><dd>${counts[name]}</dd>`;
        card2.appendChild(row);
      });
    grid.appendChild(card2);
    panel.appendChild(grid);

    const note = el("div", "note");
    note.innerHTML =
      "Present values are read with ReadProperty against each object. Commandable " +
      "outputs (AO, BO, BV, MSO, MSV) can be written with WriteProperty at a chosen " +
      "priority 1–16; priority 8 is the typical operator level, and relinquishing " +
      "returns the point to its control program.";
    panel.appendChild(note);
  }

  /* ----------------------------------------------------------------- drawer */
  function openDrawer(o) {
    state.point = o;
    state.priority = null;
    $(".shell").classList.add("drawer-open");
    $("#drawer").hidden = false;
    renderDrawer();
    $$("table.points tbody tr").forEach((tr) => tr.classList.remove("selected"));
    loadPriority(o);
  }

  function closeDrawer() {
    state.point = null;
    state.priority = null;
    $(".shell").classList.remove("drawer-open");
    const drawer = $("#drawer");
    drawer.hidden = true;
    drawer.innerHTML = "";
  }

  async function loadPriority(o) {
    if (!state.selected || !COMMANDABLE.has(o.type)) return;
    try {
      const params = new URLSearchParams({
        address: state.selected.address,
        object_type: o.type,
        instance: o.instance,
      });
      const data = await api("/api/priority?" + params.toString());
      state.priority = data.priority_array || null;
      if (state.point && state.point.type === o.type && state.point.instance === o.instance) {
        renderDrawer();
      }
    } catch (err) {
      state.priority = null;
    }
  }

  function renderDrawer() {
    const drawer = $("#drawer");
    const o = state.point;
    if (!o) return;
    const commandable = COMMANDABLE.has(o.type);
    const activeSlot = state.priority
      ? state.priority.findIndex((v) => v !== null && v !== undefined)
      : -1;

    drawer.innerHTML = "";
    const head = el("div", "drawer-head");
    head.innerHTML =
      `<div class="drawer-top"><span class="obj-badge ${OBJ_BADGE[o.type] ? "obj-" + o.type : "obj-other"}">${esc(OBJ_BADGE[o.type] || o.type)}</span>` +
      `<h2>${esc(o.name)}</h2>` +
      `<button class="icon-btn" id="drawerClose" title="Close">${icon("close")}</button></div>` +
      `<div class="drawer-sub">${esc(o.type_name)} ${esc(o.instance)}` +
      (o.description ? " · " + esc(o.description) : "") + "</div>" +
      `<div class="drawer-value"><span class="big">${esc(o.present_value_display)}</span>` +
      `<span class="u">${esc(o.units || "")}</span>` +
      `<span class="status-badge ${statusClass(o.status)}" style="margin-left:auto">${esc(o.status)}</span></div>`;
    drawer.appendChild(head);
    head.querySelector("#drawerClose").addEventListener("click", closeDrawer);

    const body = el("div", "drawer-body");

    const kv = el("div", "drawer-section");
    const flags = Array.isArray(o.status_flags) ? o.status_flags : [];
    const flagText = flags.length
      ? [
          flags[0] ? "In alarm" : null,
          flags[1] ? "Fault" : null,
          flags[2] ? "Overridden" : null,
          flags[3] ? "Out of service" : null,
        ].filter(Boolean).join(", ") || "All clear"
      : "—";
    kv.innerHTML =
      "<h4>Point detail</h4><dl class='kv'>" +
      `<dt>Object</dt><dd>${esc(o.type_name)}</dd>` +
      `<dt>Instance</dt><dd>${esc(o.instance)}</dd>` +
      `<dt>Status flags</dt><dd>${esc(flagText)}</dd>` +
      `<dt>Units</dt><dd>${esc(o.units || "—")}</dd>` +
      `<dt>Commandable</dt><dd>${commandable ? "Yes" : "No"}</dd>` +
      `<dt>Raw value</dt><dd>${esc(JSON.stringify(o.present_value))}</dd>` +
      "</dl>";
    body.appendChild(kv);

    if (commandable) {
      const prio = el("div", "drawer-section");
      prio.innerHTML = "<h4>Priority array</h4>";
      const table = el("table", "prio-table");
      const values = state.priority || new Array(16).fill(null);
      for (let i = 0; i < 16; i += 1) {
        const v = values[i];
        const tr = el("tr");
        if (v !== null && v !== undefined) tr.classList.add("active");
        tr.innerHTML =
          `<td>${i + 1}</td><td>${esc(formatPriorityValue(v, o))}</td>` +
          `<td>${v === null || v === undefined ? "—" : "active"}</td>`;
        table.appendChild(tr);
      }
      prio.appendChild(table);
      if (activeSlot >= 0) {
        const note = el("div", "muted");
        note.style.cssText = "font-size:10.5px;margin-top:6px";
        note.textContent = `Currently driven from priority ${activeSlot + 1}.`;
        prio.appendChild(note);
      }
      body.appendChild(prio);

      body.appendChild(buildCommandPanel(o, activeSlot));
    } else {
      const note = el("div", "warn-note");
      note.textContent =
        "This object type is not commandable. Inputs and read-only objects cannot " +
        "be written with WriteProperty; command the controller's output instead.";
      body.appendChild(note);
    }

    drawer.appendChild(body);
  }

  function formatPriorityValue(value, o) {
    if (value === null || value === undefined) return "NULL";
    if (typeof value === "number") {
      if (o.type === 3 || o.type === 4 || o.type === 5) return value ? "Active" : "Inactive";
      if (o.state_text && o.state_text[value - 1]) return o.state_text[value - 1];
      return Number.isInteger(value) ? String(value) : value.toFixed(2);
    }
    if (typeof value === "boolean") return value ? "Active" : "Inactive";
    return String(value);
  }

  function buildCommandPanel(o) {
    const section = el("div", "drawer-section");
    section.innerHTML = "<h4>Command / manual override</h4>";
    const form = el("div", "command");

    let valueControl;
    if (o.type === 3 || o.type === 4 || o.type === 5) {
      valueControl =
        '<select id="cmdValue"><option value="1">Active / On</option>' +
        '<option value="0">Inactive / Off</option></select>';
    } else if (o.type === 19 && Array.isArray(o.state_text) && o.state_text.length) {
      valueControl =
        '<select id="cmdValue">' +
        o.state_text
          .map((t, i) => `<option value="${i + 1}">${esc(i + 1)} — ${esc(t)}</option>`)
          .join("") +
        "</select>";
    } else {
      const current = typeof o.present_value === "number"
        ? (Number.isInteger(o.present_value) ? o.present_value : o.present_value.toFixed(2))
        : "";
      valueControl =
        `<input id="cmdValue" type="number" step="any" value="${esc(current)}" placeholder="value">`;
    }

    form.innerHTML =
      `<div class="row"><label>Value${valueControl}</label>` +
      `<label>Priority<select id="cmdPriority">` +
      Array.from({ length: 16 }, (_, i) => {
        const p = i + 1;
        const sel = p === 8 ? " selected" : "";
        const label = p === 8 ? "8 — operator" : String(p);
        return `<option value="${p}"${sel}>${label}</option>`;
      }).join("") +
      "</select></label></div>" +
      '<div class="command-actions">' +
      `<button class="btn primary" id="cmdWrite">${icon("bolt")} Write</button>` +
      `<button class="btn" id="cmdRelinquish">Relinquish</button></div>` +
      '<div class="warn-note">Writes go to the live device. Priority 8 is the normal ' +
      "operator level; 1–2 override safety logic. Relinquish writes NULL to release " +
      "the slot and return the point to automatic control.</div>";
    section.appendChild(form);

    form.querySelector("#cmdWrite").addEventListener("click", () => submitWrite(o, false));
    form.querySelector("#cmdRelinquish").addEventListener("click", () => submitWrite(o, true));
    return section;
  }

  async function submitWrite(o, relinquish) {
    if (!state.selected) return;
    const priority = Number($("#cmdPriority").value);
    let value = null;
    if (!relinquish) {
      const raw = $("#cmdValue").value;
      if (raw === "" || raw === null) {
        toast("Enter a value to write", "error");
        return;
      }
      value = raw;
      if (o.type === 3 || o.type === 4 || o.type === 5) value = raw === "1" || raw === "true";
      else if (o.type === 19) value = Number(raw);
      else if (o.type === 0 || o.type === 1 || o.type === 2) value = Number(raw);
    }
    const btn = $("#cmdWrite");
    const relBtn = $("#cmdRelinquish");
    btn.disabled = relBtn.disabled = true;
    const original = btn.innerHTML;
    btn.innerHTML = '<span class="spinner"></span> Writing';
    try {
      const payload = {
        address: state.selected.address,
        object_type: o.type,
        instance: o.instance,
        property: 85,
        value,
        priority,
        relinquish,
        point_name: o.name,
        device_name: state.selected.device.name,
      };
      const res = await postJson("/api/write", payload);
      toast(
        relinquish
          ? `Relinquished ${o.name} at priority ${priority}`
          : `Wrote ${value} to ${o.name} @ P${priority}`,
        "info"
      );
      if (res.present_value !== undefined && res.present_value !== null) {
        o.present_value = res.present_value;
        o.present_value_display = formatDisplay(o, res.present_value);
      }
      if (res.status_flags) o.status_flags = res.status_flags;
      if (res.status) o.status = res.status;
      const fresh = findObject(o.type, o.instance);
      if (fresh && res.present_value !== undefined) {
        fresh.present_value = o.present_value;
        fresh.present_value_display = o.present_value_display;
        fresh.status = o.status;
      }
      await loadPriority(o);
      renderDrawer();
      renderMain();
      loadAudit();
    } catch (err) {
      toast(err.message, "error");
      loadAudit();
    } finally {
      btn.disabled = relBtn.disabled = false;
      btn.innerHTML = original;
    }
  }

  function formatDisplay(o, value) {
    if (value === null || value === undefined) return "—";
    if (o.type === 3 || o.type === 4 || o.type === 5) return value ? "On" : "Off";
    if (o.type === 19 && o.state_text && o.state_text[value - 1]) return o.state_text[value - 1];
    if (o.type === 0 || o.type === 1 || o.type === 2) {
      return typeof value === "number" ? value.toFixed(2) : String(value);
    }
    return String(value);
  }

  /* ----------------------------------------------------------------- alarms */
  function collectAlarms() {
    const objects = (state.data && state.data.objects) || [];
    return objects.filter((o) => o.status === "Alarm" || o.status === "Fault");
  }

  function updateAlarmBanner() {
    const alarms = collectAlarms();
    const count = alarms.length;
    const railBadge = $("#railAlarmCount");
    railBadge.hidden = count === 0;
    railBadge.textContent = count;
    const banner = $("#alarmBanner");
    if (!count || state.bannerDismissed) {
      banner.hidden = true;
      return;
    }
    banner.hidden = false;
    $("#alarmBannerText").textContent =
      `${count} active alarm${count === 1 ? "" : "s"} on ${state.selected ? state.selected.device.name : "station"}`;
  }

  function renderAlarmsView() {
    const main = $("#main");
    const alarms = collectAlarms();
    main.innerHTML = "";
    const head = el("div", "device-header");
    head.innerHTML =
      '<div class="device-title"><h1>Alarms &amp; faults</h1>' +
      `<div class="chips"><span class="chip ${alarms.length ? "red" : "ok"}">${alarms.length} active</span>` +
      `<span class="chip">${state.selected ? esc(state.selected.device.name) : "No device"}</span></div></div>`;
    main.appendChild(head);

    if (!state.selected) {
      main.appendChild(
        el("div", "note", "Select a device first to review its alarm state.")
      );
      return;
    }
    if (!alarms.length) {
      main.appendChild(
        el("div", "note", "No active alarms or faults reported by status-flags on this device.")
      );
      return;
    }
    const list = el("div", "audit-list");
    alarms.forEach((alarm) => {
      const row = el("div", "audit-row");
      row.innerHTML =
        `<span class="audit-time">${esc(alarm.status)}</span>` +
        `<div><div class="audit-main"><b>${esc(alarm.name)}</b> \u00b7 ` +
        `${esc(alarm.type_name)} ${esc(alarm.instance)}</div>` +
        `<div class="audit-sub">${esc(alarm.description || "No description")} \u00b7 ` +
        `present value ${esc(alarm.present_value_display)}</div></div>` +
        `<span class="outcome ${alarm.status === "Fault" ? "failed" : ""}">${esc(alarm.status)}</span>`;
      list.appendChild(row);
    });
    main.appendChild(list);
  }

  /* ------------------------------------------------------------------ audit */
  async function loadAudit() {
    try {
      const data = await api("/api/audit");
      state.audit = data.entries || [];
      if (state.view === "commissioning") renderAuditView();
    } catch (err) {
      /* ignore */
    }
  }

  function renderAuditView() {
    const main = $("#main");
    main.innerHTML = "";
    const head = el("div", "device-header");
    const ok = state.audit.filter((e) => e.outcome === "ok" || (!e.outcome && !e.error)).length;
    const failed = state.audit.filter((e) => e.outcome === "failed" || (!e.outcome && e.error)).length;
    head.innerHTML =
      '<div class="device-title"><h1>Command log</h1>' +
      `<div class="chips"><span class="chip">${state.audit.length} operations</span>` +
      `<span class="chip ok">${ok} ok</span>` +
      `<span class="chip red">${failed} failed</span></div></div>` +
      `<div class="header-actions"><button class="btn" id="auditRefresh">${icon("refresh")} Refresh</button></div>`;
    main.appendChild(head);
    head.querySelector("#auditRefresh").addEventListener("click", loadAudit);

    if (!state.audit.length) {
      main.appendChild(
        el("div", "note", "No commands issued yet. Writes and relinquishes appear here with time, point, value, priority and outcome.")
      );
      return;
    }
    const list = el("div", "audit-list");
    state.audit.forEach((entry) => {
      const row = el("div", "audit-row");
      const isModbus = String(entry.type || "").startsWith("modbus");
      const stamp =
        typeof entry.time === "number"
          ? new Date(entry.time * 1000).toLocaleTimeString()
          : entry.time;
      if (isModbus) {
        const verb =
          entry.type === "modbus-connect"
            ? "connect"
            : entry.type === "modbus-write"
            ? `write ${(entry.values || []).join(", ")}`
            : String(entry.type);
        row.innerHTML =
          `<span class="audit-time">${esc(stamp)}</span>` +
          `<div><div class="audit-main"><b>Modbus</b> \u00b7 ${esc(verb)}` +
          (entry.type === "modbus-write" ? ` @ ${esc(entry.address)}` : "") +
          "</div>" +
          `<div class="audit-sub">${esc(entry.target || "")} \u00b7 unit ${esc(entry.unit)}` +
          (entry.kind ? ` \u00b7 ${esc(entry.kind)}` : "") +
          (entry.error ? " \u00b7 " + esc(entry.error) : "") +
          "</div></div>" +
          `<span class="outcome ${entry.error ? "failed" : "ok"}">${entry.error ? "failed" : "ok"}</span>`;
        list.appendChild(row);
        return;
      }
      const valueText =
        entry.action === "relinquish" ? "RELINQUISH" : JSON.stringify(entry.value);
      row.innerHTML =
        `<span class="audit-time">${esc(stamp)}</span>` +
        `<div><div class="audit-main"><b>${esc(entry.point || entry.object)}</b> \u00b7 ` +
        `${esc(entry.action)} ${esc(valueText)}` +
        (entry.priority ? ` @ P${esc(entry.priority)}` : "") + "</div>" +
        `<div class="audit-sub">${esc(entry.device || "")} \u00b7 ${esc(entry.address)} \u00b7 ${esc(entry.object)}` +
        (entry.error ? " \u00b7 " + esc(entry.error) : "") + "</div></div>" +
        `<span class="outcome ${entry.outcome === "ok" ? "ok" : "failed"}">${esc(entry.outcome)}</span>`;
      list.appendChild(row);
    });
    main.appendChild(list);
  }

  /* ---------------------------------------------------------------- network */
  function renderNetworkView() {
    const main = $("#main");
    const liveInput = $("#bbmdInput");
    if (liveInput) state.bbmdAddr = liveInput.value;
    main.innerHTML = "";
    const head = el("div", "device-header");
    head.innerHTML =
      '<div class="device-title"><h1>Network</h1><div class="chips">' +
      `<span class="chip blue">${state.transport === "mstp" ? "MS/TP RS-485" : "BACnet/IP"}</span>` +
      `<span class="chip">${state.devices.length} devices</span></div></div>`;
    main.appendChild(head);

    const grid = el("div", "info-grid");
    const st = state.status || {};
    const card1 = el("div", "info-card");
    card1.innerHTML = "<h3>Transport</h3>";
    const transportRows =
      state.transport === "mstp"
        ? [
            ["Mode", "MS/TP master"],
            ["Serial port", (st.mstp && st.mstp.port) || "—"],
            ["Baud", (st.mstp && st.mstp.baud) || "—"],
            ["Our MAC", (st.mstp && st.mstp.mac) || "—"],
            ["Max master", (st.mstp && st.mstp.max_master) || "—"],
            ["Connected", st.mstp && st.mstp.connected ? "Yes" : "No"],
          ]
        : [
            ["Mode", "BACnet/IP"],
            ["UDP port", "47808"],
            ["Local port", st.client_port || "—"],
            ["Simulator", st.simulator && st.simulator.running ? "Running" : "Stopped"],
            ["Demo devices", st.simulator && st.simulator.devices ? st.simulator.devices.length : 0],
          ];
    transportRows.forEach(([k, v]) => {
      const row = el("div", "info-row");
      row.innerHTML = `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`;
      card1.appendChild(row);
    });
    grid.appendChild(card1);

    const card2 = el("div", "info-card");
    card2.innerHTML = "<h3>MS/TP link statistics</h3>";
    const mstpStatus = (st.mstp && st.mstp.status) || {};
    const stats = mstpStatus.stats || {};
    const statRows = [
      ["Tokens passed", stats.tokens],
      ["Frames sent", stats.frames_sent],
      ["Frames received", stats.frames_received],
      ["CRC errors", stats.crc_errors],
      ["Timeouts", stats.timeouts],
      ["Polls", stats.polls],
      ["Next station", mstpStatus.ns],
    ];
    statRows.forEach(([k, v]) => {
      const row = el("div", "info-row");
      row.innerHTML = `<dt>${esc(k)}</dt><dd>${esc(v === undefined || v === null ? "—" : v)}</dd>`;
      card2.appendChild(row);
    });
    grid.appendChild(card2);
    main.appendChild(grid);

    /* ---- BBMD / foreign device ---- */
    const bbmdCard = el("div", "mod-card");
    bbmdCard.style.marginTop = "12px";
    const foreign = (state.bbmd && state.bbmd.foreign) || null;
    bbmdCard.innerHTML =
      "<h3>BBMD / foreign device</h3>" +
      '<div class="row">' +
      '<label class="field grow"><span>BBMD address</span>' +
      `<input id="bbmdInput" value="${esc(state.bbmdAddr || "")}" ` +
      'placeholder="10.0.0.5 or 10.0.0.5:47808" autocomplete="off"></label>' +
      '<label class="field"><span>TTL s</span>' +
      '<input id="bbmdTtl" type="number" min="30" max="65535" value="300"></label>' +
      `<button class="btn ${foreign ? "" : "primary"}" id="bbmdBtn">` +
      `${foreign ? icon("close") + " Unregister" : icon("cable") + " Register"}</button>` +
      '<button class="btn" id="bbmdBdtBtn">' + icon("clipboard") + " Read BDT</button>" +
      '<button class="btn" id="bbmdFdtBtn">' + icon("clipboard") + " Read FDT</button>" +
      "</div>" +
      '<div class="mod-status" style="margin-top:10px">' +
      `<i class="dot ${foreign ? "on" : ""}"></i>` +
      "<span>" +
      (foreign
        ? `Registered with ${esc(foreign.bbmd)} \u00b7 TTL ${esc(foreign.ttl)} s \u00b7 ` +
          `${esc(foreign.age)} s ago`
        : "Not registered \u2014 broadcasts from other subnets will not reach this tool") +
      "</span></div>" +
      '<div class="mod-note">A BBMD forwards BACnet/IP broadcasts between IP subnets. ' +
      "Registering as a foreign device asks that BBMD to relay its broadcast traffic to this " +
      "machine, and Who-Is / I-Am then works across routers. BDT lists the peer BBMDs the " +
      "device knows; FDT lists the foreign devices currently registered with it.</div>" +
      '<div id="bbmdList" style="margin-top:10px"></div>';
    main.appendChild(bbmdCard);
    bbmdCard.querySelector("#bbmdBtn").addEventListener("click", () =>
      foreign ? bbmdUnregister() : bbmdRegister()
    );
    bbmdCard.querySelector("#bbmdBdtBtn").addEventListener("click", () => bbmdTable("bdt"));
    bbmdCard.querySelector("#bbmdFdtBtn").addEventListener("click", () => bbmdTable("fdt"));
    renderBbmdList();

    const note = el("div", "note");
    note.innerHTML =
      "MS/TP runs at 9600–115200 baud over RS-485. Wire A→A, B→B, GND→GND, add 120 Ω " +
      "termination at both physical ends of the trunk, and bias the master. This tool " +
      "acts as a master node: it passes the token, polls for masters and issues " +
      "ReadProperty / WriteProperty to the MAC addresses it discovers.";
    main.appendChild(note);

    if (state.devices.length) {
      const card3 = el("div", "info-card");
      card3.style.marginTop = "12px";
      card3.innerHTML = "<h3>Discovered stations</h3>";
      state.devices.forEach((d) => {
        const row = el("div", "info-row");
        row.innerHTML =
          `<dt>${esc(d.name)}</dt><dd>#${esc(d.instance)} · ` +
          `${esc(d.transport === "mstp" ? "MAC " + d.mac : d.address + ":" + d.port)}</dd>`;
        card3.appendChild(row);
      });
      main.appendChild(card3);
    }
  }

  /* ----------------------------------------------------------------- modbus */
  const MOD_FUNCTIONS = [
    { value: 3, label: "Holding registers (03)" },
    { value: 4, label: "Input registers (04)" },
    { value: 1, label: "Coils (01)" },
    { value: 2, label: "Discrete inputs (02)" },
  ];
  const MOD_FORMATS = [
    { value: "u16", label: "Unsigned 16-bit" },
    { value: "i16", label: "Signed 16-bit" },
    { value: "u32", label: "Unsigned 32-bit" },
    { value: "i32", label: "Signed 32-bit" },
    { value: "f32", label: "Float 32-bit" },
    { value: "f32s", label: "Float 32-bit (word swap)" },
    { value: "hex", label: "Hex" },
    { value: "bin", label: "Binary" },
  ];

  function modDefaults() {
    return {
      mode: "tcp",
      host: "127.0.0.1",
      port: 502,
      serial_port: "",
      baudrate: 9600,
      parity: "N",
      unit: 1,
      timeout: 2,
      connected: false,
      kind: null,
      target: null,
      error: null,
      // read form
      function: 3,
      address: 0,
      count: 10,
      format: "u16",
      auto: false,
      // write form
      writeKind: "register",
      writeAddress: 0,
      writeValue: "",
      writeFormat: "u16",
      // scan form
      scanStart: 1,
      scanEnd: 32,
      scanFunction: 3,
      scanAddress: 0,
      // results
      rows: [],
      readMeta: null,
      scan: null,
      busy: false,
      // simulator
      sim: null,
      simPort: 5020,
      simUnits: "1,2,3",
      ports: [],
    };
  }

  function mod() {
    if (!state.modbus) state.modbus = modDefaults();
    return state.modbus;
  }

  function modCapture() {
    const m = mod();
    const val = (sel, fallback) => {
      const node = $(sel);
      return node ? node.value : fallback;
    };
    if (!state.modbus) return;
    m.mode = val("#modMode", m.mode) || m.mode;
    m.host = val("#modHost", m.host);
    m.port = Number(val("#modPort", m.port)) || m.port;
    m.serial_port = val("#modSerial", m.serial_port);
    m.baudrate = Number(val("#modBaud", m.baudrate)) || m.baudrate;
    m.parity = val("#modParity", m.parity);
    m.unit = Number(val("#modUnit", m.unit)) || m.unit;
    m.timeout = Number(val("#modTimeout", m.timeout)) || m.timeout;
    m.function = Number(val("#modFunction", m.function));
    m.address = Number(val("#modAddress", m.address));
    m.count = Number(val("#modCount", m.count));
    m.format = val("#modFormat", m.format);
    m.writeKind = val("#modWriteKind", m.writeKind);
    m.writeAddress = Number(val("#modWriteAddress", m.writeAddress));
    m.writeValue = val("#modWriteValue", m.writeValue);
    m.writeFormat = val("#modWriteFormat", m.writeFormat);
    m.scanStart = Number(val("#modScanStart", m.scanStart));
    m.scanEnd = Number(val("#modScanEnd", m.scanEnd));
    m.scanFunction = Number(val("#modScanFunction", m.scanFunction));
    m.scanAddress = Number(val("#modScanAddress", m.scanAddress));
    m.simPort = Number(val("#modSimPort", m.simPort)) || m.simPort;
    m.simUnits = val("#modSimUnits", m.simUnits);
  }

  function modOption(list, selected) {
    return list
      .map(
        (item) =>
          `<option value="${item.value}"${String(item.value) === String(selected) ? " selected" : ""}>${esc(item.label)}</option>`
      )
      .join("");
  }

  function renderModbusView() {
    const main = $("#main");
    const m = mod();
    main.innerHTML = "";

    const head = el("div", "device-header");
    head.innerHTML =
      '<div class="device-title"><h1>Modbus master</h1><div class="chips">' +
      `<span class="chip blue">${m.kind === "rtu" ? "RTU · RS-485" : "Modbus TCP"}</span>` +
      `<span class="chip ${m.connected ? "ok" : ""}">${m.connected ? esc(m.target || "connected") : "Disconnected"}</span>` +
      `<span class="chip mono">Unit ${esc(m.unit)}</span></div></div>`;
    main.appendChild(head);

    /* ---- connection bar ---- */
    const bar = el("div", "mod-bar");
    bar.innerHTML =
      '<div class="segmented" id="modModeTabs">' +
      `<button data-mode="tcp" class="${m.mode === "tcp" ? "active" : ""}">Modbus TCP</button>` +
      `<button data-mode="rtu" class="${m.mode === "rtu" ? "active" : ""}">RTU · RS-485</button>` +
      "</div>" +
      (m.mode === "tcp"
        ? '<label class="field grow"><span>Host</span>' +
          `<input id="modHost" value="${esc(m.host)}" placeholder="192.168.1.20" autocomplete="off"></label>` +
          '<label class="field"><span>Port</span>' +
          `<input id="modPort" type="number" min="1" max="65535" value="${esc(m.port)}"></label>`
        : '<label class="field grow"><span>Serial port</span><select id="modSerial">' +
          modPortOptions(m) +
          "</select></label>" +
          '<label class="field"><span>Baud</span><select id="modBaud">' +
          [1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200]
            .map((b) => `<option${b === m.baudrate ? " selected" : ""}>${b}</option>`)
            .join("") +
          "</select></label>" +
          '<label class="field"><span>Parity</span><select id="modParity">' +
          ["N", "E", "O"]
            .map((p) => `<option${p === m.parity ? " selected" : ""}>${p}</option>`)
            .join("") +
          "</select></label>") +
      '<label class="field"><span>Unit ID</span>' +
      `<input id="modUnit" type="number" min="1" max="247" value="${esc(m.unit)}"></label>` +
      '<label class="field"><span>Timeout s</span>' +
      `<input id="modTimeout" type="number" min="0.2" max="10" step="0.1" value="${esc(m.timeout)}"></label>` +
      '<div class="spacer"></div>' +
      `<button class="btn ${m.connected ? "" : "primary"}" id="modConnectBtn">` +
      `${m.connected ? icon("close") + " Disconnect" : icon("cable") + " Connect"}</button>`;
    main.appendChild(bar);

    bar.querySelectorAll("#modModeTabs button").forEach((btn) =>
      btn.addEventListener("click", () => {
        modCapture();
        m.mode = btn.dataset.mode;
        renderModbusView();
      })
    );
    bar.querySelector("#modConnectBtn").addEventListener("click", () => {
      if (m.connected) modDisconnect();
      else modConnect();
    });

    /* ---- scan + simulator ---- */
    const grid = el("div", "mod-grid");

    const scanCard = el("div", "mod-card");
    scanCard.innerHTML =
      "<h3>Unit scan — what is online</h3>" +
      '<div class="row">' +
      '<label class="field"><span>From</span>' +
      `<input id="modScanStart" type="number" min="1" max="247" value="${esc(m.scanStart)}"></label>` +
      '<label class="field"><span>To</span>' +
      `<input id="modScanEnd" type="number" min="1" max="247" value="${esc(m.scanEnd)}"></label>` +
      '<label class="field"><span>Probe</span><select id="modScanFunction">' +
      modOption(MOD_FUNCTIONS, m.scanFunction) +
      "</select></label>" +
      '<label class="field"><span>Address</span>' +
      `<input id="modScanAddress" type="number" min="0" max="65535" value="${esc(m.scanAddress)}"></label>` +
      '<button class="btn" id="modScanBtn">' + icon("radar") + " Scan</button>" +
      "</div>" +
      '<div class="mod-note">Each unit ID is asked for one register. A slave that answers — ' +
      "even with an exception — is online; silence means nothing is at that address.</div>" +
      '<div class="scan-list" id="modScanList"></div>';
    grid.appendChild(scanCard);

    const simCard = el("div", "mod-card");
    const simRunning = !!(m.sim && m.sim.running);
    simCard.innerHTML =
      "<h3>Built-in slave simulator</h3>" +
      '<div class="row">' +
      '<label class="field"><span>Listen port</span>' +
      `<input id="modSimPort" type="number" min="1" max="65535" value="${esc(m.simPort)}"></label>` +
      '<label class="field grow"><span>Unit IDs</span>' +
      `<input id="modSimUnits" value="${esc(m.simUnits)}" placeholder="1,2,3"></label>` +
      `<button class="btn ${simRunning ? "" : "primary"}" id="modSimBtn">` +
      `${simRunning ? icon("stop") + " Stop" : icon("play") + " Start"}</button>` +
      "</div>" +
      '<div class="mod-status" style="margin-top:10px">' +
      `<i class="dot ${simRunning ? "on" : ""}"></i>` +
      `<span>${simRunning ? `Serving units ${esc((m.sim.units || []).join(", "))} on port ${esc(m.sim.port)}` : "Stopped"}</span>` +
      "</div>" +
      '<div class="mod-note">A real Modbus TCP slave on your machine: holding registers 0–19 hold ' +
      "100 + 7·n, a float 21.5 sits at 40001/2 (address 100), a 32-bit counter 123456 at 110/111. " +
      "Point the master at 127.0.0.1 and the simulator port to try every screen without hardware.</div>";
    grid.appendChild(simCard);
    main.appendChild(grid);

    scanCard.querySelector("#modScanBtn").addEventListener("click", modScan);
    simCard.querySelector("#modSimBtn").addEventListener("click", modSimToggle);

    /* ---- register viewer ---- */
    const viewer = el("div", "mod-card");
    viewer.style.marginBottom = "12px";
    const bits = m.function === 1 || m.function === 2;
    viewer.innerHTML =
      '<div class="row" style="margin-bottom:10px">' +
      "<h3 style='margin:0;flex:1 1 auto'>Register viewer</h3>" +
      '<label class="field"><span>Function</span><select id="modFunction">' +
      modOption(MOD_FUNCTIONS, m.function) +
      "</select></label>" +
      '<label class="field"><span>Start address</span>' +
      `<input id="modAddress" type="number" min="0" max="65535" value="${esc(m.address)}"></label>` +
      '<label class="field"><span>Quantity</span>' +
      `<input id="modCount" type="number" min="1" max="125" value="${esc(m.count)}"></label>` +
      '<label class="field"><span>Format</span><select id="modFormat"' + (bits ? " disabled" : "") + ">" +
      modOption(MOD_FORMATS, m.format) +
      "</select></label>" +
      '<button class="btn primary" id="modReadBtn">' + icon("refresh") + " Read</button>" +
      '<label class="field" style="min-width:auto"><span>Auto 2 s</span>' +
      `<input id="modAuto" type="checkbox"${m.auto ? " checked" : ""} style="width:auto"></label>` +
      "</div>" +
      `<div class="mod-note" id="modReadMeta" style="margin:0 0 8px">${esc(m.readMeta || "")}</div>` +
      '<div class="table-wrap" id="modRegWrap"></div>';
    main.appendChild(viewer);

    viewer.querySelector("#modReadBtn").addEventListener("click", modRead);
    viewer.querySelector("#modFunction").addEventListener("change", () => {
      modCapture();
      m.rows = [];
      m.readMeta = null;
      renderModbusView();
    });
    viewer.querySelector("#modFormat").addEventListener("change", () => {
      modCapture();
      if (m.rows.length) modRead();
    });
    viewer.querySelector("#modAuto").addEventListener("change", (event) => {
      m.auto = event.target.checked;
      modSchedule();
    });

    /* ---- write panel ---- */
    const writer = el("div", "mod-card");
    const wKind = m.writeKind;
    const wIsBits = wKind === "coil" || wKind === "coils";
    writer.innerHTML =
      "<h3>Write</h3>" +
      '<div class="row">' +
      '<label class="field"><span>Target</span><select id="modWriteKind">' +
      [
        ["register", "Single register (06)"],
        ["registers", "Multiple registers (16)"],
        ["coil", "Single coil (05)"],
        ["coils", "Multiple coils (15)"],
      ]
        .map(
          ([v, label]) =>
            `<option value="${v}"${v === wKind ? " selected" : ""}>${label}</option>`
        )
        .join("") +
      "</select></label>" +
      '<label class="field"><span>Address</span>' +
      `<input id="modWriteAddress" type="number" min="0" max="65535" value="${esc(m.writeAddress)}"></label>` +
      '<label class="field grow"><span>Value' +
      (wKind === "registers" ? " (comma separated)" : wKind === "coils" ? " (1/0, comma separated)" : "") +
      "</span>" +
      `<input id="modWriteValue" value="${esc(m.writeValue)}" placeholder="${
        wIsBits ? "1" : wKind === "registers" ? "100, 200" : "1234"
      }" autocomplete="off"></label>` +
      '<label class="field"><span>Format</span><select id="modWriteFormat"' +
      (wIsBits ? " disabled" : "") +
      ">" +
      modOption(MOD_FORMATS, m.writeFormat) +
      "</select></label>" +
      '<button class="btn" id="modWriteBtn">' + icon("bolt") + " Write</button>" +
      "</div>" +
      '<div class="mod-note">Writes go out immediately on the wire and are recorded in the ' +
      "Commissioning log. Float and 32-bit formats occupy two registers.</div>";
    main.appendChild(writer);

    writer.querySelector("#modWriteKind").addEventListener("change", () => {
      modCapture();
      renderModbusView();
    });
    writer.querySelector("#modWriteBtn").addEventListener("click", modWrite);

    renderModScanList();
    renderModRegisters();
  }

  function modPortOptions(m) {
    if (!m.ports.length) {
      return `<option value="${esc(m.serial_port)}">${m.serial_port ? esc(m.serial_port) : "No ports found"}</option>`;
    }
    return m.ports
      .map(
        (p) =>
          `<option value="${esc(p.device)}"${p.device === m.serial_port ? " selected" : ""}>` +
          `${esc(p.device)}${p.description ? " — " + esc(p.description) : ""}</option>`
      )
      .join("");
  }

  function renderModScanList() {
    const list = $("#modScanList");
    if (!list) return;
    const m = mod();
    if (!m.scan) {
      list.innerHTML = '<div class="mod-empty">No scan run yet.</div>';
      return;
    }
    const { online, offline_count, elapsed, scanned } = m.scan;
    if (!online.length) {
      list.innerHTML =
        `<div class="mod-empty">Scanned ${esc(scanned)} unit IDs in ${esc(elapsed)} s — nothing answered.</div>`;
      return;
    }
    list.innerHTML =
      `<div class="mod-note" style="margin:0 0 6px">${online.length} online · ` +
      `${offline_count} silent · ${esc(elapsed)} s</div>` +
      online
        .map((item) => {
          const meta =
            item.response === "exception"
              ? `exception ${item.code !== undefined ? item.code : ""}`
              : `${item.ms} ms`;
          return (
            `<div class="scan-item"><i class="dot"></i>` +
            `<span class="unit">#${esc(item.unit)}</span>` +
            `<span>${item.response === "exception" ? "answered with exception" : "answered with data"}</span>` +
            `<span class="meta">${esc(meta)}</span></div>`
          );
        })
        .join("");
  }

  function renderModRegisters() {
    const wrap = $("#modRegWrap");
    if (!wrap) return;
    const m = mod();
    if (!m.rows.length) {
      wrap.innerHTML = m.connected
        ? '<div class="mod-empty">Connected \u2014 press <b>Read</b> to pull registers from the slave.</div>'
        : '<div class="mod-empty">Connect to a device and press Read to see live registers.</div>';
      return;
    }
    const bits = m.function === 1 || m.function === 2;
    const formatted = m.formatted || [];
    const wide = ["u32", "i32", "f32", "u32s", "i32s", "f32s"].includes(m.format);
    const width = wide ? 2 : 1;
    const rows = m.rows
      .map((row, index) => {
        if (bits) {
          return (
            `<tr><td>${esc(row.address)}</td><td>${esc(row.raw)}</td><td>—</td>` +
            `<td class="${row.value ? "bool-on" : "bool-off"}">${row.value ? "ON" : "OFF"}</td></tr>`
          );
        }
        const slot = Math.floor(index / width);
        const value =
          index % width === 0 && formatted[slot] !== undefined ? formatted[slot] : "\u2026";
        return (
          `<tr><td>${esc(row.address)}</td><td>${esc(row.raw)}</td>` +
          `<td>${esc(row.hex || "")}</td><td class="val">${esc(value)}</td></tr>`
        );
      })
      .join("");
    wrap.innerHTML =
      '<table class="reg"><thead><tr><th>Address</th><th>Raw</th><th>Hex</th><th>Value</th></tr></thead>' +
      `<tbody>${rows}</tbody></table>`;
  }

  /* ------------------------------------------------------- modbus actions */
  async function modRefreshStatus() {
    try {
      const data = await api("/api/modbus/status");
      const m = mod();
      m.connected = !!data.connected;
      m.kind = data.kind || m.kind;
      m.target = data.target || m.target;
      m.unit = data.unit || m.unit;
      m.error = data.error || null;
      // Keep the connection bar honest: if the link was opened elsewhere (or
      // restored after a reload) show the host/port that is actually in use.
      if (data.connected && data.target) {
        const parts = String(data.target).split(":");
        if (parts.length === 2) {
          m.host = parts[0];
          m.port = Number(parts[1]) || m.port;
        }
      }
      if (data.connected && data.kind) m.mode = data.kind === "rtu" ? "rtu" : "tcp";
      m.sim = data.simulator || null;
      if (state.view === "modbus") renderModbusView();
    } catch (err) {
      /* service offline — leave the view as-is */
    }
  }

  async function modConnect() {
    modCapture();
    const m = mod();
    const body =
      m.mode === "rtu"
        ? {
            mode: "rtu",
            serial_port: m.serial_port,
            baudrate: m.baudrate,
            parity: m.parity,
            unit: m.unit,
            timeout: m.timeout,
          }
        : { mode: "tcp", host: m.host, port: m.port, unit: m.unit, timeout: m.timeout };
    try {
      const data = await postJson("/api/modbus/connect", body);
      m.connected = !!data.connected;
      m.kind = data.kind;
      m.target = data.target;
      toast(`Connected to ${data.target} (unit ${data.unit})`, "info");
      renderModbusView();
      modRead();
    } catch (err) {
      m.error = err.message;
      toast(err.message, "error");
      renderModbusView();
    }
  }

  async function modDisconnect() {
    try {
      await postJson("/api/modbus/disconnect", {});
      const m = mod();
      m.connected = false;
      m.rows = [];
      m.readMeta = null;
      m.auto = false;
      toast("Modbus link closed", "info");
      renderModbusView();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function modRead() {
    const m = mod();
    if (!m.connected) {
      toast("Connect to a Modbus device first", "error");
      return;
    }
    modCapture();
    try {
      const data = await postJson("/api/modbus/read", {
        function: m.function,
        address: m.address,
        count: m.count,
        format: m.format,
        unit: m.unit,
      });
      m.rows = data.rows || [];
      m.formatted = data.formatted || [];
      m.readMeta =
        `${data.function_name} · ${data.count} value${data.count === 1 ? "" : "s"} from ` +
        `${data.address} · unit ${data.unit} · ${data.ms} ms` +
        (data.format_label ? ` · ${data.format_label}` : "");
      renderModRegisters();
      const meta = $("#modReadMeta");
      if (meta) meta.textContent = m.readMeta;
    } catch (err) {
      toast(err.message, "error");
      m.readMeta = err.message;
      const meta = $("#modReadMeta");
      if (meta) meta.textContent = m.readMeta;
    }
  }

  async function modWrite() {
    modCapture();
    const m = mod();
    if (!m.connected) {
      toast("Connect to a Modbus device first", "error");
      return;
    }
    const raw = String(m.writeValue).trim();
    if (!raw) {
      toast("Enter a value to write", "error");
      return;
    }
    const body = { kind: m.writeKind, address: m.writeAddress, unit: m.unit, format: m.writeFormat };
    if (m.writeKind === "coil") {
      body.value = /^(1|true|on)$/i.test(raw);
    } else if (m.writeKind === "coils") {
      body.values = raw.split(/[\s,]+/).filter(Boolean).map((tok) => /^(1|true|on)$/i.test(tok));
    } else if (m.writeKind === "register") {
      body.value = m.writeFormat === "f32" || m.writeFormat === "f32s" ? Number(raw) : raw;
    } else {
      body.values = raw.split(/[\s,]+/).filter(Boolean);
      if (m.writeFormat === "f32" || m.writeFormat === "f32s" ||
          m.writeFormat === "u32" || m.writeFormat === "i32" ||
          m.writeFormat === "f32s") {
        body.values = [Number(raw)];
      }
    }
    try {
      const data = await postJson("/api/modbus/write", body);
      toast(`Wrote ${data.values.join(", ")} to ${data.address} (unit ${data.unit})`, "info");
      await modRead();
      loadAudit();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function modScan() {
    modCapture();
    const m = mod();
    if (!m.connected) {
      toast("Connect to a Modbus device first", "error");
      return;
    }
    const btn = $("#modScanBtn");
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Scanning…";
    }
    try {
      const data = await postJson("/api/modbus/scan", {
        start: m.scanStart,
        end: m.scanEnd,
        function: m.scanFunction,
        address: m.scanAddress,
        count: 1,
        timeout: 0.4,
      });
      m.scan = data;
      toast(`${data.online.length} unit(s) online of ${data.scanned} probed`, "info");
    } catch (err) {
      toast(err.message, "error");
    } finally {
      renderModbusView();
    }
  }

  async function modSimToggle() {
    modCapture();
    const m = mod();
    try {
      if (m.sim && m.sim.running) {
        await postJson("/api/modbus/sim/stop", {});
        m.sim = null;
        toast("Modbus simulator stopped", "info");
      } else {
        const units = String(m.simUnits)
          .split(/[\s,]+/)
          .filter(Boolean)
          .map((tok) => parseInt(tok, 10))
          .filter((n) => n >= 1 && n <= 247);
        const data = await postJson("/api/modbus/sim/start", {
          host: "127.0.0.1",
          port: m.simPort,
          units: units.length ? units : [1],
        });
        m.sim = data.simulator;
        m.host = "127.0.0.1";
        m.port = m.simPort;
        m.mode = "tcp";
        toast(`Simulator serving units ${(data.simulator.units || []).join(", ")} on ${m.simPort}`, "info");
      }
    } catch (err) {
      toast(err.message, "error");
    } finally {
      renderModbusView();
    }
  }

  function modSchedule() {
    if (state.modbusTimer) {
      clearInterval(state.modbusTimer);
      state.modbusTimer = null;
    }
    const m = mod();
    if (m.auto && m.connected) {
      state.modbusTimer = setInterval(() => {
        if (state.view === "modbus" && mod().connected) modRead();
      }, 2000);
    }
  }

  async function loadModbusPorts() {
    try {
      const data = await api("/api/modbus/ports");
      const m = mod();
      m.ports = data.ports || [];
      if (!m.serial_port && m.ports.length) m.serial_port = m.ports[0].device;
      if (state.view === "modbus") renderModbusView();
    } catch (err) {
      /* ignore */
    }
  }

  /* ------------------------------------------------------------------- bbmd */
  async function refreshBbmd() {
    try {
      const data = await api("/api/bbmd/status");
      state.bbmd = data;
      if (state.view === "network") renderNetworkView();
    } catch (err) {
      /* ignore */
    }
  }

  async function bbmdRegister() {
    const raw = ($("#bbmdInput") || {}).value || "";
    const ttl = Number(($("#bbmdTtl") || {}).value) || 300;
    if (!raw.trim()) {
      toast("Enter the BBMD address first", "error");
      return;
    }
    try {
      const data = await postJson("/api/bbmd/register", { bbmd: raw.trim(), ttl });
      if (data.ok === false) toast(data.error || "BBMD refused the registration", "error");
      else toast(`Registered as foreign device with ${raw.trim()}`, "info");
      await refreshBbmd();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function bbmdUnregister() {
    try {
      await postJson("/api/bbmd/unregister", {});
      toast("Foreign device registration stopped", "info");
      await refreshBbmd();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  async function bbmdTable(kind) {
    const raw = (($("#bbmdInput") || {}).value || "").trim();
    if (!raw) {
      toast("Enter the BBMD address first", "error");
      return;
    }
    try {
      const data = await api(`/api/bbmd/${kind}?bbmd=${encodeURIComponent(raw)}`);
      state.bbmdTable = { kind, target: raw, entries: data.entries || [] };
      renderBbmdList();
    } catch (err) {
      toast(err.message, "error");
    }
  }

  function renderBbmdList() {
    const list = $("#bbmdList");
    if (!list) return;
    const table = state.bbmdTable;
    if (!table) {
      const sim = (state.bbmd && state.bbmd.simulator) || {};
      const bdt = (sim.bdt || []).length;
      const fdt = (sim.fdt || []).length;
      list.innerHTML =
        '<div class="mod-empty">Read the BDT (peer BBMDs) or FDT (registered foreign devices) ' +
        `from a BBMD. Local simulator: ${bdt} BDT entr${bdt === 1 ? "y" : "ies"}, ` +
        `${fdt} FDT entr${fdt === 1 ? "y" : "ies"}.</div>`;
      return;
    }
    const label = table.kind === "bdt" ? "BDT" : "FDT";
    if (!table.entries.length) {
      list.innerHTML = `<div class="mod-empty">${label} on ${esc(table.target)} is empty.</div>`;
      return;
    }
    list.innerHTML =
      `<div class="mod-note" style="margin:0 0 6px">${label} \u00b7 ${table.entries.length} ` +
      `entr${table.entries.length === 1 ? "y" : "ies"} \u00b7 ${esc(table.target)}</div>` +
      table.entries
        .map((entry) => {
          const ttl = entry.ttl !== undefined ? `TTL ${entry.ttl} s` : "";
          const remaining = entry.remaining !== undefined ? `${entry.remaining} s left` : "";
          return (
            '<div class="scan-item"><i class="dot"></i>' +
            `<span class="unit">${esc(entry.address)}</span>` +
            `<span>:${esc(entry.port)}</span>` +
            `<span class="meta">${esc([ttl, remaining].filter(Boolean).join(" \u00b7 "))}</span></div>`
          );
        })
        .join("");
  }

  /* ------------------------------------------------------------------- view */
  function setView(view) {
    state.view = view;
    $$(".rail-btn").forEach((btn) =>
      btn.classList.toggle("active", btn.dataset.view === view)
    );
    if (view !== "devices") closeDrawer();
    if (view === "commissioning") loadAudit();
    if (view === "modbus") {
      loadModbusPorts();
      modRefreshStatus();
    }
    if (view === "network") refreshBbmd();
    renderMain();
    if (view === "modbus") modSchedule();
  }

  /* ------------------------------------------------------------------- csv */
  function exportCsv() {
    if (!state.data) return;
    const rows = [["type", "instance", "name", "description", "present_value", "units", "status"]];
    (state.data.objects || []).forEach((o) => {
      rows.push([
        o.type_name,
        o.instance,
        o.name,
        o.description || "",
        o.present_value_display,
        o.units || "",
        o.status,
      ]);
    });
    const csv = rows
      .map((r) => r.map((cell) => `"${String(cell).replace(/"/g, '""')}"`).join(","))
      .join("\r\n");
    const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
    const link = document.createElement("a");
    const name = state.selected ? state.selected.device.name.replace(/[^\w-]+/g, "_") : "device";
    link.href = URL.createObjectURL(blob);
    link.download = `bacpro_${name}_${Date.now()}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
    toast("CSV exported", "info");
  }

  /* ------------------------------------------------------------ simulator */
  async function toggleSimulator() {
    const btn = $("#simBtn");
    const running = btn.textContent.trim().toLowerCase() === "stop";
    btn.disabled = true;
    try {
      const data = running
        ? await postJson("/api/simulator/stop")
        : await postJson("/api/simulator/start");
      const isRunning = !!data.running;
      btn.textContent = isRunning ? "Stop" : "Start";
      $("#simDot").classList.toggle("on", isRunning);
      const devices = data.devices || [];
      $("#simDetail").textContent = isRunning
        ? `${devices.length} device${devices.length === 1 ? "" : "s"}`
        : "3 devices";
      toast(isRunning ? "Demo network started" : "Demo network stopped", "info");
      if (isRunning) {
        setTransport("ip");
        await discover();
      }
    } catch (err) {
      toast(err.message, "error");
    } finally {
      btn.disabled = false;
      refreshStatus();
    }
  }

  /* ----------------------------------------------------------------- status */
  async function refreshStatus() {
    try {
      const data = await api("/api/status");
      state.status = data;
      const mstp = data.mstp || {};
      if (mstp.connected) {
        renderMstpStatus({ status: mstp.status || {} });
        setConnPill("ok", "MS/TP online");
      } else if (state.transport === "mstp") {
        $("#mstpStatus").innerHTML = mstp.error
          ? `<span class="bad">${esc(mstp.error)}</span>`
          : '<span class="muted">Not connected</span>';
      }
      const sim = data.simulator || {};
      $("#simDot").classList.toggle("on", !!sim.running);
      $("#simBtn").textContent = sim.running ? "Stop" : "Start";
      if (sim.running && sim.devices) {
        $("#simDetail").textContent = `${sim.devices.length} device${sim.devices.length === 1 ? "" : "s"}`;
      }
      if (state.transport === "ip" && !mstp.connected) {
        setConnPill("ok", "Ready");
      }
      if (state.view === "network") renderNetworkView();
    } catch (err) {
      setConnPill("bad", "Service offline");
    }
  }

  /* ------------------------------------------------------------------- wire */
  function wire() {
    $$("#transportTabs button").forEach((btn) =>
      btn.addEventListener("click", () => setTransport(btn.dataset.transport))
    );
    $("#scanBtn").addEventListener("click", discover);
    $("#simBtn").addEventListener("click", toggleSimulator);
    $("#refreshAll").addEventListener("click", () => {
      if (state.selected) loadDevice(true);
      else discover();
    });
    $("#mstpBtn").addEventListener("click", () => {
      const connected = state.status && state.status.mstp && state.status.mstp.connected;
      if (connected) disconnectMstp();
      else connectMstp();
    });
    $$(".rail-btn").forEach((btn) =>
      btn.addEventListener("click", () => setView(btn.dataset.view))
    );
    $$("[data-action]").forEach((btn) =>
      btn.addEventListener("click", () => {
        const action = btn.dataset.action;
        if (action === "show-alarms") setView("alarms");
        if (action === "dismiss-banner") {
          state.bannerDismissed = true;
          updateAlarmBanner();
        }
      })
    );
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape") closeDrawer();
      if (event.key === "Enter" && event.target.id === "targetsInput") discover();
    });
  }

  /* ------------------------------------------------------------------- boot */
  function boot() {
    tickClock();
    setInterval(tickClock, 1000);
    wire();
    renderEmpty();
    refreshStatus();
    loadAudit();
    loadPorts();
    setInterval(() => {
      if (state.live && state.selected && state.view === "devices") loadDevice(false);
    }, POLL_MS);
    setInterval(refreshStatus, 6000);
    // Heartbeat: the desktop launcher uses this to notice the page has gone
    // away when it could not track its own app-window process.
    const beat = () => {
      if (document.visibilityState === "visible") fetch("/api/ping").catch(() => {});
    };
    beat();
    setInterval(beat, 5000);
  }

  document.addEventListener("DOMContentLoaded", boot);
})();
