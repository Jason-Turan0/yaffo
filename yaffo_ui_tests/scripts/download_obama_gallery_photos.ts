import { createHash } from "node:crypto";
import { mkdir, rename, unlink, writeFile } from "node:fs/promises";
import { basename, extname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, type BrowserContext, type Page } from "@playwright/test";


const SCRIPT_DIR = resolve(fileURLToPath(new URL(".", import.meta.url)));
const DEFAULT_OUTPUT_DIR = resolve(SCRIPT_DIR, "../test_data/obama");
const PEOPLE_FILTER = /\b(?:Michelle|Sasha|Malia)\b/i;
const CREDIT_LINE = "Courtesy Barack Obama Presidential Library.";
const DIGITAL_SOURCE_TYPE =
  "http://cv.iptc.org/newscodes/digitalsourcetype/digitalCapture";

type GalleryConfig = {
  slug: string;
  title: string;
  url: string;
  include: "all" | "named-family-members";
};

type GalleryItem = {
  gallerySlug: string;
  galleryTitle: string;
  galleryUrl: string;
  itemAnchor: string;
  downloadUrl: string;
  artifactId: string;
  caption: string;
  altText: string;
};

type DownloadedAsset = {
  fileName: string;
  relativePath: string;
  downloadUrl: string;
  contentType: string;
  byteLength: number;
  sha256: string;
  artifactId: string;
  caption: string;
  sourceItems: GalleryItem[];
};

const GALLERIES: GalleryConfig[] = [
  {
    slug: "obama-family",
    title: "The Obama Family",
    url: "https://obamalibrary.archives.gov/galleries/obama-family#131",
    include: "all",
  },
  {
    slug: "bo-sunny",
    title: "Bo & Sunny",
    url: "https://obamalibrary.archives.gov/galleries/bo-sunny#9",
    include: "named-family-members",
  },
];

function normalizeWhitespace(value: string): string {
  return value.replace(/\s+/g, " ").trim();
}

function sanitizeFileStem(value: string): string {
  const sanitized = value
    .normalize("NFKD")
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "");
  return sanitized || "photo";
}

function escapeMarkdownCell(value: string): string {
  return normalizeWhitespace(value).replace(/\|/g, "\\|");
}

function parseOutputDir(): string {
  const outputIndex = process.argv.indexOf("--output");
  if (outputIndex === -1) {
    return DEFAULT_OUTPUT_DIR;
  }

  const value = process.argv[outputIndex + 1];
  if (!value || value.startsWith("--")) {
    throw new Error("--output requires a directory path");
  }
  return resolve(value);
}

async function scrapeGallery(
  page: Page,
  gallery: GalleryConfig,
): Promise<GalleryItem[]> {
  await page.goto(gallery.url, {
    waitUntil: "domcontentloaded",
    timeout: 60_000,
  });
  await page.locator(".main-slider li.splide__slide.is-image-slide").first().waitFor({
    state: "attached",
    timeout: 30_000,
  });

  const rawItems = await page
    .locator(".main-slider li.splide__slide.is-image-slide")
    .evaluateAll((slides) =>
      slides.map((slide) => {
        const element = slide as HTMLElement;
        const expandedCaption = element.querySelector<HTMLElement>(
          ".field--name-field-caption .readmore-text",
        );
        const captionContainer = element.querySelector<HTMLElement>(
          ".field--name-field-caption",
        );
        const image = element.querySelector<HTMLImageElement>("img");
        const artifact = element.querySelector<HTMLElement>(
          ".field--name-field-artifact-id",
        );
        const hash = element.dataset.splideHash ?? "";

        const captionSource = expandedCaption ?? captionContainer;
        const caption = captionSource?.innerText
          .replace(/\bShow (?:more|less)\b/g, "")
          .trim() ?? "";

        return {
          itemAnchor: hash ? `#${hash}` : "",
          downloadUrl: element.getAttribute("download-path") ?? "",
          artifactId: artifact?.innerText.trim() ?? "",
          caption,
          altText: image?.alt.trim() ?? "",
          searchableText: element.innerText,
        };
      }),
    );

  const selected = rawItems.filter((item) => {
    if (!item.downloadUrl) {
      return false;
    }
    if (gallery.include === "all") {
      return true;
    }
    return PEOPLE_FILTER.test(
      `${item.caption}\n${item.altText}\n${item.searchableText}`,
    );
  });

  return selected.map((item) => ({
    gallerySlug: gallery.slug,
    galleryTitle: gallery.title,
    galleryUrl: gallery.url.split("#", 1)[0],
    itemAnchor: item.itemAnchor,
    downloadUrl: new URL(item.downloadUrl, page.url()).href,
    artifactId: normalizeWhitespace(item.artifactId),
    caption: normalizeWhitespace(item.caption || item.altText),
    altText: normalizeWhitespace(item.altText),
  }));
}

function uniqueItemsByDownloadUrl(items: GalleryItem[]): GalleryItem[][] {
  const grouped = new Map<string, GalleryItem[]>();
  for (const item of items) {
    const existing = grouped.get(item.downloadUrl) ?? [];
    existing.push(item);
    grouped.set(item.downloadUrl, existing);
  }
  return [...grouped.values()];
}

function extensionFor(item: GalleryItem): string {
  const extension = extname(new URL(item.downloadUrl).pathname).toLowerCase();
  return /^\.(?:jpe?g|png|webp|gif|tiff?)$/.test(extension)
    ? extension
    : ".jpg";
}

function nextFileName(
  sourceItems: GalleryItem[],
  usedNames: Set<string>,
): string {
  const representative = sourceItems[0];
  const urlStem = basename(
    new URL(representative.downloadUrl).pathname,
    extname(new URL(representative.downloadUrl).pathname),
  );
  const baseStem = sanitizeFileStem(representative.artifactId || urlStem);
  const extension = extensionFor(representative);

  let candidate = `${baseStem}${extension}`;
  let suffix = 2;
  while (usedNames.has(candidate.toLowerCase())) {
    candidate = `${baseStem}-${suffix}${extension}`;
    suffix += 1;
  }
  usedNames.add(candidate.toLowerCase());
  return candidate;
}

async function downloadAsset(
  context: BrowserContext,
  sourceItems: GalleryItem[],
  outputDir: string,
  usedNames: Set<string>,
): Promise<DownloadedAsset> {
  const representative = sourceItems[0];
  const response = await context.request.get(representative.downloadUrl, {
    failOnStatusCode: false,
    headers: {
      Referer: representative.galleryUrl,
    },
    timeout: 60_000,
  });

  if (!response.ok()) {
    throw new Error(
      `Download failed (${response.status()}): ${representative.downloadUrl}`,
    );
  }

  const contentType = response.headers()["content-type"] ?? "";
  if (!contentType.toLowerCase().startsWith("image/")) {
    throw new Error(
      `Expected image content but received ${contentType || "unknown"}: ${representative.downloadUrl}`,
    );
  }

  const body = await response.body();
  if (body.length === 0) {
    throw new Error(`Downloaded an empty file: ${representative.downloadUrl}`);
  }

  const fileName = nextFileName(sourceItems, usedNames);
  const relativePath = `images/${fileName}`;
  const destination = resolve(outputDir, relativePath);
  const temporary = `${destination}.part`;
  await writeFile(temporary, body);
  try {
    await rename(temporary, destination);
  } catch (error) {
    await unlink(temporary).catch(() => undefined);
    throw error;
  }

  return {
    fileName,
    relativePath,
    downloadUrl: representative.downloadUrl,
    contentType,
    byteLength: body.length,
    sha256: createHash("sha256").update(body).digest("hex"),
    artifactId: representative.artifactId,
    caption: representative.caption,
    sourceItems,
  };
}

function buildAttribution(
  accessedAt: string,
  assets: DownloadedAsset[],
): string {
  const rows = assets.flatMap((asset) =>
    asset.sourceItems.map((source) => {
      const sourceUrl = `${source.galleryUrl}${source.itemAnchor}`;
      return `| ${escapeMarkdownCell(asset.relativePath)} | ${escapeMarkdownCell(source.artifactId || "Not listed")} | ${escapeMarkdownCell(source.galleryTitle)} | [Gallery item](${sourceUrl}) | [Original file](${source.downloadUrl}) | ${escapeMarkdownCell(source.caption)} |`;
    }),
  );

  return `# Attribution and source record

${CREDIT_LINE}

These files were downloaded from gallery pages published by the Barack Obama
Presidential Library, part of the National Archives and Records Administration
(NARA), on ${accessedAt.slice(0, 10)}.

Source galleries:

- [The Obama Family](${GALLERIES[0].url.split("#", 1)[0]}) — all image items.
- [Bo & Sunny](${GALLERIES[1].url.split("#", 1)[0]}) — image items whose caption,
  alt text, or gallery text refers to Michelle, Sasha, or Malia.

NARA encourages the credit line above. Federal photographic records are generally
available for publication, but some holdings may contain third-party material
with separate copyright restrictions. The downloader preserves captions and
explicit photographer credits so each item's status can be reviewed. See
[NARA's publishing guidance](https://www.archives.gov/research/still-pictures/publish-photos).

## Files

| Local file | Artifact ID | Gallery | Gallery source | Original image | Caption and credit |
|---|---|---|---|---|---|
${rows.join("\n")}
`;
}

async function main(): Promise<void> {
  const outputDir = parseOutputDir();
  const imagesDir = resolve(outputDir, "images");
  await mkdir(imagesDir, { recursive: true });

  const browser = await chromium.launch({ headless: true });
  try {
    const context = await browser.newContext({
      userAgent:
        "Yaffo test fixture downloader (+https://github.com/jasonturan/yaffo)",
    });
    const page = await context.newPage();

    const galleryItems: GalleryItem[] = [];
    for (const gallery of GALLERIES) {
      const selected = await scrapeGallery(page, gallery);
      galleryItems.push(...selected);
      console.log(`${gallery.title}: selected ${selected.length} images`);
    }

    const groupedItems = uniqueItemsByDownloadUrl(galleryItems);
    const usedNames = new Set<string>();
    const assets: DownloadedAsset[] = [];
    for (const sourceItems of groupedItems) {
      const asset = await downloadAsset(
        context,
        sourceItems,
        outputDir,
        usedNames,
      );
      assets.push(asset);
      console.log(`Downloaded ${asset.relativePath}`);
      await page.waitForTimeout(150);
    }

    assets.sort((left, right) =>
      left.relativePath.localeCompare(right.relativePath),
    );
    const accessedAt = new Date().toISOString();
    const manifest = {
      generatedAt: accessedAt,
      creditLine: CREDIT_LINE,
      digitalSourceType: DIGITAL_SOURCE_TYPE,
      sources: GALLERIES,
      assets,
    };
    await writeFile(
      resolve(outputDir, "manifest.json"),
      `${JSON.stringify(manifest, null, 2)}\n`,
      "utf8",
    );
    await writeFile(
      resolve(outputDir, "ATTRIBUTION.md"),
      buildAttribution(accessedAt, assets),
      "utf8",
    );

    console.log(
      `Saved ${assets.length} unique images and attribution metadata to ${outputDir}`,
    );
  } finally {
    await browser.close();
  }
}

main().catch((error: unknown) => {
  console.error(error instanceof Error ? error.message : error);
  process.exitCode = 1;
});
