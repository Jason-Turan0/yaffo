// @ts-check

/**
 * @typedef {Object<string, Object<string, unknown>>} TranslationCatalog
 *
 * @typedef {Object} I18nextLike
 * @property {(options: {
 *   lng: string,
 *   fallbackLng: string,
 *   resources: Record<string, TranslationCatalog>,
 *   ns: string[],
 *   defaultNS: string,
 *   interpolation: { escapeValue: boolean },
 * }) => Promise<unknown>} init
 * @property {(key: string, options?: Record<string, unknown>) => string} t
 */

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

/**
 * Load translation catalogs and return the shared i18n formatting service.
 *
 * @param {I18nConfig} config
 * @returns {Promise<I18nService>}
 */
const initI18n = async (config) => {
    const locale = config.locale;
    const resourceUrl = config.resourceUrl.replace('__lng__', locale);
    const fallbackUrl = config.resourceUrl.replace('__lng__', config.fallbackLocale);
    /**
     * @param {string} url
     * @returns {Promise<TranslationCatalog>}
     */
    const loadCatalog = async (url) => {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Failed to load translations: ${response.status}`);
        return response.json();
    };

    /** @type {TranslationCatalog} */
    let catalog;
    try {
        catalog = await loadCatalog(resourceUrl);
    } catch (error) {
        console.error(error);
        catalog = {};
    }
    /** @type {TranslationCatalog} */
    const fallbackCatalog = locale === config.fallbackLocale ? catalog : await loadCatalog(fallbackUrl);

    /** @type {I18nextLike} */
    const i18next = window.i18next;
    await i18next.init({
        lng: locale,
        fallbackLng: config.fallbackLocale,
        resources: {
            [locale]: catalog,
            [config.fallbackLocale]: fallbackCatalog
        },
        ns: Object.keys(fallbackCatalog),
        defaultNS: 'common',
        interpolation: {
            escapeValue: false
        }
    });

    return {
        locale,
        t: (key, options = {}) => i18next.t(key, options),
        number: (value, options = {}) => new Intl.NumberFormat(locale, options).format(value),
        percent: (value, options = {}) =>
            new Intl.NumberFormat(locale, { style: 'percent', ...options }).format(value),
        date: (value, options = {}) => new Intl.DateTimeFormat(locale, options).format(new Date(value)),
        relativeTime: (value, unit, options = {}) =>
            new Intl.RelativeTimeFormat(locale, options).format(value, unit),
        list: (values, options = {}) => new Intl.ListFormat(locale, options).format(values)
    };
};

window.PHOTO_ORGANIZER.initI18n = initI18n;

window.PHOTO_ORGANIZER.i18nReady = initI18n(window.APP_CONFIG.i18n)
    .then((service) => {
        window.PHOTO_ORGANIZER.i18n = service;
        document.dispatchEvent(new CustomEvent('yaffo:i18n-ready', {detail: service}));
        return service;
    });
