import { expect, test } from '@playwright/test';
import { fileURLToPath } from 'node:url';

/** Written by scripts/seed_e2e.py (synthetic: tests/fixtures.py). */
const SAMPLE_FILE = fileURLToPath(
  new URL('./.data/Acme Supply 2025.07.02 300.00 Ledger.xlsx', import.meta.url),
);

test.describe.configure({ mode: 'serial' });

test('upload → review → export: the main revenue path', async ({ page }) => {
  // Upload: the file name fills in supplier and date; processing runs in the background.
  await page.goto('/upload');
  await page.locator('input[type="file"]').setInputFiles(SAMPLE_FILE);
  await expect(page.getByText('Inferred from the file name: Acme Supply')).toBeVisible();
  await expect(page.getByLabel(/Date received/)).toHaveValue('2025-07-02');
  await page.getByRole('button', { name: 'Process file' }).click();

  await expect(page).toHaveURL(/\/upload\?job=\d+$/);
  await expect(page.getByText(/1 unmatched customer needs review/)).toBeVisible({
    timeout: 20_000,
  });
  await expect(page.getByText('PAY-ACME-1')).toBeVisible();

  // A reload keeps the result (the job is in the URL).
  await page.reload();
  await expect(page.getByText(/1 unmatched customer needs review/)).toBeVisible();

  // Review: no good suggestion for "Zyxw Qvbn Clinic", so create the facility and approve.
  await page.getByRole('link', { name: /Review page/ }).click();
  const card = page.getByRole('article', { name: 'Zyxw Qvbn Clinic' });
  await expect(card).toBeVisible();
  await card.getByRole('button', { name: /None of these/ }).click();
  await card.getByLabel(/Premier\/Alliant entity code/).fill('EC900');
  await card.getByRole('button', { name: /Create facility/ }).click();
  await expect(card.getByText('New facility created for this customer')).toBeVisible();
  await card.getByRole('button', { name: /Approve match/ }).click();
  await expect(page.getByText(/Nothing left to review for job #\d+/)).toBeVisible();

  // Export: the job is complete, the preview has the new facility, and the download is named as before.
  await page.getByRole('link', { name: /Export page/ }).click();
  await expect(page.getByText('completed', { exact: true })).toBeVisible();
  await expect(
    page.getByRole('table', { name: 'Upload tab preview' }).getByText('EC900'),
  ).toBeVisible();
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: 'Download workbook' }).click();
  expect((await download).suggestedFilename()).toBe(
    'Acme Supply 2025.07.02 300.00 Ledger Processed.xlsx',
  );

  // Dashboard counts reflect it.
  await page.getByRole('link', { name: /Dashboard/ }).click();
  await expect(page.getByText('Pending reviews')).toBeVisible();
});

test('editing a supplier keeps the fields that were not changed (F22)', async ({ page }) => {
  await page.goto('/admin/suppliers');
  await page.getByRole('button', { name: 'Edit Acme Supply' }).click();
  const dialog = page.getByRole('dialog', { name: 'Edit Acme Supply' });
  await expect(dialog.getByLabel('Salesforce contract ID')).toHaveValue('CON-1');
  await dialog.getByLabel('Description').fill('Synthetic test supplier');
  await dialog.getByRole('button', { name: 'Save supplier' }).click();
  await expect(dialog).toBeHidden();

  await page.reload();
  await page.getByRole('button', { name: 'Edit Acme Supply' }).click();
  const again = page.getByRole('dialog', { name: 'Edit Acme Supply' });
  await expect(again.getByLabel('Description')).toHaveValue('Synthetic test supplier');
  await expect(again.getByLabel('Salesforce contract ID')).toHaveValue('CON-1');
  await expect(again.getByLabel('Payor filter name')).toHaveValue('Acme Supply');
});

test('Tier C: the rule being edited is in the URL and survives a reload (F13)', async ({
  page,
}) => {
  await page.goto('/admin/tier-c');
  await expect(page).toHaveURL(/supplier=\d+/);
  await page.getByRole('button', { name: 'Edit extraction rule Gross' }).click();
  await expect(page).toHaveURL(/extractionRule=\d+/);
  await expect(page.getByRole('textbox', { name: /Cell address/ })).toHaveValue('E8');

  await page.getByRole('textbox', { name: /Cell address/ }).fill('F9');
  await page.getByRole('button', { name: 'Save extraction rule' }).click();
  await expect(page.getByText('Extraction rule “Gross” saved.')).toBeVisible();

  await page.reload();
  await expect(page.getByRole('textbox', { name: /Cell address/ })).toHaveValue('F9');
  await expect(page.getByLabel(/Sheet name/)).toHaveValue('Summary');
  await expect(page.getByLabel(/Is sales amount/)).toBeChecked();
});

test('deep links load on refresh and unknown pages show a 404 in the shell', async ({ page }) => {
  await page.goto('/admin/column-maps?supplier=1');
  await expect(page.getByRole('tab', { name: 'Column Maps' })).toHaveAttribute(
    'aria-selected',
    'true',
  );
  await expect(page.getByRole('table', { name: 'Column mappings' })).toBeVisible();

  await page.goto('/no/such/page');
  await expect(page.getByRole('heading', { name: 'Page not found' })).toBeVisible();
  await expect(page.getByRole('navigation', { name: 'Main navigation' })).toBeVisible();
});
