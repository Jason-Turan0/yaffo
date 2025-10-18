window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initPhotoView = (faceData, absoluteFilePath, absoluteFolderPath, config) => {
    let canvas, ctx, mainPhoto;

    const initializeFaceHighlighting = () => {
        canvas = document.getElementById('faceCanvas');
        mainPhoto = document.getElementById('mainPhoto');

        if (!canvas || !mainPhoto || !faceData) return;

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

    const highlightFace = (faceId) => {
        if (!faceData || !ctx || !mainPhoto) return;

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
        if (ctx) {
            ctx.clearRect(0, 0, canvas.width, canvas.height);
        }

        document.querySelectorAll('.face-thumbnail.highlighted').forEach(el => {
            el.classList.remove('highlighted');
        });
    };

    const openFile = (filePath) => {
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
                notification.error('Failed to open file: ' + data.error);
            } else {
                notification.success('Opening file in default application');
            }
        })
        .catch(error => {
            notification.error('Failed to open file');
            console.error('Error:', error);
        });
    };

    const openFolder = (folderPath) => {
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
                notification.error('Failed to open folder: ' + data.error);
            } else {
                notification.success('Opening folder in file manager');
            }
        })
        .catch(error => {
            notification.error('Failed to open folder');
            console.error('Error:', error);
        });
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
        openFolder
    };
};