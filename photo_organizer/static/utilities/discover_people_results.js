window.PHOTO_ORGANIZER = window.PHOTO_ORGANIZER || {};
window.PHOTO_ORGANIZER.initDiscoverPeopleResults = (jobId, clusters, people, config) => {
    const processedCountDisplay = document.getElementById('processed-count');
    let processedCount = 0;

    const updateProcessedCount = () => {
        if (processedCountDisplay) {
            processedCountDisplay.textContent = processedCount;
        }
    };

    const getClusterByLabel = (clusterLabel) => {
        return clusters.find(c => c.label === clusterLabel);
    };

    const getClusterFaceIds = (clusterLabel) => {
        const cluster = getClusterByLabel(clusterLabel);
        return cluster ? cluster.faces.map(f => f.id) : [];
    };

    const removeCluster = async (clusterLabel) => {
        try {
            const response = await fetch(`/utilities/discover-people/results/${jobId}/clusters/${encodeURIComponent(clusterLabel)}`, {
                method: 'DELETE'
            });

            if (!response.ok) {
                console.error('Failed to delete cluster from database');
            }
        } catch (error) {
            console.error('Error deleting cluster:', error);
        }

        const wrapper = document.querySelector(`.cluster-wrapper[data-cluster-label="${clusterLabel}"]`);
        let nextWrapper = null;

        if (wrapper) {
            nextWrapper = wrapper.nextElementSibling;

            wrapper.classList.add('removing');
            setTimeout(() => {
                wrapper.remove();

                if (nextWrapper && nextWrapper.classList.contains('cluster-wrapper')) {
                    const nextPanel = nextWrapper.querySelector('.collapsible-panel');
                    if (nextPanel) {
                        const panelId = nextPanel.getAttribute('data-panel-id');
                        const panelHeader = nextPanel.querySelector('.panel-header');
                        const isExpanded = panelHeader && panelHeader.getAttribute('aria-expanded') === 'true';

                        if (!isExpanded && panelId) {
                            window.togglePanel(panelId);
                        }
                    }
                }
            }, 300);
        }
        processedCount++;
        updateProcessedCount();
        deleteJobWhenAllProcessed();
    };

    const handleCreatePerson = async (clusterLabel, personName) => {
        const faceIds = getClusterFaceIds(clusterLabel);
        const button = document.querySelector(`.btn-create-person[data-cluster-label="${clusterLabel}"]`);

        if (!personName || !personName.trim()) {
            notification.error('Please enter a person name');
            return;
        }

        button.disabled = true;
        button.textContent = 'Creating...';

        try {
            const createResponse = await fetch('/api/people/create', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    name: personName.trim()
                })
            });

            if (!createResponse.ok) {
                const error = await createResponse.json();
                throw new Error(error.error || 'Failed to create person');
            }

            const createData = await createResponse.json();
            const personId = createData.person_id;

            const assignResponse = await fetch(config.urls.faces_assign, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    faceStatus: 'ASSIGNED',
                    person: personId,
                    faces: faceIds
                })
            });

            if (assignResponse.ok) {
                notification.success(`Created "${personName}" and assigned ${faceIds.length} faces`);
                removeCluster(clusterLabel);
            } else {
                const error = await assignResponse.json();
                notification.error(error.message || 'Failed to assign faces');
                button.disabled = false;
                button.textContent = 'Create Person';
            }
        } catch (error) {
            notification.error('Error: ' + error.message);
            button.disabled = false;
            button.textContent = 'Create Person';
        }
    };

    const handleAssignToPerson = async (clusterLabel, personId) => {
        const faceIds = getClusterFaceIds(clusterLabel);
        const button = document.querySelector(`.btn-assign-person[data-cluster-label="${clusterLabel}"]`);

        if (!personId) {
            notification.error('Please select a person');
            return;
        }

        button.disabled = true;
        button.textContent = 'Assigning...';

        try {
            const response = await fetch(config.urls.faces_assign, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    faceStatus: 'ASSIGNED',
                    person: parseInt(personId),
                    faces: faceIds
                })
            });

            if (response.ok) {
                const person = people.find(p => p.id === parseInt(personId));
                const personName = person ? person.name : 'person';
                notification.success(`Assigned ${faceIds.length} faces to ${personName}`);
                removeCluster(clusterLabel);
            } else {
                const error = await response.json();
                notification.error(error.message || 'Failed to assign faces');
                button.disabled = false;
                button.textContent = 'Assign to Person';
            }
        } catch (error) {
            notification.error('Error assigning faces: ' + error.message);
            button.disabled = false;
            button.textContent = 'Assign to Person';
        }
    };

    const handleIgnoreCluster = async (clusterLabel) => {
        const faceIds = getClusterFaceIds(clusterLabel);
        const button = document.querySelector(`.btn-ignore-cluster[data-cluster-label="${clusterLabel}"]`);

        button.disabled = true;
        button.textContent = 'Ignoring...';

        try {
            const response = await fetch(config.urls.faces_assign, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({
                    faceStatus: 'IGNORED',
                    faces: faceIds
                })
            });

            if (response.ok) {
                notification.info(`Ignored ${faceIds.length} faces`);
                removeCluster(clusterLabel);
            } else {
                const error = await response.json();
                notification.error(error.message || 'Failed to ignore faces');
                button.disabled = false;
                button.textContent = 'Ignore Cluster';
            }
        } catch (error) {
            notification.error('Error ignoring faces: ' + error.message);
            button.disabled = false;
            button.textContent = 'Ignore Cluster';
        }
    };

    const handleDeleteCluster = (clusterLabel) => {
        removeCluster(clusterLabel);
        notification.info('Cluster deleted from results');
    };

    document.querySelectorAll('.new-person-name').forEach(input => {
        input.addEventListener('input', (e) => {
            const clusterLabel = e.target.dataset.clusterLabel;
            const button = document.querySelector(`.btn-create-person[data-cluster-label="${clusterLabel}"]`);
            if (button) {
                button.disabled = !e.target.value.trim();
            }
        });

        input.addEventListener('keypress', (e) => {
            if (e.key === 'Enter') {
                const clusterLabel = e.target.dataset.clusterLabel;
                handleCreatePerson(clusterLabel, e.target.value);
            }
        });
    });

    document.querySelectorAll('.btn-create-person').forEach(button => {
        button.addEventListener('click', (e) => {
            const clusterLabel = e.target.dataset.clusterLabel;
            const input = document.querySelector(`.new-person-name[data-cluster-label="${clusterLabel}"]`);
            handleCreatePerson(clusterLabel, input.value);
        });
    });

    document.querySelectorAll('.existing-person-select').forEach(select => {
        select.addEventListener('change', (e) => {
            const clusterLabel = e.target.dataset.clusterLabel;
            const button = document.querySelector(`.btn-assign-person[data-cluster-label="${clusterLabel}"]`);
            if (button) {
                button.disabled = !e.target.value;
            }
        });
    });

    document.querySelectorAll('.btn-assign-person').forEach(button => {
        button.addEventListener('click', (e) => {
            const clusterLabel = e.target.dataset.clusterLabel;
            const select = document.querySelector(`.existing-person-select[data-cluster-label="${clusterLabel}"]`);
            handleAssignToPerson(clusterLabel, select.value);
        });
    });

    document.querySelectorAll('.btn-ignore-cluster').forEach(button => {
        button.addEventListener('click', (e) => {
            const clusterLabel = e.target.dataset.clusterLabel;
            handleIgnoreCluster(clusterLabel);
        });
    });

    document.querySelectorAll('.btn-delete-cluster').forEach(button => {
        button.addEventListener('click', (e) => {
            const clusterLabel = e.target.dataset.clusterLabel;
            handleDeleteCluster(clusterLabel);
        });
    });

    const deleteJobWhenAllProcessed = async () => {
        if (processedCount === clusters.length) {
            try {
                await fetch(config.buildUrl('utilities_delete_job', {job_id: jobId}), {
                    method: 'POST'
                });
            } catch (error) {
                console.error('Failed to delete job:', error);
            }
        }
    };

    const keyboardShortcutMap = new Map();
    document.querySelectorAll('.shortcut-item[data-shortcut]').forEach(element => {
        const shortcut = element.dataset.shortcut;
        const personId = element.dataset.personId;
        if (shortcut && personId) {
            keyboardShortcutMap.set(shortcut, {
                personId: personId,
                element: element
            });
        }
    });

    const flashElement = (element) => {
        element.classList.add('keyboard-activated');
        setTimeout(() => {
            element.classList.remove('keyboard-activated');
        }, 300);
    };

    const getFirstVisibleCluster = () => {
        const wrappers = document.querySelectorAll('.cluster-wrapper:not(.removing)');
        return wrappers.length > 0 ? wrappers[0] : null;
    };

    document.addEventListener('keydown', (e) => {
        if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA' || e.target.tagName === 'SELECT') {
            return;
        }

        const firstCluster = getFirstVisibleCluster();
        if (!firstCluster) return;

        const clusterLabel = firstCluster.dataset.clusterLabel;

        if (e.key >= '1' && e.key <= '9') {
            e.preventDefault();
            const shortcut = keyboardShortcutMap.get(e.key);
            if (shortcut) {
                flashElement(shortcut.element);
                handleAssignToPerson(clusterLabel, shortcut.personId);
            }
        }

        if (e.key === 'i' || e.key === 'I') {
            e.preventDefault();
            handleIgnoreCluster(clusterLabel);
        }

        if (e.key === 'd' || e.key === 'D') {
            e.preventDefault();
            handleDeleteCluster(clusterLabel);
        }
    });

    updateProcessedCount();

    return {
        handleCreatePerson,
        handleAssignToPerson,
        handleIgnoreCluster,
        handleDeleteCluster
    };
};