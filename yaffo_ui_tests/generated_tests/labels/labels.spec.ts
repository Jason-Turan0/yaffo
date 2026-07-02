import { test, expect, Page, Locator } from '@playwright/test';

type AutomationConfig = {
  confidence_threshold: string;
  max_labels: string;
};

type GalleryLabelOption = {
  label: string;
  value: string;
};

const CUSTOM_LABEL = `ui-label-${Date.now()}`;
const CUSTOM_PROMPT = 'a bright custom test object';

test.describe.configure({ mode: 'serial', timeout: 60_000 });

async function openLabelsSection(page: Page): Promise<Locator> {
  await page.goto('/settings');
  const section = page.locator('#labels-section');
  await expect(section).toBeVisible();
  return section;
}

function labelChip(section: Locator, name: string): Locator {
  return section.locator('.label-chip').filter({ hasText: new RegExp(`^\\s*${escapeRegExp(name)}\\s*$`, 'i') });
}

async function createLabel(page: Page, name: string, prompt = ''): Promise<void> {
  const section = await openLabelsSection(page);
  await section.locator('input[name="name"]').fill(name);
  await section.locator('input[name="prompt"]').fill(prompt);
  await Promise.all([
    page.waitForResponse(response => response.url().includes('/settings/labels') && response.request().method() === 'POST'),
    section.locator('.add-label-form button[type="submit"]').click(),
  ]);
  await expect(labelChip(page.locator('#labels-section'), name)).toHaveCount(1);
}

async function removeLabelIfPresent(page: Page, name: string): Promise<void> {
  const section = await openLabelsSection(page);
  const chip = labelChip(section, name);
  if (await chip.count() === 0) return;

  await Promise.all([
    page.waitForResponse(response => response.url().includes('/settings/labels') && response.request().method() === 'POST'),
    chip.locator('.label-chip-remove').click(),
  ]);
  await expect(labelChip(page.locator('#labels-section'), name)).toHaveCount(0);
}

async function getFirstEnabledLabelName(page: Page): Promise<string> {
  const section = await openLabelsSection(page);
  const enabledChip = section.locator('.label-chip:has(.label-chip-input:checked)').first();
  await expect(enabledChip).toBeVisible();
  const name = await enabledChip.locator('.label-chip-name').textContent();
  expect(name).toBeTruthy();
  return name!.trim();
}

async function setLabelEnabled(page: Page, name: string, enabled: boolean): Promise<void> {
  const section = await openLabelsSection(page);
  const chip = labelChip(section, name);
  await expect(chip).toHaveCount(1);
  const checkbox = chip.locator('.label-chip-input');
  const current = await checkbox.isChecked();
  if (current === enabled) return;

  const [response] = await Promise.all([
    page.waitForResponse(resp => resp.url().includes('/settings/labels') && resp.request().method() === 'POST'),
    chip.locator('.label-chip-toggle').click(),
  ]);
  expect(response.status()).toBe(204);
  await expect(checkbox).toBeChecked({ checked: enabled });
}

async function openClassifyLabelsAutomation(page: Page): Promise<void> {
  await page.goto('/utilities/automations/classify_labels');
  await expect(page.locator('.page-header')).toContainText(/Classify labels/i);
}

async function readAutomationConfig(page: Page): Promise<AutomationConfig> {
  await openClassifyLabelsAutomation(page);
  await page.locator('#configure-automation-button').click();
  const modal = page.locator('#configureAutomationModal');
  await expect(modal).toHaveClass(/active/);
  const config = {
    confidence_threshold: await modal.locator('#config-confidence_threshold').inputValue(),
    max_labels: await modal.locator('#config-max_labels').inputValue(),
  };
  await modal.locator('.modal-actions [name="cancel"]').click();
  await expect(modal).not.toHaveClass(/active/);
  return config;
}

async function saveAutomationConfig(page: Page, config: AutomationConfig): Promise<void> {
  await openClassifyLabelsAutomation(page);
  await page.locator('#configure-automation-button').click();
  const modal = page.locator('#configureAutomationModal');
  await expect(modal).toHaveClass(/active/);
  await modal.locator('#config-confidence_threshold').fill(config.confidence_threshold);
  await modal.locator('#config-max_labels').fill(config.max_labels);
  await Promise.all([
    page.waitForResponse(response => response.url().includes('/utilities/automations/classify_labels/config') && response.request().method() === 'POST'),
    modal.locator('[type="submit"]').click(),
  ]);
  await expect(page).toHaveURL(/\/utilities\/automations\/classify_labels$/);
}

async function firstGalleryCard(page: Page): Promise<Locator> {
  await page.goto('/');
  const firstCard = page.locator('.photo-card').first();
  await expect(firstCard).toBeVisible();
  return firstCard;
}

async function firstMediaViewUrl(page: Page): Promise<string> {
  const card = await firstGalleryCard(page);
  const onclick = await card.getAttribute('onclick');
  const mediaUrl = onclick?.match(/window\.open\('([^']+)'/)?.[1];
  expect(mediaUrl).toMatch(/\/media\/view\/\d+/);
  return mediaUrl!;
}

async function availableGalleryLabels(page: Page, requestedCount = 2): Promise<GalleryLabelOption[]> {
  await page.goto('/');
  const options = page.locator('.multi-select-option input[name="labels"]');
  const optionCount = await options.count();
  const labels: GalleryLabelOption[] = [];
  for (let index = 0; index < optionCount && labels.length < requestedCount; index += 1) {
    const option = options.nth(index);
    const label = await option.getAttribute('data-label');
    const value = await option.getAttribute('value');
    if (label && value) labels.push({ label, value });
  }
  return labels;
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

test.describe('Labels', () => {
  test.afterEach(async ({ page }) => {
    await removeLabelIfPresent(page, CUSTOM_LABEL);
  });

  test('labels_vocabulary_shows_default_labels', async ({ page }) => {
    const section = await openLabelsSection(page);
    await expect(section.locator('.label-chip').first()).toBeVisible();

    const initialCount = await section.locator('.label-chip').count();
    expect(initialCount).toBeGreaterThan(10);
    await expect(labelChip(section, 'dog')).toHaveCount(1);

    await section.locator('#label-filter').fill('dog');
    await expect(labelChip(section, 'dog')).toBeVisible();
    await expect(section.locator('.label-chip:visible')).toHaveCount(1);

    await section.locator('#label-filter').fill('definitely-no-label-matches-this');
    await expect(section.locator('.label-filter-empty')).toBeVisible();
    await expect(section.locator('.label-chip:visible')).toHaveCount(0);

    await section.locator('#label-filter').fill('');
    await expect(section.locator('.label-filter-empty')).toBeHidden();
    await expect(section.locator('.label-chip:visible')).toHaveCount(initialCount);
  });

  test('labels_vocabulary_add_duplicate_and_remove_label', async ({ page }) => {
    await removeLabelIfPresent(page, CUSTOM_LABEL);
    await createLabel(page, CUSTOM_LABEL, CUSTOM_PROMPT);

    let section = page.locator('#labels-section');
    const chip = labelChip(section, CUSTOM_LABEL);
    await expect(chip.locator('.label-chip-info')).toHaveAttribute('data-tooltip', CUSTOM_PROMPT);

    await section.locator('input[name="name"]').fill(CUSTOM_LABEL);
    await section.locator('input[name="prompt"]').fill('duplicate prompt');
    await Promise.all([
      page.waitForResponse(response => response.url().includes('/settings/labels') && response.status() === 204),
      section.locator('.add-label-form button[type="submit"]').click(),
    ]);
    await expect(page.locator('.notification.visible')).toContainText(/already exists/i);
    await expect(section.locator('input[name="name"]')).toHaveValue(CUSTOM_LABEL);

    await removeLabelIfPresent(page, CUSTOM_LABEL);
    section = page.locator('#labels-section');
    await expect(labelChip(section, CUSTOM_LABEL)).toHaveCount(0);
  });

  test('labels_vocabulary_toggle_label_persists', async ({ page }) => {
    const labelName = await getFirstEnabledLabelName(page);

    await setLabelEnabled(page, labelName, false);
    await page.reload();
    await expect(labelChip(page.locator('#labels-section'), labelName).locator('.label-chip-input')).not.toBeChecked();

    await setLabelEnabled(page, labelName, true);
    await page.reload();
    await expect(labelChip(page.locator('#labels-section'), labelName).locator('.label-chip-input')).toBeChecked();
  });

  test('labels_classify_automation_configurable', async ({ page }) => {
    const original = await readAutomationConfig(page);
    const updated = {
      confidence_threshold: original.confidence_threshold === '51' ? '52' : '51',
      max_labels: original.max_labels === '5' ? '6' : '5',
    };

    try {
      await openClassifyLabelsAutomation(page);
      const triggerEvents = page.locator('.automation-trigger-event');
      if (await triggerEvents.count() > 0) {
        await expect(triggerEvents).toContainText(/media_indexed|Media indexed/i);
      }
      await expect(page.locator('a[href$="/triggers/edit"]')).toBeVisible();
      await expect(page.locator('#edit-automation-button')).toHaveCount(0);
      await expect(page.locator('#delete-automation-button')).toHaveCount(0);

      await saveAutomationConfig(page, updated);
      const saved = await readAutomationConfig(page);
      expect(saved).toEqual(updated);
    } finally {
      await saveAutomationConfig(page, original);
    }
  });

  test('labels_reclassify_all_photos_enqueues_background_run', async ({ page }) => {
    const section = await openLabelsSection(page);

    await Promise.all([
      page.waitForResponse(response => response.url().includes('/settings/labels/reclassify') && response.request().method() === 'POST'),
      section.locator('.labels-reclassify button').click(),
    ]);
    await expect(page.locator('.notification.visible')).toContainText(/Re-classifying .*photo/i);

    await openClassifyLabelsAutomation(page);
    await expect(page.locator('#automation-runs')).toContainText(/classif|label|photo|running|completed/i, { timeout: 30_000 });
  });

  test('photo_details_shows_labels_or_no_labels_state', async ({ page }) => {
    const mediaUrl = await firstMediaViewUrl(page);
    await page.goto(mediaUrl);

    const labelsSection = page.locator('.detail-section').filter({ has: page.locator('h3', { hasText: /Labels?/ }) });
    await expect(labelsSection).toBeVisible();

    const chips = labelsSection.locator('.labels-chips .label-chip');
    if (await chips.count() > 0) {
      await expect(labelsSection.locator('h3')).toContainText(/Labels? \(\d+\)/);
      const title = await chips.first().getAttribute('title');
      expect(title).toMatch(/Confidence:/);
    } else {
      await expect(labelsSection.locator('.no-data')).toContainText('No labels');
    }
  });

  test('gallery_filter_by_label_ui_preserves_selected_label_and_match_type', async ({ page }) => {
    const labelNames = await availableGalleryLabels(page, 2);
    test.skip(labelNames.length < 2, 'At least two labels are required to expose the label match type selector.');

    await page.goto('/');
    const wrapper = page.locator('.multi-select-wrapper').filter({
      has: page.locator('input[name="labels"]'),
    });
    await expect(wrapper).toBeVisible();

    await wrapper.locator('.multi-select-header').click();
    for (const { label } of labelNames) {
      const labelName = label;
      await wrapper.locator('.multi-select-option').filter({ hasText: labelName }).locator('input[name="labels"]').check();
    }
    await expect(wrapper.locator('.selected-text')).toContainText(String(labelNames.length));

    await expect(page.locator('#labels-match-type')).toBeVisible();
    await page.locator('#labels-match-type .match-option').filter({ hasText: 'All of these' }).click();
    await Promise.all([
      page.waitForURL(url => url.searchParams.getAll('labels').length >= labelNames.length),
      page.locator('#filter-form button[type="submit"]').click(),
    ]);

    const filteredUrl = new URL(page.url());
    expect(filteredUrl.searchParams.get('labels-match-type')).toBe('all');
    expect(filteredUrl.searchParams.getAll('labels')).toEqual(expect.arrayContaining(labelNames.map(({ value }) => value)));
    for (const { label } of labelNames) {
      const labelName = label;
      await expect(page.locator(`input[name="labels"][data-label="${labelName}"]`)).toBeChecked();
    }
    await page.goto('/');
    await expect(page.locator('.photo-card').first()).toBeVisible();
  });
});
