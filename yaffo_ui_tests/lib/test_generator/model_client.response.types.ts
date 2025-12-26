export interface GeneratedTestFile {
  //The filename of the generated playwright test. Only include the file name. The system will organize the folder structure of the test files
  filename: string;

  //The content of the complete, valid TypeScript test file
  code: string;

  //A description of the test file
  description?: string;
}

export interface GeneratedTestResponse {
  //The list of generated files that should be created
  files: GeneratedTestFile[];

  //Context that would be helpful for troubleshooting failed tests
  testContext?: string;

  //Any additional explanation regarding the process of writing the testing code
  explanation?: string;

  //Confidence rating of the generated test scaled from 0 to 1
  confidence?: number;
}