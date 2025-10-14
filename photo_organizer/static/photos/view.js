// Photo Viewer JavaScript

// Face highlighting system
let canvas, ctx, mainPhoto;

function initializeFaceHighlighting() {
    canvas = document.getElementById('faceCanvas');
    mainPhoto = document.getElementById('mainPhoto');

    if (!canvas || !mainPhoto || !window.FACE_DATA) return;

    ctx = canvas.getContext('2d');

    // Update canvas size when image loads
    mainPhoto.addEventListener('load', updateCanvasSize);
    window.addEventListener('resize', updateCanvasSize);

    updateCanvasSize();
}

function updateCanvasSize() {
    if (!canvas || !mainPhoto) return;

    // Set canvas size to match displayed image size
    canvas.width = mainPhoto.offsetWidth;
    canvas.height = mainPhoto.offsetHeight;
}

function highlightFace(faceId) {
    if (!window.FACE_DATA || !ctx || !mainPhoto) return;

    // Find face data
    const faceData = window.FACE_DATA.find(f => f.id === faceId);
    if (!faceData || !faceData.location) return;

    // Clear canvas
    ctx.clearRect(0, 0, canvas.width, canvas.height);

    // Calculate scale factors
    const naturalWidth = mainPhoto.naturalWidth;
    const naturalHeight = mainPhoto.naturalHeight;
    const displayWidth = mainPhoto.offsetWidth;
    const displayHeight = mainPhoto.offsetHeight;

    const scaleX = displayWidth / naturalWidth;
    const scaleY = displayHeight / naturalHeight;

    // Get face location and scale to display size
    const loc = faceData.location;
    const x = loc.left * scaleX;
    const y = loc.top * scaleY;
    const width = (loc.right - loc.left) * scaleX;
    const height = (loc.bottom - loc.top) * scaleY;

    // Draw bounding box
    ctx.strokeStyle = '#007BFF';
    ctx.lineWidth = 3;
    ctx.strokeRect(x, y, width, height);

    // Add label if person assigned
    if (faceData.people && faceData.people.length > 0) {
        const names = faceData.people.map(p => p.name).join(', ');

        // Draw label background
        ctx.font = '14px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto';
        const textMetrics = ctx.measureText(names);
        const padding = 8;
        const labelWidth = textMetrics.width + padding * 2;
        const labelHeight = 24;

        ctx.fillStyle = 'rgba(0, 123, 255, 0.9)';
        ctx.fillRect(x, y - labelHeight - 5, labelWidth, labelHeight);

        // Draw label text
        ctx.fillStyle = 'white';
        ctx.fillText(names, x + padding, y - 10);
    }

    // Highlight thumbnail
    const thumbnail = document.querySelector(`.face-thumbnail[data-face-id="${faceId}"]`);
    if (thumbnail) {
        thumbnail.classList.add('highlighted');
    }
}

function clearHighlights() {
    if (ctx) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    // Remove highlight from thumbnails
    document.querySelectorAll('.face-thumbnail.highlighted').forEach(el => {
        el.classList.remove('highlighted');
    });
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', initializeFaceHighlighting);

// File and folder operations
function openFile(filePath) {
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
}

function openFolder(folderPath) {
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
}

// Keyboard navigation
document.addEventListener('keydown', (e) => {
    // ESC key to close
    if (e.key === 'Escape') {
        window.history.back();
    }
});

// Prevent accidental navigation away
window.addEventListener('beforeunload', (e) => {
    // Only show warning if coming from internal navigation
    // This prevents the warning when using browser back button
    if (document.referrer && document.referrer.includes(window.location.host)) {
        // Don't show warning, just allow navigation
        return;
    }
});
