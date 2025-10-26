window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};
window.PHOTO_ORGANIZER.COMPONENTS.percentageSlider = {
    init: (sliderDom) => {
        const percentageSlider = sliderDom.querySelector('input[type="range"]');
        const percentageDisplay = document.querySelector('.percentage-slider-display span');
        const updateSimilarityDisplay = () => {
            if (percentageDisplay) {
                percentageDisplay.textContent = parseFloat(percentageSlider.value * 100) + " %";
            }
        };
        percentageSlider.addEventListener('input', updateSimilarityDisplay);
    },
    initAll: () => {
        document.querySelectorAll('.percentage-slider').forEach(slider => {
            window.PHOTO_ORGANIZER.COMPONENTS.percentageSlider.init(slider);
        })
    }
}

document.addEventListener('DOMContentLoaded', () => {
    window.PHOTO_ORGANIZER.COMPONENTS.percentageSlider.initAll();
});