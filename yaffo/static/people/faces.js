 // Create tooltip element
 const tooltip = document.createElement('div');
tooltip.className = 'tooltip';
document.body.appendChild(tooltip);

    // Similarity slider updates
const minSimilarityRange = document.getElementById('min_similarity-range');
const minSimilarityValue = document.getElementById('min_similarity-value');
if (minSimilarityRange) {
    minSimilarityRange.addEventListener('input', (e) => {
        minSimilarityValue.textContent = Math.round(e.target.value * 100) + '%';
    });
}

const maxSimilarityRange = document.getElementById('max_similarity-range');
const maxSimilarityValue = document.getElementById('max_similarity-value');
if (maxSimilarityRange) {
    maxSimilarityRange.addEventListener('input', (e) => {
        maxSimilarityValue.textContent = Math.round(e.target.value * 100) + '%';
    });
}

// Toggle selection on click
document.querySelectorAll('.face-card').forEach(card => {
    card.addEventListener('click', () => {
        card.classList.toggle('selected');
        const checkbox = card.querySelector('input[type="checkbox"]');
        checkbox.checked = !checkbox.checked;
    });

    // Tooltip on hover
    card.addEventListener('mouseenter', (e) => {
        const similarity = card.dataset.similarity;
        const date = PHOTO_ORGANIZER.utils.date.format(card.dataset.date);
        tooltip.innerHTML = `Date: ${date}<br>Similarity: ${similarity}%`;
        tooltip.classList.add('visible');
    });

    card.addEventListener('mousemove', (e) => {
        const rect = card.getBoundingClientRect();
        tooltip.style.left = rect.left + rect.width / 2 + 'px';
        tooltip.style.top = rect.top - 10 + 'px';
        tooltip.style.transform = 'translate(-50%, -100%)';
    });

    card.addEventListener('mouseleave', () => {
        tooltip.classList.remove('visible');
    });
});

// Select all / deselect all
document.getElementById('select-all').addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelectorAll('.face-card').forEach(card => {
        card.classList.add('selected');
        card.querySelector('input[type="checkbox"]').checked = true;
    });
});

document.getElementById('deselect-all').addEventListener('click', (e) => {
    e.preventDefault();
    document.querySelectorAll('.face-card').forEach(card => {
        card.classList.remove('selected');
        card.querySelector('input[type="checkbox"]').checked = false;
    });
});

// Remove selected faces
function removeSelectedFaces() {
    const selectedCheckboxes = document.querySelectorAll('.face-card input[type="checkbox"]:checked');

    if (selectedCheckboxes.length === 0) {
        alert('Please select at least one face to remove');
        return;
    }

    const container = document.getElementById('selected-faces-container');
    container.innerHTML = '';

    selectedCheckboxes.forEach(cb => {
        const hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = 'faces';
        hidden.value = cb.value;
        container.appendChild(hidden);
    });

    document.getElementById('remove-form').submit();
}