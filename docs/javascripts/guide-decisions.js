(() => {
  const controllers = new Map();
  const button = (label) => {
    const element = document.createElement('button');
    element.type = 'button';
    element.className = 'guide-decisions__button';
    element.textContent = label;
    return element;
  };
  const initialize = (root) => {
    if (root.dataset.enhanced) return;
    const chooser = root.classList.contains('action-chooser');
    const startEmpty = chooser && root.dataset.initial === 'none';
    const statusTemplate = root.dataset.status || '';
    const entries = [...root.querySelectorAll(chooser ? ':scope > .action-choice' : ':scope > .navigator-step')];
    const headings = entries.map((entry) => entry.querySelector(chooser ? 'h3' : ':scope > :is(h3, h4), :scope > .navigator-outcome'));
    if (!entries.length || headings.some((heading) => !heading?.id) ||
        new Set(headings.map((heading) => heading.id)).size !== entries.length ||
        (chooser && entries.some((entry) => !entry.dataset.choice?.trim()))) return;
    const targets = new Map(headings.map((heading, index) => [`#${heading.id}`, index]));
    if (chooser) {
      entries.forEach((entry, index) => {
        entry.querySelectorAll('[id]').forEach((element) => targets.set(`#${element.id}`, index));
      });
    }
    const localHash = (link) => {
      const url = new URL(link.getAttribute('href'), window.location.href);
      return url.origin === window.location.origin && url.pathname === window.location.pathname &&
        url.search === window.location.search ? url.hash : '';
    };
    const localLinks = chooser ? [] : [...root.querySelectorAll('a[href]:not(.headerlink)')]
      .filter((link) => localHash(link));
    // Links to sections outside the navigator stay links; only unknown targets are invalid.
    const links = localLinks.filter((link) => targets.has(localHash(link)));
    if (localLinks.some((link) => !targets.has(localHash(link)) &&
        !document.getElementById(decodeURIComponent(localHash(link).slice(1))))) return;

    const controller = new AbortController();
    controllers.set(root, controller);
    const status = document.createElement('p');
    status.className = 'guide-decisions__status';
    status.setAttribute('aria-live', 'polite');
    status.setAttribute('aria-atomic', 'true');
    // Choosers show a status line only when they opt in with data-status.
    status.hidden = chooser && !statusTemplate;
    root.prepend(status);
    const controls = document.createElement('div');
    controls.className = 'guide-decisions__controls';
    const history = [0];
    let options = [];
    const activate = (index, focus = false) => {
      entries.forEach((entry, position) => { entry.hidden = position !== index; });
      options.forEach((option, position) => option.setAttribute('aria-pressed', String(position === index)));
      status.textContent = chooser ? (index === -1 ? 'Choose an option above' : statusTemplate.replaceAll('{choice}', entries[index].dataset.choice)) :
        `Check ${history.length} · ${entries[index].querySelector('.guide-decisions__button') ? 'Choose an answer below' : 'Next steps'}`;
      if (!chooser) {
        back.disabled = history.length === 1;
        reset.disabled = history.length === 1;
      }
      if (focus) headings[index].focus({preventScroll: true});
    };
    if (chooser) {
      controls.setAttribute('role', 'group');
      controls.setAttribute('aria-label', 'What needs attention?');
      options = entries.map((entry, index) => {
        const option = button(entry.dataset.choice);
        option.setAttribute('aria-controls', headings[index].id);
        // An empty-start chooser closes the open panel when its button is clicked again.
        option.addEventListener('click', () => activate(startEmpty && option.getAttribute('aria-pressed') === 'true' ? -1 : index));
        option.addEventListener('keydown', (event) => {
          let next;
          if (['ArrowRight', 'ArrowDown'].includes(event.key)) next = (index + 1) % entries.length;
          if (['ArrowLeft', 'ArrowUp'].includes(event.key)) next = (index + entries.length - 1) % entries.length;
          if (event.key === 'Home') next = 0;
          if (event.key === 'End') next = entries.length - 1;
          if (next !== undefined) {
            event.preventDefault();
            activate(next);
            options[next].focus();
          }
        });
        controls.append(option);
        return option;
      });
      status.before(controls);
    } else {
      links.forEach((link) => {
        const target = targets.get(localHash(link));
        const answer = button(link.textContent);
        answer.setAttribute('aria-controls', headings[target].id);
        answer.addEventListener('click', () => {
          history.push(target);
          activate(target, true);
        });
        link.replaceWith(answer);
      });
      root.append(controls);
    }
    const back = button('← Back one question');
    const reset = button('Start over');
    if (!chooser) {
      controls.append(back, reset);
      back.addEventListener('click', () => {
        if (history.length > 1) history.pop();
        activate(history.at(-1), true);
      });
      reset.addEventListener('click', () => {
        history.splice(0, history.length, 0);
        activate(0, true);
      });
    }
    headings.forEach((heading) => { heading.tabIndex = -1; });
    const selectHash = () => {
      const index = targets.get(window.location.hash);
      if (index === undefined) return;
      history.splice(0, history.length, ...(index === 0 ? [0] : [0, index]));
      activate(index);
    };
    window.addEventListener('hashchange', selectHash, {signal: controller.signal});
    root.dataset.enhanced = 'true';
    activate(startEmpty ? -1 : 0);
    selectHash();
  };
  const initializeAll = () => {
    for (const [root, controller] of controllers) {
      if (!root.isConnected) {
        controller.abort();
        controllers.delete(root);
      }
    }
    document.querySelectorAll('.action-chooser, .troubleshooting-navigator').forEach(initialize);
  };
  if (typeof document$ !== 'undefined') document$.subscribe(initializeAll);
  else if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', initializeAll);
  else initializeAll();
})();
