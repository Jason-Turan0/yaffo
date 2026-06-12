window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

window.PHOTO_ORGANIZER.initNavPagesBar = () => {
    const navbar = document.querySelector('.navbar');
    const toggle = document.getElementById('nav-pages-toggle');
    const bar = document.getElementById('navbar-pages-bar');
    if (!navbar || !toggle || !bar) return;

    const STORAGE_KEY = 'yaffo.pagesBarHidden';

    // Sticky panels offset themselves from the navbar's real height (it varies
    // by theme and with the pages strip shown/hidden), published as a variable.
    const syncNavbarHeight = () => {
        document.documentElement.style.setProperty('--navbar-height', navbar.offsetHeight + 'px');
    };

    const apply = (hidden) => {
        bar.hidden = hidden;
        toggle.setAttribute('aria-expanded', String(!hidden));
        toggle.classList.toggle('collapsed', hidden);
        syncNavbarHeight();
    };

    apply(localStorage.getItem(STORAGE_KEY) === '1');

    toggle.addEventListener('click', () => {
        const hidden = !bar.hidden;
        localStorage.setItem(STORAGE_KEY, hidden ? '1' : '0');
        apply(hidden);
    });

    window.addEventListener('resize', syncNavbarHeight);

    return { syncNavbarHeight };
};