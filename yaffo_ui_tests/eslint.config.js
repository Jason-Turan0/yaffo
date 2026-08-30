import tseslint from "@typescript-eslint/eslint-plugin";

export default [
    {
        ignores: [
            "dist/**",
            "node_modules/**",
            "reports/**",
        ],
    },
    ...tseslint.configs["flat/recommended"],
    {
        files: ["**/*.ts", "**/*.tsx"],
        rules: {
            "@typescript-eslint/no-unused-vars": [
                "error",
                {
                    argsIgnorePattern: "^_",
                    caughtErrorsIgnorePattern: "^_",
                    varsIgnorePattern: "^_",
                },
            ],
            "no-control-regex": "error",
            "prefer-const": "error",
        },
    },
    {
        files: ["generated_tests/**/*.ts"],
        rules: {
            "@typescript-eslint/no-explicit-any": "off",
        },
    },
];
