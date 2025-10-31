window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initOrganizePhotos = (config) => {
    const jobProgress = window.PHOTO_ORGANIZER.COMPONENTS.jobProgress.init(config, {
        onComplete: (job) => {
            notification.success('Photo organization completed');
        },
        onCancel: (job) => {
            setTimeout(() => window.location.reload(), 2000);
        },
        onError: (job) => {},
        hasResults: false,
        pollingInterval: 1000
    });

    const sourceDirectory = document.getElementById('source-directory');
    const changeDirectoryCheckbox = document.getElementById('change-directory-checkbox');
    const destinationDirectoryGroup = document.getElementById('destination-directory-group');
    const destinationDirectory = document.getElementById('destination-directory');
    const keepOriginalCheckbox = document.getElementById('keep-original-checkbox');
    const organizationPattern = document.getElementById('organization-pattern');
    const previewButton = document.getElementById('preview-button');
    const startButton = document.getElementById('start-button');
    const previewSection = document.getElementById('preview-section');
    const previewContent = document.getElementById('preview-content');

    const toggleDestinationDirectory = () => {
        if (changeDirectoryCheckbox.checked) {
            destinationDirectoryGroup.style.display = 'block';
        } else {
            destinationDirectoryGroup.style.display = 'none';
            destinationDirectory.value = '';
        }
    };

    const generatePreview = async () => {
        const source = sourceDirectory.value.trim();
        const pattern = organizationPattern.value;
        const changeDirectory = changeDirectoryCheckbox.checked;
        const destination = destinationDirectory.value.trim();
        const keepOriginal = keepOriginalCheckbox.checked;

        if (!source) {
            notification.error('Please select a source directory');
            return;
        }

        if (changeDirectory && !destination) {
            notification.error('Please select a destination directory');
            return;
        }

        previewButton.disabled = true;
        previewButton.textContent = 'Generating Preview...';

        try {
            const response = await fetch(config.urls.utilities_organize_photos_preview, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    source_directory: source,
                    destination_directory: changeDirectory ? destination : null,
                    pattern: pattern,
                    keep_original: keepOriginal
                })
            });

            if (response.ok) {
                const data = await response.json();
                displayPreview(data);
                previewSection.style.display = 'block';
            } else {
                const error = await response.json();
                notification.error(error.error || 'Failed to generate preview');
            }
        } catch (error) {
            notification.error('Error generating preview: ' + error.message);
        } finally {
            previewButton.disabled = false;
            previewButton.textContent = 'Preview Organization';
        }
    };

    const displayPreview = (data) => {
        const { total_files, files_to_move, files_staying, file_list } = data;
        const operationType = data.operation === 'copy' ? 'copy' : 'move';
        const operationLabel = data.operation === 'copy' ? 'Files to Copy' : 'Files to Move';

        // Show and populate stats section
        const previewStatsSection = document.getElementById('preview-stats-section');
        previewStatsSection.style.display = 'block';

        document.getElementById('stat-total-files').textContent = total_files;
        document.getElementById('stat-files-to-move').textContent = files_to_move;
        document.getElementById('stat-files-staying').textContent = files_staying;
        document.getElementById('stat-operation-label').textContent = operationLabel;

        // Update file count text
        const fileCountText = document.getElementById('file-count-text');
        if (files_to_move > 0) {
            fileCountText.textContent = `${files_to_move} file(s) to ${operationType}`;
        } else {
            fileCountText.textContent = 'All files are already organized';
        }

        // Build file list
        let html = '';
        if (file_list && file_list.length > 0) {
            file_list.forEach(file => {
                html += `
                    <div class="file-item">
                        <div class="file-source">${file.source}</div>
                        <span class="arrow">→</span>
                        <div class="file-destination">${file.destination}</div>
                    </div>
                `;
            });
        } else {
            html = `<div class="file-item all-organized">All files are already organized correctly!</div>`;
        }

        previewContent.innerHTML = html;
    };

    const startOrganizing = async () => {
        const source = sourceDirectory.value.trim();
        const pattern = organizationPattern.value;
        const changeDirectory = changeDirectoryCheckbox.checked;
        const destination = destinationDirectory.value.trim();
        const keepOriginal = keepOriginalCheckbox.checked;

        if (!source) {
            notification.error('Please select a source directory');
            return;
        }

        if (changeDirectory && !destination) {
            notification.error('Please select a destination directory');
            return;
        }

        const operationType = keepOriginal ? 'copy' : 'move';
        const targetDir = changeDirectory ? destination : source;
        const message = changeDirectory
            ? `This will ${operationType} photos from:\n${source}\n\nTo:\n${targetDir}\n\nOrganized by: ${pattern}\n\nAre you sure?`
            : `This will ${operationType} photos in:\n${source}\n\nPattern: ${pattern}\n\nAre you sure?`;

        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: 'Start Organizing Photos',
            message: message,
            confirmText: 'Start Organizing',
            confirmClass: (changeDirectory && !keepOriginal) ? 'btn-danger' : 'btn-primary'
        });

        if (!confirmed) {
            return;
        }

        startButton.disabled = true;
        startButton.textContent = 'Starting...';

        try {
            const response = await fetch(config.urls.utilities_organize_photos_start, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    source_directory: source,
                    destination_directory: changeDirectory ? destination : null,
                    pattern: pattern,
                    keep_original: keepOriginal
                })
            });

            if (response.ok) {
                const data = await response.json();
                notification.success('Photo organization job started');
                jobProgress.startPolling(data.job_id);
                window.location.reload();
            } else {
                const error = await response.json();
                notification.error(error.error || 'Failed to start organization job');
                startButton.disabled = false;
                startButton.textContent = 'Start Organizing';
            }
        } catch (error) {
            notification.error('Error starting organization: ' + error.message);
            startButton.disabled = false;
            startButton.textContent = 'Start Organizing';
        }
    };

    if (changeDirectoryCheckbox) {
        changeDirectoryCheckbox.addEventListener('change', toggleDestinationDirectory);
    }

    if (previewButton) {
        previewButton.addEventListener('click', generatePreview);
    }

    if (startButton) {
        startButton.addEventListener('click', startOrganizing);
    }

    return {
        generatePreview,
        startOrganizing,
        jobProgress
    };
};