// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};
/** @type {PercentageSliderApi} */
const percentageSliderApi = {
    /**
     * @param {Element} sliderDom
     */
    init: (sliderDom) => {
        if (sliderDom instanceof HTMLElement && sliderDom.dataset.percentageSliderReady === '1') {
            return;
        }

        const percentageSlider = /** @type {HTMLInputElement | null} */ (
            sliderDom.querySelector('input[type="range"]')
        );
        if (!percentageSlider) return;
        if (sliderDom instanceof HTMLElement) {
            sliderDom.dataset.percentageSliderReady = '1';
        }

        const percentageDisplay = document.querySelector('.percentage-slider-display span');
        const updateSimilarityDisplay = () => {
            if (percentageDisplay) {
                percentageDisplay.textContent = window.PHOTO_ORGANIZER.i18n.percent(
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

window.PHOTO_ORGANIZER.COMPONENTS.percentageSlider = percentageSliderApi;
