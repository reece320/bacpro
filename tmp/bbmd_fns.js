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

