export interface GeneratedTestFile {
  filename: string;
  code: string;
  description: string;
}

export interface GeneratedTestResponse {
  files: GeneratedTestFile[];
  notes?: string;
  confidence: number;
}