// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

window.PHOTO_ORGANIZER.COMPONENTS.initNavPagesBar = () => {
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
    const narrow = window.matchMedia('(max-width: 1200px)');
    const contextEntries = Array.from(
        document.querySelectorAll('[data-nav-panel-toggle]')
    ).flatMap((candidate) => {
        if (!(candidate instanceof HTMLButtonElement)) return [];
        const panelId = candidate.getAttribute('aria-controls');
        const panel = panelId ? document.getElementById(panelId) : null;
        if (!panel) return [];
        const marker = document.createComment(`nav panel: ${panelId}`);
        panel.before(marker);
        return [{ toggle: candidate, panel, marker }];
    });

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
        const hasOpenPanel = contextEntries.some(
            (entry) => entry.toggle.getAttribute('aria-expanded') === 'true'
        );
        contextHost.hidden = !hasOpenPanel;
        navbar.classList.toggle('is-context-open', hasOpenPanel);
        syncNavbarHeight();
    };

    /**
     * @param {{ toggle: HTMLButtonElement, panel: HTMLElement, marker: Comment }} entry
     * @param {boolean} open
     */
    const applyContextPanel = (entry, open) => {
        const isOpen = narrow.matches && open;
        entry.toggle.hidden = !narrow.matches;
        entry.toggle.setAttribute('aria-expanded', String(isOpen));
        entry.toggle.classList.toggle('is-open', isOpen);
        entry.panel.hidden = narrow.matches && !isOpen;
    };

    const closeContextPanels = () => {
        contextEntries.forEach((entry) => applyContextPanel(entry, false));
        syncContextHost();
    };

    const syncMode = () => {
        applyMenu(false);
        contextEntries.forEach((entry) => {
            applyContextPanel(entry, false);
            if (narrow.matches) {
                contextHost.appendChild(entry.panel);
            } else if (entry.marker.parentNode) {
                entry.marker.parentNode.insertBefore(entry.panel, entry.marker.nextSibling);
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

    contextEntries.forEach((entry) => {
        entry.toggle.addEventListener('click', () => {
            const open = entry.toggle.getAttribute('aria-expanded') !== 'true';
            applyMenu(false);
            contextEntries.forEach((candidate) => {
                applyContextPanel(candidate, candidate === entry && open);
            });
            syncContextHost();
        });
    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape') return;
        if (navbar.classList.contains('is-menu-open')) {
            applyMenu(false);
            menuToggle.focus();
            return;
        }
        const openContext = contextEntries.find(
            (entry) => entry.toggle.getAttribute('aria-expanded') === 'true'
        );
        if (!openContext) return;
        closeContextPanels();
        openContext.toggle.focus();
    });

    document.addEventListener('click', (event) => {
        if (!narrow.matches) return;
        const hasOpenContext = contextEntries.some(
            (entry) => entry.toggle.getAttribute('aria-expanded') === 'true'
        );
        if (!navbar.classList.contains('is-menu-open') && !hasOpenContext) return;
        if (navbar.contains(/** @type {Node} */ (event.target))) return;
        applyMenu(false);
        closeContextPanels();
    });

    narrow.addEventListener('change', syncMode);
    syncMode();

    window.addEventListener('resize', syncNavbarHeight);

    return { syncNavbarHeight, applyMenu, closeContextPanels };
};
