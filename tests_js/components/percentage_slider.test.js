import { loadModule } from '../support/load_module.js';

const loadPercentageSlider = async () => {
  await loadModule('components/percentage_slider.js');
  await Promise.resolve();
  return window.PHOTO_ORGANIZER.COMPONENTS.percentageSlider;
};

const fixture = (value = '0.75') => {
  document.body.innerHTML = `
    <div class="percentage-slider">
      <input type="range" value="${value}">
    </div>
    <div class="percentage-slider-display"><span></span></div>`;
};

describe('percentageSlider', () => {
  it('auto-initializes after i18n is ready and updates display on input', async () => {
    fixture('0.75');
    await loadPercentageSlider();

    const input = document.querySelector('input[type="range"]');
    const display = document.querySelector('.percentage-slider-display span');

    input.value = '0.6';
    input.dispatchEvent(new Event('input'));

    expect(display.textContent).toBe('60%');
  });

  it('initializes all sliders in the document', async () => {
    document.body.innerHTML = `
      <div class="percentage-slider"><input type="range" value="0.1"></div>
      <div class="percentage-slider"><input type="range" value="0.2"></div>
      <div class="percentage-slider-display"><span></span></div>`;
    const api = await loadPercentageSlider();
    const [first, second] = Array.from(document.querySelectorAll('input[type="range"]'));

    api.initAll();
    first.dispatchEvent(new Event('input'));
    expect(document.querySelector('.percentage-slider-display span').textContent).toBe('10%');

    second.dispatchEvent(new Event('input'));
    expect(document.querySelector('.percentage-slider-display span').textContent).toBe('20%');
  });

  it('no-ops when the slider root has no range input', async () => {
    const api = await loadPercentageSlider();
    const root = document.createElement('div');

    expect(() => api.init(root)).not.toThrow();
  });

  it('does not require a display element to be present', async () => {
    document.body.innerHTML = '<div class="percentage-slider"><input type="range" value="0.5"></div>';
    const api = await loadPercentageSlider();
    const input = document.querySelector('input[type="range"]');

    expect(() => {
      api.init(document.querySelector('.percentage-slider'));
      input.dispatchEvent(new Event('input'));
    }).not.toThrow();
  });
});
