import {expect} from '@playwright/test';
import {defineWalkthrough} from '../../_support';
import {remoteGallery, cleanupSharing} from '../../_support/sharing-fixtures';

const viewport = {width: 1400, height: 1000};

export default defineWalkthrough({
    page: 'sharing/transfers-connections',
    shots: {
        'transfer-status.webp': {
            viewport, goto: '/sharing/settings', clip: '#transfers-panel',
            ignoreRegions: ['.transfer-progress-meta'],
            annotations: {'transfer-state': '.transfer-batch-info', 'transfer-progress': '.transfer-progress-meta', 'transfer-actions': '.transfer-batch-actions'},
            setup: async page => {
                await remoteGallery(page, true);
                await page.locator('#pull-selected-button').click();
                await expect(page.locator('.transfer-batch[data-state="completed"]')).toBeVisible({timeout: 30000});
            }, teardown: cleanupSharing,
        },
    },
});
