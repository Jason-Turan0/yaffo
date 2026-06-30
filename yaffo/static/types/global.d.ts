type I18nConfig = {
    locale: string;
    fallbackLocale: string;
    resourceUrl: string;
};

type I18nService = {
    locale: string;
    t(key: string, options?: Record<string, unknown>): string;
    number(value: number, options?: Intl.NumberFormatOptions): string;
    percent(value: number, options?: Intl.NumberFormatOptions): string;
    date(value: string | number | Date, options?: Intl.DateTimeFormatOptions): string;
    relativeTime(
        value: number,
        unit: Intl.RelativeTimeFormatUnit,
        options?: Intl.RelativeTimeFormatOptions,
    ): string;
    list(values: Iterable<string>, options?: Intl.ListFormatOptions): string;
};

type TranslationCatalog = Record<string, Record<string, unknown>>;

type I18nextLike = {
    init(options: {
        lng: string;
        fallbackLng: string;
        resources: Record<string, TranslationCatalog>;
        ns: string[];
        defaultNS: string;
        interpolation: { escapeValue: boolean };
    }): Promise<unknown>;
    t(key: string, options?: Record<string, unknown>): string;
};

type AppConfig = {
    urls: Record<string, string>;
    buildUrl(endpoint: string, params?: Record<string, string | number | undefined>): string;
    i18n: I18nConfig;
};

type NotificationType = 'success' | 'error' | 'warning' | 'info';

type NotificationApi = {
    show(message: string, type?: NotificationType, duration?: number): void;
    hide(): void;
    flash(message: string, type?: NotificationType, duration?: number): void;
    showPendingFlash(): void;
    success(message: string, duration?: number): void;
    error(message: string, duration?: number): void;
    warning(message: string, duration?: number): void;
    info(message: string, duration?: number): void;
};

type NavPagesBarApi = {
    syncNavbarHeight(): void;
};

type ModalControl = {
    element: HTMLElement;
    formElement: HTMLFormElement | null;
    close(): void;
    open(): void;
    setFormAction(url: string): void;
};

type ModalApi = {
    init(modalId: string): ModalControl;
};

type DateUtils = {
    format(isoDate: string | null | undefined, options?: Intl.DateTimeFormatOptions): string;
    formatWithTime(isoDate: string | null | undefined, options?: Intl.DateTimeFormatOptions): string;
    formatRelative(isoDate: string | null | undefined): string;
};

type UtilsNamespace = {
    locale?: string;
    initImageFallbacks?: () => void;
    date?: DateUtils;
};

type IntlDateInputControl = {
    setValue(isoValue: string | null | undefined): void;
    sync(): boolean;
};

type IntlDateInputApi = {
    formatValue(isoValue: string | null | undefined, locale: string): string;
    formatPartial(rawValue: string, locale: string): string;
    init(root: HTMLElement, i18n: I18nService): IntlDateInputControl;
    initAll(i18n: I18nService, root?: ParentNode): IntlDateInputControl[];
    parseDate(rawValue: string, locale: string): string | null;
    placeholder(locale: string): string;
};

type PercentageSliderApi = {
    init(sliderDom: Element): void;
    initAll(): void;
};

type MultiSelectApi = {
    initAll(): void;
};

type SearchableSelectApi = {
    initAll(i18n: I18nService): void;
};

type CronBuilderDeps = {
    i18n: I18nService;
    document?: Document;
};

type CronBuilderApi = {
    initAll(scope?: Document | Element): void;
    describeCron(cron?: string): string;
    reset(root: CronBuilderRoot | null): void;
    setCron(root: CronBuilderRoot | null, cron: string): void;
};

type PhotoOrganizerComponents = {
    initAll?: () => void;
    fileBrowser?: { init?: () => void };
    modal: ModalApi;
    initNavPagesBar?: () => NavPagesBarApi | undefined;
    navPagesBar?: NavPagesBarApi;
    intlDateInput?: IntlDateInputApi;
    multiSelect?: MultiSelectApi;
    searchableSelect?: SearchableSelectApi;
    percentageSlider?: PercentageSliderApi;
    cronBuilder?: CronBuilderApi;
    createCronBuilder: (deps: CronBuilderDeps) => CronBuilderApi;
    initCronBuilder?: (deps: CronBuilderDeps) => CronBuilderApi;
};

type FavoriteNamespace = {
    init?(i18n: I18nService, config: AppConfig): void;
};

type MediaNamespace = {
    favorite?: FavoriteNamespace;
    initGalleryVideos?: (i18n: I18nService, config: AppConfig) => void;
};

type TagsFilterApi = {
    loadTagValues(tagName: string, selectedValue?: string | null): Promise<void>;
};

type FiltersNamespace = {
    initConfig?: (i18n: I18nService, config: AppConfig) => void;
    initLocationAutocomplete?: (i18n: I18nService, config: AppConfig) => LocationAutocompleteApi | undefined;
    initTags?: (i18n: I18nService, config: AppConfig) => TagsFilterApi;
    tags?: TagsFilterApi;
};

type LocationAutocompleteResult = {
    name: string;
    lat: string;
    lon: string;
    source: 'photos' | 'openstreetmap' | string;
};

type LocationAutocompleteApi = {
    clearSuggestions(): void;
    fetchSuggestions(query: string): Promise<void>;
};

type IndexPhotosNamespace = {
    init?(opts: IndexPhotoOptions, i18n: I18nService, config: IndexPhotoConfig): IndexPhotosApi;
};

type UtilitiesNamespace = {
    initBase?: () => void;
    initRemoveDuplicates?: () => void;
};

type SettingsNamespace = {
    initLabelFilter?: () => void;
};

type SearchableSelectConstructor = {
    new(selectElement: HTMLSelectElement): unknown;
    i18n: Pick<I18nService, 't'>;
    initAll(): void;
    init(selectElement: HTMLSelectElement): void;
};

type AppInitCompleteDetail = {
    app: PhotoOrganizerApp;
    PHOTO_ORGANIZER: PhotoOrganizerApp;
};

type PhotoOrganizerApp = {
    domReady?: Promise<void>;
    i18nReady: Promise<I18nService>;
    appReady?: Promise<PhotoOrganizerApp>;
    i18n: I18nService;
    COMPONENTS: PhotoOrganizerComponents;
    utils?: UtilsNamespace;
    filters?: FiltersNamespace;
    media?: MediaNamespace;
    indexPhotos?: IndexPhotosNamespace;
    settings?: SettingsNamespace;
    utilities?: UtilitiesNamespace;
    initI18n?: (config: I18nConfig) => Promise<I18nService>;
    initApp?: () => Promise<PhotoOrganizerApp>;
    closeAlert?: (button: Element | null) => void;
};

interface Window {
    APP_CONFIG: AppConfig;
    PHOTO_ORGANIZER: PhotoOrganizerApp;
    notification: NotificationApi;
    showNotification?: (message: string, type?: NotificationType, duration?: number) => void;
    closeAlert?: (button: Element | null) => void;
    togglePanel?: (panelId: string) => void;
    i18next: I18nextLike;
    SearchableSelect?: SearchableSelectConstructor;
    toggleMultiSelect?: (header: Element) => void;
    updateMultiSelectText?: (checkbox: HTMLInputElement) => void;
    filterMultiSelectOptions?: (input: HTMLInputElement) => void;
    initSearchableMultiSelects?: () => void;
}

interface DocumentEventMap {
    'yaffo:app-init-complete': CustomEvent<AppInitCompleteDetail>;
}
