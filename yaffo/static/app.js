// @ts-check

/**
 * @typedef {Object} I18nService
 * @property {(key: string, options?: Record<string, unknown>) => string} t
 *
 * @typedef {Object} ComponentInitContext
 * @property {PhotoOrganizerApp} app
 * @property {I18nService} i18n
 * @property {typeof window.APP_CONFIG} config
 *
 * @typedef {Object} PhotoOrganizerComponents
 * @property {() => void} [initAll]
 * @property {{ init?: () => void }} [fileBrowser]
 * @property {{ initAll?: (i18n: I18nService, root?: ParentNode) => unknown }} [intlDateInput]
 * @property {{ initAll?: () => void }} [percentageSlider]
 *
 * @typedef {Object} PhotoOrganizerApp
 * @property {Promise<void>} domReady
 * @property {Promise<I18nService>} i18nReady
 * @property {Promise<PhotoOrganizerApp>} [appReady]
 * @property {I18nService} [i18n]
 * @property {PhotoOrganizerComponents} COMPONENTS
 * @property {{ initImageFallbacks: () => void }} utils
 * @property {() => unknown} [initNavPagesBar]
 * @property {unknown} [navPagesBar]
 * @property {() => Promise<PhotoOrganizerApp>} [initApp]
 * @property {(button: Element | null) => void} [closeAlert]
 */

const appWindow = /** @type {Window & {
    APP_CONFIG: { i18n: unknown },
    PHOTO_ORGANIZER: PhotoOrganizerApp,
    closeAlert?: (button: Element | null) => void,
}} */ (/** @type {unknown} */ (window));

appWindow.PHOTO_ORGANIZER = appWindow.PHOTO_ORGANIZER || {};
appWindow.PHOTO_ORGANIZER.COMPONENTS = appWindow.PHOTO_ORGANIZER.COMPONENTS || {};

const app = appWindow.PHOTO_ORGANIZER;

app.domReady = app.domReady || new Promise((resolve) => {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => resolve(), { once: true });
        return;
    }
    resolve();
});

app.closeAlert = (button) => {
    if (!button) return;
    const alert = button.closest('.alert');
    if (!alert) return;
    alert.classList.add('fade-out');
    setTimeout(() => {
        alert.remove();
    }, 300);
};

appWindow.closeAlert = app.closeAlert;

app.COMPONENTS.initAll = () => {
    const components = app.COMPONENTS;
    const i18n = /** @type {I18nService} */ (app.i18n);

    components.fileBrowser?.init?.();
    components.intlDateInput?.initAll?.(i18n);
    components.percentageSlider?.initAll?.();
    components?.initCronBuilder?.({i18n, document: appWindow.document});
};

const initBasePageBehavior = () => {
    document.querySelectorAll('.alert').forEach((alert) => {
        setTimeout(() => {
            app.closeAlert?.(alert.querySelector('.alert-close'));
        }, 5000);
    });

    app.utils.initImageFallbacks();

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
        app.navPagesBar = app.initNavPagesBar?.();
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
