import js from "@eslint/js";
import globals from "globals";

export default [
    {
        ignores: [
            "yaffo/static/vendor/**",
        ],
    },
    {
        files: ["yaffo/static/**/*.js"],
        ...js.configs.recommended,
        languageOptions: {
            ecmaVersion: "latest",
            sourceType: "script",
            globals: {
                ...globals.browser,
                GridStack: "readonly",
                ol: "readonly",
            },
        },
        rules: {
            ...js.configs.recommended.rules,
            "no-unused-vars": [
                "error",
                {
                    argsIgnorePattern: "^_",
                    caughtErrorsIgnorePattern: "^_",
                    varsIgnorePattern: "^_",
                },
            ],
            "prefer-const": "error",
        },
    },
];
