
export interface GeneratorOptions {
    specPath: string;
    baseUrl?: string;
    model?: string;
    yaffoRoot?: string;
    tempDir?: string;
}

export interface GenerationResult {
    success: boolean;
    logPath?: string;
    error?: string;
}

