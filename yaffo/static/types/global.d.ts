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

type ConfirmDialogOptions = {
    title?: string;
    message?: string;
    confirmText?: string;
    cancelText?: string;
    confirmClass?: string;
};

type ConfirmDialogApi = (options: ConfirmDialogOptions) => Promise<boolean>;

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

type FileBrowserApi = {
    init(): void;
};

type FolderPickerMode = 'folder' | 'file' | 'any';

type FolderPickerOptions = {
    mode?: FolderPickerMode;
    startPath?: string | null;
};

type PickFolderApi = (options?: FolderPickerOptions) => Promise<string | null>;

type FolderPickerRoot = {
    name: string;
    path: string;
};

type FolderPickerEntry = {
    name: string;
    path: string;
    is_dir: boolean;
};

type FolderPickerListResponse = {
    path: string | null;
    parent: string | null;
    error?: string;
    roots?: FolderPickerRoot[];
    entries?: FolderPickerEntry[];
};

type OverlayPlacement = 'top' | 'bottom' | 'left' | 'right';

type OverlayOptions = {
    placement?: OverlayPlacement;
    offset?: number;
    closeOnEsc?: boolean;
    closeOnOutsideClick?: boolean;
};

type OverlayControl = {
    overlay: HTMLElement;
    close(): void;
    reposition(): void;
};

type OverlayApi = {
    init(targetElementId: string, overlayContent: string, options?: OverlayOptions): OverlayControl;
};

type ChatStatusBody = {
    status: string;
    started_at?: string | null;
    messages?: Array<{
        type: string;
        content: string;
    }>;
    [key: string]: unknown;
};

type ChatSendResult = {
    ok: boolean;
    error?: string;
};

type ChatDialogOptions = {
    startStatus: string;
    statusUrl(): string;
    onSend(message: string): Promise<ChatSendResult>;
    onCancel?: () => Promise<unknown>;
    onStatus?: (body: ChatStatusBody) => void;
    onSettled?: (body: ChatStatusBody) => void;
    onPhase?: (status: string) => void;
    runningStatus?: string;
    cancelConfirm?: Pick<ConfirmDialogOptions, 'title' | 'message' | 'confirmText'>;
    pollIntervalMs?: number;
    pollRetryMs?: number;
};

type ChatDialogApi = {
    enterRunning(): void;
    isRunning(): boolean;
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
    fileBrowser?: FileBrowserApi;
    modal: ModalApi;
    overlay?: OverlayApi;
    initChatDialog?: (id: string, options: ChatDialogOptions) => ChatDialogApi | null;
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

type FaceLocation = {
    top: number;
    right: number;
    bottom: number;
    left: number;
};

type MediaFacePerson = {
    id?: number;
    name: string;
};

type MediaFace = {
    id: number;
    location?: FaceLocation | null;
    people?: MediaFacePerson[];
};

type MediaTag = {
    tempId?: number;
    tag_name?: string;
    tag_value?: string;
    isNew?: boolean;
    modified?: boolean;
    markedForDeletion?: boolean;
    [key: string]: unknown;
};

type PhotoViewApi = {
    highlightFace(faceId: number): void;
    clearHighlights(): void;
    openFile(filePath: string): void;
    openFolder(folderPath: string): void;
};

type PhotoTagsApi = {
    openEditModal(): void;
    addTagToList(): void;
    removeTagFromList(tempId: number): void;
    updateTagName(tempId: number, newName: string): void;
    updateTagValue(tempId: number, newValue: string): void;
};

type FaceReassignApi = Record<string, never>;

type ViewPhotoNamespace = {
    initPhotoView?: (
        faceData: MediaFace[] | null,
        absoluteFilePath: string,
        absoluteFolderPath: string,
        i18n: I18nService,
        config: AppConfig,
    ) => PhotoViewApi;
    initPhotoTags?: (photoId: number, initialTags: MediaTag[], i18n: I18nService, config: AppConfig) => PhotoTagsApi;
    initFaceReassign?: (allPeople: MediaFacePerson[], i18n: I18nService, config: AppConfig) => FaceReassignApi;
    photoView?: PhotoViewApi;
    photoTags?: PhotoTagsApi;
    faceReassign?: FaceReassignApi;
};

type LocationMediaItem = {
    id: number;
    lat?: number;
    lon?: number;
    name?: string | null;
    location?: string | null;
    photo_path?: string | null;
    filename?: string;
    media_type?: string | null;
    year?: number | null;
    month?: number | null;
    device?: string | null;
    favorite?: boolean;
    person_ids?: number[];
    genders?: number[];
    label_ids?: number[];
    tags?: { name: string; value: string | null }[];
};

type ClientFilterItem = LocationMediaItem;

type ClientFilterCriteria = {
    path: string | null;
    year: number | null;
    month: number | null;
    device: string | null;
    favorite: boolean;
    mediaType: 'photo' | 'video' | null;
    personIds: number[];
    personMatchType: string;
    gender: number | null;
    labelIds: number[];
    labelsMatchType: string;
    tagName: string | null;
    tagValue: string | null;
    locationNames: string[];
    unnamed: boolean;
    proximity: { lat: number; lon: number; distance: number } | null;
};

type ClientFilterApi = {
    apply(): void;
    readCriteria(): ClientFilterCriteria;
};

type LocationMapApi = {
    map: unknown;
    vectorSource: unknown;
    selectedPhotoIds: Set<number>;
    updateSelectionPanel(): Promise<void>;
    setClientFilter(predicate: (item: ClientFilterItem) => boolean): void;
};

type LocationsNamespace = {
    initMap?: (
        locations: LocationMediaItem[],
        i18n: I18nService,
        config: AppConfig,
        options?: { nearbyRadiusKm?: number },
    ) => LocationMapApi;
    map?: LocationMapApi;
};

type TagsFilterApi = {
    loadTagValues(tagName: string, selectedValue?: string | null): Promise<void>;
};

type FiltersNamespace = {
    initConfig?: (i18n: I18nService, config: AppConfig) => void;
    initLocationAutocomplete?: (i18n: I18nService, config: AppConfig) => LocationAutocompleteApi | undefined;
    initTags?: (i18n: I18nService, config: AppConfig) => TagsFilterApi;
    tags?: TagsFilterApi;
    initClientFilter?: (opts: {
        form: HTMLFormElement | null;
        distanceUnit?: string;
        onApply: (predicate: (item: ClientFilterItem) => boolean) => void;
    }) => ClientFilterApi | undefined;
    clientFilter?: ClientFilterApi;
    clientFilterCore?: {
        readCriteria(form: HTMLFormElement): ClientFilterCriteria;
        buildPredicate(
            criteria: ClientFilterCriteria,
            options?: { distanceUnit?: string },
        ): (item: ClientFilterItem) => boolean;
    };
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
    init?(initialMediaDirs: SettingsMediaDir[], i18n: I18nService, config: AppConfig): SettingsApi;
    instance?: SettingsApi;
    initLabelFilter?: () => void;
};

type AutomationsNamespace = {
    initAutomationDelete?: (selectedName: string, i18n: I18nService) => void;
    initAutomationDetails?: () => void;
    initAutomationRunNow?: (
        runUrl: string,
        config: AppConfig,
        hasTriggers: boolean,
        defaultPath: string | null | undefined,
        i18n: I18nService,
    ) => void;
    initAutomationConfigure?: () => void;
    initAutomationTest?: (
        slug: string,
        config: AppConfig,
        defaultPath: string | null | undefined,
        i18n: I18nService,
    ) => void;
    initTriggerEditor?: (i18n?: I18nService, cronBuilder?: CronBuilderApi) => void;
    initAutomationChat?: (
        slug: string,
        startStatus: string,
        config: AppConfig,
        i18n: I18nService,
    ) => ChatDialogApi | null;
    automationChat?: ChatDialogApi | null;
};

type PageDetailApi = {
    confirmDelete(): Promise<void>;
};

type PageDesignGridApi = {
    grid: any;
    getVersionId: () => number;
};

type PagesNamespace = {
    initDetail?: (pageTitle: string, i18n: I18nService) => PageDetailApi;
    initWidgetApi?: (data?: WidgetData, state?: WidgetState, locale?: string) => WidgetApi;
    initWidgetBroker?: (pageId: number, getVersionId: () => number, config: AppConfig) => void;
    initPresentationGrid?: () => any;
    initDesignGrid?: (
        pageId: number,
        editVersionId: number,
        startStatus: string,
        config: AppConfig,
        i18n: I18nService,
    ) => PageDesignGridApi;
    detail?: PageDetailApi;
    grid?: PageDesignGridApi;
};

type WidgetData = Record<string, unknown>;
type WidgetState = Record<string, unknown>;
type WidgetQuery = Record<string, unknown>;
type WidgetPayload = unknown;

type WidgetApi = {
    data: WidgetData;
    state: WidgetState;
    locale: string;
    number(value: number, options?: Intl.NumberFormatOptions): string;
    percent(value: number, options?: Intl.NumberFormatOptions): string;
    date(value: string | number | Date, options?: Intl.DateTimeFormatOptions): string;
    relativeTime(value: number, unit: Intl.RelativeTimeFormatUnit, options?: Intl.RelativeTimeFormatOptions): string;
    list(values: Iterable<string>, options?: Intl.ListFormatOptions): string;
    query(query: WidgetQuery): Promise<unknown>;
    publish(topic: string, payload: WidgetPayload): void;
    subscribe(topic: string, handler: (payload: WidgetPayload) => void): void;
    saveState(newState: WidgetState): void;
    mediaUrl(id: string | number): string;
};

type WidgetMessage = {
    type?: string;
    requestId?: number;
    topic?: string;
    payload?: WidgetPayload;
    state?: WidgetState;
    query?: WidgetQuery;
    message?: string;
    data?: unknown;
    [key: string]: unknown;
};

type PeopleListApi = {
    openAddModal(): void;
    openEditModal(personId: number, personName: string, birthdate?: string, gender?: number | null): void;
    confirmDelete(personId: number, personName: string): Promise<void>;
};

type FacesAssignmentApi = {
    submitFaces(personId: string | number | null, faceStatus: string): Promise<void>;
    selectWholeCluster(on: boolean): void;
    randomSample(arr: FaceRecord[], n: number): FaceRecord[];
    getSelectedIds(): Set<number>;
};

type FacesNamespace = {
    initAssignment?: (
        sampleSize: number,
        topPeople: PersonShortcut[],
        i18n: I18nService,
        config: AppConfig,
    ) => FacesAssignmentApi;
    assignment?: FacesAssignmentApi;
};

type PeopleFacesApi = {
    setAllSelected(selected: boolean): void;
};

type PeopleNamespace = {
    initFaces?: (i18n: I18nService, config: AppConfig) => PeopleFacesApi;
    faces?: PeopleFacesApi;
    initList?: (i18n: I18nService, config: AppConfig) => PeopleListApi;
    list?: PeopleListApi;
};

type ThemesPageApi = {
    confirmDelete(): Promise<void>;
};

type ThemesNamespace = {
    initPage?: (selectedLabel: string, config: AppConfig, i18n: I18nService) => ThemesPageApi;
    initChat?: (slug: string, startStatus: string, config: AppConfig, i18n: I18nService) => ChatDialogApi | null;
    page?: ThemesPageApi;
    chat?: ChatDialogApi | null;
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
    VIEW_PHOTO?: ViewPhotoNamespace;
    indexPhotos?: IndexPhotosNamespace;
    faces?: FacesNamespace;
    locations?: LocationsNamespace;
    pages?: PagesNamespace;
    people?: PeopleNamespace;
    settings?: SettingsNamespace;
    automations?: AutomationsNamespace;
    themes?: ThemesNamespace;
    utilities?: UtilitiesNamespace;
    confirmDialog: ConfirmDialogApi;
    pickFolder: PickFolderApi;
    widgetErrors?: Record<string, string[]>;
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
    yaffo?: WidgetApi;
    SearchableSelect?: SearchableSelectConstructor;
    toggleMultiSelect?: (header: Element) => void;
    updateMultiSelectText?: (checkbox: HTMLInputElement) => void;
    filterMultiSelectOptions?: (input: HTMLInputElement) => void;
    initSearchableMultiSelects?: () => void;
}

type CronChangeDetail = {
    cron: string;
    valid: boolean | null;
};

interface DocumentEventMap {
    'yaffo:app-init-complete': CustomEvent<AppInitCompleteDetail>;
    'cron:change': CustomEvent<CronChangeDetail>;
}

declare const ol: any;

// Vendored gridstack; its full surface is not modelled here.
declare const GridStack: any;

interface HTMLElement {
    intlDateInput?: IntlDateInputControl;
}
