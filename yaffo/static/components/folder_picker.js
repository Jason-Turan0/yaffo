// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

/**
 * In-app folder/file picker. Browses the server's filesystem (which, in this local
 * desktop app, is the user's own disk) via /api/fs/list — a truly cross-platform
 * replacement for the native OS dialog. Promise-based, like confirmDialog:
 *
 *   const path = await window.PHOTO_ORGANIZER.pickFolder({ mode: 'folder' });
 *   if (path) { ... }   // null when cancelled
 *
 * mode: 'folder' (default) shows folders + a "Select this folder" action; 'file'
 * also lists files and resolves when one is clicked; 'any' lists files and folders
 * and also allows selecting the current folder.
 */
/**
 * @type {PickFolderApi}
 */
window.PHOTO_ORGANIZER.pickFolder = ({ mode = 'folder', startPath = null } = {}) => {
    return new Promise((resolve) => {
        const i18n = window.PHOTO_ORGANIZER.i18n;
        const modal = document.getElementById('folder-picker-modal');
        const title = document.getElementById('folder-picker-title');
        const pathLabel = document.getElementById('folder-picker-path');
        const list = document.getElementById('folder-picker-list');
        const upBtn = document.getElementById('folder-picker-up');
        const rootsBox = document.getElementById('folder-picker-roots');
        const createForm = document.getElementById('folder-picker-create');
        const createInput = document.getElementById('folder-picker-create-name');
        const createSubmit = document.getElementById('folder-picker-create-submit');
        const selectBtn = document.getElementById('folder-picker-select');
        const cancelBtn = document.getElementById('folder-picker-cancel');
        const errorBox = document.getElementById('folder-picker-error');
        if (
            !modal ||
            !title ||
            !pathLabel ||
            !list ||
            !(upBtn instanceof HTMLButtonElement) ||
            !rootsBox ||
            !(createForm instanceof HTMLFormElement) ||
            !(createInput instanceof HTMLInputElement) ||
            !(createSubmit instanceof HTMLButtonElement) ||
            !(selectBtn instanceof HTMLButtonElement) ||
            !(cancelBtn instanceof HTMLButtonElement) ||
            !errorBox
        ) {
            throw new Error('Folder picker markup is incomplete');
        }

        let currentPath = startPath;
        /** @type {string | null} */
        let parentPath = null;

        const isFileMode = mode === 'file';
        const isAnyMode = mode === 'any';

        title.textContent = isAnyMode
            ? i18n.t('components:folderPicker.selectFileOrFolder')
            : (isFileMode
                ? i18n.t('components:folderPicker.selectFile')
                : i18n.t('components:folderPicker.selectFolder'));
        upBtn.dataset.icon = 'arrow-up';
        upBtn.textContent = i18n.t('components:folderPicker.up');
        createInput.placeholder = i18n.t('components:folderPicker.newFolderName');
        createInput.setAttribute('aria-label', i18n.t('components:folderPicker.newFolderName'));
        createSubmit.textContent = i18n.t('components:folderPicker.createFolder');
        selectBtn.dataset.icon = 'folder';
        // "Select this folder" is hidden only for file-only selection; files resolve on click.
        selectBtn.style.display = isFileMode ? 'none' : '';

        /**
         * @param {string | null} value
         */
        const settle = (value) => {
            cleanup();
            resolve(value);
        };

        const cleanup = () => {
            modal.classList.remove('active');
            list.innerHTML = '';
            rootsBox.innerHTML = '';
            selectBtn.removeEventListener('click', onSelect);
            cancelBtn.removeEventListener('click', onCancel);
            upBtn.removeEventListener('click', onUp);
            createForm.removeEventListener('submit', onCreateFolder);
            modal.removeEventListener('click', onBackdrop);
            document.removeEventListener('keydown', onKeydown);
        };

        const onSelect = () => settle(currentPath);
        const onCancel = () => settle(null);
        const onUp = () => { if (parentPath) navigate(parentPath); };
        const onBackdrop = (/** @type {MouseEvent} */ e) => { if (e.target === modal) onCancel(); };
        const onKeydown = (/** @type {KeyboardEvent} */ e) => { if (e.key === 'Escape') onCancel(); };
        const onCreateFolder = (/** @type {SubmitEvent} */ e) => {
            e.preventDefault();
            void createFolder();
        };

        const createFolder = async () => {
            const name = createInput.value.trim();
            if (!name) {
                errorBox.textContent = i18n.t('components:folderPicker.folderNameRequired');
                createInput.focus();
                return;
            }
            if (!currentPath) {
                errorBox.textContent = i18n.t('components:folderPicker.createFailed');
                return;
            }
            createSubmit.disabled = true;
            try {
                const response = await fetch(window.APP_CONFIG.urls.fs_create_folder, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ path: currentPath, name }),
                });
                const data = await response.json();
                if (!response.ok) {
                    errorBox.textContent = data.error || i18n.t('components:folderPicker.createFailed');
                    return;
                }
                createInput.value = '';
                await navigate(data.path);
            } catch {
                errorBox.textContent = i18n.t('components:folderPicker.createFailed');
            } finally {
                createSubmit.disabled = false;
            }
        };

        /**
         * @param {FolderPickerRoot[]} roots
         */
        const renderRoots = (roots) => {
            rootsBox.innerHTML = '';
            roots.forEach((r) => {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'folder-picker-root';
                chip.dataset.icon = 'folder';
                chip.textContent = r.name;
                chip.title = r.path;
                chip.addEventListener('click', () => navigate(r.path));
                rootsBox.appendChild(chip);
            });
        };

        /**
         * @param {FolderPickerEntry[]} entries
         */
        const renderEntries = (entries) => {
            list.innerHTML = '';
            if (entries.length === 0) {
                const empty = document.createElement('li');
                empty.className = 'folder-picker-empty';
                empty.textContent = isFileMode || isAnyMode
                    ? i18n.t('components:folderPicker.noFilesOrFolders')
                    : i18n.t('components:folderPicker.noSubfolders');
                list.appendChild(empty);
                return;
            }
            entries.forEach((entry) => {
                const item = document.createElement('li');
                item.className = `folder-picker-item ${entry.is_dir ? 'is-dir' : 'is-file'}`;
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'folder-picker-entry';
                button.dataset.icon = entry.is_dir ? 'folder' : 'file';
                button.textContent = entry.name;
                button.addEventListener('click', () => {
                    if (entry.is_dir) {
                        navigate(entry.path);
                    } else {
                        settle(entry.path);
                    }
                });
                item.appendChild(button);
                list.appendChild(item);
            });
        };

        /**
         * @param {string | null} path
         */
        const navigate = async (path) => {
            const params = new URLSearchParams({ mode });
            if (path) params.set('path', path);
            /** @type {FolderPickerListResponse} */
            let data;
            try {
                data = /** @type {FolderPickerListResponse} */ (await (await fetch(`${window.APP_CONFIG.urls.fs_list}?${params}`)).json());
            } catch {
                errorBox.textContent = i18n.t('components:folderPicker.readFailed');
                return;
            }
            currentPath = data.path;
            parentPath = data.parent;
            const displayPath = data.path || '';
            pathLabel.textContent = displayPath;
            pathLabel.title = displayPath;
            errorBox.textContent = data.error || '';
            upBtn.disabled = !data.parent;
            createSubmit.disabled = !data.path;
            renderRoots(data.roots || []);
            renderEntries(data.entries || []);
        };

        selectBtn.addEventListener('click', onSelect);
        cancelBtn.addEventListener('click', onCancel);
        upBtn.addEventListener('click', onUp);
        createForm.addEventListener('submit', onCreateFolder);
        modal.addEventListener('click', onBackdrop);
        document.addEventListener('keydown', onKeydown);

        createInput.value = '';
        modal.classList.add('active');
        navigate(currentPath);
    });
};
