import {execFileSync} from "child_process";
import {unlinkSync} from "fs";
import {VENV_PYTHON} from "./python";

/**
 * WebP quality. 88 is visually lossless on UI text and roughly a tenth the size of
 * PNG on shots containing photographs (8.3 MB -> 0.85 MB across the first five).
 */
export const WEBP_QUALITY = 88;

/** Re-encode a captured PNG as WebP in place, dropping the PNG. */
export const toWebp = (pngPath: string): string => {
    const webpPath = pngPath.replace(/\.png$/, ".webp");
    execFileSync(VENV_PYTHON, [
        "-c",
        "import sys;from PIL import Image;" +
        "Image.open(sys.argv[1]).convert('RGB')" +
        ".save(sys.argv[2],'WEBP',quality=int(sys.argv[3]),method=6)",
        pngPath, webpPath, String(WEBP_QUALITY),
    ]);
    unlinkSync(pngPath);
    return webpPath;
};
