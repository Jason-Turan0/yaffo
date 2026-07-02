import { expect, APIRequestContext } from '@playwright/test';

export async function findMediaIdByFilename(request: APIRequestContext, filename: string): Promise<number> {
  const response = await request.get(`/?path=${encodeURIComponent(filename)}&page-size=250`);
  expect(response.ok()).toBeTruthy();

  const match = (await response.text()).match(/\/media\/view\/(\d+)/);
  expect(match, `Expected gallery to include ${filename}`).not.toBeNull();
  return Number(match![1]);
}

export async function findFirstPhotoIdWithDetectedFaces(request: APIRequestContext): Promise<number> {
  const response = await request.get('/?media-type=photo&page-size=250');
  expect(response.ok()).toBeTruthy();

  const ids = [...new Set([...((await response.text()).matchAll(/\/media\/view\/(\d+)/g))]
    .map(match => Number(match[1])))];
  expect(ids.length).toBeGreaterThan(0);

  for (const id of ids) {
    const detailResponse = await request.get(`/media/view/${id}`);
    if (!detailResponse.ok()) {
      continue;
    }
    const detailHtml = await detailResponse.text();
    if (/Faces? \(([1-9]\d*)\)/.test(detailHtml)) {
      return id;
    }
  }

  throw new Error('Expected at least one seeded photo with detected faces');
}
