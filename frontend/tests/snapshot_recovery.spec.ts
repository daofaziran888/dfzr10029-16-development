import { expect, test } from '@playwright/test'

type Json = Record<string, any>

/**
 * 数据快照：可恢复清单、恢复依据包与检查点。
 *
 * 用例只依赖页面可观察结果和少量只读接口；会新建一份快照记录来做检查点并发，
 * 不修改示例数据里的既有记录。
 */
test('快照可恢复清单可按数据集与校验结果核查', async ({ page }) => {
  const errors: string[] = []
  page.on('pageerror', (e) => errors.push(e.message))
  page.on('response', (r) => {
    if (r.url().includes('/api/') && r.status() >= 400) errors.push(`${r.status()} ${r.url()}`)
  })

  await page.goto('/snapshot')
  await expect(page.getByTestId('recoverable-summary')).toBeVisible()

  const rows = page.locator('[data-testid^="recoverable-row-"]')
  await expect(rows.first()).toBeVisible()
  const totalRows = await rows.count()
  expect(totalRows).toBeGreaterThanOrEqual(3)

  // 每行都必须同时带校验结果和可恢复状态，且两者口径一致。
  for (let i = 0; i < totalRows; i++) {
    const row = rows.nth(i)
    expect(['校验通过', '校验失败', '未校验']).toContain(await row.getAttribute('data-verification'))
    expect(['true', 'false']).toContain(await row.getAttribute('data-recoverable'))
    const text = await row.innerText()
    expect(text).toMatch(/校验(通过|失败)/)
    expect(text).toMatch(/可恢复|不可恢复/)
  }

  // 按数据集分组：切换到某个数据集后，清单只保留该数据集。
  const datasetSelect = page.getByTestId('recoverable-dataset-filter')
  const optionCount = await datasetSelect.locator('option').count()
  expect(optionCount).toBeGreaterThan(1)
  const datasetName = (await datasetSelect.locator('option').nth(1).innerText()).split('（')[0]
  await datasetSelect.selectOption({ label: (await datasetSelect.locator('option').nth(1).innerText()) })
  await expect(page.getByTestId('recoverable-selection')).toContainText(`数据集=${datasetName}`)
  await expect(page.getByTestId(`recoverable-group-${datasetName}`)).toBeVisible()
  await expect(page.locator('[data-testid^="recoverable-group-"]')).toHaveCount(1)

  // 校验结果筛选 + 导出保留选择依据。
  await page.getByTestId('recoverable-verification-filter').selectOption('校验失败')
  await page.getByTestId('recoverable-query').click()
  await expect(page.getByTestId('recoverable-selection')).toContainText('校验结果=校验失败')
  const failedRows = page.locator('[data-testid^="recoverable-row-"]')
  const failedCount = await failedRows.count()
  for (let i = 0; i < failedCount; i++) {
    expect(await failedRows.nth(i).getAttribute('data-verification')).toBe('校验失败')
  }

  const downloadPromise = page.waitForEvent('download')
  await page.getByTestId('recoverable-export').click()
  const download = await downloadPromise
  expect(download.suggestedFilename()).toBe('snapshot_recoverable.csv')

  await page.getByTestId('recoverable-reset').click()
  await expect(page.getByTestId('recoverable-selection')).toContainText('全部已执行快照')

  expect(errors).toEqual([])
})

test('恢复依据包概览与明细、跨领域来源一致', async ({ page }) => {
  await page.goto('/snapshot')
  const rows = page.locator('[data-testid^="recoverable-row-"]')
  await expect(rows.first()).toBeVisible()

  await rows.first().locator('[data-testid^="view-basis-"]').click()
  await expect(page.getByTestId('basis-overview')).toBeVisible()
  await expect(page.getByTestId('basis-detail')).toBeVisible()
  await expect(page.getByTestId('basis-current-version')).toContainText('v')
  await expect(page.getByTestId('basis-diff-summary')).toBeVisible()
  await expect(page.getByTestId('basis-consistent')).toHaveText('与当前快照记录一致')

  // 明细里的处理输入是持久化的输入，不随当前快照重算。
  const detail = await page.getByTestId('basis-detail').innerText()
  expect(detail).toContain('处理输入')
  expect(detail).toContain('快照编号')

  // 另一处来源：数据审计、数据导出只读同一份依据包，版本与校验结果一致。
  await page.getByTestId('basis-cross-domain-load').click()
  await expect(page.getByTestId('basis-cross-domain-table')).toBeVisible()
  const crossRows = page.locator('[data-testid="basis-cross-domain-table"] tbody tr')
  const crossCount = await crossRows.count()
  expect(crossCount).toBeGreaterThanOrEqual(2)

  const modules = await crossRows.locator('td:nth-child(1)').allInnerTexts()
  expect(new Set(modules)).toEqual(new Set(['数据审计', '数据导出']))

  // 同一快照可能有多份历史依据包，两个模块读到的版本集合必须完全相同。
  const rowsByModule: Record<string, string[]> = { 数据审计: [], 数据导出: [] }
  for (let i = 0; i < crossCount; i++) {
    const cells = await crossRows.nth(i).locator('td').allInnerTexts()
    rowsByModule[cells[0]].push(`${cells[1]}@${cells[3]}`)
  }
  expect(rowsByModule['数据审计'].sort()).toEqual(rowsByModule['数据导出'].sort())

  // 按版本回查：老版本的概览仍显示当时的校验结果，并标明不是最新版本。
  const versionSelect = page.getByTestId('basis-version-select')
  const versionOptions = await versionSelect.locator('option').count()
  if (versionOptions > 2) {
    await versionSelect.selectOption({ index: versionOptions - 1 })
    await expect(page.getByTestId('basis-current-version')).not.toContainText('最新')
    await expect(page.getByTestId('basis-detail')).toBeVisible()
  }
})

test('检查点并发提交只保留一个确认版本', async ({ page }) => {
  const unique = Date.now().toString()
  const snapshotNo = `SNAP-E2E-${unique}`
  const created = await page.request.post('/api/snapshot', {
    data: {
      values: {
        快照编号: snapshotNo,
        数据集名称: '订单明细数据集',
        快照时间: '2026-11-17 12:00:00',
        行数快照: 4096,
        存储位置: `oss://snapshots/e2e/${unique}`,
      },
    },
  })
  const createdBody = (await created.json()) as Json
  expect(createdBody.ok).toBe(true)
  const snapshotId = createdBody.entry.id

  const completed = await page.request.post(`/api/snapshot/${snapshotId}/actions`, {
    data: { action: '完成快照' },
  })
  const completedBody = (await completed.json()) as Json
  expect(completedBody.entry.可恢复状态).toBe('可恢复')

  const checkpoint = await page.request.post('/api/snapshot/checkpoints', {
    data: { snapshotId, checkpointNo: `CKPT-E2E-${unique}` },
  })
  const checkpointBody = (await checkpoint.json()) as Json
  const checkpointId = checkpointBody.entry.id

  const [cleanup, recovery] = await Promise.all([
    page.request.post(`/api/snapshot/checkpoints/${checkpointId}/events`, { data: { event: '清理完成通知' } }),
    page.request.post(`/api/snapshot/checkpoints/${checkpointId}/events`, { data: { event: '恢复请求' } }),
  ])
  const results = [(await cleanup.json()) as Json, (await recovery.json()) as Json]
  expect(results.filter((item) => item.ok)).toHaveLength(1)
  expect(results.filter((item) => item.conflict)).toHaveLength(1)

  const state = await page.request.get(`/api/snapshot/checkpoints/${checkpointId}`)
  const stateBody = (await state.json()) as Json
  expect(stateBody.检查点状态).toBe('已确认')
  expect(stateBody.确认版本).toBe(1)
  expect(stateBody.事件列表.filter((item: Json) => item.状态 === '已确认')).toHaveLength(1)
  expect(stateBody.事件列表.filter((item: Json) => item.状态 === '已驳回')).toHaveLength(1)

  // 页面上的检查点表也按这个口径展示。
  await page.goto('/snapshot')
  await expect(page.getByTestId(`checkpoint-row-${checkpointId}`)).toBeVisible()
  await expect(page.getByTestId(`checkpoint-row-${checkpointId}`)).toContainText('已确认')
  await expect(page.getByTestId(`checkpoint-row-${checkpointId}`)).toContainText('v1')
})
