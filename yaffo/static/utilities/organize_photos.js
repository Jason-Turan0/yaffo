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
        pollingInterval: 5000
    });

    const sourceDirectory = document.getElementById('source-directory');
    const moveFilesCheckbox = document.getElementById('move-files-checkbox');
    const destinationDirectoryGroup = document.getElementById('destination-directory-group');
    const destinationDirectory = document.getElementById('destination-directory');
    const organizationPattern = document.getElementById('organization-pattern');
    const previewButton = document.getElementById('preview-button');
    const startButton = document.getElementById('start-button');
    const previewSection = document.getElementById('preview-section');
    const previewContent = document.getElementById('preview-content');

    const toggleDestinationDirectory = () => {
        if (moveFilesCheckbox.checked) {
            destinationDirectoryGroup.style.display = 'block';
        } else {
            destinationDirectoryGroup.style.display = 'none';
            destinationDirectory.value = '';
        }
    };

    const generatePreview = async () => {
        const source = sourceDirectory.value.trim();
        const pattern = organizationPattern.value;
        const moveFiles = moveFilesCheckbox.checked;
        const destination = destinationDirectory.value.trim();

        if (!source) {
            notification.error('Please select a source directory');
            return;
        }

        if (moveFiles && !destination) {
            notification.error('Please select a destination directory');
            return;
        }

        previewButton.disabled = true;
        previewButton.textContent = 'Generating Preview...';

        try {
            // TODO: Call backend API to generate preview
            // For now, show placeholder
            const modeText = moveFiles ? 'move to destination' : 'organize in place';
            previewContent.innerHTML = `
                <div>Source: ${source}</div>
                <div>Pattern: ${pattern}</div>
                <div>Mode: ${modeText}</div>
                ${moveFiles ? `<div>Destination: ${destination}</div>` : ''}
                <div>This will be populated with actual folder structure preview</div>
            `;
            previewSection.style.display = 'block';
        } catch (error) {
            notification.error('Error generating preview: ' + error.message);
        } finally {
            previewButton.disabled = false;
            previewButton.textContent = 'Preview Organization';
        }
    };

    const startOrganizing = async () => {
        const source = sourceDirectory.value.trim();
        const pattern = organizationPattern.value;
        const moveFiles = moveFilesCheckbox.checked;
        const destination = destinationDirectory.value.trim();

        if (!source) {
            notification.error('Please select a source directory');
            return;
        }

        if (moveFiles && !destination) {
            notification.error('Please select a destination directory');
            return;
        }

        const modeText = moveFiles ? 'move' : 'organize in place';
        const message = moveFiles
            ? `This will move photos from:\n${source}\n\nTo:\n${destination}\n\nOrganized by: ${pattern}\n\nAre you sure?`
            : `This will organize photos in:\n${source}\n\nPattern: ${pattern}\n\nAre you sure?`;

        const confirmed = await window.PHOTO_ORGANIZER.confirmDialog({
            title: 'Start Organizing Photos',
            message: message,
            confirmText: 'Start Organizing',
            confirmClass: moveFiles ? 'btn-danger' : 'btn-primary'
        });

        if (!confirmed) {
            return;
        }

        startButton.disabled = true;
        startButton.textContent = 'Starting...';

        try {
            // TODO: Call backend API to start organizing
            notification.info('Photo organization job will be started here');
            // const response = await fetch(config.urls.utilities_organize_photos_start, {
            //     method: 'POST',
            //     headers: {
            //         'Content-Type': 'application/json',
            //     },
            //     body: JSON.stringify({
            //         source_directory: source,
            //         destination_directory: moveFiles ? destination : null,
            //         pattern: pattern,
            //         move_files: moveFiles
            //     })
            // });
            //
            // if (response.ok) {
            //     notification.success('Photo organization job started');
            //     window.location.reload();
            // } else {
            //     const error = await response.json();
            //     notification.error(error.error || 'Failed to start organization job');
            //     startButton.disabled = false;
            //     startButton.textContent = 'Start Organizing';
            // }
        } catch (error) {
            notification.error('Error starting organization: ' + error.message);
            startButton.disabled = false;
            startButton.textContent = 'Start Organizing';
        }
    };

    if (moveFilesCheckbox) {
        moveFilesCheckbox.addEventListener('change', toggleDestinationDirectory);
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