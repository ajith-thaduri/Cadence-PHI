import { expect, test, type Page } from '@playwright/test';

// Fabricated, conspicuous test data only (555-01xx phone, example.com).
async function waitReady(page: Page) {
  await expect
    .poll(async () => (await (await page.request.get('/api/v1/healthz')).json()).status, { timeout: 90_000 })
    .toBe('ready');
}

test.beforeEach(async ({ page }) => {
  await waitReady(page);
  page.on('console', (m) => {
    if (m.type() === 'error') throw new Error(`browser console error: ${m.text()}`);
  });
});

test('overview shows a ready engine and the safety settings', async ({ page }) => {
  await page.goto('/');
  await expect(page.getByRole('heading', { name: 'Overview' })).toBeVisible();
  await expect(page.getByText('Ready', { exact: true })).toBeVisible();
  await expect(page.getByText('Outbound guard')).toBeVisible();
});

test('redact the example: placeholders, double-check, highlights and explanation', async ({ page }) => {
  await page.goto('/redact');
  await page.getByRole('button', { name: 'Example' }).click();
  await page.getByRole('button', { name: 'Redact', exact: true }).click();
  const out = page.getByTestId('sanitized-text');
  await expect(out).toContainText('[NAME_1]');
  await expect(out).toContainText('[PHONE_1]');
  await expect(out).not.toContainText('Maria Lopez');
  await expect(out).not.toContainText('415-555-0100');
  await expect(page.getByText('Ready to share')).toBeVisible();
  await page.screenshot({ path: 'test-results/redact-result.png', fullPage: true });

  await page.getByRole('tab', { name: 'Highlighted original' }).click();
  const marks = page.getByTestId('highlighted-text').getByRole('button');
  expect(await marks.count()).toBeGreaterThanOrEqual(5);
  await marks.first().click();
  await expect(page.getByText('Why was this found?')).toBeVisible();
  await page.screenshot({ path: 'test-results/redact-explain.png' });
  await page.keyboard.press('Escape');

  await page.getByRole('tab', { name: /Findings/ }).click();
  await expect(page.getByRole('cell', { name: 'Phone' }).first()).toBeVisible();
});

test('a too-long text is refused in the browser before it is sent', async ({ page }) => {
  await page.goto('/redact');
  await page.getByLabel('Text to redact').fill('x'.repeat(50_001));
  await expect(page.getByText(/Too long/)).toBeVisible();
  await expect(page.getByRole('button', { name: 'Redact', exact: true })).toBeDisabled();
});

test('run the fast behavioural checklist from the Checks page', async ({ page }) => {
  await page.goto('/checks');
  await page
    .locator('div.mantine-Card-root', { hasText: 'Behavioural checklist' })
    .getByRole('button', { name: 'Run…' })
    .click();
  await page.getByRole('button', { name: 'Start' }).click();
  await expect(page.getByText('python -m harness_eval.checklist run --tier fast').first()).toBeVisible();
  await expect(page.getByRole('dialog').getByText('succeeded')).toBeVisible({ timeout: 90_000 });
  await page.screenshot({ path: 'test-results/checks-job.png' });
});

test('configuration lists the entity catalog and validates', async ({ page }) => {
  await page.goto('/config');
  await page.getByPlaceholder('Search entity types').fill('SSN');
  await expect(page.getByRole('row')).toHaveCount(2); // header + SSN
  await expect(page.getByRole('cell', { name: 'Social Security number SSN' })).toBeVisible();
  await page.getByRole('button', { name: 'Validate all environments' }).click();
  await expect(page.getByText('valid', { exact: true }).first()).toBeVisible();
});

test('release readiness shows the decision and every gate', async ({ page }) => {
  await page.goto('/gates');
  await expect(page.getByText(/Decision: (GO|NO-GO)/)).toBeVisible();
  for (const g of ['G1', 'G5', 'G8', 'G9']) await expect(page.getByText(new RegExp(`^${g} ·`))).toBeVisible();
});
