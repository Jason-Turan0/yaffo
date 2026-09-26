// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

const app = window.PHOTO_ORGANIZER;

// Flashes close themselves after this long; one with an action (e.g. "Ask Yaffo")
// gets longer, to be reached.
const ALERT_DISMISS_MS = 5000;
const ACTION_ALERT_DISMISS_MS = 10000;

app.domReady = app.domReady || new Promise((resolve) => {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => resolve(), { once: true });
        return;
    }
    resolve();
});

app.closeAlert = (/** @type {Element | null} */ button) => {
    if (!button) return;
    const alert = button.closest('.alert');
    if (!alert) return;
    alert.classList.add('fade-out');
    setTimeout(() => {
        alert.remove();
    }, 300);
};

window.closeAlert = app.closeAlert;

app.COMPONENTS.initAll = () => {
    const components = app.COMPONENTS;
    const i18n = /** @type {I18nService} */ (app.i18n);

    components.navPagesBar = components.initNavPagesBar?.();
    components.fileBrowser?.init?.();
    components.multiSelect?.initAll?.();
    components.searchableSelect?.initAll?.(i18n);
    components.intlDateInput?.initAll?.(i18n);
    components.percentageSlider?.initAll?.();
    components?.initCronBuilder?.({i18n, document: window.document});
};

const initBasePageBehavior = () => {
    document.querySelectorAll('.alert').forEach((alert) => {
        const hasAction = alert.querySelector('.message-action') !== null;
        const timer = setTimeout(() => {
            app.closeAlert?.(alert.querySelector('.alert-close'));
        }, hasAction ? ACTION_ALERT_DISMISS_MS : ALERT_DISMISS_MS);
        // Reaching one with an action (pointer or keyboard) keeps it until closed.
        if (hasAction) {
            const keep = () => clearTimeout(timer);
            alert.addEventListener('mouseenter', keep, { once: true });
            alert.addEventListener('focusin', keep, { once: true });
        }
    });

    app.utils?.initImageFallbacks?.();
    app.utils?.initLocalDateTimes?.();

    const activePageTab = document.querySelector('.navbar-pages .nav-page-tab.active');
    if (activePageTab) {
        activePageTab.closest('.nav-page-li')?.scrollIntoView({ block: 'nearest', inline: 'nearest' });
    }
};

app.initApp = () => {
    if (app.appReady) {
        return app.appReady;
    }

    app.appReady = Promise.all([
        app.domReady,
        app.i18nReady,
    ]).then(([, i18n]) => {
        app.i18n = i18n;
        app.COMPONENTS.initAll?.();
        initBasePageBehavior();

        document.dispatchEvent(new CustomEvent('yaffo:app-init-complete', {
            detail: {
                app,
                PHOTO_ORGANIZER: app,
            },
        }));

        return app;
    });

    return app.appReady;
};

app.initApp();
