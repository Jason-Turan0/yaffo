// Create tooltip element
const tooltip = document.createElement('div');
tooltip.className = 'tooltip';
document.body.appendChild(tooltip);

// Async submit function
async function submitFaces(personId, faceStatus) {
    const selectedCheckboxes = document.querySelectorAll('#main-form input[name="faces"]:checked');

    if (selectedCheckboxes.length === 0) {
        showNotification('Please select at least one face', 'error');
        return;
    }

    const faceIds = Array.from(selectedCheckboxes).map(cb => cb.value);

    try {
        const response = await fetch(APP_CONFIG.urls.faces_assign, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest'
            },
            body: JSON.stringify({
                faces: faceIds,
                person: personId,
                face_status: faceStatus
            })
        });

        const result = await response.json();

        if (result.success) {
            showNotification(result.message, 'success');

            // Remove face elements from DOM
            result.face_ids.forEach(faceId => {
                const faceElement = document.querySelector(`.face[data-face-id="${faceId}"]`);
                if (faceElement) {
                    faceElement.style.transition = 'opacity 0.3s';
                    faceElement.style.opacity = '0';
                    setTimeout(() => faceElement.remove(), 300);
                }
            });

            // Update face count after removal
            setTimeout(() => {
                const remainingFaces = document.querySelectorAll('.face').length;
                const subtitle = document.querySelector('.subtitle');
                const mainContent = document.querySelector('.main-content');
                const totalUnassigned = mainContent ? mainContent.dataset.unassignedCount : 0;
                if (subtitle) {
                    subtitle.textContent = `Showing ${remainingFaces} of ${totalUnassigned} unassigned face${remainingFaces !== 1 ? 's' : ''}`;
                }

                // Remove empty suggestion groups
                document.querySelectorAll('.suggestion-group').forEach(group => {
                    if (group.querySelectorAll('.face').length === 0) {
                        group.remove();
                    }
                });
            }, 350);
        } else {
            showNotification(result.message, 'error');
        }
    } catch (error) {
        showNotification(`Error: ${error.message}`, 'error');
        console.error('Error submitting faces:', error);
    }
}

// Threshold slider update
const thresholdRange = document.getElementById('threshold-range');
const thresholdValue = document.getElementById('threshold-value');
thresholdRange.addEventListener('input', (e) => {
    thresholdValue.textContent = Math.round(e.target.value * 100) + '%';
});

// Toggle selection on click
document.querySelectorAll('.face').forEach(div => {
    div.addEventListener('click', () => {
        div.classList.toggle('selected');
        const checkbox = div.querySelector('input[type="checkbox"]');
        checkbox.checked = !checkbox.checked;
    });

    // Tooltip on hover
    div.addEventListener('mouseenter', (e) => {
        const similarity = div.dataset.similarity;
        const date = div.dataset.date;
        tooltip.innerHTML = `Similarity: ${similarity}%<br>Date: ${date}`;
        tooltip.classList.add('visible');
        const rect = div.getBoundingClientRect();
        tooltip.style.left = rect.left + rect.width / 2 + 'px';
        tooltip.style.top = rect.top + window.scrollY - 10 + 'px';
        tooltip.style.transform = 'translate(-50%, -100%)';
    });

    div.addEventListener('mouseleave', () => {
        tooltip.classList.remove('visible');
    });
});

// Global select/deselect
document.getElementById('select-all').addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelectorAll('.face').forEach(div => {
        div.classList.add('selected');
        div.querySelector('input[type="checkbox"]').checked = true;
    });
    // Update all group checkboxes
    document.querySelectorAll('.group-select-checkbox').forEach(cb => cb.checked = true);
});

document.getElementById('deselect-all').addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelectorAll('.face').forEach(div => {
        div.classList.remove('selected');
        div.querySelector('input[type="checkbox"]').checked = false;
    });
    // Update all group checkboxes
    document.querySelectorAll('.group-select-checkbox').forEach(cb => cb.checked = false);
});

// Group-level checkboxes
document.querySelectorAll('.group-select-checkbox').forEach(checkbox => {
    const group = checkbox.closest('.suggestion-group');

    checkbox.addEventListener('change', () => {
        const faces = group.querySelectorAll('.face');
        faces.forEach(f => {
            if (checkbox.checked) {
                f.classList.add('selected');
                f.querySelector('input[type="checkbox"]').checked = true;
            } else {
                f.classList.remove('selected');
                f.querySelector('input[type="checkbox"]').checked = false;
            }
        });
    });

    // Also toggle when clicking the label
    const label = checkbox.nextElementSibling;
    label.addEventListener('click', () => {
        checkbox.checked = !checkbox.checked;
        checkbox.dispatchEvent(new Event('change'));
    });
});

// Main form submission - now async
document.querySelectorAll('.assign-group-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
        e.preventDefault();
        const personId = btn.dataset.personId;
        submitFaces(personId, 'ASSIGNED');
    });
});

// Sidebar assign person buttons - now async
document.querySelectorAll('.sidebar-assign-person').forEach(btn => {
    btn.addEventListener('click', (e) => {
        e.preventDefault();
        const personId = btn.dataset.personId;
        submitFaces(personId, 'ASSIGNED');
    });
});

// Sidebar ignore button - now async
document.getElementById('sidebar-ignore-btn').addEventListener('click', (e) => {
    e.preventDefault();
    submitFaces(null, 'IGNORED');
});