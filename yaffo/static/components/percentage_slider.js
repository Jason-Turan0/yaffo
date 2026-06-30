// @ts-check

/**
 * @typedef {Object} I18nService
 * @property {(value: number, options?: Intl.NumberFormatOptions) => string} percent
 *
 * @typedef {Object} PercentageSliderApi
 * @property {(sliderDom: Element) => void} init
 * @property {() => void} initAll
 */

const percentageSliderWindow = /** @type {Window & {
    PHOTO_ORGANIZER: {
        COMPONENTS: {
            percentageSlider?: PercentageSliderApi,
        },
        i18n: I18nService,
        i18nReady: Promise<I18nService>,
    },
}} */ (/** @type {unknown} */ (window));

percentageSliderWindow.PHOTO_ORGANIZER = percentageSliderWindow.PHOTO_ORGANIZER || {};
percentageSliderWindow.PHOTO_ORGANIZER.COMPONENTS = percentageSliderWindow.PHOTO_ORGANIZER.COMPONENTS || {};
/** @type {PercentageSliderApi} */
const percentageSliderApi = {
    /**
     * @param {Element} sliderDom
     */
    init: (sliderDom) => {
        const percentageSlider = /** @type {HTMLInputElement | null} */ (
            sliderDom.querySelector('input[type="range"]')
        );
        if (!percentageSlider) return;
        const percentageDisplay = document.querySelector('.percentage-slider-display span');
        const updateSimilarityDisplay = () => {
            if (percentageDisplay) {
                percentageDisplay.textContent = percentageSliderWindow.PHOTO_ORGANIZER.i18n.percent(
                    Number(percentageSlider.value)
                );
            }
        };
        percentageSlider.addEventListener('input', updateSimilarityDisplay);
    },
    initAll: () => {
        document.querySelectorAll('.percentage-slider').forEach(slider => {
            percentageSliderApi.init(slider);
        });
    },
};

percentageSliderWindow.PHOTO_ORGANIZER.COMPONENTS.percentageSlider = percentageSliderApi;

percentageSliderWindow.PHOTO_ORGANIZER.i18nReady.then(() => {
    percentageSliderApi.initAll();
});
