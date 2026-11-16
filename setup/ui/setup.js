(() => {
  "use strict";

  const { languages, catalogs } = window.SetupI18n;
  const main = document.getElementById("main");
  const dialog = document.getElementById("erase-dialog");
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
    wifi: { enabled: false, ssid: "", password: "", hidden: false, country: "JP" },
    wifiError: null,
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

  function pill(key) {
    return el("span", { class: "pill" }, t(key));
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
      node.append(el("li", {
        class: finished ? "step complete" : "step",
        "aria-current": number === state.stage ? "step" : null,
      }, el("span", { class: "step-number", "aria-hidden": "true" }, finished ? icon("check") : number), el("span", {}, t(key))));
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
    return el("div", { class: "error-box", role: "alert" }, callout(key, "red"), detail
      ? el("details", { class: "error-details" }, el("summary", {}, t("technicalDetails")), el("p", {}, detail))
      : null);
  }

  function progressPanel() {
    const node = el("section", { class: "panel" },
      el("div", { class: "panel-heading" }, tile("shield"),
        el("div", {}, el("h2", { id: "progress-title" }), el("p", { id: "progress-description" }))));
    node.append(el("div", { class: "progress-body" },
      el("div", { class: "progress-label", "aria-hidden": "true" }, el("span", { id: "bytes" }), el("span", { id: "percent" })),
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
    const nextTitle = t(local && phase === "downloading" ? "localCopying" : local && phase === "verifying" ? "localVerify" : phaseLabels[phase]);
    if (progressTitle.textContent !== nextTitle) {
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
      if (state.target.id !== "docker") listMedia();
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

  function wifiCapable() {
    return state.target?.id === "pi-5" && state.release?.source === "local-test" && state.release?.wifi_seed === true;
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
      return [heading, layout([mediaPanel(), errorBox()]), actions(null, reveal, launch)];
    }
    const review = button("reviewErase", confirmErase, { style: "primary", glyph: "arrow", trailing: true,
      disabled: Boolean(state.busy || state.mediaScan || !state.mediaId) });
    review.id = "media-review";
    return [heading, layout([mediaPanel(), wifiCapable() && !state.busy ? wifiPanel() : null, errorBox()].filter(Boolean)),
      actions(back(2), review)];
  }

  function enterLaunch(from) {
    if (state.busy || state.stage !== from || (!nativeReady && !preview)) return;
    if (from === 3 && (!state.downloaded || (state.target?.id !== "docker" && !state.flashDone))) return;
    if (from === 0) state.target = null;
    state.launch = { from, bridges: null, selected: null, summary: null, form: null, outcome: null, manualIp: "" };
    navigate(4);
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

  function bridgeChoice(bridge, index) {
    const selected = state.launch.selected === bridge;
    const id = "bridge-" + index;
    const input = el("input", { type: "radio", name: "bridge", id, checked: selected, disabled: Boolean(state.busy),
      "aria-labelledby": id + "-name " + id + "-details" });
    input.addEventListener("change", () => {
      if (state.busy) return;
      Object.assign(state.launch, { selected: bridge, summary: null, form: null, outcome: null });
      state.error = null;
      render(id);
    });
    return el("label", { class: selected ? "media-option selected" : "media-option" }, input, tile("chip"),
      el("span", { class: "choice-body" }, el("strong", { id: id + "-name" }, el("bdi", {}, bridge.name)),
        el("span", { class: "details", id: id + "-details" }, el("bdi", {}, bridge.version), " · ", t(launchPaths[bridge.path]),
          bridge.sshHost ? " · " : null, bridge.sshHost ? el("bdi", {}, bridge.sshHost) : null)),
      el("span", { class: "radio-mark", "aria-hidden": "true" }));
  }

  function discoveryPanel() {
    const { bridges } = state.launch;
    const busy = Boolean(state.busy);
    const content = panel("findBridges", "refresh", "discoverBridgesDetail");
    content.append(el("div", { class: "launch-buttons" },
      button(bridges === null ? "discoverBridges" : "searchBridgesAgain", () => discoverBridges(false), { glyph: "refresh", disabled: busy })));
    if (bridges?.length) {
      content.append(el("fieldset", { class: "media-options bridge-options" },
        el("legend", { class: "group-label" }, t("chooseBridge")), ...bridges.map(bridgeChoice)));
    } else if (bridges) {
      content.append(el("div", { class: "launch-notice" }, callout("noBridges", "gold")));
    }
    const manual = el("form", { class: "launch-credentials", id: "manual-ip-form" });
    const ip = el("input", { id: "manual-ip", type: "text", inputmode: "decimal", autocomplete: "off", required: true, value: state.launch.manualIp, disabled: busy });
    ip.addEventListener("input", () => { state.launch.manualIp = ip.value; });
    manual.append(el("label", { class: "launch-field", for: "manual-ip" }, el("span", {}, t("enterIp")), ip),
      el("div", { class: "launch-buttons" }, button("checkIp", null, { type: "submit", disabled: busy })));
    manual.addEventListener("submit", event => { event.preventDefault(); addBridgeIp(); });
    content.append(manual);
    if (!busy) {
      const consent = el("input", { type: "checkbox" });
      const search = button("discoverHotspot", () => {
        if (consent.checked) discoverBridges(true);
      }, { glyph: "refresh", disabled: true });
      consent.addEventListener("change", () => { search.disabled = !consent.checked; });
      content.append(el("details", { class: "launch-hotspot" },
        el("summary", {}, t("hotspotFallback")), el("p", {}, t("hotspotJoin")),
        el("label", { class: "confirm-check" }, consent, el("span", {}, t("hotspotConsent"))), search));
    }
    return content;
  }

  function bridgeNetworkSummary(summary) {
    const row = (key, value) => el("div", {}, el("dt", {}, t(key)), el("dd", {}, value));
    return el("section", { class: "launch-summary" }, el("h3", {}, t("networkSummary")),
      el("dl", { class: "release-meta" },
        row("homeSsid", summary.homeSsid ? el("bdi", {}, summary.homeSsid) : t("networkUnset")),
        row("homeStatus", t(summary.homeConnected ? "networkConnected" : "networkDisconnected")),
        row("hotspotSsid", summary.hotspotSsid ? el("bdi", {}, summary.hotspotSsid) : t("networkUnset")),
        row("hotspotStatus", t(summary.hotspotActive ? "hotspotActive" : "hotspotInactive"))));
  }

  function clearPasswordFields() {
    for (const field of main.querySelectorAll('#launch-credentials input[type="password"]')) field.value = "";
  }

  function credentialForm(mode) {
    const form = el("form", { id: "launch-credentials", class: "launch-credentials", autocomplete: "off" });
    const current = el("input", { id: "bridge-current-password", type: "password", autocomplete: "off", required: true });
    const field = (key, input) => el("label", { class: "launch-field" }, el("span", {}, t(key)), input);
    const changing = mode !== "read";
    form.append(el("h3", {}, t(changing ? (mode === "web" ? "changeWebPassword" : "changeHotspotPassword") : "readBridge")),
      field("currentAdminPassword", current));
    const next = el("input", { type: "password", autocomplete: "off", required: true });
    const repeat = el("input", { type: "password", autocomplete: "off", required: true });
    const feedback = el("p", { class: "launch-form-error", role: "alert", hidden: true });
    if (changing) {
      form.append(field("newPassword", next), field("confirmNewPassword", repeat),
        el("p", {}, t(mode === "hotspot" ? "hotspotPasswordRules" : "webPasswordRules")), feedback);
      form.addEventListener("input", () => { feedback.hidden = true; });
    }
    form.append(el("div", { class: "launch-buttons" },
      button(changing ? "savePassword" : "readBridge", null, { style: "primary", type: "submit" }),
      button("cancel", () => setLaunchForm(null), { style: "ghost" })));
    form.addEventListener("submit", event => {
      event.preventDefault();
      if (changing && (!validNewPassword(mode, next.value) || next.value !== repeat.value)) {
        const matches = next.value === repeat.value;
        feedback.textContent = t(matches ? (mode === "hotspot" ? "hotspotPasswordRules" : "webPasswordRules") : "passwordMismatch");
        feedback.hidden = false;
        (matches ? next : repeat).focus();
        return;
      }
      const args = changing ? { currentPassword: current.value, newPassword: next.value, target: mode } : { password: current.value };
      clearPasswordFields();
      useBridgeCredentials(mode, args);
    });
    return form;
  }

  function validNewPassword(target, password) {
    const length = [...password].length;
    if (length < 8 || length > 128 || /[\x00-\x1f\x7f]/.test(password)) return false;
    if (target === "web") return true;
    const bytes = new TextEncoder().encode(password).length;
    return (bytes >= 8 && bytes <= 63) || /^[0-9a-f]{64}$/i.test(password);
  }

  function setLaunchForm(mode) {
    if (state.busy) return;
    state.launch.form = mode;
    state.error = null;
    render(mode ? "bridge-current-password" : "bridge-manage-title");
  }

  function manageBridge(bridge) {
    const { form, summary } = state.launch;
    const busy = Boolean(state.busy);
    const content = panel("manageBridge", "shield", bridge.authMode === "none" ? null : "manageBridgeDetail");
    const heading = content.querySelector("h2");
    heading.id = "bridge-manage-title";
    heading.tabIndex = -1;
    content.append(el("p", { class: "launch-target" }, el("bdi", {}, bridge.name)),
      el("div", { class: "launch-buttons" },
        button("openBridgeUi", () => run("openingBridge", "openBridgeError", () => invoke("open_bridge_ui", { id: bridge.id })),
          { glyph: "arrow", trailing: true, disabled: busy })));
    if (summary) content.append(bridgeNetworkSummary(summary));
    if (bridge.authMode === "none") {
      content.append(el("div", { class: "launch-notice" }, callout("bridgeAuthUnavailable", "gold")));
    } else {
      if (bridge.authMode === "hotspot") {
        content.append(el("div", { class: "launch-notice" }, callout("hotspotHttpDisclosure", "gold")));
      }
      if (!busy && form) content.append(credentialForm(form));
      else content.append(el("div", { class: "launch-buttons" },
        button("readBridge", () => setLaunchForm("read"), { disabled: busy }),
        button("changeWebPassword", () => setLaunchForm("web"), { disabled: busy }),
        button("changeHotspotPassword", () => setLaunchForm("hotspot"), { disabled: busy })));
    }
    return content;
  }

  function sshPanel(bridge) {
    const content = panel("sshTitle", "info", "sshDetail");
    content.classList.add("launch-ssh");
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
    const { from, selected, outcome } = state.launch;
    const outcomeBox = outcome ? el("div", { role: "status" }, callout(outcome, "green")) : null;
    return [title(4, "launchTitle", "launchIntro"),
      el("div", { class: "wizard-layout launch-layout" }, startupPanel(),
        el("div", { class: "wizard-content launch-discovery" }, preview ? callout("previewLaunch", "gold") : null,
          discoveryPanel(), state.busy ? progressPanel() : null,
          errorBox(), outcomeBox, selected ? manageBridge(selected) : null),
        sshPanel(selected)),
      actions(back(from), button("startOver", restart, { disabled: Boolean(state.busy) }))];
  }

  function discoverBridges(joinedHotspot) {
    if (state.stage !== 4 || state.busy) return;
    const launch = state.launch;
    Object.assign(launch, { bridges: null, selected: null, summary: null, form: null, outcome: null });
    return run("discovery", "discoverBridgesError", async active => {
      const bridges = await invoke("discover_bridges", { joinedHotspot });
      if (!Array.isArray(bridges) || bridges.some(bridge => typeof bridge?.id !== "string" || !bridge.id
        || typeof bridge.name !== "string" || !bridge.name || typeof bridge.version !== "string" || !bridge.version
        || !Object.hasOwn(launchPaths, bridge.path) || !["none", "https", "hotspot"].includes(bridge.authMode)
        || (bridge.authMode === "hotspot" && !joinedHotspot)
        || (bridge.sshHost != null && typeof bridge.sshHost !== "string"))
        || new Set(bridges.map(bridge => bridge.id)).size !== bridges.length) throw new Error(t("invalidResponse"));
      launch.bridges = bridges;
      active.success = bridges.length ? "chooseBridge" : "noBridges";
    });
  }

  function addBridgeIp() {
    if (state.stage !== 4 || state.busy) return;
    const launch = state.launch, address = launch.manualIp;
    return run("discovery", "manualIpError", async active => {
      const bridge = await invoke("add_bridge_ip", { address });
      if (typeof bridge?.id !== "string" || !bridge.id || typeof bridge.name !== "string" || !bridge.name
        || typeof bridge.version !== "string" || !bridge.version || bridge.path !== "home"
        || bridge.authMode !== "none" || bridge.sshHost !== address) throw new Error(t("invalidResponse"));
      launch.bridges = [...(launch.bridges || []).filter(item => item.sshHost !== address), bridge];
      Object.assign(launch, { selected: bridge, summary: null, form: null, outcome: null });
      active.success = "chooseBridge";
    });
  }

  function useBridgeCredentials(mode, args) {
    if (state.stage !== 4 || state.busy) return;
    const launch = state.launch;
    const bridge = launch.selected;
    if (!bridge || bridge.authMode === "none" || launch.form !== mode) return;
    launch.form = null;
    launch.outcome = null;
    if (mode === "read") {
      launch.summary = null;
      return run("readingBridge", "readBridgeError", async active => {
        const result = await invoke("read_bridge", { id: bridge.id, ...args });
        if (typeof result?.homeSsid !== "string" || typeof result.homeConnected !== "boolean"
          || typeof result.hotspotSsid !== "string" || typeof result.hotspotActive !== "boolean") throw new Error(t("invalidResponse"));
        launch.summary = result;
        active.success = "networkSummary";
      });
    }
    return run("changingPassword", "changePasswordError", async () => {
      const result = await invoke("change_bridge_password", { id: bridge.id, ...args });
      if (typeof result?.sessionsInvalidated !== "boolean" || typeof result.hotspotReconnect !== "boolean") throw new Error(t("invalidResponse"));
      launch.outcome = result.hotspotReconnect ? "passwordReconnect" : result.sessionsInvalidated ? "passwordSessionsEnded" : "passwordChanged";
      if (result.hotspotReconnect) Object.assign(launch, { bridges: null, selected: null, summary: null });
    });
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
  }

  function navigate(stage) {
    if (state.busy) return;
    state.wifi.password = ""; state.wifiError = null;
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
      state.error = { key: errorKey, detail: state.stage === 4 ? null : typeof error === "string" ? error : error?.message };
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

  function confirmErase() {
    const media = state.media.find(device => device.id === state.mediaId);
    if (state.busy || state.mediaScan || !media || state.stage !== 3 || state.target.id === "docker" || state.flashDone) return;
    if (wifiCapable() && state.wifi.enabled && !validWifi()) { state.wifiError = "wifiInvalid"; render("wifi-ssid"); return; }
    state.wifiError = null;
    const confirm = button("eraseWrite", () => {
      if (!consent.checked || !dialog.open) return;
      const wifi = wifiCapable() && state.wifi.enabled ? { ssid: state.wifi.ssid, password: state.wifi.password, hidden: state.wifi.hidden, country: state.wifi.country } : null;
      state.wifi.password = "";
      dialog.close();
      flashMedia(media.id, wifi);
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

  function flashMedia(id, wifi) {
    if (state.stage !== 3 || state.target.id === "docker" || !state.downloaded || state.flashDone
      || !state.media.some(media => media.id === id) || state.busy || state.mediaScan) return;
    return run("preparing", "flashError", async active => {
      state.media = [];
      state.mediaId = null;
      const result = await invoke("flash_media", { id, confirmed: true, wifi }, event => {
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
            if (!["authorizing", "mediaPreparing"].includes(active.phase)) return;
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
    const demoReleases = {
      "pi-zero-w": { asset: "preview-alpine-armhf.img.xz", size: 128000000, sha256: "0123456789abcdef".repeat(4) },
      "pi-5": { asset: "preview-alpine-pi5.img.xz", size: 256000000, sha256: "0123456789abcdef".repeat(4) },
      docker: { asset: "install-docker.sh", size: 24000, sha256: "fedcba9876543210".repeat(4) },
    };
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
      case "discover_bridges":
        await pause(420);
        return [args.joinedHotspot
          ? { id: "preview-hotspot", name: "astro-bridge-preview", version: "1.0.1", path: "hotspot", authMode: "hotspot", sshHost: "192.168.77.1" }
          : { id: "preview-lan", name: "astro-bridge-preview", version: "1.0.1", path: "home", authMode: "https", sshHost: "192.168.1.80" }];
      case "add_bridge_ip":
        await pause(250);
        if (args.address !== "192.168.1.80") throw new Error(t("manualIpError"));
        return { id: "preview-manual", name: "astro-bridge-preview", version: "1.0.1", path: "home", authMode: "none", sshHost: args.address };
      case "read_bridge":
        return { homeSsid: "Preview Wi-Fi", homeConnected: true, hotspotSsid: "astro-bridge-preview", hotspotActive: true };
      case "change_bridge_password":
        return { sessionsInvalidated: args.target === "web", hotspotReconnect: args.target === "hotspot" };
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
