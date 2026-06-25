window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

window.PHOTO_ORGANIZER.initI18n = async (config) => {
    const locale = config.locale;
    const resourceUrl = config.resourceUrl.replace('__lng__', locale);
    const fallbackUrl = config.resourceUrl.replace('__lng__', config.fallbackLocale);
    const loadCatalog = async (url) => {
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Failed to load translations: ${response.status}`);
        return response.json();
    };

    let catalog;
    let fallbackCatalog;
    try {
        catalog = await loadCatalog(resourceUrl);
    } catch (error) {
        console.error(error);
        catalog = {};
    }
    fallbackCatalog = locale === config.fallbackLocale ? catalog : await loadCatalog(fallbackUrl);

    await window.i18next.init({
        lng: locale,
        fallbackLng: config.fallbackLocale,
        resources: {
            [locale]: catalog,
            [config.fallbackLocale]: fallbackCatalog
        },
        ns: Object.keys(fallbackCatalog),
        defaultNS: 'common'
    });

    return {
        locale,
        t: (key, options = {}) => window.i18next.t(key, options),
        number: (value, options = {}) => new Intl.NumberFormat(locale, options).format(value),
        percent: (value, options = {}) =>
            new Intl.NumberFormat(locale, { style: 'percent', ...options }).format(value),
        date: (value, options = {}) => new Intl.DateTimeFormat(locale, options).format(new Date(value)),
        relativeTime: (value, unit, options = {}) =>
            new Intl.RelativeTimeFormat(locale, options).format(value, unit),
        list: (values, options = {}) => new Intl.ListFormat(locale, options).format(values)
    };
};

window.PHOTO_ORGANIZER.i18nReady = window.PHOTO_ORGANIZER.initI18n(window.APP_CONFIG.i18n)
    .then((service) => {
        window.PHOTO_ORGANIZER.i18n = service;
        document.dispatchEvent(new CustomEvent('yaffo:i18n-ready'));
        return service;
    });
