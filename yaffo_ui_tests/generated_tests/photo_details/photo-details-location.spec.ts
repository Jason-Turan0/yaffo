import { test, expect } from '@playwright/test';

test.describe('Photo Details - Location Section', () => {
  const PHOTO_ID = 14;

  test('photo_details_location_section_works', async ({ page, context }) => {
    // Navigate to the photo details page
    await page.goto(`/media/view/${PHOTO_ID}`);

    // Wait for page to load
    await expect(page.locator('h2').filter({ hasText: 'Photo Details' })).toBeVisible();

    // Find the Location section
    const locationSection = page.locator('.detail-section').filter({ hasText: 'Location' });
    await expect(locationSection).toBeVisible();

    // Check if location data is present (coordinates)
    const locationDetails = locationSection.locator('.location-details');
    const noData = locationSection.locator('p.no-data');
    
    // If no location data, test is not applicable
    if (await noData.isVisible()) {
      test.skip(true, 'Photo does not have location data');
      return;
    }

    // Verify Location section displays coordinates
    await expect(locationDetails).toBeVisible();
    const coordinatesItem = locationDetails.locator('.detail-item').filter({ hasText: 'Coordinates:' });
    await expect(coordinatesItem).toBeVisible();
    
    const coordinatesValue = coordinatesItem.locator('.detail-value');
    const coordText = await coordinatesValue.textContent();
    // Coordinates are displayed as "latitude°, longitude°"
    expect(coordText).toMatch(/[-+]?\d+\.\d+°?,\s*[-+]?\d+\.\d+°?/);

    // Verify View on Map link is present
    const viewMapLink = locationDetails.locator('a.action-button').filter({ hasText: 'View on Map' });
    await expect(viewMapLink).toBeVisible();

    // Verify link contains correct coordinates in href
    const href = await viewMapLink.getAttribute('href');
    expect(href).toContain('https://www.google.com/maps?q=');
    expect(href).toMatch(/q=[-+]?\d+\.\d+,[-+]?\d+\.\d+/);

    // Verify link opens in new tab
    const target = await viewMapLink.getAttribute('target');
    expect(target).toBe('_blank');

    // Verify the URL coordinates match the displayed coordinates (with tolerance for precision)
    // Extract coordinates from both display and URL
    const displayMatch = coordText?.match(/([-+]?\d+\.\d+)°?,\s*([-+]?\d+\.\d+)°?/);
    const urlMatch = href?.match(/q=([-+]?\d+\.\d+),([-+]?\d+\.\d+)/);
    
    if (displayMatch && urlMatch) {
      const displayLat = parseFloat(displayMatch[1]);
      const displayLng = parseFloat(displayMatch[2]);
      const urlLat = parseFloat(urlMatch[1]);
      const urlLng = parseFloat(urlMatch[2]);
      
      // Compare with small tolerance for rounding differences
      expect(Math.abs(displayLat - urlLat)).toBeLessThan(0.01);
      expect(Math.abs(displayLng - urlLng)).toBeLessThan(0.01);
    }
  });
});