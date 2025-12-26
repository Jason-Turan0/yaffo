
export interface GeneratorOptions {
    specPath: string;
    outputDir: string;
    baseUrl?: string;
    model?: string;
    yaffoRoot?: string;
    tempDir?: string;
}

export interface GenerationResult {
    success: boolean;
    outputPath?: string;
    logPath?: string;
    error?: string;
}

