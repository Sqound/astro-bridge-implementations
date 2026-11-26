(() => {
  "use strict";

  const { languages, catalogs } = window.SetupI18n;
  const main = document.getElementById("main");
  const dialog = document.getElementById("erase-dialog");
  const ipDialog = document.getElementById("ip-dialog");
  const announcer = document.getElementById("announcer");
  const language = document.getElementById("language");
  const preference = "astro-bridge-setup.locale";
  const tauri = window.__TAURI__?.core;
  const nativeContext = Boolean(window.__TAURI__ || window.__TAURI_INTERNALS__)
    || location.protocol === "tauri:" || location.hostname === "tauri.localhost";
  const preview = !nativeContext && new URLSearchParams(location.search).get("preview") === "1";
  const nativeReady = typeof tauri?.invoke === "function" && typeof tauri?.Channel === "function";

  const targets = [
    { id: "pi-zero-w", name: "Raspberry Pi Zero W", group: "raspberry", icon: "chip", medium: "microSd", detail: "zeroDetail" },
    { id: "pi-3", name: "Raspberry Pi 3", group: "raspberry", icon: "chip", medium: "microSd", detail: "piDetail" },
    { id: "pi-4", name: "Raspberry Pi 4", group: "raspberry", icon: "chip", medium: "microSd", detail: "piDetail" },
    { id: "pi-5", name: "Raspberry Pi 5", group: "raspberry", icon: "chip", medium: "microSd", detail: "pi5Detail" },
    { id: "live-usb-x86_64", name: "x86-64", group: "usb", icon: "usb", medium: "usbBoot", detail: "usbDetail" },
    { id: "live-usb-aarch64", name: "ARM64", group: "usb", icon: "usb", medium: "usbBoot", detail: "usbDetail" },
    { id: "docker", name: "Docker", group: "host", icon: "container", medium: "linuxHost", detail: "dockerDetail" },
  ];
  // The five phases of writing a card, in order. They never go back, and `authorizing` can be skipped.
  const flashPhases = ["preparing", "authorizing", "mediaPreparing", "writing", "flashVerification"];
  const flashPhaseNames = ["phaseName1", "phaseName2", "phaseName3", "phaseName4", "phaseName5"];
  // Share of the whole writing bar given to each phase, in the order above; the five add up to 1.
  // Origin: bench of 23/11/2026, writing plus read-back of one card took 1 min 43 s (15:10:55 to 15:12:38).
  // The three first phases last a few seconds each (image check, system prompt, media preparation);
  // the bench did not time writing and read-back apart, so the split of the remaining 100 s or so is an
  // estimate: about 60 s writing and 40 s verification. Fixed weights, not measured per card: the tool
  // reports only percentages, and writing speed depends on the card and the port.
  const flashWeights = [0.03, 0.04, 0.05, 0.55, 0.33];
  // Narrowest a segment is drawn, so the shortest phases stay visible and distinct.
  const flashSegmentMinWidth = "1.1rem";
  const steps = ["stepDevice", "stepRelease", "stepPrepare", "stepLaunch"];
  const launchPaths = { home: "bridgeHomePath", hotspot: "bridgeHotspotPath", other: "bridgeOtherPath" };
  const phaseLabels = {
    resolving: "searching",
    downloading: "downloading",
    verifying: "verifying",
    preparing: "preparing",
    authorizing: "authorizing",
    writing: "writing",
    flashVerification: "flashVerification",
    mediaPreparing: "mediaPreparing",
    revealing: "revealing",
    discovery: "discoveringBridges",
    readingBridge: "readingBridge",
    changingPassword: "changingPassword",
    openingBridge: "openingBridge",
    drivers: "driversDownloadTitle",
  };
  const paths = {
    arrow: '<path d="M4 12h15m-6-6 6 6-6 6"/>',
    back: '<path d="M20 12H5m6-6-6 6 6 6"/>',
    check: '<path d="m5 12 4 4L19 6"/>',
    chip: '<rect x="5" y="5" width="14" height="14" rx="2"/><rect x="9" y="9" width="6" height="6" rx="1"/><path d="M9 2v3m6-3v3M9 19v3m6-3v3M2 9h3m-3 6h3m14-6h3m-3 6h3"/>',
    container: '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7.5 8v8m4.5-8v8m4.5-8v8"/>',
    download: '<path d="M12 3v12m-5-5 5 5 5-5M4 16v4h16v-4"/>',
    folder: '<path d="M3 18V6a1 1 0 0 1 1-1h5l2 2h9a1 1 0 0 1 1 1v10a1 1 0 0 1-1 1H4a1 1 0 0 1-1-1Z"/><path d="M3 10h18"/>',
    info: '<circle cx="12" cy="12" r="9"/><path d="M12 11v6m0-9v.1"/>',
    media: '<path d="M7 3h9l3 3v14a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V6zM9 5v5m3-5v5m3-5v5"/>',
    refresh: '<path d="M20 11a8 8 0 1 0-2 6m2-11v5h-5"/>',
    shield: '<path d="M12 22s8-4 8-10V6l-8-3-8 3v6c0 6 8 10 8 10Z"/><path d="m9 12 2 2 4-4"/>',
    usb: '<path d="M9 8V3h6v5m-7 0h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2v-9a2 2 0 0 1 2-2Z"/><path d="M10 12h4"/>',
    warning: '<path d="M10.3 4.4a2 2 0 0 1 3.4 0l7.5 13.4a2 2 0 0 1-1.7 3H4.5a2 2 0 0 1-1.7-3Z"/><path d="M12 9v4m0 4v.1"/>',
  };
  // What the erase confirmation approved, kept while drivers download; holds the password until written.
  let pendingWrite = null;
  const state = {
    stage: 0,
    target: null,
    release: null,
    downloaded: null,
    checked: false,
    media: [],
    mediaId: null,
    mediaScan: null,
    mediaError: null,
    flashDone: false,
    wifi: { enabled: false, ssid: "", password: "", hidden: false, country: defaultCountry() },
    wifiError: null,
    // The single bridge password given at flash; only in memory, cleared after writing or leaving.
    password: { value: "", confirm: "" },
    passwordError: null,
    // Camera drivers of the release being written: panel data, ticked ids (never ticked in advance).
    drivers: { open: true, panel: null, loading: false, error: false, selected: [] },
    driverRun: null,
    driverNotice: null,
    launch: null,
    busy: null,
    error: null,
  };

  function initialLanguage() {
    try {
      const saved = localStorage.getItem(preference);
      if (Object.hasOwn(catalogs, saved)) return saved;
    } catch { /* Session-only if storage is unavailable. */ }
    for (const locale of navigator.languages ?? []) {
      if (Object.hasOwn(catalogs, locale)) return locale;
      if (/^zh-(TW|HK|MO|Hant)/i.test(locale)) return "zh-Hant";
      if (/^zh(?:-|$)/i.test(locale)) return "zh-Hans";
      if (/^pt(?:-|$)/i.test(locale)) return "pt-BR";
      const base = locale.split("-")[0];
      if (Object.hasOwn(catalogs, base)) return base;
    }
    return "en";
  }

  let locale = initialLanguage();

  function t(key, substitutions = {}) {
    return catalogs[locale][key].replace(/\{(\w+)\}/g, (_, name) => substitutions[name]);
  }

  function el(tag, attributes = {}, ...children) {
    const node = document.createElement(tag);
    for (const [name, value] of Object.entries(attributes)) {
      if (value !== null && value !== false) node.setAttribute(name, value === true ? "" : value);
    }
    node.append(...children.filter(child => child != null));
    return node;
  }

  function icon(name) {
    const wrapper = el("span", { class: "icon", "aria-hidden": "true" });
    wrapper.innerHTML = '<svg viewBox="0 0 24 24" width="100%" height="100%" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round">' + paths[name] + '</svg>';
    return wrapper;
  }

  function tile(name, tone = "") {
    return el("span", { class: "icon-tile " + tone }, icon(name));
  }

  function pill(key, tone = "") {
    return el("span", { class: tone ? `pill ${tone}` : "pill" }, t(key));
  }

  function button(key, action, { style = "", glyph, trailing = false, disabled = false, type = "button" } = {}) {
    const label = el("span", {}, t(key));
    const symbol = glyph ? icon(glyph) : null;
    const node = el("button", { class: "button " + style, type, disabled },
      trailing ? label : symbol, trailing ? symbol : label);
    if (action) node.addEventListener("click", action);
    return node;
  }

  function callout(key, tone = "", values = {}) {
    const glyph = tone === "red" ? "warning" : tone === "green" ? "check" : "info";
    return el("div", { class: "callout " + tone }, icon(glyph), el("p", {}, t(key, values)));
  }

  function panel(title, glyph, text, tone = "") {
    return el("section", { class: "panel" },
      el("div", { class: "panel-heading" }, tile(glyph, tone),
        el("div", {}, el("h2", {}, t(title)), text ? el("p", {}, t(text)) : null)));
  }

  function title(stage, heading, detail) {
    return el("div", { class: "page-heading" },
      el("p", { class: "eyebrow" }, t("stepCount", { step: stage })),
      el("h1", { id: "stage-title", tabindex: "-1" }, t(heading)),
      el("p", {}, t(detail)));
  }

  function stepper() {
    const node = el("ol", { class: "stepper", "aria-label": t("stepsLabel") });
    steps.forEach((key, index) => {
      const number = index + 1;
      const finished = (number < state.stage && (state.stage !== 4 || state.target))
        || (number === 3 && state.flashDone);
      const parts = [el("span", { class: "step-number", "aria-hidden": "true" }, finished ? icon("check") : number), el("span", {}, t(key))];
      // Launch is always one click away, for a bridge that is already written.
      const jump = number === 4 && state.stage >= 1 && state.stage <= 3 && !state.busy && (nativeReady || preview);
      let body = parts;
      if (jump) {
        const link = el("button", { type: "button", class: "step-link", id: "step-launch", title: t("runningBridge") }, ...parts);
        link.addEventListener("click", () => enterLaunch(state.stage, true));
        body = [link];
      }
      node.append(el("li", {
        class: [finished ? "step complete" : "step", jump ? "jump" : ""].join(" ").trim(),
        "aria-current": number === state.stage ? "step" : null,
      }, ...body));
    });
    return node;
  }

  function home() {
    const intro = el("section", { class: "hero" },
      el("div", { class: "hero-copy" },
        el("p", { class: "eyebrow" }, t("welcomeKicker")),
        el("h1", { id: "stage-title", tabindex: "-1" }, t("welcomeTitle"), el("br"), el("span", {}, t("welcomeAccent"))),
        el("p", {}, t("welcomeBody")),
        el("div", { class: "hero-actions" },
          button("start", () => navigate(1), { style: "primary", glyph: "arrow", trailing: true, disabled: !preview && !nativeReady }),
          button("runningBridge", () => enterLaunch(0), { disabled: !preview && !nativeReady }))),
      el("img", { src: "orbit.svg", alt: "", width: "490", height: "400" }));
    const cards = el("ol", { class: "welcome-steps" });
    [["chip", "introDevice"], ["shield", "introRelease"], ["media", "introPrepare"], ["arrow", "introLaunch"]].forEach(([glyph, key], index) => {
      cards.append(el("li", {},
        el("div", { class: "welcome-icon" }, tile(glyph), el("span", { "aria-hidden": "true" }, "0" + (index + 1))),
        el("h2", {}, t(steps[index])), el("p", {}, t(key))));
    });
    return [intro, cards, !preview && !nativeReady ? el("div", { class: "home-notice" }, callout("appRequired", "gold")) : null];
  }

  function displayTarget(target) {
    return target.group === "usb" ? t("usbName", { arch: target.name }) : target.name;
  }

  function deviceChoice(target) {
    const selected = state.target?.id === target.id;
    const input = el("input", { type: "radio", name: "target", id: "target-" + target.id, value: target.id,
      checked: selected, "aria-labelledby": "name-" + target.id, "aria-describedby": "medium-" + target.id });
    input.addEventListener("change", () => {
      if (selected || state.busy) return;
      state.wifi.password = "";
      clearBridgePassword();
      state.target = target;
      render(input.id);
    });
    return el("label", { class: selected ? "choice selected" : "choice" },
      input, tile(target.icon), el("span", { class: "choice-body" },
        el("strong", { id: "name-" + target.id }, displayTarget(target)),
        el("span", { id: "medium-" + target.id }, pill(target.medium))),
      el("span", { class: "radio-mark", "aria-hidden": "true" }));
  }

  function summary() {
    const target = state.target;
    if (!target) return el("aside", { class: "panel selection" },
      tile("chip"), el("h2", {}, t("noTarget")), el("p", {}, t("noTargetBody")));
    return el("aside", { class: "panel selection" }, tile(target.icon),
      el("p", { class: "eyebrow" }, t("selectedTarget")), el("h2", {}, displayTarget(target)),
      pill(target.medium), el("p", {}, t(target.detail)),
      state.stage === 1 ? callout("hostSeparate") : null);
  }

  function layout(content) {
    return el("div", { class: "wizard-layout" },
      el("div", { class: "wizard-content" }, ...content), summary());
  }

  function actions(back, ...next) {
    return el("div", { class: "actions" }, back, el("div", { class: "actions-end" }, ...next));
  }

  function back(stage) {
    return button("back", () => navigate(stage), { style: "ghost", glyph: "back", disabled: Boolean(state.busy) });
  }

  function deviceStage() {
    const groups = el("fieldset", { class: "choices" }, el("legend", { class: "sr-only" }, t("deviceTitle")));
    const groupBody = el("div", { class: "device-groups" });
    for (const group of ["raspberry", "usb", "host"]) {
      groupBody.append(el("div", { role: "group", "aria-labelledby": "group-" + group },
        el("h2", { class: "group-label", id: "group-" + group }, t(group)),
        el("div", { class: group === "host" ? "choice-grid single" : "choice-grid" },
          ...targets.filter(target => target.group === group).map(deviceChoice))));
    }
    groups.append(groupBody);
    return [title(1, "deviceTitle", "deviceIntro"), layout([groups]),
      actions(back(0), button("findRelease", () => {
        navigate(2);
        resolveRelease();
      }, { style: "primary", glyph: "arrow", trailing: true, disabled: !state.target }))];
  }

  function size(bytes) {
    const units = ["byte", "kilobyte", "megabyte", "gigabyte", "terabyte"];
    const exponent = bytes ? Math.min(Math.floor(Math.log10(bytes) / 3), units.length - 1) : 0;
    return new Intl.NumberFormat(locale, { style: "unit", unit: units[exponent], unitDisplay: "short", maximumFractionDigits: 1 }).format(bytes / (1000 ** exponent));
  }

  function releaseMeta(release) {
    const row = (label, value, wide = false, hash = false) => el("div", { class: wide ? "full" : "" },
      el("dt", {}, t(label)), el("dd", { class: hash ? "hash" : "", dir: "ltr" }, value));
    return el("dl", { class: "release-meta" },
      row("version", release.tag), row("size", size(release.size)),
      row("filename", release.asset, true), row(release.source === "local-test" ? "localChecksum" : "checksum", release.sha256, true, true));
  }

  function errorBox() {
    if (!state.error) return null;
    const { key, detail } = state.error;
    const failedRank = key === "flashError" ? flashPhases.indexOf(state.error.phase) : -1;
    const message = callout(key, "red");
    if (failedRank >= 0) message.querySelector("p").append(el("span", { class: "error-phase" },
      t("flashErrorAt", { n: failedRank + 1, total: flashPhases.length, name: t(flashPhaseNames[failedRank]) })));
    return el("div", { class: "error-box", role: "alert" }, message, detail
      ? el("details", { class: "error-details" }, el("summary", {}, t("technicalDetails")), el("p", {}, detail))
      : null);
  }

  function progressPanel() {
    const node = el("section", { class: "panel" },
      el("div", { class: "panel-heading" }, tile("shield"),
        el("div", {}, el("h2", { id: "progress-title" }), el("p", { id: "progress-description" }))));
    node.append(el("div", { class: "progress-body" },
      el("div", { class: "progress-label", "aria-hidden": "true" }, el("span", { id: "phase-text", class: "phase-text" }), el("span", { id: "bytes" }), el("span", { id: "percent" })),
      el("div", { id: "flash-progress", class: "segments", role: "progressbar", "aria-labelledby": "progress-title", "aria-valuemin": "0", "aria-valuemax": "100", hidden: true },
        ...flashPhases.map((phase, index) => el("span", { class: "seg", "data-phase": phase, "data-weight": String(flashWeights[index]), "aria-hidden": "true" }, el("i")))),
      el("progress", { class: "progress-meter", id: "progress", "aria-labelledby": "progress-title", hidden: true }),
      el("span", { id: "activity-track", class: "activity-track", "aria-hidden": "true" })));
    node.append(el("details", { id: "progress-detail", class: "error-details", hidden: true },
      el("summary", {}, t("technicalDetails")), el("p", { id: "progress-message" })));
    return node;
  }

  function progressView() {
    const phase = state.busy.phase;
    const details = {
      resolving: "searchingDetail",
      downloading: "downloadingDetail",
      verifying: "verifyingDetail",
      preparing: "preparingDetail",
      authorizing: "authorizingDetail",
      writing: "writingDetail",
      flashVerification: "writingDetail",
      mediaPreparing: "writingDetail",
      revealing: "revealingDetail",
      discovery: "discoveringBridgesDetail",
    };
    const progressTitle = document.getElementById("progress-title");
    if (!progressTitle) return;
    const local = state.release?.source === "local-test";
    const rank = flashPhases.indexOf(phase);
    const nextTitle = t(local && phase === "downloading" ? "localCopying" : local && phase === "verifying" ? "localVerify" : phaseLabels[phase]);
    if (rank >= 0) {
      // One fixed title for the whole write; the phase is named on the line above the bar.
      progressTitle.textContent = t("flashTitle");
      document.getElementById("progress-description").textContent = t(details[phase]);
      if (state.busy.announced !== phase) {
        state.busy.announced = phase;
        announcer.textContent = nextTitle;
      }
    } else if (progressTitle.textContent !== nextTitle) {
      progressTitle.textContent = nextTitle;
      const detail = local && phase === "downloading" ? "localCopyingDetail" : details[phase];
      document.getElementById("progress-description").textContent = detail ? t(detail) : "";
      announcer.textContent = nextTitle;
    }
    document.getElementById("progress-detail").hidden = !state.busy.message;
    document.getElementById("progress-message").textContent = state.busy.message ?? "";
    const bytes = document.getElementById("bytes");
    const percent = document.getElementById("percent");
    const progress = document.getElementById("progress");
    const activity = document.getElementById("activity-track");
    const { received, total } = state.busy;
    const downloadMeasured = phase === "downloading" && Number.isSafeInteger(total) && total > 0
      && Number.isSafeInteger(received) && received >= 0 && received <= total;
    const flashMeasured = ["writing", "flashVerification"].includes(phase) && Number.isInteger(state.busy.flashPercent);
    const measurable = downloadMeasured || flashMeasured;
    const flashBar = document.getElementById("flash-progress");
    if (rank >= 0) {
      // Whole-operation progress: finished phases count in full, the current one by its share.
      const overall = flashWeights.slice(0, rank).reduce((sum, weight) => sum + weight, 0)
        + (flashMeasured ? flashWeights[rank] * state.busy.flashPercent / 100 : 0);
      const overallPercent = Math.round(overall * 100);
      const rankText = t("phaseCount", { n: rank + 1, total: flashPhases.length });
      document.getElementById("phase-text").textContent = rankText + " \u00b7 " + t(flashPhaseNames[rank]);
      bytes.textContent = "";
      percent.textContent = flashMeasured ? new Intl.NumberFormat(locale, { style: "percent" }).format(overallPercent / 100) : "";
      progress.hidden = true;
      activity.hidden = true;
      flashBar.hidden = false;
      flashBar.style.gridTemplateColumns = flashWeights.map(weight => "minmax(" + flashSegmentMinWidth + ", " + weight * 100 + "fr)").join(" ");
      if (flashMeasured) flashBar.setAttribute("aria-valuenow", String(overallPercent)); else flashBar.removeAttribute("aria-valuenow");
      flashBar.setAttribute("aria-valuetext", rankText + ", " + t(flashPhaseNames[rank]) + (flashMeasured ? ", " + percent.textContent : ""));
      [...flashBar.children].forEach((segment, index) => {
        segment.classList.toggle("active", index === rank && !flashMeasured);
        segment.firstElementChild.style.width = index < rank ? "100%" : index === rank && flashMeasured ? state.busy.flashPercent + "%" : "";
      });
      return;
    }
    flashBar.hidden = true;
    document.getElementById("phase-text").textContent = "";
    bytes.textContent = downloadMeasured ? t("byteProgress", { received: size(received), total: size(total) }) : "";
    percent.textContent = measurable ? new Intl.NumberFormat(locale, { style: "percent" }).format(downloadMeasured ? received / total : state.busy.flashPercent / 100) : "";
    progress.hidden = !measurable;
    activity.hidden = measurable;
    if (measurable) {
      progress.max = downloadMeasured ? total : 100;
      progress.value = downloadMeasured ? received : state.busy.flashPercent;
    }
  }

  function releaseStage() {
    let content;
    if (state.busy) {
      content = progressPanel();
      if (state.release) content.append(releaseMeta(state.release));
    } else if (state.downloaded) {
      content = panel(preview ? "previewVerified" : state.release.source === "local-test" ? "localVerified" : "downloadVerified", "check",
        preview ? "previewVerifiedDetail" : "verifiedDetail", "green");
      content.append(releaseMeta(state.release));
      if (state.release.source === "local-test") content.append(el("p", {}, t("localFound")));
    } else if (state.release) {
      content = state.target.id === "docker" ? panel("installerFound", "container", "releaseDetail")
        : panel(state.release.source === "local-test" ? "localFound" : "imageFound", "download", state.release.source === "local-test" ? "localDetail" : "releaseDetail");
      content.append(releaseMeta(state.release));
    } else if (state.checked && !state.error) {
      content = panel("noRelease", "info", "noReleaseDetail");
    } else {
      content = panel("releaseTitle", "shield", "releaseIntro");
    }

    const reveal = preview ? null : button("reveal", revealDownload, { glyph: "folder", disabled: Boolean(state.busy) });
    const download = button(state.release?.source === "local-test" ? "localVerify" : "download", downloadRelease, { style: "primary", glyph: "download", disabled: Boolean(state.busy) });
    const proceed = button("continue", () => {
      navigate(3);
      if (state.target.id !== "docker") { listMedia(); loadDrivers(); }
    }, { style: "primary", glyph: "arrow", trailing: true, disabled: Boolean(state.busy) });
    const check = button("checkAgain", resolveRelease, { glyph: "refresh", disabled: Boolean(state.busy) });
    return [title(2, "releaseTitle", state.release?.source === "local-test" ? "localDetail" : "releaseIntro"), layout([content, errorBox()]),
      actions(back(1), ...(state.downloaded ? [reveal, proceed] : state.release ? [download] : [check]))];
  }

  function mediaChoice(media, index) {
    const selected = state.mediaId === media.id;
    const id = "media-" + index;
    const input = el("input", { type: "radio", name: "media", id, checked: selected,
      "aria-labelledby": id + "-name", "aria-describedby": id + "-details" });
    input.addEventListener("change", () => {
      if (selected || state.busy || state.mediaScan) return;
      state.mediaId = media.id;
      updateMediaPanel(id);
    });
    return el("label", { class: selected ? "media-option selected" : "media-option" }, input, tile("media"),
      el("span", { class: "choice-body" },
        el("strong", { id: id + "-name" }, media.name),
        el("span", { class: "details", id: id + "-details" }, size(media.size), " · ",
          el("bdi", { class: "media-id" }, media.id))),
      el("span", { class: "radio-mark", "aria-hidden": "true" }));
  }

  function mediaResults() {
    const content = el("div", { id: "media-results" });
    if (state.mediaScan) {
      content.append(el("div", { role: "status", "aria-live": "polite" }, el("p", {}, t("listing")), el("p", {}, t("listingDetail"))));
    } else if (state.mediaError) {
      const error = el("div", { role: "alert" }, callout("mediaError", "red"));
      if (state.mediaError.detail) error.append(el("details", { class: "error-details" }, el("summary", {}, t("technicalDetails")), el("p", {}, state.mediaError.detail)));
      content.append(error);
    } else if (state.media.length) {
      content.append(el("fieldset", { class: "media-options" }, el("legend", { class: "sr-only" }, t("removableMedia")),
        ...state.media.map(mediaChoice)));
    } else {
      content.append(el("p", {}, t("noMedia")));
    }
    return content;
  }

  function mediaPanel() {
    if (state.busy) return progressPanel();
    if (state.flashDone) {
      return panel(preview ? "previewWritten" : "mediaReady", "check",
        preview ? "previewWrittenDetail" : "mediaReadyDetail", "green");
    }
    const content = panel("removableMedia", "media", "removableDetail");
    content.id = "media-panel";
    const refresh = button("refreshMedia", listMedia, { glyph: "refresh" });
    refresh.id = "media-refresh";
    refresh.setAttribute("aria-disabled", String(Boolean(state.mediaScan)));
    content.append(mediaResults(), refresh);
    return content;
  }

  function updateMediaPanel(focusId) {
    document.getElementById("media-results")?.replaceWith(mediaResults());
    document.getElementById("media-refresh")?.setAttribute("aria-disabled", String(Boolean(state.mediaScan)));
    const review = document.getElementById("media-review");
    if (review) review.disabled = Boolean(state.busy || state.mediaScan || !state.mediaId);
    if (focusId) document.getElementById(focusId)?.focus({ preventScroll: true });
  }

  function dockerPanel() {
    const content = panel("installerReady", "check", preview ? "previewHandoff" : "dockerHandoff", "green");
    const basename = preview ? state.downloaded.name : state.release.sha256 + "-" + state.downloaded.name;
    const name = "'./" + basename.replaceAll("'", "'\\''") + "'";
    const commands = el("ol", { class: "command-list" });
    commands.append(
      el("li", {}, t("dockerPlan"), el("pre", {}, el("code", {}, "sh " + name + " --mode linux"))),
      el("li", {}, t("dockerApply"), el("pre", {}, el("code", {}, "sudo sh " + name + " --mode linux --apply"))),
    );
    content.append(commands);
    return content;
  }

  // The release says what its image reads at first boot (published Pi 5 images,
  // and any image of a format 2 manifest). Same panels on every hardware; the
  // writing helper checks the image bytes again before writing.
  function announced(flag) {
    return ["published", "local-test"].includes(state.release?.source) && state.release?.[flag] === true;
  }
  function wifiCapable() { return announced("wifi_seed"); }
  function passwordCapable() { return announced("password_seed"); }
  function driversCapable() { return announced("driver_drop"); }
  // The regulatory country starts from the computer's region and stays editable.
  function defaultCountry() {
    for (const tag of navigator.languages?.length ? navigator.languages : [navigator.language]) {
      try {
        const locale = new Intl.Locale(tag);
        const region = locale.region || locale.maximize().region;
        if (/^[A-Z]{2}$/.test(region || "")) return region;
      } catch { /* An invalid language tag gives no region. */ }
    }
    return "";
  }
  function validWifi() {
    const { ssid, password, country } = state.wifi;
    const bytes = value => new TextEncoder().encode(value).length;
    const controls = value => /[\u0000-\u001f\u007f-\u009f]/u.test(value);
    return bytes(ssid) >= 1 && bytes(ssid) <= 32 && !controls(ssid)
      && !controls(password) && ((bytes(password) >= 8 && bytes(password) <= 63) || /^[0-9a-fA-F]{64}$/.test(password))
      && /^[A-Z]{2}$/.test(country);
  }
  function wifiPanel() {
    const content = panel("wifiTitle", "info", "wifiDetail");
    // The Pi Zero W has one radio: say what private Wi-Fi costs the hotspot.
    if (state.target?.id === "pi-zero-w") content.querySelector(".panel-heading div").append(el("p", { class: "panel-note" }, t("wifiSingleRadio")));
    const enabled = el("input", { type: "checkbox", id: "wifi-enabled", checked: state.wifi.enabled });
    enabled.addEventListener("change", () => { state.wifi.enabled = enabled.checked; state.wifi.password = ""; state.wifiError = null; render("wifi-enabled"); });
    content.append(el("label", { class: "confirm-check" }, enabled, el("span", {}, t("wifiEnable"))));
    if (!state.wifi.enabled) return content;
    for (const [key, label, type, autocomplete] of [["ssid", "wifiSsid", "text", "off"], ["password", "wifiPassword", "password", "new-password"], ["country", "wifiCountry", "text", "off"]]) {
      const id = "wifi-" + key, input = el("input", { id, type, autocomplete, value: state.wifi[key], ...(key === "country" ? { maxlength: "2" } : {}) });
      input.addEventListener("input", () => { state.wifi[key] = key === "country" ? input.value.toUpperCase() : input.value; if (key === "country") input.value = state.wifi[key]; });
      content.append(el("label", { class: "launch-field", for: id }, el("span", {}, t(label)), input));
    }
    const hidden = el("input", { type: "checkbox", id: "wifi-hidden", checked: state.wifi.hidden });
    hidden.addEventListener("change", () => { state.wifi.hidden = hidden.checked; });
    content.append(el("label", { class: "confirm-check" }, hidden, el("span", {}, t("wifiHidden"))));
    if (state.wifiError) content.append(el("p", { class: "wifi-error", role: "alert" }, t(state.wifiError)));
    return content;
  }
  // The bridge rule (wifi_bridge/password.py): 8 to 63 printable ASCII characters, no space, not 12345678.
  function bridgePasswordProblem() {
    const { value, confirm } = state.password;
    if (!value && !confirm) return null;
    if (!/^[\x21-\x7e]{8,63}$/.test(value)) return "bridgePasswordRule";
    if (value === "12345678") return "bridgePasswordDefault";
    if (value !== confirm) return "bridgePasswordMismatch";
    return null;
  }
  function clearBridgePassword() {
    state.password.value = ""; state.password.confirm = ""; state.passwordError = null;
    for (const id of ["bridge-password", "bridge-password-confirm"]) { const field = document.getElementById(id); if (field) field.value = ""; }
  }
  function passwordPanel() {
    const content = panel("bridgePasswordTitle", "info", "bridgePasswordDetail");
    content.id = "password-panel";
    for (const [key, label, id] of [["value", "bridgePassword", "bridge-password"], ["confirm", "bridgePasswordConfirm", "bridge-password-confirm"]]) {
      const input = el("input", { id, type: "password", autocomplete: "new-password", value: state.password[key], "aria-describedby": "bridge-password-hint" });
      input.addEventListener("input", () => {
        state.password[key] = input.value;
        if (state.passwordError) { state.passwordError = null; document.getElementById("bridge-password-error")?.remove(); }
      });
      content.append(el("label", { class: "launch-field", for: id }, el("span", {}, t(label)), input));
    }
    content.append(el("p", { class: "field-hint", id: "bridge-password-hint" }, t("bridgePasswordHint")));
    if (state.passwordError) content.append(el("p", { class: "wifi-error", id: "bridge-password-error", role: "alert" }, t(state.passwordError)));
    return content;
  }

  function driverOffers() { return state.drivers.panel?.offers ?? []; }
  function driverName(id) { return driverOffers().find(offer => offer.id === id)?.name ?? id; }
  function driversSummary() {
    if (state.drivers.panel && !state.drivers.panel.supported) return t("driversNotSupported");
    const names = state.drivers.selected.map(driverName);
    if (!names.length) return t("driversSummaryNone");
    const list = new Intl.ListFormat(locale, { style: "short", type: "unit" }).format(names);
    return names.length === 1 ? t("driversSummaryOne", { names: list }) : t("driversSummaryMany", { count: names.length, names: list });
  }
  function driversPanel() {
    const { panel: data, open, loading, error } = state.drivers;
    const content = el("section", { class: "panel drivers-panel", id: "drivers-panel" });
    const supported = !data || data.supported;
    const heading = el("div", { class: "panel-heading drivers-heading" }, tile("info"), el("h2", { id: "drivers-title" }, t("driversTitle")));
    content.append(heading);
    if (!supported) {
      // No camera service on this hardware: the panel stays, and says so.
      content.append(el("p", { class: "drivers-unsupported" }, t("driversNotSupported")));
      return content;
    }
    const toggle = el("button", { type: "button", class: "drivers-toggle", id: "drivers-toggle", "aria-expanded": String(open), "aria-controls": "drivers-body", disabled: Boolean(state.busy) },
      el("span", { id: "drivers-summary" }, driversSummary()), el("span", { class: "chevron", "aria-hidden": "true" }, open ? "▲" : "▼"));
    toggle.addEventListener("click", () => { state.drivers.open = !state.drivers.open; render("drivers-toggle"); });
    heading.append(toggle);
    if (!open) return content;
    const body = el("div", { id: "drivers-body" });
    if (loading) body.append(el("p", { role: "status" }, t("listing")));
    else if (error) body.append(el("p", {}, t("driversUnavailable")));
    else if (!driverOffers().length) body.append(el("p", {}, t("driversNone")));
    else {
      body.append(el("p", {}, t("driversDetail")));
      const list = el("ul", { class: "driver-list", "aria-labelledby": "drivers-title" });
      for (const offer of driverOffers()) {
        const id = "driver-" + offer.id;
        const box = el("input", { type: "checkbox", id, checked: state.drivers.selected.includes(offer.id), disabled: Boolean(state.busy) });
        box.addEventListener("change", () => {
          const selected = state.drivers.selected.filter(item => item !== offer.id);
          if (box.checked) selected.push(offer.id);
          state.drivers.selected = selected;
          document.getElementById("drivers-summary").textContent = driversSummary();
        });
        const licence = el("button", { type: "button", class: "link-button" }, t("driversReadLicence"));
        licence.addEventListener("click", () => { invoke("open_licence", { id: offer.id }).catch(() => {}); });
        list.append(el("li", { class: "driver-row" },
          el("label", { for: id }, box, el("span", { class: "driver-name" }, el("bdi", {}, offer.name)),
            offer.verifiedOnHardware ? null : el("span", { class: "driver-note" }, t("driversUnverified"))),
          licence));
      }
      body.append(list);
    }
    content.append(body);
    return content;
  }
  function driverStateText(row) {
    return t({ waiting: "driverWaiting", downloading: "driverWaiting", ready: "driverVerified", changed: "driverChanged" }[row.state] ?? "driverFailed");
  }
  function driverDownloadPanel() {
    const content = panel("driversDownloadTitle", "info", "driversDownloadDetail");
    content.id = "driver-download";
    const grid = el("div", { class: "driver-progress" });
    for (const row of state.driverRun.rows) {
      const meter = el("progress", { id: "driver-meter-" + row.id, max: row.total || 1, value: row.state === "ready" ? (row.total || 1) : ["waiting", "downloading"].includes(row.state) ? row.received || 0 : 0, "aria-label": row.name });
      grid.append(el("div", { class: "driver-progress-row" }, el("span", { class: "driver-name" }, el("bdi", {}, row.name)), meter,
        el("span", { id: "driver-state-" + row.id, class: "state" + (["failed", "changed", "offline"].includes(row.state) ? " failed" : "") }, driverProgressText(row))));
    }
    content.append(grid);
    if (state.driverRun.status === "failed") {
      for (const row of state.driverRun.rows.filter(item => item.state !== "ready")) {
        content.append(el("div", { class: "driver-message", role: "alert" }, callout(row.state === "changed" ? "driversChangedMessage" : "driversFailedMessage", "gold", { name: row.name })));
      }
    }
    return content;
  }
  function driverProgressText(row) {
    if (row.state === "downloading" && row.total) return new Intl.NumberFormat(locale, { style: "percent" }).format(Math.min(1, row.received / row.total));
    return driverStateText(row);
  }
  function updateDriverRows() {
    for (const row of state.driverRun?.rows ?? []) {
      const meter = document.getElementById("driver-meter-" + row.id);
      if (meter) { meter.max = row.total || 1; meter.value = row.received || 0; }
      const text = document.getElementById("driver-state-" + row.id);
      if (text) text.textContent = driverProgressText(row);
    }
  }

  function prepareStage() {
    const docker = state.target.id === "docker";
    const heading = title(3, docker ? "dockerTitle" : "mediaTitle", docker ? "dockerIntro" : "mediaIntro");
    const reveal = preview ? null : button("reveal", revealDownload, { glyph: "folder", disabled: Boolean(state.busy) });
    const launch = button("launchContinue", () => enterLaunch(3), { style: "primary", glyph: "arrow", trailing: true, disabled: Boolean(state.busy) });
    if (docker) {
      return [heading, layout([state.busy ? progressPanel() : dockerPanel(), errorBox()]),
        actions(back(2), reveal, launch)];
    }
    if (state.flashDone) {
      const notice = state.driverNotice ? el("div", { role: "status" }, callout(state.driverNotice, "gold")) : null;
      return [heading, layout([mediaPanel(), notice, errorBox()].filter(Boolean)), actions(null, reveal, launch)];
    }
    if (state.driverRun) {
      // Drivers download before anything is written: the card is not erased yet.
      const failed = state.driverRun.status === "failed";
      const missing = state.driverRun.rows.filter(row => row.state !== "ready").length;
      const retry = failed ? button("driversRetry", retryDrivers) : null;
      const without = failed ? button(missing > 1 ? "driversWriteWithoutMany" : "driversWriteWithout", writeWithoutFailed, { style: "primary", glyph: "arrow", trailing: true }) : null;
      return [heading, layout([driverDownloadPanel()]), actions(back(2), retry, without)];
    }
    const review = button("reviewErase", confirmErase, { style: "primary", glyph: "arrow", trailing: true,
      disabled: Boolean(state.busy || state.mediaScan || !state.mediaId) });
    review.id = "media-review";
    const idle = !state.busy;
    const notice = state.busy && state.driverNotice ? el("div", { role: "status" }, callout(state.driverNotice, "gold")) : null;
    return [heading, layout([mediaPanel(), notice, idle && wifiCapable() ? wifiPanel() : null, idle && passwordCapable() ? passwordPanel() : null,
      idle && driversCapable() ? driversPanel() : null, errorBox()].filter(Boolean)),
      actions(back(2), review)];
  }

  // Step 4. The search starts by itself and keeps running; nothing on the page waits for it.
  // Delays can be shortened by a browser test (never set in the app).
  const launchTiming = { pause: 4000, refresh: 10000, none: 120000, ...(window.__SETUP_TEST_TIMING__ || {}) };

  // direct: the Launch step of the indicator, for a bridge that is already written.
  function enterLaunch(from, direct = false) {
    if (state.busy || state.stage !== from || (!nativeReady && !preview)) return;
    if (!direct && from === 3 && (!state.downloaded || (state.target?.id !== "docker" && !state.flashDone))) return;
    if (from === 0) state.target = null;
    state.launch = { from, bridges: [], selectedId: null, searching: true, scanning: false, joined: false,
      emptySince: Date.now(), roundTimer: null, refreshTimer: null, hotspotOpen: false, hotspotConsent: false,
      autoPicked: false, unlocked: false, details: null, stale: false, pending: null, form: null, outcome: null };
    navigate(4);
    searchRound(state.launch);
  }

  // Leaving the page stops the search and the refresh, and signs out of the bridge.
  function leaveLaunch() {
    const launch = state.launch;
    if (!launch) return;
    clearTimeout(launch.roundTimer); clearTimeout(launch.refreshTimer);
    launch.searching = false;
    // Always: a sign-in or a password change still running must not leave a session behind.
    invoke("forget_bridge_session").catch(() => {});
  }

  function selectedBridge() {
    const launch = state.launch;
    return launch?.bridges.find(bridge => bridge.id === launch.selectedId) ?? null;
  }

  function startupPanel() {
    const target = state.target;
    const instructions = {
      raspberry: ["launchPiBoot", target?.id === "pi-zero-w" ? "launchPiZeroPower" : "launchPiPower"],
      usb: ["launchUsbBoot", "launchUsbPower"],
      host: ["launchDockerBoot", "launchDockerNetwork"],
      existing: ["launchExistingNetwork"],
    };
    const content = panel("launchStartup", target?.icon ?? "info");
    content.classList.add("launch-startup");
    if (target) content.append(el("p", { class: "launch-target" }, displayTarget(target)));
    content.append(el("ol", { class: "command-list" }, ...instructions[target?.group ?? "existing"].map(key => el("li", {}, t(key)))));
    return content;
  }

  function domId(id) { return "bridge-" + id.replace(/[^A-Za-z0-9_-]/g, "_"); }

  function bridgeChoice(bridge) {
    const selected = state.launch.selectedId === bridge.id;
    const id = domId(bridge.id);
    const input = el("input", { type: "radio", name: "bridge", id, checked: selected,
      "aria-labelledby": id + "-name " + id + "-details" });
    input.addEventListener("change", () => selectBridge(bridge.id, id));
    return el("label", { class: selected ? "media-option selected" : "media-option" }, input, tile("chip"),
      el("span", { class: "choice-body" }, el("strong", { id: id + "-name" }, el("bdi", {}, bridge.name)),
        el("span", { class: "details", id: id + "-details" }, el("bdi", {}, bridge.version), " · ", t(launchPaths[bridge.path]),
          bridge.sshHost ? " · " : null, bridge.sshHost ? el("bdi", {}, bridge.sshHost) : null,
          bridge.manual ? el("span", { class: "pill inline-pill" }, t("manualBadge")) : null)),
      el("span", { class: "radio-mark", "aria-hidden": "true" }));
  }

  function noneYet() {
    const launch = state.launch;
    return !launch.bridges.length && launch.searching && launch.emptySince != null && Date.now() - launch.emptySince >= launchTiming.none;
  }

  function searchPanel() {
    const launch = state.launch;
    const { bridges, searching, selectedId } = launch;
    const none = noneYet();
    const [heading, detail] = selectedId ? ["foundTitle", null] : bridges.length ? ["chooseTitle", "chooseDetail"]
      : none ? ["noneTitle", "noneDetail"] : ["searchTitle", "searchDetail"];
    const content = panel(heading, "refresh", detail);
    content.id = "launch-search";
    if (none) content.append(el("div", { class: "launch-notice" }, callout("noneCallout", "gold")),
      el("ol", { class: "command-list" }, ...["noneCheck1", "noneCheck2", "noneCheck3"].map(key => el("li", {}, t(key)))));
    const toggle = searching
      ? button("stopSearch", stopSearch, { style: "ghost" })
      : button("resumeSearch", resumeSearch, { glyph: "refresh" });
    toggle.id = "search-toggle";
    content.append(el("div", { class: "search-line" },
      el("span", { class: "search-state", role: "status" }, searching ? el("span", { class: "spinner", "aria-hidden": "true" }) : null,
        el("span", { class: searching ? "phase-text" : "search-stopped" }, t(searching ? "searchingBridges" : "searchStopped"))),
      toggle));
    if (bridges.length) {
      content.append(el("fieldset", { class: "media-options bridge-options" },
        el("legend", { class: selectedId ? "sr-only" : "group-label" }, t("chooseBridge")), ...bridges.map(bridgeChoice)));
    }
    if (selectedId) return content;
    const link = el("button", { type: "button", class: "link-button ip-link", id: "ip-link" }, t("enterIp"));
    link.addEventListener("click", openIpDialog);
    content.append(el("div", {}, link));
    const consent = el("input", { type: "checkbox", id: "hotspot-consent", checked: launch.hotspotConsent });
    const search = button("discoverHotspot", searchHotspot, { glyph: "refresh", disabled: !launch.hotspotConsent });
    consent.addEventListener("change", () => { launch.hotspotConsent = consent.checked; search.disabled = !consent.checked; });
    const details = el("details", { class: "launch-hotspot", open: none || launch.hotspotOpen },
      el("summary", {}, t("hotspotFallback")), el("p", {}, t("hotspotJoin")),
      el("label", { class: "confirm-check" }, consent, el("span", {}, t("hotspotConsent"))), search);
    details.addEventListener("toggle", () => { launch.hotspotOpen = details.open; });
    content.append(details);
    return content;
  }

  function chooseHint() {
    const { bridges, selectedId } = state.launch;
    return bridges.length && !selectedId ? el("div", { id: "choose-hint" }, callout("chooseBridgeHint", "gold")) : null;
  }

  function bridgeHead(bridge) {
    return el("p", { class: "launch-target", id: "bridge-head" },
      el("bdi", {}, t("bridgeNameVersion", { name: bridge.name, version: bridge.version })),
      bridge.hotspotActive === true ? el("span", { class: "pill inline-pill" }, t("hotspotOn")) : null);
  }

  function row(key, value) {
    return el("div", {}, el("dt", {}, t(key)), el("dd", {}, value));
  }

  function addressesSection(bridge) {
    const rows = [];
    for (const [path, key] of [["home", "addressHome"], ["hotspot", "addressHotspot"]]) {
      const address = bridge.addresses.find(item => item.path === path && !item.tls);
      if (address) rows.push(row(key, el("bdi", {}, address.host)));
    }
    if (!rows.length && bridge.sshHost) {
      rows.push(row({ home: "addressHome", hotspot: "addressHotspot", other: "bridgeOtherPath" }[bridge.path], el("bdi", {}, bridge.sshHost)));
    }
    const open = button("openAdmin", () => openBridge(bridge.id), { glyph: "arrow", trailing: true });
    open.id = "open-admin";
    return el("div", { class: "sub", id: "bridge-addresses" }, el("h3", { class: "group-label" }, t("addresses")),
      rows.length ? el("dl", { class: "release-meta" }, ...rows) : null,
      el("div", { class: "launch-buttons" }, open),
      el("span", { class: "url", dir: "ltr" }, bridge.adminUrl));
  }

  function hwRow(glyph, name, detail, labels, end) {
    return el("li", { class: "media-option hw-row" }, tile(glyph),
      el("span", { class: "choice-body" }, el("strong", {}, el("bdi", {}, name)),
        detail ? el("span", { class: "details" }, detail) : null,
        el("span", { class: "pills" }, ...labels.map(key => pill(key)))),
      end);
  }

  const kindLabels = { mount: "kindMount", telescope: "kindTelescope", smart_telescope: "kindTelescope", other: "kindOther", camera: "kindCamera" };
  const linkLabels = { wifi: "linkWifi", serial_usb: "linkSerial", usb: "linkUsb" };
  const cameraLabels = { ready: "driverReady", driver_missing: "driverMissing", preparing: "driverPreparing", detected: "cameraDetected", unsupported: "cameraUnsupported" };
  const stateLabels = { online: "deviceOnline", offline: "deviceOffline", unknown: "deviceUnknown" };

  function interfacesText(value) {
    const part = (count, key) => count === 1 ? t(key) : count + " × " + t(key);
    const parts = [value.builtin ? part(value.builtin, "ifaceBuiltin") : null, value.usb ? part(value.usb, "ifaceUsb") : null].filter(Boolean);
    return parts.length ? value.total + " (" + parts.join(" + ") + ")" : String(value.total);
  }

  function detailsView(details) {
    const launch = state.launch;
    const modes = { private: "modePrivate", hotspot_isolated: "modeHotspotIsolated", hotspot_shared: "modeHotspotShared" };
    const join = (...parts) => parts.filter(Boolean).join(" · ");
    const config = el("dl", { class: "release-meta" },
      details.interfaces ? row("wifiInterfaces", interfacesText(details.interfaces)) : null,
      details.connectionMode ? row("configType", t(modes[details.connectionMode])) : null,
      row("hotspotNetwork", join(details.hotspotSsid || t("networkUnset"), details.hotspotSubnet, t(details.hotspotActive ? "stateActive" : "stateInactive"))),
      row("privateNetwork", details.homeSsid ? join(details.homeSsid, t(details.homeConnected ? "stateConnected" : "stateDisconnected")) : t("networkUnset")));
    const mounts = details.found.filter(item => item.canAdd).length;
    const found = details.found.length ? el("ul", { class: "hw-list" }, ...details.found.map(item => {
      const add = item.canAdd ? button("add", () => addDevice(item.id), { disabled: Boolean(launch.pending) }) : null;
      return hwRow("chip", item.name, item.signal != null ? t("signal", { value: item.signal }) : null,
        [kindLabels[item.kind], linkLabels[item.link]], add);
    })) : el("p", { class: "field-hint" }, t("noHardwareFound"));
    const cameras = details.cameras.length ? el("ul", { class: "hw-list" }, ...details.cameras.map(camera =>
      hwRow("usb", camera.name, null, ["kindCamera", "linkUsb"], pill(cameraLabels[camera.state])))) : el("p", { class: "field-hint" }, t("noCameras"));
    const added = details.added.length ? el("ul", { class: "hw-list" }, ...details.added.map(device =>
      hwRow("chip", device.name, device.port != null ? t("devicePort", { port: device.port }) : null,
        [kindLabels[device.kind], linkLabels[device.linkType]], pill(stateLabels[device.state], device.state === "online" ? "green" : "")))) : el("p", { class: "field-hint" }, t("noHardwareAdded"));
    return el("div", { id: "bridge-details" },
      launch.stale ? el("div", { class: "launch-notice" }, callout("detailsStale", "gold")) : null,
      el("div", { class: "sub" }, el("h3", { class: "group-label" }, t("configuration")), config),
      el("div", { class: "sub" }, el("div", { class: "sub-row" }, el("h3", { class: "group-label" }, t("hardwareFound")),
        mounts ? el("span", { class: "pill" }, mounts === 1 ? t("mountToAddOne") : t("mountsToAddMany", { count: mounts })) : null),
        found, el("h3", { class: "group-label cameras-label" }, t("camerasDetected")), cameras),
      el("div", { class: "sub" }, el("h3", { class: "group-label" }, t("hardwareAdded")), added));
  }

  function unlockForm(bridge) {
    const pending = state.launch.pending === "unlock";
    const form = el("form", { class: "sub", id: "bridge-unlock", autocomplete: "off" });
    const field = el("input", { id: "bridge-current-password", type: "password", autocomplete: "off", required: true, disabled: pending });
    form.append(el("h3", {}, t("unlockTitle")), el("p", { class: "field-hint" }, t("unlockDetail")),
      el("label", { class: "launch-field", for: "bridge-current-password" }, el("span", {}, t("currentAdminPassword")), field),
      el("div", { class: "launch-buttons" }, button(pending ? "unlocking" : "unlock", null, { glyph: "arrow", trailing: true, type: "submit", disabled: pending })));
    form.addEventListener("submit", event => {
      event.preventDefault();
      const password = field.value;
      field.value = "";
      if (password) unlockBridge(bridge.id, password);
    });
    return form;
  }

  function credentialForm() {
    const form = el("form", { id: "launch-credentials", class: "launch-credentials", autocomplete: "off" });
    const current = el("input", { id: "bridge-change-current", type: "password", autocomplete: "off", required: true });
    const field = (key, input) => el("label", { class: "launch-field" }, el("span", {}, t(key)), input);
    const next = el("input", { type: "password", autocomplete: "off", required: true });
    const repeat = el("input", { type: "password", autocomplete: "off", required: true });
    const feedback = el("p", { class: "launch-form-error", role: "alert", hidden: true });
    form.append(el("h3", {}, t("changePassword")), field("currentAdminPassword", current),
      field("newPassword", next), field("confirmNewPassword", repeat), el("p", {}, t("bridgePasswordRule")), feedback);
    form.addEventListener("input", () => { feedback.hidden = true; });
    form.append(el("div", { class: "launch-buttons" },
      button("savePassword", null, { style: "primary", type: "submit" }),
      button("cancel", () => setLaunchForm(null), { style: "ghost" })));
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (!validNewPassword(next.value) || next.value !== repeat.value) {
        const matches = next.value === repeat.value;
        feedback.textContent = t(matches ? (next.value === "12345678" ? "bridgePasswordDefault" : "bridgePasswordRule") : "passwordMismatch");
        feedback.hidden = false;
        (matches ? next : repeat).focus();
        return;
      }
      const args = { currentPassword: current.value, newPassword: next.value, target: "all" };
      clearPasswordFields();
      changeBridgePassword(args);
    });
    return form;
  }

  function clearPasswordFields() {
    for (const field of main.querySelectorAll('#launch-credentials input[type="password"], #bridge-unlock input[type="password"]')) field.value = "";
  }

  // One bridge password (web, hotspot and SSH): the bridge rule.
  function validNewPassword(password) {
    return /^[\x21-\x7e]{8,63}$/.test(password) && password !== "12345678";
  }

  function setLaunchForm(mode) {
    if (state.launch.pending) return;
    state.launch.form = mode;
    state.error = null;
    render(mode ? "bridge-change-current" : "change-password");
  }

  function bridgePanel(bridge) {
    const launch = state.launch;
    const content = panel("yourBridge", "shield");
    content.id = "bridge-panel";
    const heading = content.querySelector("h2");
    heading.id = "bridge-manage-title";
    heading.tabIndex = -1;
    heading.parentElement.append(bridgeHead(bridge));
    content.append(addressesSection(bridge));
    if (bridge.authMode === "none") {
      content.append(el("div", { class: "launch-notice" }, callout("bridgeAuthUnavailable", "gold")));
      return content;
    }
    if (!launch.unlocked) {
      content.append(unlockForm(bridge));
      httpNotice(content, bridge);
      return content;
    }
    const forget = el("button", { type: "button", class: "link-button", id: "forget-password" }, t("forgetPassword"));
    forget.addEventListener("click", forgetPassword);
    content.append(detailsView(launch.details),
      el("div", { class: "sub" }, el("div", { class: "sub-row" }, el("p", { class: "field-hint" }, t("detailsLine")), forget)));
    if (launch.form) {
      content.append(credentialForm());
    } else {
      const change = button("changePassword", () => setLaunchForm("all"), { disabled: Boolean(launch.pending) });
      change.id = "change-password";
      content.append(el("div", { class: "sub" }, el("div", { class: "launch-buttons" }, change,
        el("span", { class: "field-hint" }, t("changePasswordHint")))));
    }
    httpNotice(content, bridge);
    return content;
  }

  // The password travels over unencrypted local HTTP on the hotspot and on the home network.
  function httpNotice(content, bridge) {
    const key = { hotspot: "hotspotHttpDisclosure", http: "lanHttpDisclosure" }[bridge.authMode];
    if (key) content.append(el("div", { class: "launch-notice" }, callout(key, "gold")));
  }

  // The SSH account the bridge reports (status.ssh.account), else the account of the image written.
  function sshAccount() {
    const read = state.launch?.details?.sshAccount;
    if (read) return read;
    return { "pi-5": "root", "pi-zero-w": "bridge" }[state.target?.id] ?? null;
  }

  function sshPanel(bridge) {
    const account = sshAccount();
    if (account || (state.target?.group !== "host" && state.target?.group !== "usb")) {
      // One bridge password: SSH opens once it is no longer 12345678.
      const content = panel("sshTitle", "info", "sshDetailBridge");
      content.classList.add("launch-ssh");
      content.id = "launch-ssh";
      const host = bridge?.sshHost;
      if (host) content.append(el("p", {}, t("sshHost"), " · ", el("bdi", {}, host)));
      content.append(el("p", {}, account ? t("sshAccountLine", { account }) : t("sshAccountRead")));
      if (host && account) content.append(el("pre", { dir: "ltr" }, el("code", {}, "ssh " + account + "@" + host)));
      return content;
    }
    const content = panel("sshTitle", "info", "sshDetail");
    content.classList.add("launch-ssh");
    content.id = "launch-ssh";
    const docker = state.target?.group === "host";
    const host = docker ? "HOST_IP" : bridge?.sshHost;
    if (host && !docker) content.append(el("p", {}, t("sshHost"), " · ", el("bdi", {}, host)));
    const guide = { raspberry: "sshPi", usb: "sshUsb", host: "sshDocker" };
    content.append(el("p", {}, t(guide[state.target?.group] ?? "sshExisting")), el("p", {}, t("sshAccount")));
    content.append(el("p", {}, t("sshFingerprint")),
      el("pre", { dir: "ltr" }, el("code", {}, "ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub")));
    if (host) content.append(el("p", {}, t(docker ? "sshDockerCommand" : "sshCommand")),
      el("pre", { dir: "ltr" }, el("code", {}, "ssh USER@" + host)));
    return content;
  }

  function launchStage() {
    const { from, outcome } = state.launch;
    const selected = selectedBridge();
    const outcomeBox = outcome ? el("div", { role: "status" }, callout(outcome, pendingOutcomes.has(outcome) ? "gold" : "green")) : null;
    return [title(4, "launchTitle", "launchIntro"),
      el("div", { class: "wizard-layout launch-layout" }, startupPanel(),
        el("div", { class: "wizard-content launch-discovery" }, preview ? callout("previewLaunch", "gold") : null,
          searchPanel(), chooseHint(), errorBox(), outcomeBox, selected ? bridgePanel(selected) : null),
        sshPanel(selected)),
      actions(back(from), button("startOver", restart))];
  }

  // Replace parts of the page in place: typed passwords and open forms stay as they are.
  function replacePart(id, node) {
    const old = document.getElementById(id);
    if (!old || !node) return;
    const focus = old.contains(document.activeElement) ? document.activeElement.id : null;
    old.replaceWith(node);
    if (focus) document.getElementById(focus)?.focus({ preventScroll: true });
  }

  function updateLaunchList() {
    if (state.stage !== 4 || !state.launch) return;
    replacePart("launch-search", searchPanel());
    const selected = selectedBridge();
    const hint = document.getElementById("choose-hint"), next = chooseHint();
    if (hint && !next) hint.remove();
    else if (!hint && next) document.getElementById("launch-search")?.after(next);
    else replacePart("choose-hint", next);
    if (selected) {
      replacePart("bridge-head", bridgeHead(selected));
      replacePart("bridge-addresses", addressesSection(selected));
    }
    replacePart("launch-ssh", sshPanel(selected));
  }

  function updateLaunchDetails() {
    if (state.stage !== 4 || !state.launch?.details) return;
    replacePart("bridge-details", detailsView(state.launch.details));
    replacePart("launch-ssh", sshPanel(selectedBridge()));
  }

  function validBridge(bridge, joinedHotspot) {
    return typeof bridge?.id === "string" && bridge.id && typeof bridge.name === "string" && bridge.name
      && typeof bridge.version === "string" && bridge.version && Object.hasOwn(launchPaths, bridge.path)
      && ["none", "https", "hotspot", "http"].includes(bridge.authMode) && (bridge.authMode !== "hotspot" || joinedHotspot)
      && (bridge.sshHost == null || typeof bridge.sshHost === "string")
      && (bridge.hotspotActive == null || typeof bridge.hotspotActive === "boolean")
      && Array.isArray(bridge.addresses) && bridge.addresses.every(item => ["home", "hotspot"].includes(item?.path)
        && typeof item.host === "string" && typeof item.url === "string" && typeof item.tls === "boolean")
      && typeof bridge.adminUrl === "string" && typeof bridge.manual === "boolean";
  }

  async function searchRound(launch) {
    if (state.launch !== launch || !launch.searching || launch.scanning) return;
    launch.scanning = true;
    clearTimeout(launch.roundTimer);
    try {
      const joined = launch.joined;
      const bridges = await invoke("discover_bridges", { joinedHotspot: joined });
      if (state.launch !== launch) return;
      if (!Array.isArray(bridges) || !bridges.every(bridge => validBridge(bridge, joined))
        || new Set(bridges.map(bridge => bridge.id)).size !== bridges.length) throw new Error(t("invalidResponse"));
      applyBridges(launch, bridges);
    } catch { /* The next round tries again. */ } finally {
      launch.scanning = false;
      if (state.launch === launch && launch.searching) launch.roundTimer = setTimeout(() => searchRound(launch), launchTiming.pause);
    }
  }

  function applyBridges(launch, bridges) {
    launch.bridges = bridges;
    if (bridges.length) launch.emptySince = null;
    else if (launch.emptySince == null) launch.emptySince = Date.now();
    if (launch.selectedId && !bridges.some(bridge => bridge.id === launch.selectedId)) {
      // The chosen bridge left the network: its session goes with it.
      resetBridge(launch);
      launch.selectedId = null;
      return render(main.contains(document.activeElement) ? "launch-search" : null);
    }
    if (!launch.selectedId && !launch.autoPicked && bridges.length === 1) {
      // A single bridge: choose it for the user, once.
      launch.autoPicked = true;
      launch.selectedId = bridges[0].id;
      announcer.textContent = t("chooseBridge");
      return render(null);
    }
    updateLaunchList();
  }

  function stopSearch() {
    const launch = state.launch;
    if (!launch?.searching) return;
    launch.searching = false;
    clearTimeout(launch.roundTimer);
    updateLaunchList();
    document.getElementById("search-toggle")?.focus({ preventScroll: true });
    announcer.textContent = t("searchStopped");
  }

  function resumeSearch() {
    const launch = state.launch;
    if (!launch || launch.searching) return;
    launch.searching = true;
    if (!launch.bridges.length) launch.emptySince = Date.now();
    updateLaunchList();
    document.getElementById("search-toggle")?.focus({ preventScroll: true });
    announcer.textContent = t("searchingBridges");
    searchRound(launch);
  }

  function searchHotspot() {
    const launch = state.launch;
    if (!launch?.hotspotConsent) return;
    launch.joined = true;
    if (!launch.searching) return resumeSearch();
    searchRound(launch);
  }

  function resetBridge(launch) {
    clearTimeout(launch.refreshTimer);
    if (launch.unlocked || launch.pending) invoke("forget_bridge_session").catch(() => {});
    Object.assign(launch, { unlocked: false, details: null, stale: false, pending: null, form: null, outcome: null });
    state.error = null;
  }

  function selectBridge(id, focusId) {
    const launch = state.launch;
    if (!launch || launch.selectedId === id) return;
    resetBridge(launch);
    launch.selectedId = id;
    launch.autoPicked = true;
    render(focusId);
  }

  function openIpDialog() {
    const launch = state.launch;
    if (!launch) return;
    const field = el("input", { id: "manual-ip", type: "text", inputmode: "decimal", autocomplete: "off", required: true });
    const error = el("p", { class: "launch-form-error", id: "manual-ip-error", role: "alert", hidden: true }, t("manualIpError"));
    const cancel = button("cancel", () => ipDialog.close(), { style: "ghost" });
    const add = button("add", null, { style: "primary", type: "submit" });
    const form = el("form", { id: "manual-ip-form", autocomplete: "off" },
      el("label", { class: "launch-field", for: "manual-ip" }, el("span", {}, t("ipDialogField")), field), error, actions(null, cancel, add));
    field.addEventListener("input", () => { error.hidden = true; });
    form.addEventListener("submit", async event => {
      event.preventDefault();
      const address = field.value.trim();
      if (!address || add.disabled) return;
      add.disabled = true; error.hidden = true;
      try {
        const bridge = await invoke("add_bridge_ip", { address });
        if (!validBridge(bridge, true) || bridge.manual !== true) throw new Error(t("invalidResponse"));
        if (state.launch !== launch) return;
        launch.bridges = [...launch.bridges.filter(item => item.id !== bridge.id), bridge];
        launch.emptySince = null;
        ipDialog.close();
        if (!launch.selectedId) { launch.selectedId = bridge.id; launch.autoPicked = true; render(domId(bridge.id)); }
        else updateLaunchList();
        announcer.textContent = t("manualBadge");
      } catch {
        error.hidden = false;
        field.focus();
      } finally {
        add.disabled = false;
      }
    });
    ipDialog.replaceChildren(tile("chip"), el("h2", { id: "ip-heading" }, t("enterIp")),
      el("p", { id: "ip-description" }, t("ipDialogDetail")), form);
    ipDialog.showModal();
    field.focus();
  }

  function validDetails(value) {
    const text = item => typeof item === "string";
    return value && text(value.homeSsid) && typeof value.homeConnected === "boolean" && text(value.hotspotSsid)
      && typeof value.hotspotActive === "boolean"
      && (value.sshAccount == null || (text(value.sshAccount) && /^[a-z][a-z0-9_-]{0,31}$/.test(value.sshAccount)))
      && (value.interfaces == null || [value.interfaces.total, value.interfaces.builtin, value.interfaces.usb].every(Number.isInteger))
      && (value.connectionMode == null || ["private", "hotspot_isolated", "hotspot_shared"].includes(value.connectionMode))
      && (value.hotspotSubnet == null || text(value.hotspotSubnet))
      && Array.isArray(value.found) && value.found.every(item => text(item?.id) && text(item.name)
        && Object.hasOwn(kindLabels, item.kind) && Object.hasOwn(linkLabels, item.link) && typeof item.canAdd === "boolean"
        && (item.signal == null || Number.isInteger(item.signal)))
      && Array.isArray(value.cameras) && value.cameras.every(item => text(item?.name) && Object.hasOwn(cameraLabels, item.state))
      && Array.isArray(value.added) && value.added.every(item => text(item?.name) && Object.hasOwn(kindLabels, item.kind)
        && Object.hasOwn(linkLabels, item.linkType) && Object.hasOwn(stateLabels, item.state) && (item.port == null || Number.isInteger(item.port)));
  }

  function failure(error) { return typeof error === "string" ? error : error?.message ?? ""; }

  function scheduleRefresh(launch) {
    clearTimeout(launch.refreshTimer);
    launch.refreshTimer = setTimeout(() => refreshDetails(launch), launchTiming.refresh);
  }

  async function unlockBridge(id, password) {
    const launch = state.launch;
    if (!launch || launch.pending || launch.selectedId !== id) return;
    launch.pending = "unlock"; launch.outcome = null; state.error = null;
    render("bridge-current-password");
    try {
      const details = await invoke("unlock_bridge", { id, password });
      if (!validDetails(details)) throw new Error(t("invalidResponse"));
      if (state.launch !== launch || launch.selectedId !== id) return;
      Object.assign(launch, { unlocked: true, details, stale: false });
      scheduleRefresh(launch);
      announcer.textContent = t("configuration");
    } catch {
      if (state.launch === launch) state.error = { key: "unlockError" };
    } finally {
      if (state.launch === launch) {
        launch.pending = null;
        render(launch.unlocked ? "bridge-manage-title" : "bridge-current-password");
      }
    }
  }

  async function refreshDetails(launch) {
    const id = launch.selectedId;
    if (state.launch !== launch || !launch.unlocked || !id) return;
    if (launch.pending) return scheduleRefresh(launch);
    try {
      const details = await invoke("refresh_bridge", { id });
      if (!validDetails(details)) throw new Error(t("invalidResponse"));
      if (state.launch !== launch || launch.selectedId !== id || !launch.unlocked) return;
      Object.assign(launch, { details, stale: false });
      updateLaunchDetails();
    } catch (error) {
      if (state.launch !== launch || launch.selectedId !== id || !launch.unlocked) return;
      if (failure(error) === "SESSION_EXPIRED") {
        Object.assign(launch, { unlocked: false, details: null, stale: false, form: null });
        state.error = { key: "sessionExpired" };
        return render(main.contains(document.activeElement) ? "bridge-current-password" : null);
      }
      launch.stale = true;
      updateLaunchDetails();
    }
    if (state.launch === launch && launch.unlocked) scheduleRefresh(launch);
  }

  function forgetPassword() {
    const launch = state.launch;
    if (!launch?.unlocked || launch.pending) return;
    resetBridge(launch);
    render("bridge-current-password");
    announcer.textContent = t("forgetPassword");
  }

  // What add_bridge_device returns, and the text that says it.
  const addOutcomes = { added: "deviceAdded", connecting: "deviceAddedConnecting",
    no_free_adapter: "deviceAddedNoAdapter", not_connected: "deviceAddedNotConnected" };
  // Outcomes that leave something to do: said in gold, not in green.
  const pendingOutcomes = new Set(["deviceAddedNoAdapter", "deviceAddedNotConnected"]);

  async function addDevice(device) {
    const launch = state.launch, id = launch?.selectedId;
    if (!launch?.unlocked || launch.pending || !id) return;
    launch.pending = "add"; launch.outcome = null; state.error = null;
    updateLaunchDetails();
    try {
      const result = await invoke("add_bridge_device", { id, device });
      if (state.launch !== launch) return;
      // A Wi-Fi mount is only said connecting when the bridge accepted to connect it.
      launch.outcome = addOutcomes[result] || "deviceAddedNotConnected";
      const details = await invoke("refresh_bridge", { id });
      if (validDetails(details) && state.launch === launch && launch.selectedId === id) Object.assign(launch, { details, stale: false });
    } catch (error) {
      if (state.launch !== launch) return;
      const reason = failure(error);
      if (reason === "SESSION_EXPIRED") Object.assign(launch, { unlocked: false, details: null, form: null });
      if (!launch.outcome) state.error = { key: reason === "SESSION_EXPIRED" ? "sessionExpired" : reason === "USE_ADMIN_INTERFACE" ? "addDeviceUseAdmin" : "addDeviceError" };
    } finally {
      if (state.launch === launch) {
        launch.pending = null;
        render(null);
        if (launch.outcome) announcer.textContent = t(launch.outcome);
      }
    }
  }

  async function openBridge(id) {
    const launch = state.launch;
    try { await invoke("open_bridge_ui", { id }); } catch {
      if (state.launch === launch) { state.error = { key: "openBridgeError" }; render(null); }
    }
  }

  async function changeBridgePassword(args) {
    const launch = state.launch, bridge = selectedBridge();
    if (!launch || !bridge || bridge.authMode === "none" || launch.pending || launch.form !== "all") return;
    launch.pending = "change"; launch.form = null; launch.outcome = null; state.error = null;
    render(null);
    try {
      const result = await invoke("change_bridge_password", { id: bridge.id, ...args });
      if (typeof result?.sessionsInvalidated !== "boolean" || typeof result.hotspotReconnect !== "boolean"
        || typeof result.sessionKept !== "boolean") throw new Error(t("invalidResponse"));
      if (state.launch !== launch) return;
      launch.outcome = result.hotspotReconnect ? "passwordReconnect" : result.sessionsInvalidated ? "passwordSessionsEnded" : "passwordChanged";
      if (!result.sessionKept) { clearTimeout(launch.refreshTimer); Object.assign(launch, { unlocked: false, details: null }); }
      // The hotspot restarts with the new password: the search finds the bridge again.
      if (result.hotspotReconnect) launch.selectedId = null;
    } catch {
      if (state.launch === launch) state.error = { key: "changePasswordError" };
    } finally {
      if (state.launch === launch) {
        launch.pending = null;
        render(null);
        if (launch.outcome) announcer.textContent = t(launch.outcome);
      }
    }
  }

  function render(focusId) {
    clearPasswordFields();
    document.documentElement.lang = locale;
    language.value = locale;
    language.disabled = Boolean(state.busy);
    document.getElementById("language-label").textContent = t("language");
    document.getElementById("brand-label").textContent = t("brandLabel");
    const badge = document.getElementById("mode-badge");
    badge.textContent = t(preview ? "preview" : "desktop");
    badge.classList.toggle("gold", preview);
    const previewNote = document.getElementById("preview-note");
    previewNote.hidden = !preview;
    previewNote.textContent = t("previewDetail");
    const stages = [home, deviceStage, releaseStage, prepareStage, launchStage];
    main.replaceChildren(...[state.stage ? stepper() : null, ...stages[state.stage]()].filter(Boolean));
    if (state.busy) progressView();
    if (focusId) document.getElementById(focusId)?.focus({ preventScroll: true });
    measureTop();
  }

  // The fixed header and step indicator give their height to the CSS, so the
  // indicator sits right under the header and focused fields never hide behind them.
  function measureTop() {
    const root = document.documentElement.style;
    root.setProperty("--header-height", `${document.querySelector(".app-header")?.offsetHeight || 0}px`);
    root.setProperty("--stepper-height", `${main.querySelector(":scope > .stepper")?.offsetHeight || 0}px`);
  }
  if (typeof ResizeObserver === "function") new ResizeObserver(measureTop).observe(document.querySelector(".app-header"));
  window.addEventListener("resize", measureTop);

  function navigate(stage) {
    if (state.busy) return;
    if (state.stage === 4 && stage !== 4) leaveLaunch();
    if (ipDialog.open) ipDialog.close();
    state.wifi.password = ""; state.wifiError = null;
    clearBridgePassword();
    pendingWrite = null;
    state.driverRun = null; state.driverNotice = null;
    if (stage <= 2) state.drivers = { open: true, panel: null, loading: false, error: false, selected: [] };
    if (stage <= 1) {
      state.release = null;
      state.downloaded = null;
      state.checked = false;
      state.flashDone = false;
    }
    state.media = [];
    state.mediaId = null;
    state.mediaScan = null;
    state.mediaError = null;
    state.error = null;
    if (stage !== 4) state.launch = null;
    state.stage = stage;
    announcer.textContent = "";
    render("stage-title");
    window.scrollTo(0, 0);
  }

  function restart() {
    state.target = null;
    navigate(1);
  }

  async function run(phase, errorKey, operation) {
    if (state.busy) return;
    const active = { phase };
    state.busy = active;
    state.error = null;
    render("stage-title");
    try {
      await operation(active);
    } catch (error) {
      state.error = { key: errorKey, phase: active.phase, detail: state.stage === 4 ? null : typeof error === "string" ? error : error?.message };
    } finally {
      state.busy = null;
      announcer.textContent = "";
      const focus = main.contains(document.activeElement) || document.activeElement === document.body;
      render(focus ? "stage-title" : null);
      if (!state.error && active.success) announcer.textContent = t(active.success);
    }
  }

  function resolveRelease() {
    if (state.stage !== 2) return;
    const target = state.target.id;
    return run("resolving", "releaseError", async active => {
      const release = await invoke("resolve_release", { target });
      if (release !== null && (release?.target !== target || typeof release.tag !== "string"
        || typeof release.asset !== "string" || !release.asset || !Number.isSafeInteger(release.size)
        || release.size <= 0 || !/^[a-f0-9]{64}$/i.test(release.sha256))) throw new Error(t("invalidResponse"));
      state.release = release;
      state.checked = true;
      active.success = release ? (release.source === "local-test" ? "localFound" : target === "docker" ? "installerFound" : "imageFound") : "noRelease";
    });
  }

  function downloadRelease() {
    if (state.stage !== 2 || !state.release || state.downloaded) return;
    const target = state.target.id;
    return run("downloading", "downloadError", async active => {
      const download = await invoke("download_release", { target }, event => {
        if (active !== state.busy || !["downloading", "verifying"].includes(event?.phase)
          || (active.phase === "verifying" && event.phase === "downloading")) return;
        Object.assign(active, { phase: event.phase, received: event.received, total: event.total });
        progressView();
      });
      if (download?.name !== state.release.asset) throw new Error(t("invalidResponse"));
      state.downloaded = download;
      active.success = preview ? "previewVerified" : state.release.source === "local-test" ? "localVerified" : "downloadVerified";
    });
  }

  function revealDownload() {
    if (!state.downloaded) return;
    return run("revealing", "revealError", () => invoke("reveal_download"));
  }

  async function listMedia() {
    if (state.stage !== 3 || state.target.id === "docker" || !state.downloaded || state.flashDone || state.busy || state.mediaScan) return;
    const scan = {}; state.mediaScan = scan; state.mediaError = null;
    state.media = []; state.mediaId = null;
    updateMediaPanel();
    try {
      const devices = await invoke("list_media");
      if (!Array.isArray(devices) || devices.some(device => typeof device?.id !== "string" || !device.id
        || typeof device.name !== "string" || !device.name || !Number.isSafeInteger(device.size) || device.size <= 0)
        || new Set(devices.map(device => device.id)).size !== devices.length) throw new Error(t("invalidResponse"));
      if (state.mediaScan === scan) state.media = devices;
    } catch (error) {
      if (state.mediaScan === scan) state.mediaError = { detail: typeof error === "string" ? error : error?.message };
    } finally {
      if (state.mediaScan === scan) { state.mediaScan = null; updateMediaPanel(); }
    }
  }

  async function loadDrivers() {
    if (state.stage !== 3 || !driversCapable() || state.drivers.panel || state.drivers.loading) return;
    const drivers = state.drivers;
    drivers.loading = true; drivers.error = false;
    try {
      const panel = await invoke("driver_offers");
      if (typeof panel?.supported !== "boolean" || !Array.isArray(panel.offers) || panel.offers.some(offer => typeof offer?.id !== "string"
        || typeof offer.name !== "string" || typeof offer.verifiedOnHardware !== "boolean" || offer.source !== "download"
        || (offer.size != null && !Number.isSafeInteger(offer.size)))) throw new Error(t("invalidResponse"));
      if (state.drivers === drivers) drivers.panel = panel;
    } catch {
      if (state.drivers === drivers) drivers.error = true;
    } finally {
      drivers.loading = false;
      if (state.drivers === drivers && state.stage === 3 && !state.busy && !state.driverRun && !state.flashDone) {
        const focus = document.activeElement?.id;
        document.getElementById("drivers-panel")?.replaceWith(driversPanel());
        if (focus) document.getElementById(focus)?.focus({ preventScroll: true });
      }
    }
  }

  function confirmErase() {
    const media = state.media.find(device => device.id === state.mediaId);
    if (state.busy || state.mediaScan || !media || state.stage !== 3 || state.target.id === "docker" || state.flashDone) return;
    if (wifiCapable() && state.wifi.enabled && !validWifi()) { state.wifiError = "wifiInvalid"; render("wifi-ssid"); return; }
    state.wifiError = null;
    const passwordProblem = passwordCapable() ? bridgePasswordProblem() : null;
    if (passwordProblem) { state.passwordError = passwordProblem; render("bridge-password"); return; }
    state.passwordError = null;
    const confirm = button("eraseWrite", () => {
      if (!consent.checked || !dialog.open) return;
      const wifi = wifiCapable() && state.wifi.enabled ? { ssid: state.wifi.ssid, password: state.wifi.password, hidden: state.wifi.hidden, country: state.wifi.country } : null;
      const password = passwordCapable() && state.password.value ? state.password.value : null;
      const drivers = driversCapable() ? state.drivers.selected.filter(id => driverOffers().some(offer => offer.id === id)) : [];
      state.wifi.password = "";
      clearBridgePassword();
      dialog.close();
      startWrite(media.id, { wifi, password, drivers });
    }, { style: "danger", glyph: "warning", disabled: true });
    const consent = el("input", { type: "checkbox" });
    consent.addEventListener("change", () => { confirm.disabled = !consent.checked; });
    const cancel = button("cancel", () => { clearWifiPassword(); dialog.close(); }, { style: "ghost" });
    dialog.replaceChildren(tile("warning", "red"),
      el("h2", { id: "erase-heading" }, t("eraseTitle")),
      el("p", { id: "erase-description" }, t("eraseDescription", { name: media.name, size: size(media.size) })),
      el("div", { class: "callout red" }, icon("media"), el("p", {}, el("bdi", { class: "media-id" }, media.id))),
      el("label", { class: "confirm-check" }, consent, el("span", {}, t("eraseConsent"))),
      actions(null, cancel, confirm));
    dialog.showModal();
    cancel.focus();
  }

  // Ticked drivers that must be downloaded are fetched first; the card is written afterwards.
  function startWrite(id, request) {
    pendingWrite = { id, ...request };
    const downloads = request.drivers;
    if (!downloads.length) return flashMedia(id, request);
    state.driverRun = { status: "running", rows: downloads.map(item => {
      const offer = driverOffers().find(entry => entry.id === item);
      return { id: item, name: offer.name, state: "waiting", received: 0, total: offer.size || 0 };
    }) };
    return fetchDrivers(downloads);
  }

  async function fetchDrivers(ids) {
    const current = state.driverRun;
    if (!current || state.busy) return;
    current.status = "running";
    for (const row of current.rows) if (ids.includes(row.id)) Object.assign(row, { state: "waiting", received: 0 });
    state.busy = { phase: "drivers" };
    render("stage-title");
    announcer.textContent = t("driversDownloadTitle");
    let outcomes;
    try {
      outcomes = await invoke("download_drivers", { ids }, event => {
        const row = current.rows.find(item => item.id === event?.id);
        if (!row || !Number.isSafeInteger(event.received) || !Number.isSafeInteger(event.total)) return;
        Object.assign(row, { state: "downloading", received: event.received, total: event.total });
        updateDriverRows();
      });
      if (!Array.isArray(outcomes) || outcomes.some(item => !ids.includes(item?.id) || !["ready", "offline", "failed", "changed"].includes(item.outcome))) throw new Error(t("invalidResponse"));
    } catch {
      outcomes = ids.map(id => ({ id, outcome: "failed" }));
    } finally {
      state.busy = null;
    }
    if (state.driverRun !== current || !pendingWrite) return;
    for (const { id, outcome } of outcomes) {
      const row = current.rows.find(item => item.id === id);
      row.state = outcome;
      if (outcome === "ready") row.received = row.total;
    }
    const missing = current.rows.filter(row => row.state !== "ready");
    if (!missing.length) { state.driverRun = null; return flashMedia(pendingWrite.id, pendingWrite); }
    if (missing.every(row => row.state === "offline")) {
      // No connection at all: the card is written without these drivers, and the page says so.
      state.driverNotice = "driversOffline";
      return writeWithoutFailed();
    }
    current.status = "failed";
    render("stage-title");
  }

  function retryDrivers() {
    if (!state.driverRun || state.busy) return;
    return fetchDrivers(state.driverRun.rows.filter(row => row.state !== "ready").map(row => row.id));
  }

  function writeWithoutFailed() {
    if (!state.driverRun || !pendingWrite || state.busy) return;
    const missing = state.driverRun.rows.filter(row => row.state !== "ready").map(row => row.id);
    pendingWrite.drivers = pendingWrite.drivers.filter(id => !missing.includes(id));
    state.driverRun = null;
    return flashMedia(pendingWrite.id, pendingWrite);
  }

  function flashMedia(id, request) {
    if (state.stage !== 3 || state.target.id === "docker" || !state.downloaded || state.flashDone
      || !state.media.some(media => media.id === id) || state.busy || state.mediaScan) return;
    const { wifi = null, password = null, drivers = [] } = request ?? {};
    return run("preparing", "flashError", async active => {
      state.media = [];
      state.mediaId = null;
      pendingWrite = null;
      const result = await invoke("flash_media", { id, confirmed: true, wifi, password, drivers: drivers.length ? drivers : null }, event => {
        if (active !== state.busy) return;
        const previousPhase = active.phase;
        switch (event?.phase) {
          case "preparing":
            if (active.phase === "preparing") break;
            return;
          case "authorizing":
            if (!["mediaPreparing", "writing", "flashVerification"].includes(active.phase)) {
              active.phase = "authorizing";
              break;
            }
            return;
          case "writing":
            if (active.phase === "flashVerification") return;
            active.phase = "writing";
            break;
          case "verifying": // Compatibility with older helpers: also used during preparation.
            if (active.phase === "writing") active.phase = "flashVerification";
            break;
          case "flash_verifying":
            active.phase = "flashVerification";
            break;
          case "media_preparing":
            // Also accepted straight after "preparing": the system may ask for no authorization at all.
            if (!["preparing", "authorizing", "mediaPreparing"].includes(active.phase)) return;
            active.phase = "mediaPreparing";
            break;
          default:
            return;
        }
        if (active.phase !== previousPhase) active.flashPercent = null;
        active.message = typeof event.message === "string" ? event.message : "";
        // Accept only complete Imager progress lines for the current operation and phase.
        const match = /^\s*(Writing|Verifying):\s*(?:\[[-=># .]*\]\s*)?(\d{1,3})\s*%\s*$/.exec(active.message);
        const expected = active.phase === "writing" ? "Writing" : active.phase === "flashVerification" ? "Verifying" : null;
        if (match && match[1] === expected && (expected === "Writing" ? event.phase === "writing" : ["verifying", "flash_verifying"].includes(event.phase))) {
          const value = Number(match[2]);
          if (value <= 100 && (active.flashPercent == null || value >= active.flashPercent)) active.flashPercent = value;
        }
        progressView();
      });
      if (result !== null) throw new Error(t("invalidResponse"));
      state.flashDone = true;
      active.success = preview ? "previewWritten" : "mediaReady";
    });
  }

  async function invoke(command, args = {}, progress) {
    if (preview) return previewInvoke(command, args, progress);
    if (!nativeReady) throw new Error(t("appRequired"));
    if (progress) {
      const channel = new tauri.Channel();
      channel.onmessage = progress;
      return tauri.invoke(command, { ...args, onProgress: channel });
    }
    return tauri.invoke(command, args);
  }

  function clearWifiPassword() { state.wifi.password = ""; const field = document.getElementById("wifi-password"); if (field) field.value = ""; }
  dialog.addEventListener("cancel", clearWifiPassword);

  // This fixture is reachable only in an explicitly requested browser preview.
  async function previewInvoke(command, args, progress) {
    // Both Raspberry Pi images read the first-boot zone version 2; only the Pi 5 runs the camera service.
    const firstBoot = { source: "published", wifi_seed: true, password_seed: true, driver_drop: true };
    const demoReleases = {
      "pi-zero-w": { asset: "preview-alpine-armhf.img.xz", size: 128000000, sha256: "0123456789abcdef".repeat(4), ...firstBoot, camera_service: false },
      "pi-5": { asset: "preview-alpine-pi5.img.xz", size: 256000000, sha256: "0123456789abcdef".repeat(4), ...firstBoot, camera_service: true, driver_arch: "arm64" },
      docker: { asset: "install-docker.sh", size: 24000, sha256: "fedcba9876543210".repeat(4) },
    };
    // ?drivers=download|fail|offline shows a slow, a failed or an offline download of the driver.
    const driverMode = new URLSearchParams(location.search).get("drivers");
    const zwo = { id: "zwo_asi", name: "ZWO ASI", verifiedOnHardware: false, termsUrl: "https://www.zwoastro.com/software/product-sdk/", version: "1.42", arch: "arm64" };
    const demoOffers = [{ ...zwo, source: "download", size: 114589633 }];
    const demoMediaId = "PREVIEW-SD-01";
    const release = demoReleases[args.target];
    const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
    switch (command) {
      case "resolve_release":
        await pause(450);
        return release ? { target: args.target, tag: "v1.0.1-preview", ...release } : null;
      case "download_release":
        if (!release) throw new Error(t("invalidResponse"));
        for (const fraction of [0.15, 0.45, 0.73, 1]) {
          await pause(200);
          progress({ phase: "downloading", received: Math.round(release.size * fraction), total: release.size });
        }
        progress({ phase: "verifying", received: release.size, total: release.size });
        await pause(460);
        return { name: release.asset };
      case "list_media":
        await pause(320);
        return [{ id: demoMediaId, name: t("demoMedia"), size: 32000000000 }];
      case "flash_media":
        if (!args.confirmed || args.id !== demoMediaId) throw new Error(t("invalidResponse"));
        for (const phase of ["preparing", "authorizing", "media_preparing", "writing", "flash_verifying"]) {
          progress({ phase, message: "" });
          await pause(550);
        }
        return null;
      case "reveal_download":
        return null;
      case "driver_offers": {
        await pause(260);
        const current = demoReleases[state.target?.id];
        return current?.camera_service ? { supported: true, offers: demoOffers } : { supported: false, offers: [] };
      }
      case "download_drivers":
        for (const id of args.ids) {
          const offer = demoOffers.find(item => item.id === id);
          for (const fraction of driverMode === "download" ? [0, 0.31, 0.62, 1] : [0, 0.31]) {
            await pause(driverMode === "download" ? 700 : 300);
            progress({ id, received: Math.round(offer.size * fraction), total: offer.size });
          }
        }
        return args.ids.map(id => ({ id, outcome: driverMode === "fail" ? "failed" : driverMode === "offline" ? "offline" : "ready" }));
      case "open_licence":
        return null;
      case "discover_bridges": {
        await pause(420);
        const sample = (id, name, path, authMode, host) => ({ id, name, version: "1.2.0", path, authMode, sshHost: host, hotspotActive: true,
          addresses: [{ path, host, url: "http://" + host + "/", tls: false }], adminUrl: "http://" + host + "/", manual: false });
        return [args.joinedHotspot
          ? sample("preview-hotspot", "astro-bridge-preview", "hotspot", "hotspot", "192.168.77.1")
          : sample("preview-lan", "astro-bridge-preview", "home", "https", "192.168.1.80")];
      }
      case "add_bridge_ip":
        await pause(250);
        if (args.address !== "192.168.1.81") throw new Error(t("manualIpError"));
        return { id: "preview-manual", name: "astro-bridge-manual", version: "1.2.0", path: "home", authMode: "http", sshHost: args.address,
          hotspotActive: false, addresses: [], adminUrl: "http://" + args.address + "/", manual: true };
      case "unlock_bridge":
      case "refresh_bridge":
        await pause(200);
        return { homeSsid: "Preview Wi-Fi", homeConnected: true, hotspotSsid: "astro-bridge-preview", hotspotActive: true,
          sshAccount: state.target?.id === "pi-zero-w" ? "bridge" : "root", interfaces: { total: 2, builtin: 1, usb: 1 },
          connectionMode: "hotspot_shared", hotspotSubnet: "192.168.77.0/24",
          found: [{ id: "wifi-preview", name: "SynScan_0001", kind: "mount", link: "wifi", signal: 80, canAdd: true }],
          cameras: [{ name: "ZWO ASI", state: "ready" }], added: [] };
      case "forget_bridge_session":
        return null;
      case "add_bridge_device":
        return "connecting";
      case "change_bridge_password":
        return { sessionsInvalidated: true, hotspotReconnect: args.target === "all", sessionKept: false };
      case "open_bridge_ui":
        return null;
      default:
        throw new Error(t("invalidResponse"));
    }
  }

  for (const [tag, name] of languages) language.append(el("option", { value: tag, lang: tag }, name));
  language.addEventListener("change", () => {
    locale = language.value;
    try { localStorage.setItem(preference, locale); } catch { /* Session-only if storage is unavailable. */ }
    render("language");
  });
  render();
})();
