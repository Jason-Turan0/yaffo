// @ts-check

window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.VIEW_PHOTO = window.PHOTO_ORGANIZER.VIEW_PHOTO || {};
/**
 * @param {MediaFace[] | null} faceData
 * @param {string} absoluteFilePath
 * @param {string} absoluteFolderPath
 * @param {I18nService} i18n
 * @param {AppConfig} config
 * @returns {PhotoViewApi}
 */
window.PHOTO_ORGANIZER.VIEW_PHOTO.initPhotoView = (
    faceData,
    absoluteFilePath,
    absoluteFolderPath,
    i18n,
    config
) => {
    /** @type {HTMLCanvasElement | null} */
    let canvas = null;
    /** @type {CanvasRenderingContext2D | null} */
    let ctx = null;
    /** @type {HTMLImageElement | null} */
    let mainPhoto = null;

    const initializeFaceHighlighting = () => {
        const canvasElement = document.getElementById('faceCanvas');
        const photoElement = document.getElementById('mainPhoto');

        if (!(canvasElement instanceof HTMLCanvasElement) || !(photoElement instanceof HTMLImageElement) || !faceData) return;
        canvas = canvasElement;
        mainPhoto = photoElement;

        ctx = canvas.getContext('2d');

        mainPhoto.addEventListener('load', updateCanvasSize);
        window.addEventListener('resize', updateCanvasSize);

        updateCanvasSize();
    };

    const updateCanvasSize = () => {
        if (!canvas || !mainPhoto) return;

        canvas.width = mainPhoto.offsetWidth;
        canvas.height = mainPhoto.offsetHeight;
    };

    const highlightFace = (/** @type {number} */ faceId) => {
        if (!faceData || !ctx || !mainPhoto || !canvas) return;

        const faceInfo = faceData.find(f => f.id === faceId);
        if (!faceInfo || !faceInfo.location) return;

        ctx.clearRect(0, 0, canvas.width, canvas.height);

        const naturalWidth = mainPhoto.naturalWidth;
        const naturalHeight = mainPhoto.naturalHeight;
        const displayWidth = mainPhoto.offsetWidth;
        const displayHeight = mainPhoto.offsetHeight;

        const scaleX = displayWidth / naturalWidth;
        const scaleY = displayHeight / naturalHeight;

        const loc = faceInfo.location;
        const x = loc.left * scaleX;
        const y = loc.top * scaleY;
        const width = (loc.right - loc.left) * scaleX;
        const height = (loc.bottom - loc.top) * scaleY;

        ctx.strokeStyle = '#007BFF';
        ctx.lineWidth = 3;
        ctx.strokeRect(x, y, width, height);

        if (faceInfo.people && faceInfo.people.length > 0) {
            const names = faceInfo.people.map(p => p.name).join(', ');

            ctx.font = '14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto';
            const textMetrics = ctx.measureText(names);
            const padding = 8;
            const labelWidth = textMetrics.width + padding * 2;
            const labelHeight = 24;

            ctx.fillStyle = 'rgba(0, 123, 255, 0.9)';
            ctx.fillRect(x, y - labelHeight - 5, labelWidth, labelHeight);

            ctx.fillStyle = 'white';
            ctx.fillText(names, x + padding, y - 10);
        }

        const thumbnail = document.querySelector(`.face-thumbnail[data-face-id="${faceId}"]`);
        if (thumbnail) {
            thumbnail.classList.add('highlighted');
        }
    };

    const clearHighlights = () => {
        if (ctx && canvas) {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }

        document.querySelectorAll('.face-thumbnail.highlighted').forEach(el => {
            el.classList.remove('highlighted');
        });
    };

    const openFile = (/** @type {string} */ filePath) => {
        fetch('/api/open-file', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ path: filePath })
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                window.notification.error(i18n.t('media:actions.openFileFailedWithReason', { reason: data.error }));
            } else {
                window.notification.success(i18n.t('media:actions.openingFile'));
            }
        })
        .catch(error => {
            window.notification.error(i18n.t('media:actions.openFileFailed'));
            console.error('Error:', error);
        });
    };

    const openFolder = (/** @type {string} */ folderPath) => {
        fetch('/api/open-folder', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
            },
            body: JSON.stringify({ path: folderPath })
        })
        .then(response => response.json())
        .then(data => {
            if (data.error) {
                window.notification.error(i18n.t('media:actions.openFolderFailedWithReason', { reason: data.error }));
            } else {
                window.notification.success(i18n.t('media:actions.openingFolder'));
            }
        })
        .catch(error => {
            window.notification.error(i18n.t('media:actions.openFolderFailed'));
            console.error('Error:', error);
        });
    };

    // Re-run indexing for this one photo. Destructive in one specific way — the faces
    // are re-detected, so whoever was assigned to them is forgotten — so it confirms
    // before firing rather than after.
    const reindex = async (/** @type {number} */ mediaItemId) => {
        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: i18n.t('media:reindex.title'),
            message: i18n.t('media:reindex.confirm'),
            confirmText: i18n.t('media:reindex.action'),
            confirmClass: 'btn-danger',
        });
        if (!confirmed) return;

        try {
            const response = await fetch(`/api/media/${mediaItemId}/reindex`, { method: 'POST' });
            const data = await response.json();
            if (!response.ok) {
                window.notification.error(
                    i18n.t('media:reindex.failedWithReason', { reason: data.error })
                );
                return;
            }
            window.notification.success(i18n.t('media:reindex.started'));
        } catch (error) {
            window.notification.error(i18n.t('media:reindex.failed'));
            console.error('Error:', error);
        }
    };

    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            window.history.back();
        }
    });

    window.addEventListener('beforeunload', () => {
        if (document.referrer && document.referrer.includes(window.location.host)) {
            return;
        }
    });

    initializeFaceHighlighting();

    return {
        highlightFace,
        clearHighlights,
        openFile,
        openFolder,
        reindex
    };
};
