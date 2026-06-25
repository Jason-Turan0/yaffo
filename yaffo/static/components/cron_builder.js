// Self-contained cron editor. A tidy alternative to a raw cron textbox: a
// "Common schedules" preset list plus a Period-driven single-value builder
// (Hourly/Daily/Weekly/Monthly) and an Advanced (raw cron) escape hatch, all
// composing one 5-field cron string client-side. The server is the trust
// boundary and validates the result; this component owns the UX only.
//
// Usage: drop `<div data-cron-builder data-cron-name="cron"></div>` inside a
// form. The component renders into it and keeps a hidden input (the given name)
// holding the live cron, so a normal form/HTMX submit carries it. Existing
// schedules render their cron in plain English via [data-cron] spans. Both are
// (re)initialised automatically on load and after every htmx swap.
window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

window.PHOTO_ORGANIZER.COMPONENTS.cronBuilderReady =
window.PHOTO_ORGANIZER.i18nReady.then((i18n) => {
    const t = (key, options = {}) => i18n.t(`components:cron.${key}`, options);
    const PRESETS = [
        { cron: "*/15 * * * *", label: t("everyMinutes", { count: 15 }) },
        { cron: "0 * * * *", label: t("everyHour") },
        { cron: "0 */6 * * *", label: t("everyHours", { count: 6 }) },
        { cron: "0 0 * * *", label: t("dailyAtMidnight") },
        { cron: "0 9 * * *", label: t("dailyAt", { time: "9:00 AM" }) },
        { cron: "0 9 * * 1", label: t("weeklyOnAt", { day: t("monday"), time: "9:00 AM" }) },
    ];
    const WEEKDAYS = [
        [1, t("monday")], [2, t("tuesday")], [3, t("wednesday")],
        [4, t("thursday")], [5, t("friday")], [6, t("saturday")], [0, t("sunday")],
    ];
    const WEEKDAY_LABEL = {
        0: t("sunday"), 1: t("monday"), 2: t("tuesday"), 3: t("wednesday"),
        4: t("thursday"), 5: t("friday"), 6: t("saturday"), 7: t("sunday"),
    };
    const CADENCES = [
        ["hourly", t("hourly")], ["daily", t("daily")], ["weekly", t("weekly")],
        ["monthly", t("monthly")], ["advanced", t("advanced")],
    ];

    const pad = (n) => String(n).padStart(2, "0");
    const isInt = (s) => /^\d+$/.test(s);
    const fmtTime = (h, m) => i18n.date(
        new Date(Date.UTC(2000, 0, 1, h, m)),
        { hour: "numeric", minute: "2-digit", timeZone: "UTC" }
    );

    // cron -> friendly text, covering the shapes the presets/builder produce and
    // falling back to the raw expression for anything richer.
    const describeCron = (cron) => {
        if (!cron) return "";
        const parts = cron.trim().split(/\s+/);
        if (parts.length !== 5) return cron;
        const [minute, hour, dom, month, dow] = parts;

        const stepMin = /^\*\/(\d+)$/.exec(minute);
        if (stepMin && hour === "*" && dom === "*" && month === "*" && dow === "*")
            return t("everyMinutes", { count: Number(stepMin[1]) });
        const stepHour = /^\*\/(\d+)$/.exec(hour);
        if (minute === "0" && stepHour && dom === "*" && month === "*" && dow === "*")
            return t("everyHours", { count: Number(stepHour[1]) });

        if (month !== "*" || !isInt(minute)) return cron;
        const m = parseInt(minute, 10);
        if (hour === "*" && dom === "*" && dow === "*")
            return m === 0 ? t("everyHour") : t("everyHourAtMinute", { minute: pad(m) });
        if (!isInt(hour)) return cron;
        const when = fmtTime(parseInt(hour, 10), m);
        if (dom === "*" && dow === "*") return t("dailyAt", { time: when });
        if (dom === "*" && isInt(dow))
            return t("weeklyOnAt", {
                day: WEEKDAY_LABEL[parseInt(dow, 10)] || dow,
                time: when,
            });
        if (dow === "*" && isInt(dom))
            return t("monthlyOnDayAt", { day: parseInt(dom, 10), time: when });
        return cron;
    };

    const options = (pairs, selected) => pairs
        .map(([value, label]) => `<option value="${value}"${value == selected ? " selected" : ""}>${label}</option>`)
        .join("");
    const numOptions = (from, to, step, selected) => {
        const pairs = [];
        for (let n = from; n <= to; n += step) pairs.push([n, pad(n)]);
        return options(pairs, selected);
    };

    const TEMPLATE = (name) => `
        <input type="hidden" name="${name}" class="cron-value">
        <div class="form-row">
            <label class="form-row-label">${t("schedule")}</label>
            <select class="form-control cron-mode" aria-label="${t("scheduleType")}">
                <option value="preset">${t("commonSchedules")}</option>
                <option value="custom">${t("customSchedule")}</option>
            </select>
        </div>
        <div class="form-row cron-block-preset">
            <span class="form-row-label"></span>
            <select class="form-control cron-preset" aria-label="${t("presetSchedule")}">
                ${options(PRESETS.map((p) => [p.cron, p.label]), "0 * * * *")}
            </select>
        </div>
        <div class="form-row cron-block-custom">
            <label class="form-row-label">${t("repeat")}</label>
            <select class="form-control cron-cadence" aria-label="${t("repeat")}">
                ${options(CADENCES, "daily")}
            </select>
            <span class="cron-fields-row">
                <span class="cron-field cron-field-weekday">
                    <label class="cron-label">${t("on")}</label>
                    <select class="form-control cron-weekday" aria-label="${t("dayOfWeek")}">
                        ${options(WEEKDAYS, 1)}
                    </select>
                </span>
                <span class="cron-field cron-field-dom">
                    <label class="cron-label">${t("onDay")}</label>
                    <select class="form-control cron-dom" aria-label="${t("dayOfMonth")}">
                        ${numOptions(1, 31, 1, 1)}
                    </select>
                </span>
                <span class="cron-field cron-field-time">
                    <label class="cron-label">${t("at")}</label>
                    <select class="form-control cron-hour" aria-label="${t("hour")}">
                        ${numOptions(0, 23, 1, 9)}
                    </select>
                    <span class="cron-colon">:</span>
                </span>
                <span class="cron-field cron-field-minute">
                    <label class="cron-label cron-label-minute">${t("atMinute")}</label>
                    <select class="form-control cron-minute" aria-label="${t("minute")}">
                        ${numOptions(0, 55, 5, 0)}
                    </select>
                </span>
            </span>
            <span class="cron-block-advanced">
                <input type="text" class="form-control cron-raw" placeholder="*/30 * * * *" aria-label="${t("cronExpression")}">
                <span class="cron-hint">${t("cronHint")}</span>
            </span>
        </div>
        <div class="form-row cron-preview-row">
            <span class="form-row-label">${t("runs")}</span>
            <span class="cron-preview" aria-live="polite"></span>
        </div>`;

    const buildCron = (els) => {
        if (els.mode.value === "preset") return els.preset.value;
        const { minute, hour, weekday, dom } = els;
        switch (els.cadence.value) {
            case "hourly": return `${minute.value} * * * *`;
            case "daily": return `${minute.value} ${hour.value} * * *`;
            case "weekly": return `${minute.value} ${hour.value} * * ${weekday.value}`;
            case "monthly": return `${minute.value} ${hour.value} ${dom.value} * *`;
            case "advanced": return els.raw.value.trim();
            default: return "";
        }
    };

    const MINUTE_SET = new Set();
    for (let n = 0; n <= 55; n += 5) MINUTE_SET.add(n);
    const DEFAULT_STATE = () => ({
        mode: "preset", preset: "0 * * * *", cadence: "daily",
        minute: 0, hour: 9, weekday: 1, dom: 1, raw: "",
    });

    // Inverse of buildCron: a cron string -> control state, so editing an existing
    // schedule reopens the builder on the right fields. Any shape the single-value
    // builder can't represent (lists, steps, out-of-grid minutes) falls to Advanced.
    const parseCron = (cron) => {
        const def = DEFAULT_STATE();
        const c = (cron || "").trim();
        if (!c) return def;
        if (PRESETS.some((p) => p.cron === c)) return { ...def, mode: "preset", preset: c };

        const advanced = { ...def, mode: "custom", cadence: "advanced", raw: c };
        const parts = c.split(/\s+/);
        if (parts.length !== 5) return advanced;
        const [minute, hour, dom, month, dow] = parts;
        if (month !== "*" || !isInt(minute)) return advanced;
        const m = parseInt(minute, 10);
        if (!MINUTE_SET.has(m)) return advanced;
        const base = { ...def, mode: "custom", minute: m };

        if (hour === "*" && dom === "*" && dow === "*") return { ...base, cadence: "hourly" };
        if (!isInt(hour)) return advanced;
        const h = parseInt(hour, 10);
        if (h > 23) return advanced;
        const timed = { ...base, cadence: "daily", hour: h };
        if (dom === "*" && dow === "*") return timed;
        if (dom === "*" && isInt(dow)) {
            let d = parseInt(dow, 10);
            if (d === 7) d = 0;
            return d <= 6 ? { ...timed, cadence: "weekly", weekday: d } : advanced;
        }
        if (dow === "*" && isInt(dom)) {
            const d = parseInt(dom, 10);
            return d >= 1 && d <= 31 ? { ...timed, cadence: "monthly", dom: d } : advanced;
        }
        return advanced;
    };

    const create = (root) => {
        root.classList.add("cron-builder");
        root.innerHTML = TEMPLATE(root.dataset.cronName || "cron");
        const q = (sel) => root.querySelector(sel);
        const els = {
            hidden: q(".cron-value"), mode: q(".cron-mode"), preset: q(".cron-preset"),
            cadence: q(".cron-cadence"), weekday: q(".cron-weekday"), dom: q(".cron-dom"),
            hour: q(".cron-hour"), minute: q(".cron-minute"), raw: q(".cron-raw"),
            preview: q(".cron-preview"),
        };
        const show = (sel, on) => root.querySelectorAll(sel)
            .forEach((el) => el.classList.toggle("is-hidden", !on));

        const refresh = () => {
            const custom = els.mode.value === "custom";
            const cadence = els.cadence.value;
            const advanced = custom && cadence === "advanced";
            const fields = custom && !advanced;
            show(".cron-block-preset", !custom);
            show(".cron-block-custom", custom);
            show(".cron-fields-row", fields);
            show(".cron-block-advanced", advanced);
            show(".cron-field-weekday", cadence === "weekly");
            show(".cron-field-dom", cadence === "monthly");
            show(".cron-field-time", cadence !== "hourly");
            // "at minute" only reads right when there's no hour beside it; otherwise
            // the hour field's "at" + colon already frames it as HH:MM.
            show(".cron-label-minute", fields && cadence === "hourly");

            const cron = buildCron(els);
            els.hidden.value = cron;
            els.preview.textContent = cron ? describeCron(cron) : t("enterExpression");

            // Preset/builder output is valid by construction (true); only the raw
            // Advanced field can be wrong, so report it as unknown (null) for the host
            // to check against the server. Bubbles so delegated host glue can listen.
            root.dispatchEvent(new CustomEvent("cron:change", {
                bubbles: true,
                detail: { cron, valid: advanced ? null : true },
            }));
        };

        const setState = (st) => {
            els.mode.value = st.mode;
            els.preset.value = st.preset;
            els.cadence.value = st.cadence;
            els.minute.value = st.minute;
            els.hour.value = st.hour;
            els.weekday.value = st.weekday;
            els.dom.value = st.dom;
            els.raw.value = st.raw;
            refresh();
        };

        // Host glue (automations.js) opens the editor in add vs edit mode through these.
        root._cron = {
            reset: () => setState(DEFAULT_STATE()),
            setCron: (cron) => setState(parseCron(cron)),
        };

        root.addEventListener("change", refresh);
        root.addEventListener("input", refresh);
        refresh();
    };

    const initAll = (scope) => {
        const inScope = (el) => scope === el || scope.contains(el);
        (scope.matches?.("[data-cron-builder]") ? [scope] : [])
            .concat(Array.from(scope.querySelectorAll?.("[data-cron-builder]") || []))
            .forEach((el) => { if (!el.dataset.cronReady) { el.dataset.cronReady = "1"; create(el); } });
        document.querySelectorAll("[data-cron]").forEach((el) => {
            if (inScope(el)) el.textContent = describeCron(el.dataset.cron);
        });
    };

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", () => initAll(document));
    } else {
        initAll(document);
    }
    document.body.addEventListener("htmx:afterSwap", (e) => initAll(e.target));

    const reset = (root) => root && root._cron && root._cron.reset();
    const setCron = (root, cron) => root && root._cron && root._cron.setCron(cron);

    const api = { initAll, describeCron, reset, setCron };
    window.PHOTO_ORGANIZER.COMPONENTS.cronBuilder = api;
    return api;
});
