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
window.PHOTO_ORGANIZER.pickFolder = ({ mode = 'folder', startPath = null } = {}) => {
    return new Promise((resolve) => {
        const i18n = window.PHOTO_ORGANIZER.i18n;
        const modal = document.getElementById('folder-picker-modal');
        const title = document.getElementById('folder-picker-title');
        const pathLabel = document.getElementById('folder-picker-path');
        const list = document.getElementById('folder-picker-list');
        const upBtn = document.getElementById('folder-picker-up');
        const rootsBox = document.getElementById('folder-picker-roots');
        const selectBtn = document.getElementById('folder-picker-select');
        const cancelBtn = document.getElementById('folder-picker-cancel');
        const errorBox = document.getElementById('folder-picker-error');

        let currentPath = startPath;
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
        selectBtn.dataset.icon = 'folder';
        // "Select this folder" is hidden only for file-only selection; files resolve on click.
        selectBtn.style.display = isFileMode ? 'none' : '';

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
            modal.removeEventListener('click', onBackdrop);
            document.removeEventListener('keydown', onKeydown);
        };

        const onSelect = () => settle(currentPath);
        const onCancel = () => settle(null);
        const onUp = () => { if (parentPath) navigate(parentPath); };
        const onBackdrop = (e) => { if (e.target === modal) onCancel(); };
        const onKeydown = (e) => { if (e.key === 'Escape') onCancel(); };

        const renderRoots = (roots) => {
            rootsBox.innerHTML = '';
            roots.forEach((r) => {
                const chip = document.createElement('button');
                chip.type = 'button';
                chip.className = 'folder-picker-root';
                chip.dataset.icon = 'folder';
                chip.textContent = r.name;
                chip.addEventListener('click', () => navigate(r.path));
                rootsBox.appendChild(chip);
            });
        };

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

        const navigate = async (path) => {
            const params = new URLSearchParams({ mode });
            if (path) params.set('path', path);
            let data;
            try {
                data = await (await fetch(`${window.APP_CONFIG.urls.fs_list}?${params}`)).json();
            } catch {
                errorBox.textContent = i18n.t('components:folderPicker.readFailed');
                return;
            }
            currentPath = data.path;
            parentPath = data.parent;
            pathLabel.textContent = data.path;
            pathLabel.title = data.path;
            errorBox.textContent = data.error || '';
            upBtn.disabled = !data.parent;
            renderRoots(data.roots || []);
            renderEntries(data.entries || []);
        };

        selectBtn.addEventListener('click', onSelect);
        cancelBtn.addEventListener('click', onCancel);
        upBtn.addEventListener('click', onUp);
        modal.addEventListener('click', onBackdrop);
        document.addEventListener('keydown', onKeydown);

        modal.classList.add('active');
        navigate(currentPath);
    });
};
