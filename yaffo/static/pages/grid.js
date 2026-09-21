// @ts-check

/**
 * The in-flight generation phase for the design page.
 * @typedef {Object} Generation
 * @property {number} versionId
 * @property {string} status
 * @property {string | null} startedAt
 * @property {ReturnType<typeof setTimeout>} [pollTimer]
 * @property {ReturnType<typeof setInterval>} [elapsedTimer]
 *
 * A conversation-feed line from the polled transcript.
 * @typedef {Object} FeedMessage
 * @property {string} type
 * @property {string} content
 *
 * The polled version-status payload.
 * @typedef {Object} VersionStatusBody
 * @property {string} status
 * @property {string | null} started_at
 * @property {FeedMessage[]} messages
 * @property {Record<string, any>[]} widgets
 *
 * One band of the canvas policy — see CANVAS_BANDS.
 * @typedef {Object} CanvasBand
 * @property {number} minWidth
 * @property {number} columns
 * @property {number} minRows
 *
 * A widget's geometry in the canonical 12-column layout.
 * @typedef {Object} WideGeometry
 * @property {number} x
 * @property {number} y
 * @property {number} w
 * @property {number} h
 */

const pagesGridWindow = window;

pagesGridWindow.PHOTO_ORGANIZER = pagesGridWindow.PHOTO_ORGANIZER || {};
const pagesGridNamespace = pagesGridWindow.PHOTO_ORGANIZER.pages =
    /** @type {PagesNamespace} */ (pagesGridWindow.PHOTO_ORGANIZER.pages || {});

// Runtime widget errors, kept locally (in memory, per session) keyed by widget
// id. Not persisted — they're only sent along as context the next time the model
// generates, so it can fix code that threw.
pagesGridWindow.PHOTO_ORGANIZER.widgetErrors = pagesGridWindow.PHOTO_ORGANIZER.widgetErrors || {};

// The authored layout is always the 12-column one: it is what the model writes,
// what the server stores, and what Save has to keep writing no matter how narrow
// the canvas being edited is.
const MAX_COLUMNS = 12;

const BASE_GRID_OPTS = {
    column: MAX_COLUMNS,
    cellHeight: 80,
    margin: 8,
    float: true
    // No `columnOpts`: the bands below are driven from the *canvas* width by
    // observeCanvas. GridStack's own dynamic-column support installs a second
    // ResizeObserver and can only change the column count, not the row floor the
    // reflowed content needs, so the two would fight over the same element.
};

// Canvas policy. Measured on the `.grid-stack` element, never on the viewport:
// the design canvas shares its row with a 360px editor panel until 900px, so the
// window width says nothing about the space the grid actually has.
//
//   canvas >= 900px  12 columns, >= 1 row  (80px)  — the authored desktop layout
//   canvas >= 600px   6 columns, >= 2 rows (160px) — tablets, and the design
//                                                    canvas beside the editor
//   canvas <  600px   1 column,  >= 3 rows (240px) — phones: one full-bleed
//                                                    widget per row
//
// `minRows` is a floor, applied to the live grid only. A widget authored four
// columns wide re-wraps its content when it becomes full-bleed, and a widget left
// at its authored height would have to scroll inside its own frame — a nested
// scroll region the page contract rules out. The authored height is restored
// verbatim once the canvas is wide enough again.
/** @type {CanvasBand[]} */
const CANVAS_BANDS = [
    { minWidth: 900, columns: MAX_COLUMNS, minRows: 1 },
    { minWidth: 600, columns: 6, minRows: 2 },
    { minWidth: 0, columns: 1, minRows: 3 }
];

/**
 * @param {number} width
 * @returns {CanvasBand}
 */
const bandForWidth = (width) =>
    CANVAS_BANDS.find((band) => width >= band.minWidth) || CANVAS_BANDS[CANVAS_BANDS.length - 1];

/**
 * The authored (12-column) geometry of every widget, read from the server-rendered
 * `gs-*` attributes. Must be called *before* GridStack initializes: the first
 * layout pass rewrites those attributes for whichever band the canvas starts in,
 * so afterwards they no longer describe what the page actually stores.
 * @returns {Map<string, WideGeometry>}
 */
const readAuthoredLayout = () => {
    /** @type {Map<string, WideGeometry>} */
    const layout = new Map();
    document.querySelectorAll('.grid-stack > .grid-stack-item').forEach((el) => {
        const id = el.getAttribute('gs-id');
        if (!id) return;
        layout.set(id, {
            x: Number(el.getAttribute('gs-x')) || 0,
            y: Number(el.getAttribute('gs-y')) || 0,
            w: Number(el.getAttribute('gs-w')) || 1,
            h: Number(el.getAttribute('gs-h')) || 1
        });
    });
    return layout;
};

/**
 * Put every widget on the band's geometry: the authored layout at full width, or
 * the authored height raised to the band's floor once the canvas has narrowed.
 * @param {any} grid
 * @param {Map<string, WideGeometry>} layout
 * @param {CanvasBand} band
 */
const applyBandGeometry = (grid, layout, band) => {
    grid.batchUpdate();
    [...grid.engine.nodes].forEach((/** @type {any} */ node) => {
        const wide = layout.get(node.id);
        if (!wide || !node.el) return;
        if (band.columns === MAX_COLUMNS) {
            // Restore from our own record rather than GridStack's layout cache,
            // which round-trips x/y/w but drops h — so a height raised for a narrow
            // band would never come back down.
            grid.update(node.el, { ...wide, minH: 1 });
        } else {
            grid.update(node.el, { h: Math.max(band.minRows, wide.h), minH: band.minRows });
        }
    });
    grid.batchUpdate(false);
    // One column is a stack, not a grid: close the gaps the wide layout's
    // side-by-side rows leave behind, keeping (y, x) — i.e. reading — order.
    if (band.columns === 1) grid.compact();
};

/**
 * Move a grid onto a band: the column count first, then the geometry that count
 * implies. Callers that track their own authored layout must run this inside
 * their "a reflow is in flight" guard — `grid.column()` fires a change event of
 * its own, and treating that as an edit would capture the narrow geometry.
 * @param {any} grid
 * @param {Map<string, WideGeometry>} layout
 * @param {CanvasBand} band
 */
const applyBand = (grid, layout, band) => {
    grid.column(band.columns);
    applyBandGeometry(grid, layout, band);
};

/**
 * Keep a grid on the band its own width allows and report band changes.
 * Container-driven, so an htmx swap or the editor panel stacking re-lays the
 * canvas out even when the window never changed size. The callback owns the
 * column change (see applyBand), so a caller can wrap the whole transition.
 * @param {HTMLElement | null} gridEl
 * @param {(band: CanvasBand) => void} onBand
 * @returns {{ getBand: () => CanvasBand | null, apply: () => void }}
 */
const observeCanvas = (gridEl, onBand) => {
    /** @type {CanvasBand | null} */
    let current = null;
    const apply = () => {
        const width = gridEl ? gridEl.clientWidth : 0;
        if (!width) return;  // detached, or hidden behind a closed panel
        const band = bandForWidth(width);
        if (band === current) return;
        current = band;
        /** @type {HTMLElement} */ (gridEl).classList.toggle('is-single-column', band.columns === 1);
        onBand(band);
    };
    // rAF-deferred so mutating the grid from inside the callback cannot trip
    // ResizeObserver's "undelivered notifications" loop guard.
    const schedule = () => window.requestAnimationFrame(apply);
    if (gridEl && typeof ResizeObserver === 'function') new ResizeObserver(schedule).observe(gridEl);
    window.addEventListener('resize', schedule);
    apply();
    return { getBand: () => current, apply };
};

const POLL_INTERVAL_MS = 1500;
const POLL_RETRY_MS = 3000;

// Page-version statuses (mirror PAGE_VERSION_STATUS_* in yaffo/db/models.py).
const STATUS = {
    IN_PROGRESS: 'IN_PROGRESS',
    READY: 'READY',
    FAILED: 'FAILED',
    ACCEPTED: 'ACCEPTED',
    CANCELLED: 'CANCELLED',
};

// The widget iframe broker (window.PHOTO_ORGANIZER.pages.initWidgetBroker) lives in
// widget_broker.js — the host-side counterpart of the in-iframe widget_api.js.

pagesGridNamespace.initPresentationGrid = () => {
    // Read before init — GridStack rewrites gs-* for the starting band.
    const authored = readAuthoredLayout();
    const grid = GridStack.init({ ...BASE_GRID_OPTS, staticGrid: true });
    const gridEl = /** @type {HTMLElement} */ (document.querySelector('.grid-stack'));
    // A published page reflows exactly the way it did while it was being edited.
    // At one column GridStack lays nodes out in (y, x) order, and the server
    // renders widgets in that same reading order, so the narrow stack is the
    // page's source order.
    observeCanvas(gridEl, (band) => applyBand(grid, authored, band));
    return grid;
};

// Design grid. The page edits exactly one version — `editVersionId` (its status is
// `startStatus`): the working draft if a generation has produced one, else the
// published version. Every edit targets that version, so there's no published-vs-
// working ambiguity. `ACCEPTED` means it's the live published version (idle editing);
// IN_PROGRESS/READY/FAILED mean it's a working draft (poll + review). A new draft is
// forked on the first chat message, and `generation.versionId` then tracks it.
// See docs/ai-page-builder-async-generation.md.
/**
 * @param {number} pageId
 * @param {number} editVersionId
 * @param {string} startStatus
 * @param {AppConfig} config
 * @param {I18nService} i18n
 * @returns {PageDesignGridApi}
 */
pagesGridNamespace.initDesignGrid = (pageId, editVersionId, startStatus, config, i18n) => {
    /**
     * @param {string} key
     * @param {Record<string, unknown>} [options]
     */
    const t = (key, options = {}) => i18n.t(key, options);
    // The canonical 12-column geometry, seeded from the server-rendered markup
    // before GridStack can rewrite it. GridStack's layout cache keeps x/y/w but
    // not h, and while the canvas is narrowed its live nodes *are* the narrow
    // layout — so saving straight off the grid from a phone would flatten every
    // widget to one column and persist the heights the reflow inflated. This map,
    // not the grid, is what Save writes.
    /** @type {Map<string, WideGeometry>} */
    const wideLayout = readAuthoredLayout();
    const grid = GridStack.init({ ...BASE_GRID_OPTS, handle: '.widget-header' });
    const gridEl = /** @type {HTMLElement} */ (document.querySelector('.grid-stack'));

    // True while the band change is rewriting the grid, so the change handler below
    // does not mistake a reflow for an edit and write the narrow geometry back.
    let applyingBand = false;

    const syncWideLayout = () => {
        if (applyingBand || grid.getColumn() !== MAX_COLUMNS) return;
        grid.engine.nodes.forEach((/** @type {any} */ node) => {
            wideLayout.set(node.id, { x: node.x, y: node.y, w: node.w, h: node.h });
        });
    };

    // Gesture policy. Drag-to-move and drag-to-resize are a fine-pointer
    // affordance on a multi-column canvas. On a coarse pointer a drag that starts
    // on a widget is indistinguishable from the swipe that scrolls the page, and
    // on a single-column canvas there is nowhere to drag *to* — so in both cases
    // the gestures are turned off and the explicit ↑/↓/−/+ controls (always
    // visible in this mode) are the way to move and resize.
    const finePointer = window.matchMedia('(pointer: fine)');
    /** @type {CanvasBand | null} */
    let currentBand = null;
    const syncGridInteraction = () => {
        const direct = !finePointer.matches || !currentBand || currentBand.columns === 1;
        grid.enableMove(!direct);
        grid.enableResize(!direct);
        gridEl.classList.toggle('is-direct-controls', direct);
    };

    observeCanvas(gridEl, (band) => {
        currentBand = band;
        // The guard has to cover the column change as well as the geometry:
        // grid.column() emits its own change event, and on the way *back* to a
        // wide canvas that event fires while the nodes still carry the narrow
        // band's inflated heights — recording those as authored would make the
        // reflow permanent.
        applyingBand = true;
        applyBand(grid, wideLayout, band);
        applyingBand = false;
        syncGridInteraction();
    });
    finePointer.addEventListener('change', syncGridInteraction);
    grid.on('change', syncWideLayout);

    // Generated/edited widget content the client holds but hasn't saved (manual
    // adds), keyed by widget id. Nothing is persisted until Save sends these to the
    // server.
    /** @type {Map<string, Record<string, any>>} */
    const drafts = new Map();

    const designLayout = document.querySelector('.page-design');
    const messagesEl = document.getElementById('conversation-messages');
    const messageInput = /** @type {HTMLInputElement | null} */ (document.getElementById('conversation-message'));
    const sendButton = /** @type {HTMLButtonElement | null} */ (document.querySelector('#conversation-form button[type="submit"]'));
    const addButton = /** @type {HTMLButtonElement | null} */ (document.getElementById('add-widget-button'));
    const saveButton = /** @type {HTMLButtonElement | null} */ (document.getElementById('save-page-button'));
    const cancelButton = /** @type {HTMLButtonElement | null} */ (document.getElementById('conversation-cancel'));
    const statusBar = document.getElementById('conversation-status');
    const elapsedEl = document.getElementById('conversation-elapsed');

    // The version being edited + its phase: { versionId, status, startedAt,
    // pollTimer, elapsedTimer }. versionId is always a real version (the published
    // one when status is ACCEPTED); status drives the UI phase. enterRunning swaps in
    // the working version once a generation starts.
    /** @type {Generation} */
    let generation = { versionId: editVersionId, status: startStatus, startedAt: null };
    // id -> JSON signature of the version widget last rendered, so polls only
    // re-render widgets that actually changed.
    /** @type {Map<string, string>} */
    const rendered = new Map();

    // The full widget set for Save: layout for every grid item, plus content for
    // any the client holds as a draft (untouched saved widgets send layout only,
    // so the server keeps their stored content). The geometry always comes from
    // the 12-column record, so saving from a phone publishes the desktop layout
    // rather than the one-column reflow that happens to be on screen.
    const getWidgets = () => grid.engine.nodes.map((/** @type {any} */ node) => {
        const id = node.id;
        const titleInput = node.el.querySelector('.widget-title-input');
        const wide = wideLayout.get(id) || { x: node.x, y: node.y, w: node.w, h: node.h };
        /** @type {Record<string, any>} */
        const layout = { id, x: wide.x, y: wide.y, w: wide.w, h: wide.h };
        if (titleInput) layout.title = titleInput.value;
        const content = drafts.get(id);
        return content ? { ...content, ...layout } : layout;
    });

    const savePage = async () => {
        const body = {
            title: /** @type {HTMLInputElement} */ (document.getElementById('page-title')).value,
            subtitle: /** @type {HTMLInputElement} */ (document.getElementById('page-subtitle')).value,
            show_title: /** @type {HTMLInputElement} */ (document.getElementById('page-show-title')).checked,
            tab_order: parseInt(/** @type {HTMLInputElement} */ (document.getElementById('page-tab-order')).value, 10) || 0,
            widgets: getWidgets()
        };
        const response = await fetch(config.buildUrl('pages_update', { page_id: pageId }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        if (!response.ok) return;
        window.location.href = config.buildUrl('pages_detail', { page_id: pageId });
    };

    // Wire per-widget controls. The title is a display span (so the header
    // stays freely draggable); the edit pencil reveals an input only while
    // renaming, and its mousedown is stopped from starting a drag.
    /** @param {Element} el */
    const wireWidget = (el) => {
        const titleSpan = /** @type {HTMLElement | null} */ (el.querySelector('.widget-title'));
        const titleInput = /** @type {HTMLInputElement | null} */ (el.querySelector('.widget-title-input'));
        const editButton = /** @type {HTMLElement | null} */ (el.querySelector('.widget-edit'));
        if (titleSpan && titleInput && editButton) {
            const endEdit = () => {
                titleSpan.textContent = titleInput.value.trim() || t('pages:widgets.untitled');
                titleInput.hidden = true;
                titleSpan.hidden = false;
                editButton.hidden = false;
            };
            editButton.addEventListener('mousedown', (event) => event.stopPropagation());
            editButton.addEventListener('click', (event) => {
                event.stopPropagation();
                titleSpan.hidden = true;
                editButton.hidden = true;
                titleInput.hidden = false;
                titleInput.focus();
                titleInput.select();
            });
            titleInput.addEventListener('mousedown', (event) => event.stopPropagation());
            titleInput.addEventListener('blur', endEdit);
            titleInput.addEventListener('keydown', (event) => {
                if (event.key === 'Enter') {
                    event.preventDefault();
                    titleInput.blur();
                }
            });
        }
        const deleteButton = /** @type {HTMLElement | null} */ (el.querySelector('.widget-delete'));
        if (deleteButton) {
            deleteButton.addEventListener('click', async (event) => {
                event.stopPropagation();
                const confirmed = await pagesGridWindow.PHOTO_ORGANIZER.confirmDialog({
                    title: t('pages:widgets.deleteTitle'),
                    message: t('pages:widgets.deleteMessage'),
                    confirmText: t('common:delete'),
                    confirmClass: 'btn-danger'
                });
                if (!confirmed) return;
                // Delete from the version being edited (the working draft, or the
                // published version when idle) — never a guess.
                await fetch(
                    config.buildUrl('pages_version_delete_widget', {
                        page_id: pageId, version_id: generation.versionId, widget_id: deleteButton.dataset.widgetId
                    }),
                    { method: 'POST' }
                );
                grid.removeWidget(el);
                rendered.delete(deleteButton.dataset.widgetId ?? '');
                wideLayout.delete(deleteButton.dataset.widgetId ?? '');
            });
        }

        const moveWidget = (/** @type {number} */ offset) => {
            const nodes = [...grid.engine.nodes].sort((a, b) => a.y - b.y || a.x - b.x);
            const currentIndex = nodes.findIndex(node => node.el === el);
            const current = nodes[currentIndex];
            const target = nodes[currentIndex + offset];
            if (!current || !target) return;
            // Prefer GridStack's collision-aware swap primitive. Updating each item
            // independently makes the engine push the first move back because the
            // destination is still occupied, especially in the one-column layout.
            if (grid.engine.swap(current, target)) {
                grid._writePosAttr(current.el, current);
                grid._writePosAttr(target.el, target);
                grid._updateContainerHeight();
                grid._triggerChangeEvent();
            } else {
                // swap() refuses two items that are neither the same size nor
                // touching — and on an authoring canvas (float: true) a hole is
                // exactly what a delete or a shrink leaves behind. Exchange their
                // positions instead, inside a batch so the engine resolves the two
                // moves together rather than bouncing the first one back. A control
                // that quietly does nothing is worse than either outcome.
                const currentPosition = { x: current.x, y: current.y };
                const targetPosition = { x: target.x, y: target.y };
                grid.batchUpdate();
                grid.update(current.el, targetPosition);
                grid.update(target.el, currentPosition);
                grid.batchUpdate(false);
                if (currentBand && currentBand.columns === 1) grid.compact();
            }
            // Mirror the swap into the 12-column record. Without this a reorder made
            // on a phone would be discarded when the canvas widens again, because
            // the wide layout is what Save writes.
            const currentWide = wideLayout.get(current.id);
            const targetWide = wideLayout.get(target.id);
            if (currentWide && targetWide) {
                wideLayout.set(current.id, { ...currentWide, x: targetWide.x, y: targetWide.y });
                wideLayout.set(target.id, { ...targetWide, x: currentWide.x, y: currentWide.y });
            }
            // Keep focus on the control that was pressed, so a second tap repeats
            // the same move instead of landing on the Up button.
            /** @type {HTMLElement | null} */ (
                current.el?.querySelector(offset < 0 ? '.widget-order-up' : '.widget-order-down') ?? null
            )?.focus();
        };

        el.querySelectorAll('.widget-order').forEach((button) => {
            button.addEventListener('mousedown', (event) => event.stopPropagation());
        });
        el.querySelector('.widget-order-up')?.addEventListener('click', (event) => {
            event.stopPropagation();
            moveWidget(-1);
        });
        el.querySelector('.widget-order-down')?.addEventListener('click', (event) => {
            event.stopPropagation();
            moveWidget(1);
        });
        const resizeWidget = (/** @type {number} */ offset) => {
            const node = /** @type {any} */ (el).gridstackNode;
            if (!node) return;
            // The floor is the band's, not a bare 1: a widget must not be shrunk
            // below the height its reflowed content needs on this canvas.
            const floor = Math.max(1, node.minH || (currentBand ? currentBand.minRows : 1));
            const nextHeight = Math.max(floor, node.h + offset);
            if (nextHeight === node.h) return;
            grid.update(el, { h: nextHeight });
            // One column is a stack, not a canvas: with float: true the row a
            // shrink frees stays as a hole, which also parts two neighbours far
            // enough that GridStack's swap() refuses to reorder them — so the very
            // next press of Move down would do nothing at all.
            if (currentBand && currentBand.columns === 1) grid.compact();
            // The authored height moves by the same step, so the change survives
            // the trip back to a wide canvas.
            const wide = wideLayout.get(node.id);
            if (wide) wideLayout.set(node.id, { ...wide, h: Math.max(1, wide.h + offset) });
            /** @type {HTMLElement | null} */ (
                el.querySelector(offset < 0 ? '.widget-size-shorter' : '.widget-size-taller')
            )?.focus();
        };
        el.querySelector('.widget-size-shorter')?.addEventListener('click', (event) => {
            event.stopPropagation();
            resizeWidget(-1);
        });
        el.querySelector('.widget-size-taller')?.addEventListener('click', (event) => {
            event.stopPropagation();
            resizeWidget(1);
        });
    };

    /**
     * @param {string} html
     * @param {{ x?: number, y?: number }} [pos]
     */
    const addWidgetEl = (html, pos = {}) => {
        const wrapper = document.createElement('div');
        wrapper.innerHTML = html.trim();
        const el = /** @type {Element} */ (wrapper.firstElementChild);
        // The shell carries the widget's authored 12-column size; read it before
        // GridStack clamps the live node to the current band.
        const id = el.getAttribute('gs-id') ?? '';
        const authoredW = Number(el.getAttribute('gs-w')) || 4;
        const authoredH = Number(el.getAttribute('gs-h')) || 3;
        // Honor an explicit position from the model; otherwise place at the bottom
        // of the current (live) layout so it never collides with or displaces
        // existing widgets, regardless of unsaved moves.
        const x = Number.isInteger(pos.x) ? pos.x : 0;
        const y = Number.isInteger(pos.y) ? pos.y : grid.getRow();
        el.setAttribute('gs-x', String(x));
        el.setAttribute('gs-y', String(y));
        if (id) {
            wideLayout.set(id, {
                x: Number.isInteger(pos.x) ? /** @type {number} */ (pos.x) : 0,
                y: Number.isInteger(pos.y) ? /** @type {number} */ (pos.y) : wideBottom(),
                w: authoredW,
                h: authoredH
            });
        }
        grid.addWidget(el);
        // A widget added while the canvas is narrow still has to satisfy the band's
        // height floor, and must not carry the wide width into a one-column stack.
        if (currentBand && currentBand.columns !== MAX_COLUMNS) {
            applyingBand = true;
            applyBandGeometry(grid, wideLayout, currentBand);
            applyingBand = false;
        }
        wireWidget(el);
    };

    /** The y just below the authored layout — where a new, unplaced widget lands. */
    const wideBottom = () =>
        [...wideLayout.values()].reduce((bottom, item) => Math.max(bottom, item.y + item.h), 0);

    const newWidgetId = () =>
        (pagesGridWindow.crypto && crypto.randomUUID ? crypto.randomUUID() : String(Date.now())).replace(/-/g, '');

    // Manual add: an empty client-side draft — nothing is persisted until Save.
    const addWidget = () => addDraftWidget({
        id: newWidgetId(),
        title: t('pages:widgets.new'),
        data_query: {},
        html: '',
        css: '',
        js: '',
        grid_w: 8,
        grid_h: 4,
        state: {}
    });

    const scrollConversation = () => {
        if (messagesEl) messagesEl.scrollTop = messagesEl.scrollHeight;
    };

    // Render a widget's grid-item shell from its content (server resolves its data
    // and inlines the frame as srcdoc — nothing is persisted by this call).
    /** @param {Record<string, any>} content */
    const renderShell = async (content) => {
        const response = await fetch(config.buildUrl('pages_widget_preview', { page_id: pageId }), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(content)
        });
        return await response.text();
    };

    // Hold content as a manual draft and drop it on the grid (at the given position
    // if any, else the bottom).
    /** @param {Record<string, any>} content */
    const addDraftWidget = async (content) => {
        drafts.set(content.id, content);
        addWidgetEl(await renderShell(content), { x: content.grid_x, y: content.grid_y });
    };

    // Render content into the grid: swap an existing item's contents in place, or
    // add it if it isn't on the grid yet. Used by manual edits and by the locked
    // reconcile. The model may reposition on edit — if it supplied coordinates,
    // move the item; otherwise keep its grid position.
    /** @param {Record<string, any>} content */
    const renderWidget = async (content) => {
        const existing = document.querySelector(`.grid-stack-item[gs-id="${content.id}"]`);
        if (!existing) {
            addWidgetEl(await renderShell(content), { x: content.grid_x, y: content.grid_y });
            return;
        }
        const wrapper = document.createElement('div');
        wrapper.innerHTML = (await renderShell(content)).trim();
        /** @type {Element} */ (existing.querySelector('.grid-stack-item-content'))
            .replaceWith(/** @type {Element} */ (/** @type {Element} */ (wrapper.firstElementChild).querySelector('.grid-stack-item-content')));
        wireWidget(existing);
        if (Number.isInteger(content.grid_x) && Number.isInteger(content.grid_y)) {
            const wide = wideLayout.get(content.id);
            if (wide) wideLayout.set(content.id, { ...wide, x: content.grid_x, y: content.grid_y });
            // Only the wide canvas can honour a model-supplied x: a narrowed canvas
            // has no column to put it in, and applyBandGeometry owns the geometry
            // there.
            if (!currentBand || currentBand.columns === MAX_COLUMNS) {
                grid.update(existing, { x: content.grid_x, y: content.grid_y });
            }
        }
    };

    // ---- Generation ----
    const isRunning = () => {
        return generation.status === STATUS.IN_PROGRESS;
    }

    // Reflect the generation phase in the UI. Phase from `generation`:
    //   idle (none)            — normal editing; Save commits to the published page.
    //   running (IN_PROGRESS)  — the model is mutating the version: lock the grid and
    //                            Send; only Cancel is available; status bar ticks.
    //   review (READY/FAILED)  — the draft is the user's again: move widgets, send
    //                            follow-ups to iterate; Save publishes (READY only),
    //                            Cancel discards.
    const refreshUi = () => {
        const running = isRunning();
        if (designLayout) designLayout.classList.toggle('is-generating', running);
        grid.setStatic(running);
        // enableMove/enableResize are no-ops while the grid is static, so a band
        // change during a run would be dropped; re-assert the policy on unlock.
        if (!running) syncGridInteraction();
        if (addButton) addButton.disabled = running;
        if (cancelButton) cancelButton.disabled = !(generation.status === STATUS.FAILED || generation.status === STATUS.IN_PROGRESS);
        if (messageInput) messageInput.disabled = running;
        if (sendButton) sendButton.disabled = running;
        if (statusBar) statusBar.hidden = !running;
        // Save commits manual edits when idle, publishes a READY draft; disabled
        // while running or on a failed draft (nothing to publish).
        if (saveButton) saveButton.disabled = running || generation.status === STATUS.FAILED;
    };



    /**
     * @param {string | null} fromIso
     * @param {number} toMs
     */
    const formatElapsed = (fromIso, toMs) => {
        const start = fromIso ? new Date(fromIso).getTime() : toMs;
        const secs = Math.max(0, Math.round((toMs - start) / 1000));
        return `${Math.floor(secs / 60)}:${String(secs % 60).padStart(2, '0')}`;
    };

    const updateElapsed = () => {
        if (elapsedEl && isRunning()) elapsedEl.textContent = formatElapsed(generation.startedAt, Date.now());
    };

    // Rebuild the conversation feed from the polled transcript (the source of truth
    // while generating): user / assistant bubbles and interleaved status / error
    // lines. #TODO refactor to use chat_dialog component
    /** @param {FeedMessage[]} messages */
    const renderFeed = (messages) => {
        if (!messagesEl) return;
        messagesEl.innerHTML = '';
        for (const message of messages) {
            const el = document.createElement('div');
            el.className = `chat-message chat-message-${message.type}`;
            el.textContent = message.content;
            messagesEl.appendChild(el);
        }
        scrollConversation();
    };

    // Reconcile the grid to the version's widget set: drop widgets no longer
    // present, (re-)render those that are new or changed.
    /** @param {Record<string, any>[]} widgets */
    const reconcileWidgets = async (widgets) => {
        const incoming = new Set(widgets.map((w) => w.id));
        grid.engine.nodes.slice().forEach((/** @type {any} */ node) => {
            if (!incoming.has(node.id)) {
                grid.removeWidget(node.el);
                rendered.delete(node.id);
                wideLayout.delete(node.id);
            }
        });
        for (const widget of widgets) {
            const signature = JSON.stringify(widget);
            if (rendered.get(widget.id) === signature) continue;
            rendered.set(widget.id, signature);
            await renderWidget(widget);
        }
    };

    const stopPolling = () => {
        if (!generation) return;
        clearTimeout(generation.pollTimer);
        clearInterval(generation.elapsedTimer);
    };

    /** @param {VersionStatusBody} body */
    const applyStatus = async (body) => {
        generation.status = body.status;
        generation.startedAt = body.started_at;
        renderFeed(body.messages);
        await reconcileWidgets(body.widgets);
        if (body.status === STATUS.CANCELLED) {
            // The version was deleted out from under us; revert to the published page.
            window.location.reload();
            return;
        }
        if (body.status !== STATUS.IN_PROGRESS) stopPolling();  // READY / FAILED -> review
        refreshUi();
    };

    const poll = async () => {
        try {
            const url = config.buildUrl('pages_version_status', { page_id: pageId, version_id: generation.versionId });
            const response = await fetch(url);
            if (response.status === 404) { window.location.reload(); return; }
            const body = await response.json();
            await applyStatus(body);
            if (body.status === STATUS.IN_PROGRESS) generation.pollTimer = setTimeout(poll, POLL_INTERVAL_MS);
        } catch {
            generation.pollTimer = setTimeout(poll, POLL_RETRY_MS);
        }
    };

    // Enter the running phase for a new or continued generation: lock, restart the
    // elapsed timer, and poll. Reconcile re-renders the grid from the version's poll,
    // so a follow-up's committed manual edits come back from the server.
    /** @param {number} versionId */
    const enterRunning = (versionId) => {
        stopPolling();  // a follow-up re-enters; clear any prior timers first
        generation = { versionId, status: STATUS.IN_PROGRESS, startedAt: null };
        rendered.clear();
        refreshUi();
        updateElapsed();
        generation.elapsedTimer = setInterval(updateElapsed, 1000);
        poll();
    };

    const publishVersion = async () => {
        const response = await fetch(
            config.buildUrl('pages_version_publish', { page_id: pageId, version_id: generation.versionId }),
            {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // Commit manual moves made during review before publishing.
                body: JSON.stringify({ widgets: getWidgets() })
            }
        );
        if (!response.ok) return;
        window.location.href = config.buildUrl('pages_detail', { page_id: pageId });
    };

    const cancelGeneration = async () => {
        const confirmed = await pagesGridWindow.PHOTO_ORGANIZER.confirmDialog({
            title: t('components:chat.cancelGeneration'),
            message: t('pages:chat.cancelMessage'),
            confirmText: t('components:chat.cancelGeneration'),
            confirmClass: 'btn-danger'
        });
        if (!confirmed) return;
        stopPolling();
        await fetch(
            config.buildUrl('pages_version_cancel', { page_id: pageId, version_id: generation.versionId }),
            { method: 'POST' }
        );
        window.location.reload();  // back to the published version
    };

    const onSave = () => {
        // While a finished generation is under review, Save publishes it; otherwise
        // it commits manual edits.
        if (generation.status === STATUS.READY) return publishVersion();
        return savePage();
    };

    /** @param {Event} event */
    const sendMessage = async (event) => {
        event.preventDefault();
        if (generation.status === STATUS.IN_PROGRESS) return;  // a run is active
        if (!messageInput) return;
        const message = messageInput.value.trim();
        if (!message) return;
        messageInput.value = '';
        try {
            const response = await fetch(config.buildUrl('pages_chat', { page_id: pageId }), {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // Send the current widget set (drafts + layout) so the server captures
                // manual edits — on the initial fork and on every follow-up — and the
                // model edits with sight of the code.
                body: JSON.stringify({
                    message,
                    widgets: getWidgets(),
                    widget_errors: pagesGridWindow.PHOTO_ORGANIZER.widgetErrors
                })
            });
            if (!response.ok) {
                const body = await response.json().catch(() => ({}));
                pagesGridWindow.notification.error(body.error || t('components:chat.startFailed'));
                messageInput.value = message;  // let the user retry
                return;
            }
            const { version_id } = await response.json();
            enterRunning(version_id);
        } catch {
            pagesGridWindow.notification.error(t('components:chat.startFailed'));
            messageInput.value = message;
        }
    };

    grid.on('dragstart resizestart', () => gridEl.classList.add('is-interacting'));
    grid.on('dragstop resizestop', () => gridEl.classList.remove('is-interacting'));

    if (addButton) addButton.addEventListener('click', addWidget);
    if (saveButton) saveButton.addEventListener('click', onSave);
    if (cancelButton) cancelButton.addEventListener('click', cancelGeneration);

    const conversationForm = document.getElementById('conversation-form');
    if (conversationForm) conversationForm.addEventListener('submit', sendMessage);

    grid.engine.nodes.forEach((/** @type {any} */ node) => wireWidget(node.el));
    scrollConversation();
    refreshUi();  // initial (idle) button state

    // Resume: the edit version is a working draft (status is not ACCEPTED) — re-enter
    // and poll; the first response settles the phase (running vs. ready/failed review).
    if (startStatus !== STATUS.ACCEPTED) enterRunning(editVersionId);

    // getVersionId lets the widget broker target the version currently being edited
    // (it changes when a draft is forked, so it's read on demand, not captured).
    return { grid, getVersionId: () => generation.versionId };
};
