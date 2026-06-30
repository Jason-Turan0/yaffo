// @ts-check

/**
 * Toggle a collapsible panel rendered with matching header/content children.
 *
 * @param {string} panelId
 */
window.togglePanel = function(panelId) {
    const panel = document.querySelector(`[data-panel-id="${panelId}"]`);
    if (!panel) return;

    const header = panel.querySelector('.panel-header');
    const content = /** @type {HTMLElement | null} */ (
        panel.querySelector('.panel-content')
    );
    if (!header || !content) return;

    const isExpanded = header.getAttribute('aria-expanded') === 'true';

    header.setAttribute('aria-expanded', String(!isExpanded));
    content.style.display = isExpanded ? 'none' : 'block';
};
