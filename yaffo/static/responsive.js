// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

window.PHOTO_ORGANIZER.COMPONENTS.initResponsivePanels = () => {
    const narrow = window.matchMedia('(max-width: 1200px)');
    const selectors = [
        '.main-container-layout > .sidebar-container',
        '.albums-container > .albums-sidebar',
        '.utilities-container > .utilities-sidebars',
        '.sharing-container > .sharing-sidebar',
        '.themes-container > .themes-sidebar',
    ];

    const panels = selectors.flatMap((selector) =>
        Array.from(document.querySelectorAll(selector))
    );

    const entries = panels.map((panel, index) => {
        const element = /** @type {HTMLElement} */ (panel);
        const headings = Array.from(element.querySelectorAll('h2'))
            .map((heading) => heading.textContent?.trim())
            .filter(Boolean);
        const label = headings.join(' / ') || '';
        const id = element.id || `responsive-panel-${index + 1}`;
        element.id = id;

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'btn-secondary responsive-panel-toggle';
        button.setAttribute('aria-controls', id);
        const labelElement = document.createElement('span');
        labelElement.textContent = label;
        const icon = document.createElement('span');
        icon.className = 'responsive-panel-toggle-icon';
        icon.setAttribute('aria-hidden', 'true');
        icon.textContent = '▾';
        button.append(labelElement, icon);
        element.before(button);

        const setOpen = (open) => {
            const expanded = narrow.matches && open;
            element.hidden = narrow.matches && !expanded;
            button.hidden = !narrow.matches;
            button.setAttribute('aria-expanded', String(expanded));
            button.classList.toggle('is-open', expanded);
        };

        button.addEventListener('click', () => {
            setOpen(button.getAttribute('aria-expanded') !== 'true');
        });

        element.addEventListener('keydown', (event) => {
            if (event.key !== 'Escape' || !narrow.matches) return;
            setOpen(false);
            button.focus();
        });

        return { setOpen };
    });

    const sync = () => entries.forEach(({ setOpen }) => setOpen(false));
    narrow.addEventListener('change', sync);
    sync();

    return { sync };
};
