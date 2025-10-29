window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.COMPONENTS = window.PHOTO_ORGANIZER.COMPONENTS || {};

window.PHOTO_ORGANIZER.COMPONENTS.fileBrowser = {
    init: (fileBrowserDom) => {
        const input = fileBrowserDom.querySelector('.file-browser-input');
        const browseBtn = fileBrowserDom.querySelector('.file-browser-btn');

        if (!browseBtn || !input) {
            return;
        }

        browseBtn.addEventListener('click', async () => {
            try {
                const response = await fetch(window.APP_CONFIG.urls.select_folder);
                const data = await response.json();

                if (data.success && data.path) {
                    input.value = data.path;
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                } else if (data.error) {
                    window.notification.error(data.error);
                }
            } catch (error) {
                console.error('Error opening folder dialog:', error);
                window.notification.error('Failed to open folder dialog');
            }
        });
    },

    initAll: () => {
        document.querySelectorAll('.file-browser-group').forEach(fileBrowser => {
            window.PHOTO_ORGANIZER.COMPONENTS.fileBrowser.init(fileBrowser);
        });
    }
};

document.addEventListener('DOMContentLoaded', () => {
    window.PHOTO_ORGANIZER.COMPONENTS.fileBrowser.initAll();
});