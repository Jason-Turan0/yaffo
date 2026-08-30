// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

const app = window.PHOTO_ORGANIZER;

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
    components.responsivePanels = components.initResponsivePanels?.();
    components.fileBrowser?.init?.();
    components.multiSelect?.initAll?.();
    components.searchableSelect?.initAll?.(i18n);
    components.intlDateInput?.initAll?.(i18n);
    components.percentageSlider?.initAll?.();
    components?.initCronBuilder?.({i18n, document: window.document});
};

const initBasePageBehavior = () => {
    document.querySelectorAll('.alert').forEach((alert) => {
        setTimeout(() => {
            app.closeAlert?.(alert.querySelector('.alert-close'));
        }, 5000);
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
