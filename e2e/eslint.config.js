import js from '@eslint/js'
import globals from 'globals'

export default [
  { ignores: ['node_modules', 'test-results', 'playwright-report'] },
  js.configs.recommended,
  {
    files: ['**/*.js'],
    languageOptions: {
      ecmaVersion: 2023,
      sourceType: 'module',
      // The specs run in node. Code inside page.evaluate() runs in the browser.
      globals: { ...globals.node, ...globals.browser },
    },
    rules: {
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_' }],
    },
  },
]
