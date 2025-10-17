window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};

window.PHOTO_ORGANIZER.initLocationAutocomplete = () => {
    const searchInput = document.getElementById('location-search');
    const suggestionsContainer = document.getElementById('location-suggestions');
    const latInput = document.getElementById('proximity-lat');
    const lonInput = document.getElementById('proximity-lon');
    const locationInput = document.getElementById('proximity-location');

    if (!searchInput) return;

    let debounceTimer;
    let currentRequest = null;
    let selectedIndex = -1;
    let currentResults = [];

    const clearSuggestions = () => {
        suggestionsContainer.innerHTML = '';
        suggestionsContainer.classList.remove('active');
        selectedIndex = -1;
        currentResults = [];
    };

    const selectSuggestion = (result) => {
        searchInput.value = result.name;
        latInput.value = result.lat;
        lonInput.value = result.lon;
        locationInput.value = result.name;
        clearSuggestions();
    };

    const highlightSuggestion = (index) => {
        const items = suggestionsContainer.querySelectorAll('.location-suggestion-item:not(.loading)');
        items.forEach((item, i) => {
            if (i === index) {
                item.classList.add('highlighted');
                item.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
            } else {
                item.classList.remove('highlighted');
            }
        });
    };

    const showSuggestions = (results) => {
        clearSuggestions();
        currentResults = results;

        if (results.length === 0) {
            const noResults = document.createElement('div');
            noResults.className = 'location-suggestion-item loading';
            noResults.textContent = 'No results found';
            suggestionsContainer.appendChild(noResults);
            suggestionsContainer.classList.add('active');
            return;
        }

        results.forEach((result, index) => {
            const item = document.createElement('div');
            item.className = 'location-suggestion-item';

            const nameSpan = document.createElement('span');
            nameSpan.className = 'suggestion-name';
            nameSpan.textContent = result.name;

            const sourceSpan = document.createElement('span');
            sourceSpan.className = 'suggestion-source';
            sourceSpan.textContent = result.source === 'photos' ? '(from photos)' : '(OpenStreetMap)';

            item.appendChild(nameSpan);
            item.appendChild(sourceSpan);

            item.addEventListener('click', () => {
                selectSuggestion(result);
            });

            item.addEventListener('mouseenter', () => {
                selectedIndex = index;
                highlightSuggestion(selectedIndex);
            });

            suggestionsContainer.appendChild(item);
        });

        suggestionsContainer.classList.add('active');
    };

    const fetchSuggestions = async (query) => {
        if (query.length < 2) {
            clearSuggestions();
            return;
        }

        if (currentRequest) {
            currentRequest.abort();
        }

        const controller = new AbortController();
        currentRequest = controller;

        suggestionsContainer.innerHTML = '<div class="location-suggestion-item loading">Loading...</div>';
        suggestionsContainer.classList.add('active');

        try {
            const response = await fetch(`/api/location-autocomplete?q=${encodeURIComponent(query)}`, {
                signal: controller.signal
            });

            if (!response.ok) {
                throw new Error('Failed to fetch suggestions');
            }

            const data = await response.json();
            showSuggestions(data.results);
        } catch (error) {
            if (error.name !== 'AbortError') {
                console.error('Error fetching location suggestions:', error);
                clearSuggestions();
            }
        } finally {
            currentRequest = null;
        }
    };

    searchInput.addEventListener('input', (e) => {
        const query = e.target.value.trim();

        clearTimeout(debounceTimer);

        if (query.length < 2) {
            clearSuggestions();
            latInput.value = '';
            lonInput.value = '';
            locationInput.value = '';
            return;
        }

        debounceTimer = setTimeout(() => {
            fetchSuggestions(query);
        }, 300);
    });

    searchInput.addEventListener('focus', (e) => {
        const query = e.target.value.trim();
        if (query.length >= 2 && suggestionsContainer.children.length > 0) {
            suggestionsContainer.classList.add('active');
        }
    });

    searchInput.addEventListener('keydown', (e) => {
        const isOpen = suggestionsContainer.classList.contains('active');
        const hasResults = currentResults.length > 0;

        if (!isOpen || !hasResults) {
            if (e.key === 'Escape') {
                clearSuggestions();
            }
            return;
        }

        switch (e.key) {
            case 'ArrowDown':
                e.preventDefault();
                selectedIndex = Math.min(selectedIndex + 1, currentResults.length - 1);
                highlightSuggestion(selectedIndex);
                break;

            case 'ArrowUp':
                e.preventDefault();
                selectedIndex = Math.max(selectedIndex - 1, 0);
                highlightSuggestion(selectedIndex);
                break;

            case 'Enter':
                e.preventDefault();
                if (selectedIndex >= 0 && selectedIndex < currentResults.length) {
                    selectSuggestion(currentResults[selectedIndex]);
                }
                break;

            case 'Escape':
                e.preventDefault();
                clearSuggestions();
                break;

            case 'Tab':
                if (selectedIndex >= 0 && selectedIndex < currentResults.length) {
                    e.preventDefault();
                    selectSuggestion(currentResults[selectedIndex]);
                }
                break;
        }
    });

    document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !suggestionsContainer.contains(e.target)) {
            clearSuggestions();
        }
    });

    return {
        clearSuggestions,
        fetchSuggestions
    };
};