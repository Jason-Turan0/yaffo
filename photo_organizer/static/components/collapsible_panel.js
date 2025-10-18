window.togglePanel = function(panelId) {
    const panel = document.querySelector(`[data-panel-id="${panelId}"]`);
    if (!panel) return;

    const header = panel.querySelector('.panel-header');
    const content = panel.querySelector('.panel-content');
    const isExpanded = header.getAttribute('aria-expanded') === 'true';

    header.setAttribute('aria-expanded', !isExpanded);
    content.style.display = isExpanded ? 'none' : 'block';
};