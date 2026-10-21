import { defineConfig } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const outputRoot = process.env.E2E_OUTPUT_DIR
  ? path.resolve(process.env.E2E_OUTPUT_DIR)
  : path.join(root, 'output/frontend-e2e');

export default defineConfig({
  testDir: './tests',
  testMatch: '**/*.spec.js',
  fullyParallel: false,
  workers: 1,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  outputDir: path.join(outputRoot, 'test-results'),
  reporter: [
    ['list'],
    ['html', { outputFolder: path.join(outputRoot, 'report'), open: 'never' }],
    ['json', { outputFile: path.join(outputRoot, 'results.json') }],
  ],
  use: {
    baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:5174',
    channel: 'msedge',
    headless: true,
    viewport: { width: 1440, height: 1000 },
    actionTimeout: 10_000,
    navigationTimeout: 15_000,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
});
