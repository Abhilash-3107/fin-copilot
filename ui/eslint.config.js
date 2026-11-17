import js from '@eslint/js'
import globals from 'globals'
import react from 'eslint-plugin-react'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default [
  { ignores: ['dist', 'node_modules'] },
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      ...js.configs.recommended.rules,
      ...reactHooks.configs.recommended.rules,
      // Count components referenced only in JSX as "used" so no-unused-vars
      // doesn't flag every imported component. (We skip the rest of the noisy
      // react ruleset - no prop-types in this codebase.)
      'react/jsx-uses-vars': 'error',
      'react/jsx-uses-react': 'off', // new JSX runtime; React import not required
      // Unused vars are real bugs (dead imports, typos), but allow deliberately
      // ignored args/catch bindings via a leading underscore.
      'no-unused-vars': ['error', { argsIgnorePattern: '^_', varsIgnorePattern: '^_', caughtErrors: 'none' }],
      // Deliberate best-effort swallows (`catch (_) {}`) are an intentional idiom here.
      'no-empty': ['error', { allowEmptyCatch: true }],
      'no-console': ['warn', { allow: ['warn', 'error'] }],
      // Off by choice: this codebase deliberately co-locates each context's hook
      // with its provider and small helpers (formatRupees, SOURCE_PILL) with
      // their component. That trips this HMR-only heuristic without any
      // correctness cost, and the noise was drowning real exhaustive-deps signal.
      'react-refresh/only-export-components': 'off',
    },
  },
  // Test files run under vitest globals and jsdom.
  {
    files: ['**/*.test.{js,jsx}', 'src/test/**'],
    languageOptions: {
      globals: { ...globals.node, ...globals.vitest },
    },
    rules: {
      // vi.waitFor polls outside act(), so a state update it waits on logs an
      // "not wrapped in act" warning. Testing Library's waitFor wraps it.
      'no-restricted-properties': ['error', {
        object: 'vi',
        property: 'waitFor',
        message: "Use waitFor from '@testing-library/react': vi.waitFor polls outside act().",
      }],
      // A router built straight from react-router-dom lacks the future flags
      // and makes React Router warn on every test. Use src/test/router.jsx.
      'no-restricted-imports': ['error', {
        paths: [{
          name: 'react-router-dom',
          importNames: ['MemoryRouter', 'HashRouter', 'BrowserRouter'],
          message: "Import the router from the test helper (src/test/router.jsx): it sets the app's future flags.",
        }],
      }],
    },
  },
  // The helper itself is the one place that builds routers.
  {
    files: ['src/test/router.jsx'],
    rules: { 'no-restricted-imports': 'off' },
  },
]
