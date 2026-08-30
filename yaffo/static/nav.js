// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

/**
 * Shared navbar behavior: the Pages strip toggle, the narrow-screen Menu, and
 * the peer page panels that replace every desktop sidebar below the breakpoint.
 *
 * The panel contract lives in docs/development/responsive.md and is declared in
 * templates/components/nav_panel.html. In short: a panel is `[data-nav-panel]`
 * with an id, its peer button is `[data-nav-panel-toggle][aria-controls=<id>]`
 * rendered server-side into the navbar, only one of Menu or one panel is open
 * at a time, and the *live* panel DOM moves between its desktop slot and the
 * navbar host so form values and component state survive a resize.
 */
window.PHOTO_ORGANIZER.COMPONENTS.initNavPagesBar = () => {
    // The single source of truth for "the shell is narrow". Structural layout
    // keys off the same width in responsive.css; nothing else should hardcode it.
    const NARROW_QUERY = '(max-width: 1200px)';

    const navbar = /** @type {HTMLElement | null} */ (document.querySelector('.navbar'));
    const menuToggle = /** @type {HTMLButtonElement | null} */ (
        document.getElementById('nav-menu-toggle')
    );
    const primary = /** @type {HTMLElement | null} */ (
        document.getElementById('navbar-primary')
    );
    const toggle = /** @type {HTMLElement | null} */ (
        document.getElementById('nav-pages-toggle')
    );
    const bar = /** @type {HTMLElement | null} */ (
        document.getElementById('navbar-pages-bar')
    );
    const contextHost = /** @type {HTMLElement | null} */ (
        document.getElementById('navbar-context-panels')
    );
    if (!navbar || !toggle || !bar || !menuToggle || !primary || !contextHost) return;

    const STORAGE_KEY = 'yaffo.pagesBarHidden';
    const narrow = window.matchMedia(NARROW_QUERY);

    // A panel's element is resolved by id on every use rather than captured:
    // htmx swaps some panels (the sharing device list re-fetches itself), which
    // replaces the node the entry was built from. The marker comment is a stable
    // sibling of the desktop slot, so restoring never depends on that node.
    const entries = Array.from(
        document.querySelectorAll('[data-nav-panel-toggle]')
    ).flatMap((candidate) => {
        if (!(candidate instanceof HTMLButtonElement)) return [];
        const panelId = candidate.getAttribute('aria-controls');
        const panel = panelId ? document.getElementById(panelId) : null;
        if (!panelId || !panel) return [];
        const marker = document.createComment(`nav panel: ${panelId}`);
        panel.before(marker);
        return [{ toggle: candidate, panelId, marker }];
    });

    /** @param {{ panelId: string }} entry */
    const panelOf = (entry) => document.getElementById(entry.panelId);

    // Sticky panels offset themselves from the navbar's real height (it varies
    // by theme and with the pages strip shown/hidden), published as a variable.
    const syncNavbarHeight = () => {
        const navbarBar = navbar.querySelector('.navbar-container');
        if (navbarBar) {
            document.documentElement.style.setProperty(
                '--navbar-bar-height', `${navbarBar.getBoundingClientRect().height}px`
            );
        }
        document.documentElement.style.setProperty('--navbar-height', navbar.offsetHeight + 'px');
    };

    /**
     * @param {boolean} hidden
     */
    const apply = (hidden) => {
        bar.hidden = hidden;
        toggle.setAttribute('aria-expanded', String(!hidden));
        toggle.classList.toggle('collapsed', hidden);
        syncNavbarHeight();
    };

    /** @param {boolean} open */
    const applyMenu = (open) => {
        const isOpen = narrow.matches && open;
        navbar.classList.toggle('is-menu-open', isOpen);
        menuToggle.setAttribute('aria-expanded', String(isOpen));
        primary.hidden = narrow.matches && !isOpen;
        document.body.classList.toggle('nav-menu-open', isOpen);
        syncNavbarHeight();
    };

    const syncContextHost = () => {
        const hasOpenPanel = entries.some(
            (entry) => entry.toggle.getAttribute('aria-expanded') === 'true'
        );
        contextHost.hidden = !hasOpenPanel;
        navbar.classList.toggle('is-context-open', hasOpenPanel);
        document.body.classList.toggle('nav-panel-open', hasOpenPanel);
        syncNavbarHeight();
    };

    /**
     * @param {{ toggle: HTMLButtonElement, panelId: string, marker: Comment }} entry
     * @param {boolean} open
     */
    const applyContextPanel = (entry, open) => {
        const isOpen = narrow.matches && open;
        const panel = panelOf(entry);
        entry.toggle.hidden = !narrow.matches;
        entry.toggle.setAttribute('aria-expanded', String(isOpen));
        entry.toggle.classList.toggle('is-open', isOpen);
        if (panel) panel.hidden = narrow.matches && !isOpen;
    };

    /**
     * Did this click start inside the navbar?
     *
     * Asked of the event's *path*, not of `event.target`, because a control
     * inside a panel may rebuild itself while the click is still bubbling: the
     * searchable select re-renders its option list on `change`, which detaches
     * the very node that was clicked. By the time this document-level listener
     * runs, `navbar.contains(target)` is false for a node that was inside the
     * navbar a moment ago — and the panel would close out from under the user
     * as they picked a filter value. The path is computed at dispatch, so it
     * still names the original ancestors.
     * @param {Event} event
     */
    const isInsideNavbar = (event) => {
        const path = typeof event.composedPath === 'function' ? event.composedPath() : [];
        return path.length
            ? path.includes(navbar)
            : navbar.contains(/** @type {Node} */ (event.target));
    };

    const openContextEntries = () => entries.filter(
        (entry) => entry.toggle.getAttribute('aria-expanded') === 'true'
    );

    const closeContextPanels = () => {
        entries.forEach((entry) => applyContextPanel(entry, false));
        syncContextHost();
    };

    /**
     * Park every panel on the correct side of the breakpoint. Panels are moved,
     * never cloned, so a resize keeps entered values, selected options, and any
     * component instances bound to those nodes.
     */
    const syncMode = () => {
        applyMenu(false);
        entries.forEach((entry) => {
            applyContextPanel(entry, false);
            const panel = panelOf(entry);
            if (!panel) return;
            if (narrow.matches) {
                contextHost.appendChild(panel);
            } else if (entry.marker.parentNode) {
                entry.marker.parentNode.insertBefore(panel, entry.marker.nextSibling);
            }
        });
        syncContextHost();
    };

    apply(localStorage.getItem(STORAGE_KEY) === '1');

    toggle.addEventListener('click', () => {
        const hidden = !bar.hidden;
        localStorage.setItem(STORAGE_KEY, hidden ? '1' : '0');
        apply(hidden);
    });

    menuToggle.addEventListener('click', () => {
        const open = !navbar.classList.contains('is-menu-open');
        if (open) closeContextPanels();
        applyMenu(open);
        if (navbar.classList.contains('is-menu-open')) {
            primary.querySelector('a')?.focus();
        }
    });

    entries.forEach((entry) => {
        entry.toggle.addEventListener('click', () => {
            const open = entry.toggle.getAttribute('aria-expanded') !== 'true';
            applyMenu(false);
            entries.forEach((candidate) => {
                applyContextPanel(candidate, candidate === entry && open);
            });
            syncContextHost();
        });
    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        // Escape belongs to the topmost surface. A dialog opened from inside a
        // panel (the filter configurator, say) would otherwise be dismissed
        // together with the panel hosting it, and the panel's focus return
        // would overwrite the dialog's — leaving focus on the wrong control.
        if (document.querySelector('.modal.active')) return;
        if (navbar.classList.contains('is-menu-open')) {
            applyMenu(false);
            menuToggle.focus();
            return;
        }
        const [openContext] = openContextEntries();
        if (!openContext) return;
        closeContextPanels();
        openContext.toggle.focus();
    });

    document.addEventListener('click', (event) => {
        if (!narrow.matches) return;
        const menuOpen = navbar.classList.contains('is-menu-open');
        const [openContext] = openContextEntries();
        if (!menuOpen && !openContext) return;
        if (isInsideNavbar(event)) return;
        applyMenu(false);
        closeContextPanels();
    });

    // An htmx swap can replace a panel's element wholesale (and with it the
    // server's closed/open markup). Re-park only when the swap actually touched
    // a panel, so unrelated swaps elsewhere on the page don't close the Menu.
    document.body.addEventListener('htmx:afterSwap', (event) => {
        const target = /** @type {any} */ (event).detail?.target;
        if (!(target instanceof HTMLElement)) return;
        const touchesPanel = entries.some((entry) => {
            const panel = panelOf(entry);
            return !!panel && (target === panel || target.contains(panel));
        });
        if (touchesPanel) syncMode();
    });

    narrow.addEventListener('change', syncMode);
    syncMode();

    window.addEventListener('resize', syncNavbarHeight);

    return { syncNavbarHeight, applyMenu, closeContextPanels, syncMode, NARROW_QUERY };
};
