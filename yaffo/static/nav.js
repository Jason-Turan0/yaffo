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
    if (!navbar || !toggle || !bar || !menuToggle || !primary) return;

    const STORAGE_KEY = 'yaffo.pagesBarHidden';
    const narrow = window.matchMedia('(max-width: 1200px)');

    // Sticky panels offset themselves from the navbar's real height (it varies
    // by theme and with the pages strip shown/hidden), published as a variable.
    const syncNavbarHeight = () => {
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

    const syncMode = () => applyMenu(false);

    apply(localStorage.getItem(STORAGE_KEY) === '1');

    toggle.addEventListener('click', () => {
        const hidden = !bar.hidden;
        localStorage.setItem(STORAGE_KEY, hidden ? '1' : '0');
        apply(hidden);
    });

    menuToggle.addEventListener('click', () => {
        applyMenu(!navbar.classList.contains('is-menu-open'));
        if (navbar.classList.contains('is-menu-open')) {
            primary.querySelector('a')?.focus();
        }
    });

    document.addEventListener('keydown', (event) => {
        if (event.key !== 'Escape' || !navbar.classList.contains('is-menu-open')) return;
        applyMenu(false);
        menuToggle.focus();
    });

    document.addEventListener('click', (event) => {
        if (!narrow.matches || !navbar.classList.contains('is-menu-open')) return;
        if (navbar.contains(/** @type {Node} */ (event.target))) return;
        applyMenu(false);
    });

    narrow.addEventListener('change', syncMode);
    applyMenu(false);

    window.addEventListener('resize', syncNavbarHeight);

    return { syncNavbarHeight, applyMenu };
};
