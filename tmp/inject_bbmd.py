import io, re, sys

path = "static/app.js"
src = io.open(path, encoding="utf-8").read()

if "bbmdCard" in src:
    print("already injected")
    sys.exit(0)

anchor = '    const note = el("div", "note");\n    note.innerHTML =\n      "MS/TP runs at 9600'
assert anchor in src, "anchor not found"

card = '''    /* ---- BBMD / foreign device ---- */
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
      '<button class="btn" id="bbmdBdtBtn">' + icon("table") + " Read BDT</button>" +
      '<button class="btn" id="bbmdFdtBtn">' + icon("table") + " Read FDT</button>" +
      "</div>" +
      '<div class="mod-status" style="margin-top:10px">' +
      `<i class="dot ${foreign ? "on" : ""}"></i>` +
      "<span>" +
      (foreign
        ? `Registered with ${esc(foreign.bbmd)} \\u00b7 TTL ${esc(foreign.ttl)} s \\u00b7 ` +
          `${esc(foreign.age)} s ago`
        : "Not registered \\u2014 broadcasts from other subnets will not reach this tool") +
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

'''
src = src.replace(anchor, card + anchor, 1)

# remember the typed address across re-renders
cap = '''  function renderNetworkView() {
    const main = $("#main");'''
assert cap in src
src = src.replace(cap, '''  function renderNetworkView() {
    const main = $("#main");
    const liveInput = $("#bbmdInput");
    if (liveInput) state.bbmdAddr = liveInput.value;''', 1)

fns = io.open("tmp/bbmd_fns.js", encoding="utf-8").read()
marker = "  /* ------------------------------------------------------------------- view */"
assert marker in src
src = src.replace(marker, fns + marker, 1)

old = '''    if (view === "modbus") {
      loadModbusPorts();
      modRefreshStatus();
    }
    renderMain();'''
assert old in src
src = src.replace(old, '''    if (view === "modbus") {
      loadModbusPorts();
      modRefreshStatus();
    }
    if (view === "network") refreshBbmd();
    renderMain();''', 1)

io.open(path, "w", encoding="utf-8").write(src)
print("injected ok")
