/**
 * ESM-only. `lib/types.ts` and others use `import.meta`, which requires ts-jest's ESM
 * mode, which in turn requires Node's VM modules flag — hence `npm run test:unit`
 * rather than a bare `jest`.
 *
 * Without the flag ts-jest silently falls back to CommonJS and every suite touching
 * `import.meta` dies with "TS1343: The 'import.meta' meta-property is only allowed
 * when the '--module' option is ...", which reads like a tsconfig bug and is not one.
 * Fail with the actual cause instead.
 */
if (!(process.env.NODE_OPTIONS || "").includes("--experimental-vm-modules")) {
  throw new Error(
    "jest must run with NODE_OPTIONS='--experimental-vm-modules' — use `npm run test:unit`."
  );
}

/** @type {import('ts-jest').JestConfigWithTsJest} */
export default {
  preset: 'ts-jest/presets/default-esm',
  testEnvironment: 'node',
  extensionsToTreatAsEsm: ['.ts'],
  moduleNameMapper: {
    '^@lib/(.*)$': '<rootDir>/lib/$1',
    '^(\\.{1,2}/.*)\\.js$': '$1',
  },
  transform: {
    '^.+\\.tsx?$': [
      'ts-jest',
      {
        useESM: true,
        tsconfig: {
          module: 'ESNext',
          moduleResolution: 'bundler',
        },
      },
    ],
  },
  testMatch: ['**/__tests__/**/*.test.ts'],
  moduleFileExtensions: ['ts', 'tsx', 'js', 'jsx', 'json', 'node'],
};