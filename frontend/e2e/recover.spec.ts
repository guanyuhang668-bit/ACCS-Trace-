import { expect, test } from '@playwright/test'
import { spawn, spawnSync } from 'node:child_process'
import type { ChildProcess } from 'node:child_process'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const here = path.dirname(fileURLToPath(import.meta.url))
const sampleDir = path.resolve(here, '..', '..', 'sample_data')
const projectRoot = path.resolve(here, '..', '..')
const accsInputSchema = 'forward(text_input_ids:int64[B,512],code_input_ids:int64[B,512])->raw_logits:float[B,2]'
let worker: ChildProcess | undefined

test.beforeAll(() => {
  const python = path.join(projectRoot, '.venv', 'Scripts', 'python.exe')
  const buildScript = `
import hashlib
import json
import os
import shutil
from pathlib import Path
from tests.test_accs_torchscript_runtime import _build_package

models_root = Path(os.environ["ACCSTRACE_DATA_DIR"]) / "models"
_, tika_root = _build_package(models_root)
hadoop_root = models_root / "hadoop-package"
shutil.copytree(tika_root, hadoop_root)
preprocessing_path = hadoop_root / "preprocessing.json"
preprocessing = json.loads(preprocessing_path.read_text(encoding="utf-8"))
preprocessing["fixed_project_key"] = "HADOOP"
preprocessing_path.write_text(json.dumps(preprocessing, sort_keys=True, separators=(",", ":")), encoding="utf-8")
manifest_path = hadoop_root / "manifest.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["model_name"] = "Tiny Hadoop ACCS"
manifest["model_version"] = "test-hadoop-v1"
manifest["preprocessing_sha256"] = hashlib.sha256(preprocessing_path.read_bytes()).hexdigest()
manifest_path.write_text(json.dumps(manifest, sort_keys=True, separators=(",", ":")), encoding="utf-8")
`
  const packageBuild = spawnSync(python, ['-c', buildScript], {
    cwd: path.join(projectRoot, 'backend'),
    env: process.env,
    encoding: 'utf8',
    windowsHide: true,
  })
  if (packageBuild.error || packageBuild.status !== 0) {
    throw new Error(`Could not build the test-only ACCS package: ${packageBuild.stderr}`)
  }
  worker = spawn(
    python,
    ['-m', 'app.worker'],
    {
      cwd: path.join(projectRoot, 'backend'),
      env: process.env,
      stdio: 'ignore',
      windowsHide: true,
    },
  )
})

test.afterAll(() => {
  worker?.kill()
})

test('runs transparent demo and registered ACCS TorchScript recovery workflows', async ({ page }) => {
  const consoleProblems: string[] = []
  page.on('console', (event) => {
    if (event.type() === 'warning' || event.type() === 'error') consoleProblems.push(event.text())
  })
  await page.goto('/projects')
  await page.getByRole('button', { name: 'New project' }).click()
  await page.getByLabel('Project name').fill(`E2E-${Date.now()}`)
  await page.getByLabel('Description').fill('Playwright recovery workflow')
  await page.getByLabel('Project key').fill('TIKA')
  await page.getByRole('button', { name: 'Create project', exact: true }).click()

  await expect(page.getByRole('tab', { name: 'Data' })).toBeVisible()
  const projectUrl = page.url()
  const fileInputs = page.locator('input[type="file"]')
  await fileInputs.nth(0).setInputFiles(path.join(sampleDir, 'issues.csv'))
  await fileInputs.nth(1).setInputFiles(path.join(sampleDir, 'commits.csv'))
  await page.getByRole('button', { name: 'Validate and import' }).click()
  await expect(page.getByText(/Imported 4 issues and 7 commits/)).toBeVisible()

  await page.getByRole('tab', { name: 'Recover' }).click()
  await expect(page.getByText('Project key: TIKA', { exact: true })).toBeVisible()
  const demoModelSelect = page.getByRole('combobox', { name: 'Model release' })
  await demoModelSelect.click()
  await page.locator('.ant-select-dropdown:visible').getByText(
    'Deterministic Demo Runtime 1.0.0-demo · DEMO',
    { exact: true },
  ).click()
  await expect(page.getByText('Demo runtime — not an ACCS model')).toBeVisible()
  await expect(page.locator('.ant-alert').getByText('Raw model score only', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Queue recovery run' }).click()

  await expect(page.getByRole('heading', { name: /Recovery run/ })).toBeVisible()
  await expect(page.getByText('Completed', { exact: true }).first()).toBeVisible({ timeout: 30_000 })

  await page.getByRole('tab', { name: 'Reliability' }).click()
  await expect(page.getByRole('heading', { name: 'Score distribution' })).toBeVisible()
  await expect(page.locator('.echarts-for-react svg')).toBeVisible()
  await page.getByRole('tab', { name: 'Candidates' }).click()

  let scriptDialogOpened = false
  page.on('dialog', async (dialog) => {
    scriptDialogOpened = true
    await dialog.dismiss()
  })
  const search = page.getByPlaceholder('Issue key or commit SHA')
  await search.fill('TIKA-404')
  await search.press('Enter')
  await page.getByRole('row').filter({ hasText: 'TIKA-404' }).click()
  const reviewDrawer = page.getByRole('dialog', { name: 'Candidate review' })
  await expect(reviewDrawer.getByRole('heading', { name: 'TIKA-404' })).toBeVisible()
  await expect(reviewDrawer.getByText(/<script>alert\('review'\)<\/script>/)).toBeVisible()
  expect(scriptDialogOpened).toBe(false)

  await page.getByPlaceholder('Optional audit note').fill('Verified against the issue and code evidence.')
  await page.getByRole('button', { name: 'Accept' }).click()
  await expect(page.getByText('Accepted', { exact: true }).first()).toBeVisible()
  await reviewDrawer.getByRole('button', { name: 'Close', exact: true }).click()

  await page.getByRole('button', { name: 'Export' }).click()
  const [manifestPage] = await Promise.all([
    page.waitForEvent('popup'),
    page.getByRole('button', { name: 'View run manifest' }).click(),
  ])
  await expect(manifestPage.locator('body')).toContainText('manifest_schema_version')
  await manifestPage.close()

  const downloadPromise = page.waitForEvent('download')
  await page.getByRole('button', { name: 'Create and download' }).click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toMatch(/\.csv$/)

  await page.goto('/models')
  await page.getByRole('button', { name: 'Register package' }).click()
  await page.getByLabel('Package directory name').fill('hadoop-package')
  await page.getByRole('button', { name: 'Validate and register' }).click()
  await expect(page.getByRole('heading', { name: /Tiny Hadoop ACCS test-hadoop-v1/ })).toBeVisible({ timeout: 30_000 })

  await page.getByRole('button', { name: 'Register package' }).click()
  await page.getByLabel('Package directory name').fill('accs-package')
  await page.getByRole('button', { name: 'Validate and register' }).click()
  await expect(page.getByRole('heading', { name: /Tiny ACCS TorchScript test-v1/ })).toBeVisible({ timeout: 30_000 })

  const accsCard = page.locator('section.model-panel').filter({ hasText: 'Tiny ACCS TorchScript' })
  await expect(accsCard.getByText('Package evidence listed')).toBeVisible()
  await expect(accsCard.getByText('models/accs-package', { exact: true })).toBeVisible()
  await expect(accsCard.getByText('models/accs-package/manifest.json', { exact: true })).toBeVisible()
  await expect(accsCard.getByText(/^[a-f0-9]{64}$/)).toBeVisible()
  await expect(accsCard.getByText(accsInputSchema, { exact: true })).toBeVisible()
  await expect(accsCard.getByText('No deployment calibration profiles.')).toBeVisible()

  await page.setViewportSize({ width: 390, height: 844 })
  await expect.poll(
    () => page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth),
    { message: 'Wait for the responsive model layout to settle without horizontal overflow' },
  ).toBeLessThanOrEqual(0)
  const viewportWidth = await page.evaluate(() => ({
    scrollWidth: document.documentElement.scrollWidth,
    clientWidth: document.documentElement.clientWidth,
    offenders: Array.from(document.querySelectorAll<HTMLElement>('*'))
      .map((element) => {
        const rect = element.getBoundingClientRect()
        return {
          tag: element.tagName,
          className: element.className?.toString().slice(0, 120),
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
          scrollWidth: element.scrollWidth,
        }
      })
      .filter((item) => item.right > document.documentElement.clientWidth + 1 || item.left < -1)
      .slice(0, 12),
  }))
  expect(
    viewportWidth.scrollWidth,
    `Mobile overflow elements: ${JSON.stringify(viewportWidth.offenders)}`,
  ).toBeLessThanOrEqual(viewportWidth.clientWidth)

  await page.setViewportSize({ width: 1440, height: 900 })
  await page.goto(projectUrl)
  await page.getByRole('tab', { name: 'Recover' }).click()
  const modelSelect = page.getByRole('combobox', { name: 'Model release' })
  await modelSelect.click()
  const modelDropdown = page.locator('.ant-select-dropdown:visible')
  await expect(modelDropdown.getByText('Tiny Hadoop ACCS test-hadoop-v1', { exact: true })).toHaveCount(0)
  await modelDropdown.getByText(
    'Tiny ACCS TorchScript test-v1',
    { exact: true },
  ).click()

  const projectId = new URL(projectUrl).pathname.split('/').at(-1)
  if (!projectId) throw new Error(`Could not resolve project ID from ${projectUrl}`)
  const [projectDetailResponse, modelsResponse] = await Promise.all([
    page.request.get(`/api/projects/${projectId}`),
    page.request.get('/api/models'),
  ])
  expect(projectDetailResponse.ok()).toBe(true)
  expect(modelsResponse.ok()).toBe(true)
  const projectDetail = await projectDetailResponse.json()
  const registeredModels = await modelsResponse.json()
  const hadoopModel = registeredModels.find((item: { name: string }) => item.name === 'Tiny Hadoop ACCS')
  expect(hadoopModel).toBeTruthy()
  const mismatchResponse = await page.request.post('/api/runs', {
    data: {
      project_id: projectId,
      dataset_revision_id: projectDetail.revisions.at(-1).id,
      model_release_id: hadoopModel.id,
      calibration_profile_id: null,
      mode: 'recover',
      days_before: 3,
      days_after: 14,
      unresolved_window_days: 90,
      max_candidates_per_issue: 200,
    },
  })
  expect(mismatchResponse.status()).toBe(422)
  expect((await mismatchResponse.json()).error.message).toContain("does not support project key 'TIKA'")

  await expect(page.locator('.ant-alert').getByText('Raw model score only', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Queue recovery run' }).click()

  await expect(page.getByRole('heading', { name: /Recovery run/ })).toBeVisible()
  await expect(page.getByText('Completed', { exact: true }).first()).toBeVisible({ timeout: 30_000 })
  await expect(page.locator('.ant-alert').getByText('Raw model scores', { exact: true })).toBeVisible()

  const runId = new URL(page.url()).pathname.split('/').at(-1)
  expect(runId).toBeTruthy()
  const manifestResponse = await page.request.get(`/api/runs/${runId}/manifest`)
  expect(manifestResponse.ok()).toBe(true)
  const accsManifest = await manifestResponse.json()
  expect(accsManifest.model.is_demo).toBe(false)
  expect(accsManifest.model.runtime_kind).toBe('accs_torchscript_v1')
  expect(accsManifest.deployment_calibration).toBeNull()
  expect(consoleProblems).toEqual([])
})
