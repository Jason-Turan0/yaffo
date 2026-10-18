(() => {
  const controllers = new Map();
  const parseGeometry = (annotation) => {
    const {x, y, region} = annotation.dataset;
    if (!x?.trim() || !y?.trim() || !region?.trim()) return null;
    const geometry = [Number(x), Number(y), ...region.trim().split(/\s+/).map(Number)];
    if (geometry.length !== 6 || !geometry.every(Number.isFinite)) return null;
    const [markerX, markerY, left, top, width, height] = geometry;
    if ([markerX, markerY, left, top].some((value) => value < 0 || value > 100) ||
        width <= 0 || height <= 0 || left + width > 100 || top + height > 100) return null;
    return {markerX, markerY, left, top, width, height};
  };

  const initialize = (root) => {
    if (root.dataset.enhanced) return;
    const annotations = [...root.querySelectorAll(':scope > .screenshot-annotation')];
    const images = root.querySelectorAll('img');
    if (images.length !== 1 || annotations.some((entry) => entry.contains(images[0]))) return;
    const entries = annotations.map((element) => ({
      element,
      heading: element.querySelector('h3, h4'),
      geometry: parseGeometry(element),
    }));
    if (images.length !== 1 || !entries.length || entries.some((entry) =>
      !entry.heading?.id || !entry.geometry
    )) return;

    const controller = new AbortController();
    controllers.set(root, controller);
    const image = images[0];
    const imageBlock = [...root.children].find((element) => element.contains(image));
    const figure = document.createElement('figure');
    figure.className = 'annotated-screenshot__figure';
    const canvas = document.createElement('div');
    canvas.className = 'annotated-screenshot__canvas';
    const highlight = document.createElement('div');
    highlight.className = 'annotated-screenshot__highlight';
    highlight.setAttribute('aria-hidden', 'true');
    const markers = document.createElement('div');
    markers.className = 'annotated-screenshot__markers';
    const caption = document.createElement('figcaption');
    caption.textContent = 'Select a numbered area to learn more. Select the screenshot to enlarge.';
    canvas.append(imageBlock, highlight, markers);
    figure.append(canvas, caption);
    const explanations = document.createElement('div');
    explanations.className = 'annotated-screenshot__explanations';
    const status = document.createElement('p');
    status.className = 'annotated-screenshot__status';
    status.setAttribute('aria-live', 'polite');
    status.setAttribute('aria-atomic', 'true');

    const steps = entries.map(({element, heading, geometry}, index) => {
      const title = heading.cloneNode(true);
      title.querySelector('.headerlink')?.remove();
      const name = title.textContent.trim();
      const body = document.createElement('div');
      body.className = 'annotated-screenshot__body';
      body.id = `${heading.id}-explanation`;
      [...element.children].filter((child) => child !== heading).forEach((child) => body.append(child));
      const select = document.createElement('button');
      select.type = 'button';
      select.className = 'annotated-screenshot__select';
      select.setAttribute('aria-controls', body.id);
      const number = document.createElement('span');
      number.className = 'annotated-screenshot__number';
      number.textContent = String(index + 1).padStart(2, '0');
      number.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span');
      label.textContent = name;
      select.append(number, label);
      heading.replaceChildren(select);
      element.replaceChildren(heading, body);
      explanations.append(element);
      const marker = document.createElement('button');
      marker.type = 'button';
      marker.className = 'annotated-screenshot__marker';
      marker.textContent = String(index + 1);
      marker.setAttribute('aria-label', `${index + 1}. ${name}`);
      marker.setAttribute('aria-controls', body.id);
      marker.style.setProperty('--marker-x', `${geometry.markerX}%`);
      marker.style.setProperty('--marker-y', `${geometry.markerY}%`);
      markers.append(marker);
      return {element, heading, body, select, marker, geometry, name};
    });
    explanations.append(status);
    root.replaceChildren(figure, explanations);
    root.dataset.enhanced = 'true';

    const activate = (index) => {
      steps.forEach((step, position) => {
        const active = index === position;
        step.element.classList.toggle('is-active', active);
        step.select.setAttribute('aria-expanded', String(active));
        step.marker.setAttribute('aria-pressed', String(active));
        step.body.hidden = !active;
      });
      const step = steps[index];
      const {left, top, width, height} = step.geometry;
      highlight.style.left = `${left}%`;
      highlight.style.top = `${top}%`;
      highlight.style.width = `${width}%`;
      highlight.style.height = `${height}%`;
      status.textContent = `Area ${index + 1} of ${steps.length}: ${step.name}`;
    };
    steps.forEach((step, index) => {
      [step.marker, step.select].forEach((button) => {
        button.addEventListener('click', () => activate(index));
        button.addEventListener('keydown', (event) => {
          let next;
          if (event.key === 'ArrowDown' || event.key === 'ArrowRight') next = (index + 1) % steps.length;
          if (event.key === 'ArrowUp' || event.key === 'ArrowLeft') next = (index + steps.length - 1) % steps.length;
          if (event.key === 'Home') next = 0;
          if (event.key === 'End') next = steps.length - 1;
          if (next !== undefined) {
            event.preventDefault();
            activate(next);
            const target = button === step.marker ? steps[next].marker : steps[next].select;
            target.focus({preventScroll: true});
          }
        });
      });
    });
    const selectHash = () => {
      const index = steps.findIndex((step) => `#${step.heading.id}` === window.location.hash);
      if (index >= 0) activate(index);
    };
    window.addEventListener('hashchange', selectHash, {signal: controller.signal});
    activate(0);
    selectHash();
  };

  const initializeAll = () => {
    for (const [root, controller] of controllers) {
      if (!root.isConnected) {
        controller.abort();
        controllers.delete(root);
      }
    }
    document.querySelectorAll('.annotated-screenshot').forEach(initialize);
  };
  if (typeof document$ !== 'undefined') document$.subscribe(initializeAll);
  else if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initializeAll);
  else initializeAll();
})();
