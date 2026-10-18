(() => {
  const controllers = new Map();
  const initialize = (root) => {
    if (root.dataset.enhanced) return;
    const groups = [];
    for (const child of [...root.children]) {
      if (child.tagName === 'H3') groups.push({heading: child, content: []});
      else if (groups.length) groups.at(-1).content.push(child);
    }
    if (!groups.length || groups.some((group) =>
      group.content.filter((element) => element.querySelector('img')).length !== 1
    )) return;

    const controller = new AbortController();
    controllers.set(root, controller);
    const list = document.createElement('div');
    list.className = 'guide-walkthrough__steps';
    const viewer = document.createElement('div');
    viewer.className = 'guide-walkthrough__viewer';
    const status = document.createElement('p');
    status.className = 'guide-walkthrough__status';
    status.setAttribute('aria-live', 'polite');
    status.setAttribute('aria-atomic', 'true');
    const figures = document.createElement('div');
    const navigation = document.createElement('div');
    navigation.className = 'guide-walkthrough__navigation';
    const previous = document.createElement('button');
    previous.type = 'button';
    previous.textContent = '← Previous step';
    const next = document.createElement('button');
    next.type = 'button';
    next.textContent = 'Next step →';
    navigation.append(previous, next);
    viewer.append(status, figures, navigation);

    const steps = groups.map(({heading, content}, index) => {
      const title = heading.cloneNode(true);
      title.querySelector('.headerlink')?.remove();
      const name = title.textContent.trim();
      const item = document.createElement('section');
      item.className = 'guide-walkthrough__step';
      const body = document.createElement('div');
      body.className = 'guide-walkthrough__body';
      body.id = `${heading.id}-instructions`;
      const screenshot = content.find((element) => element.querySelector('img'));
      const figure = document.createElement('figure');
      figure.id = `${heading.id}-screenshot`;
      const caption = document.createElement('figcaption');
      caption.textContent = screenshot.querySelector('img').alt;
      figure.append(screenshot, caption);
      figures.append(figure);
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'guide-walkthrough__select';
      button.id = `${heading.id}-select`;
      button.setAttribute('aria-controls', `${body.id} ${figure.id}`);
      const number = document.createElement('span');
      number.className = 'guide-walkthrough__number';
      number.textContent = String(index + 1).padStart(2, '0');
      number.setAttribute('aria-hidden', 'true');
      const label = document.createElement('span');
      label.textContent = name;
      button.append(number, label);
      heading.replaceChildren(button);
      content.filter((element) => element !== screenshot).forEach((element) => body.append(element));
      figure.setAttribute('aria-labelledby', button.id);
      item.append(heading, body);
      list.append(item);
      return {item, heading, body, figure, button, name};
    });
    root.replaceChildren(list, viewer);
    root.dataset.enhanced = 'true';
    const mobile = window.matchMedia('(max-width: 700px)');
    let selected = 0;
    const select = (index, focus = false) => {
      selected = index;
      steps.forEach((step, position) => {
        const active = position === selected;
        step.item.classList.toggle('is-active', active);
        step.button.setAttribute('aria-expanded', String(active));
        step.body.hidden = !active;
        step.figure.hidden = !active;
      });
      status.textContent = `Step ${selected + 1} of ${steps.length}: ${steps[selected].name}`;
      previous.disabled = selected === 0;
      next.disabled = selected === steps.length - 1;
      // Keep the selected image beside its instructions when the columns stack.
      (mobile.matches ? steps[selected].item : root).append(viewer);
      if (focus) steps[selected].button.focus({preventScroll: true});
    };
    steps.forEach((step, index) => {
      step.button.addEventListener('click', () => select(index));
      step.button.addEventListener('keydown', (event) => {
        let target;
        if (event.key === 'ArrowDown') target = Math.min(index + 1, steps.length - 1);
        if (event.key === 'ArrowUp') target = Math.max(index - 1, 0);
        if (event.key === 'Home') target = 0;
        if (event.key === 'End') target = steps.length - 1;
        if (target !== undefined) {
          event.preventDefault();
          select(target, true);
        }
      });
    });
    previous.addEventListener('click', () => select(selected - 1, true));
    next.addEventListener('click', () => select(selected + 1, true));
    const fromHash = () => {
      const index = steps.findIndex((step) => `#${step.heading.id}` === window.location.hash);
      if (index >= 0) select(index);
    };
    window.addEventListener('hashchange', fromHash, {signal: controller.signal});
    mobile.addEventListener('change', () => select(selected), {signal: controller.signal});
    select(0);
    fromHash();
  };
  const initializeAll = () => {
    for (const [root, controller] of controllers) {
      if (!root.isConnected) {
        controller.abort();
        controllers.delete(root);
      }
    }
    document.querySelectorAll('.guide-walkthrough').forEach(initialize);
  };
  if (typeof document$ !== 'undefined') document$.subscribe(initializeAll);
  else if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initializeAll);
  else initializeAll();
})();
