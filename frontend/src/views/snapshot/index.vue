<template>
  <section class="page" data-module="snapshot">
    <header class="page-head">
      <div>
        <h2>数据快照</h2>
        <p class="page-desc">
          维护快照记录，并按数据集解析快照校验哈希、生成可恢复快照清单。已执行快照可查看恢复依据包、
          按时间和校验结果核查，恢复入口只使用已确认版本的校验哈希。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" data-testid="open-create" @click="openCreate">登记快照记录</button>
        <button class="btn" type="button" data-testid="export" @click="exportRows">导出作业清单</button>
        <button class="btn" type="button" data-testid="recoverable-export" @click="exportRecoverable">导出可恢复清单</button>
      </div>
    </header>

    <div class="stat-row" data-testid="recoverable-summary">
      <article class="stat-card">
        <span class="stat-label">可恢复快照</span>
        <strong class="stat-value" data-testid="recoverable-count">{{ recData.summary.可恢复 ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">不可恢复快照</span>
        <strong class="stat-value" data-testid="not-recoverable-count">{{ recData.summary.不可恢复 ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">校验通过</span>
        <strong class="stat-value" data-testid="verified-count">{{ recData.summary.校验通过 ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">校验失败</span>
        <strong class="stat-value" data-testid="failed-count">{{ recData.summary.校验失败 ?? 0 }}</strong>
      </article>
    </div>

    <h3 class="section-title">可恢复快照清单</h3>
    <form class="filter-bar" data-testid="recoverable-filter-bar" @submit.prevent="loadRecoverable">
      <label class="filter-item">
        <span>数据集</span>
        <select v-model="recFilters.dataset" data-testid="recoverable-dataset-filter" @change="loadRecoverable">
          <option value="">全部数据集</option>
          <option v-for="dataset in recData.datasets" :key="dataset.数据集名称" :value="dataset.数据集名称">
            {{ dataset.数据集名称 }}（可恢复 {{ dataset.可恢复 }}/{{ dataset.总数 }}）
          </option>
        </select>
      </label>
      <label class="filter-item">
        <span>校验结果</span>
        <select v-model="recFilters.verification" data-testid="recoverable-verification-filter">
          <option value="">全部</option>
          <option value="校验通过">校验通过</option>
          <option value="校验失败">校验失败</option>
          <option value="未校验">未校验</option>
        </select>
      </label>
      <label class="filter-item"><span>开始日期</span><input v-model="recFilters.date_from" type="date" data-testid="recoverable-date-from" /></label>
      <label class="filter-item"><span>结束日期</span><input v-model="recFilters.date_to" type="date" data-testid="recoverable-date-to" /></label>
      <label class="filter-item" style="flex-direction:row;align-items:center;gap:6px">
        <input v-model="recFilters.only_recoverable" type="checkbox" data-testid="recoverable-only" />
        <span>仅看可恢复</span>
      </label>
      <button class="btn" type="submit" data-testid="recoverable-query">查询</button>
      <button class="btn ghost" type="button" data-testid="recoverable-reset" @click="resetRecoverableFilters">重置条件</button>
      <button class="btn ghost" type="button" data-testid="recoverable-refresh" :disabled="refreshing" @click="refreshRecoverable">
        {{ refreshing ? '解析中…' : '重新解析校验哈希' }}
      </button>
    </form>

    <p class="selection-note" data-testid="recoverable-selection">
      当前选择依据：{{ selectionLabel }}
    </p>

    <div data-testid="recoverable-list">
      <section v-for="group in recData.groups" :key="group.数据集名称" class="dataset-group" :data-testid="`recoverable-group-${group.数据集名称}`">
        <header class="dataset-group-head">
          <strong>{{ group.数据集名称 }}</strong>
          <span>共 {{ group.总数 }} 份 · 可恢复 {{ group.可恢复 }} · 不可恢复 {{ group.不可恢复 }} · 最近 {{ group.最近快照时间 || '—' }}</span>
        </header>
        <table class="data-table">
          <thead>
            <tr>
              <th>快照编号</th><th>快照时间</th><th>行数快照</th><th>校验结果</th>
              <th>可恢复状态</th><th>依据包版本</th><th>确认校验哈希</th><th>可执行动作</th>
            </tr>
          </thead>
          <tbody>
            <tr
              v-for="item in group.items"
              :key="item.快照记录id"
              :data-testid="`recoverable-row-${item.快照记录id}`"
              :data-verification="item.校验结果"
              :data-recoverable="String(item.可恢复状态 === '可恢复')"
            >
              <td>{{ item.快照编号 }}</td>
              <td>{{ item.快照时间 }}</td>
              <td>{{ item.行数快照 }}</td>
              <td>
                <span class="badge" :class="item.校验结果 === '校验通过' ? 'badge-ok' : 'badge-bad'">{{ item.校验结果 }}</span>
              </td>
              <td>
                <span class="badge" :class="item.可恢复状态 === '可恢复' ? 'badge-ok' : 'badge-bad'">{{ item.可恢复状态 }}</span>
                <small v-if="item.清理状态 === '已清理'" style="margin-left:6px;color:var(--muted)">目录已清理</small>
              </td>
              <td>{{ item.依据包版本 ? `v${item.依据包版本}` : '—' }}</td>
              <td class="mono">{{ shortHash(item.确认校验哈希) }}</td>
              <td class="row-actions">
                <button class="link" type="button" :data-testid="`view-basis-${item.快照记录id}`" @click="openBasis(item)">查看依据包</button>
                <button
                  class="link"
                  type="button"
                  :data-testid="`recover-${item.快照记录id}`"
                  :disabled="item.可恢复状态 !== '可恢复'"
                  @click="openRecovery(item)"
                >恢复</button>
              </td>
            </tr>
          </tbody>
        </table>
      </section>
      <p v-if="!recData.items.length" class="empty-state" data-testid="recoverable-empty">暂无符合条件的可恢复快照，可调整数据集或校验结果后重试</p>
    </div>

    <h3 class="section-title">快照恢复依据包</h3>
    <p v-if="!selectedSnapshot" class="empty-state" data-testid="basis-empty">在上方可恢复清单里选择一份快照，查看它的恢复依据包概览与明细</p>
    <div v-else class="basis-panel" data-testid="basis-panel">
      <header class="basis-head">
        <div>
          <strong data-testid="basis-title">{{ selectedSnapshot.快照编号 }}</strong>
          <span style="margin-left:8px;color:var(--muted)">{{ selectedSnapshot.数据集名称 }} · {{ selectedSnapshot.快照时间 }}</span>
        </div>
        <div class="page-actions">
          <label class="filter-item">
            <span>按版本回查</span>
            <select v-model="basisVersion" data-testid="basis-version-select" @change="reloadBasis">
              <option :value="null">最新确认版本</option>
              <option v-for="version in basisVersions" :key="version.版本" :value="version.版本">
                v{{ version.版本 }} · {{ version.来源 }} · {{ version.校验结果 }}
              </option>
            </select>
          </label>
          <label class="filter-item">
            <span>按来源回查</span>
            <select v-model="basisSource" data-testid="basis-source-select" @change="reloadBasis">
              <option value="">全部来源</option>
              <option v-for="source in basisSourceOptions" :key="source" :value="source">{{ source }}</option>
            </select>
          </label>
        </div>
      </header>

      <p v-if="basisError" class="error-text" data-testid="basis-error">{{ basisError }}</p>

      <div v-if="basisView" class="basis-overview" data-testid="basis-overview">
        <h4>概览 · 当前版本与差异</h4>
        <dl class="dag-detail">
          <div><dt>当前版本</dt><dd data-testid="basis-current-version">v{{ basisView.overview.当前版本 }}（{{ basisView.overview.版本状态 }}）</dd></div>
          <div><dt>来源</dt><dd data-testid="basis-source">{{ basisView.overview.来源 }}</dd></div>
          <div><dt>校验结果</dt><dd data-testid="basis-verification">{{ basisView.overview.校验结果 }}</dd></div>
          <div><dt>可恢复状态</dt><dd data-testid="basis-recoverable">{{ basisView.overview.可恢复状态 }}</dd></div>
          <div><dt>相对版本</dt><dd data-testid="basis-relative-version">{{ basisView.overview.相对版本 ? `v${basisView.overview.相对版本}` : '无（首次确认）' }}</dd></div>
          <div><dt>差异摘要</dt><dd data-testid="basis-diff-summary">{{ basisView.overview.差异说明 }}（{{ basisView.overview.差异数量 }} 项）</dd></div>
          <div><dt>确认校验哈希</dt><dd class="mono" data-testid="basis-confirmed-checksum">{{ basisView.overview.确认校验哈希 || '—' }}</dd></div>
          <div><dt>回查一致</dt><dd data-testid="basis-consistent">{{ basisView.overview.回查一致 ? '与当前快照记录一致' : '与当前快照记录不一致' }}</dd></div>
          <div><dt>检查点</dt><dd>{{ basisView.overview.检查点编号 || '—' }}</dd></div>
          <div><dt>版本列表</dt><dd>{{ basisView.overview.版本列表.map((v: number) => `v${v}`).join('、') || '—' }}</dd></div>
        </dl>
      </div>

      <div v-if="basisView" class="basis-detail" data-testid="basis-detail">
        <h4>明细 · 处理输入</h4>
        <table class="data-table">
          <tbody>
            <tr v-for="(value, key) in basisView.detail.处理输入" :key="String(key)">
              <th style="width:140px">{{ key }}</th>
              <td class="mono">{{ value ?? '—' }}</td>
            </tr>
          </tbody>
        </table>
        <h4 style="margin-top:12px">明细 · 与上一确认版本的差异</h4>
        <table class="data-table" data-testid="basis-diff">
          <thead><tr><th>范围</th><th>字段</th><th>原值</th><th>新值</th></tr></thead>
          <tbody>
            <tr v-for="change in basisView.detail.差异.变化字段" :key="`${change.范围}-${change.字段}`">
              <td>{{ change.范围 }}</td><td>{{ change.字段 }}</td>
              <td class="mono">{{ change.原值 ?? '—' }}</td><td class="mono">{{ change.新值 ?? '—' }}</td>
            </tr>
            <tr v-if="!basisView.detail.差异.变化字段.length">
              <td colspan="4" class="empty-state">{{ basisView.detail.差异.说明 }}</td>
            </tr>
          </tbody>
        </table>
        <p class="selection-note">
          存储校验哈希：<span class="mono">{{ basisView.detail.存储校验哈希 || '—' }}</span><br />
          计算校验哈希：<span class="mono">{{ basisView.detail.计算校验哈希 || '—' }}</span><br />
          {{ basisView.detail.校验说明 }}
        </p>
      </div>

      <div class="basis-cross" data-testid="basis-cross-domain">
        <h4>跨领域核对 · 另一处来源</h4>
        <p class="selection-note">
          按既有系统调用链，数据审计与数据导出各自只读同一份依据包；版本必须一致，否则说明快照记录已被改写。
        </p>
        <button class="btn" type="button" data-testid="basis-cross-domain-load" @click="loadCrossDomain">读取审计侧依据包</button>
        <p v-if="crossDomainError" class="error-text">{{ crossDomainError }}</p>
        <table v-if="crossDomainItems.length" class="data-table" data-testid="basis-cross-domain-table">
          <thead><tr><th>模块</th><th>依据包编号</th><th>快照编号</th><th>版本</th><th>来源</th><th>校验结果</th><th>可恢复状态</th></tr></thead>
          <tbody>
            <tr v-for="row in crossDomainItems" :key="`${row.模块}-${row.依据包编号}`">
              <td>{{ row.模块 }}</td><td>{{ row.依据包编号 }}</td><td>{{ row.快照编号 }}</td>
              <td>v{{ row.版本 }}</td><td>{{ row.来源 }}</td><td>{{ row.校验结果 }}</td><td>{{ row.可恢复状态 }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>

    <h3 class="section-title">快照目录检查点</h3>
    <div class="checkpoint-wrap" data-testid="checkpoint-list">
      <table class="data-table">
        <thead><tr><th>检查点编号</th><th>快照编号</th><th>检查点状态</th><th>确认版本</th><th>确认来源</th><th>事件留痕</th><th>可执行动作</th></tr></thead>
        <tbody>
          <tr v-for="checkpoint in checkpoints" :key="checkpoint.id" :data-testid="`checkpoint-row-${checkpoint.id}`" :data-status="checkpoint.检查点状态">
            <td>{{ checkpoint.检查点编号 }}</td>
            <td>{{ checkpoint.快照编号 }}</td>
            <td>{{ checkpoint.检查点状态 }}</td>
            <td>{{ checkpoint.确认版本 ? `v${checkpoint.确认版本}` : '—' }}</td>
            <td>{{ checkpoint.确认来源 || '—' }}</td>
            <td>
              <span v-for="event in checkpoint.事件列表" :key="event.id" class="event-chip" :class="event.状态 === '已确认' ? 'badge-ok' : 'badge-bad'">
                v{{ event.版本 }} {{ event.事件 }}（{{ event.状态 }}）
              </span>
            </td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`checkpoint-cleanup-${checkpoint.id}`" @click="submitCheckpointEvent(checkpoint, '清理完成通知')">清理完成通知</button>
              <button class="link" type="button" :data-testid="`checkpoint-recovery-${checkpoint.id}`" @click="submitCheckpointEvent(checkpoint, '恢复请求')">恢复请求</button>
            </td>
          </tr>
          <tr v-if="!checkpoints.length"><td colspan="7" class="empty-state">暂无检查点</td></tr>
        </tbody>
      </table>
      <p v-if="checkpointMessage" class="selection-note" data-testid="checkpoint-message">{{ checkpointMessage }}</p>
    </div>

    <h3 class="section-title">快照作业时间线</h3>
    <div class="filter-bar">
      <label><span>开始日期</span><input v-model="dateFrom" type="date" /></label>
      <label><span>结束日期</span><input v-model="dateTo" type="date" /></label>
      <button class="btn" type="button" @click="reload">查询</button>
    </div>
    <div class="timeline-wrap">
      <div v-for="item in timelineItems" :key="item.id" class="timeline-row">
        <span class="timeline-label">{{ item.name || item.label }}</span>
        <div class="timeline-track">
          <div
            class="timeline-bar"
            :class="`status-${item.status}`"
            :style="{ left: barLeft(item) + '%', width: barWidth(item) + '%' }"
            :data-testid="`bar-${item.id}`"
            :title="`${item.start} ~ ${item.end}`"
            @click="selectItem(item)"
          >&nbsp;</div>
        </div>
        <span style="font-size:12px;color:var(--muted);margin-left:8px;">{{ item.start }} ~ {{ item.end }}</span>
      </div>
      <p v-if="!timelineItems.length" style="text-align:center;color:var(--muted)">暂无作业记录</p>
    </div>
    <div v-if="selectedItem" class="timeline-log" data-testid="timeline-log">
      <strong>{{ selectedItem.name || selectedItem.label }}</strong> — 状态：{{ selectedItem.status }}
      <div>校验结果：{{ selectedItem.verification }} · 可恢复：{{ selectedItem.recoverable }}</div>
      <div style="margin-top:4px;">{{ selectedItem.logs || '暂无日志' }}</div>
    </div>

    <div v-if="recoveryTarget" class="modal-backdrop" data-testid="recover-modal">
      <div class="modal">
        <h3>发起快照恢复</h3>
        <p class="selection-note">
          将使用依据包 <strong data-testid="recover-basis-version">v{{ recoveryTarget.依据包版本 }}</strong>
          中已确认的校验哈希 <span class="mono">{{ recoveryTarget.确认校验哈希 }}</span>，
          数据集：{{ recoveryTarget.数据集名称 }}，快照时间：{{ recoveryTarget.快照时间 }}。
        </p>
        <p class="selection-note">选择依据：{{ recoveryBasisText }}</p>
        <p v-if="recoveryError" class="error-text" data-testid="recover-error">{{ recoveryError }}</p>
        <div v-if="recoveryReceipt" class="timeline-log" data-testid="recover-receipt">
          已受理：{{ recoveryReceipt.快照编号 }} · {{ recoveryReceipt.使用校验哈希 }}
          <div>{{ recoveryReceipt.选择依据 }}</div>
        </div>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="closeRecovery">关闭</button>
          <button class="btn primary" type="button" data-testid="recover-confirm" :disabled="recovering || !!recoveryReceipt" @click="confirmRecovery">
            {{ recovering ? '提交中…' : '确认恢复' }}
          </button>
        </div>
      </div>
    </div>

    <div v-if="showCreate" class="modal-backdrop" data-testid="create-modal">
      <form class="modal" @submit.prevent="submitCreate">
        <h3>登记快照记录</h3>
        <label v-for="field in columns.slice(0,3)" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="createValues[field]" :placeholder="`请输入${field}`" required />
        </label>
        <div style="display:flex;gap:8px;justify-content:flex-end;margin-top:12px">
          <button class="btn" type="button" @click="showCreate=false">取消</button>
          <button class="btn primary" type="submit" data-testid="submit-create">提交</button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>可恢复清单 {{ recData.total ?? 0 }} 份 · 检查点 {{ checkpoints.length }} 个</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/snapshot'
const columns = ["快照编号", "数据集名称", "快照时间", "行数快照", "校验哈希", "快照策略", "存储位置", "快照状态"]

const allItems = ref<Row[]>([])
const timelineItems = ref<Row[]>([])
const selectedItem = ref<Row | null>(null)
const dateFrom = ref('')
const dateTo = ref('')
const errorMessage = ref('')
const showCreate = ref(false)
const createValues = ref<Record<string, string>>({})

const recFilters = ref<{ dataset: string; verification: string; date_from: string; date_to: string; only_recoverable: boolean }>({
  dataset: '', verification: '', date_from: '', date_to: '', only_recoverable: false,
})
const recData = ref<Row>({ items: [], groups: [], summary: {}, datasets: [], selection: {}, total: 0 })
const refreshing = ref(false)

const selectedSnapshot = ref<Row | null>(null)
const basisView = ref<Row | null>(null)
const basisVersions = ref<Row[]>([])
const basisSourceOptions = ref<string[]>([])
const basisVersion = ref<number | null>(null)
const basisSource = ref('')
const basisError = ref('')
const crossDomainItems = ref<Row[]>([])
const crossDomainError = ref('')

const checkpoints = ref<Row[]>([])
const checkpointMessage = ref('')

const recoveryTarget = ref<Row | null>(null)
const recoveryReceipt = ref<Row | null>(null)
const recoveryError = ref('')
const recovering = ref(false)

const selectionLabel = computed(() => {
  const selection = recData.value.selection || {}
  const parts: string[] = []
  if (selection.数据集名称) parts.push(`数据集=${selection.数据集名称}`)
  if (selection.校验结果) parts.push(`校验结果=${selection.校验结果}`)
  if (selection.开始日期 || selection.结束日期) parts.push(`快照时间=${selection.开始日期 || '不限'}~${selection.结束日期 || '不限'}`)
  if (selection.仅看可恢复) parts.push('仅看可恢复=是')
  return parts.join('；') || '全部已执行快照'
})
const recoveryBasisText = computed(() => {
  if (!recoveryTarget.value) return ''
  return `依据包 v${recoveryTarget.value.依据包版本}（${recoveryTarget.value.依据包来源 || '—'}）确认校验哈希`
})

function shortHash(value: string | undefined | null) {
  if (!value) return '—'
  return value.length > 22 ? `${value.slice(0, 14)}…${value.slice(-6)}` : value
}

function barLeft(item: Row) {
  const all = allItems.value.filter((i) => i.start && i.end)
  if (!all.length) return 0
  const min = Math.min(...all.map((i) => new Date(i.start).getTime()))
  const max = Math.max(...all.map((i) => new Date(i.end).getTime()))
  const range = max - min || 86400000
  return ((new Date(item.start).getTime() - min) / range) * 100
}
function barWidth(item: Row) {
  const all = allItems.value.filter((i) => i.start && i.end)
  if (!all.length) return 20
  const min = Math.min(...all.map((i) => new Date(i.start).getTime()))
  const max = Math.max(...all.map((i) => new Date(i.end).getTime()))
  const range = max - min || 86400000
  return Math.max(5, ((new Date(item.end).getTime() - new Date(item.start).getTime()) / range) * 100)
}
function selectItem(item: Row) {
  selectedItem.value = selectedItem.value?.id === item.id ? null : item
}

async function reload() {
  try {
    const res = await request(ENDPOINT)
    const data = await res.json()
    allItems.value = data.items || []
    applyTimelineFilter()
  } catch (e) {
    errorMessage.value = '加载失败'
  }
  await Promise.all([loadRecoverable(), loadCheckpoints()])
}

function applyTimelineFilter() {
  let items = allItems.value
  if (dateFrom.value) items = items.filter((i: Row) => i.start >= dateFrom.value)
  if (dateTo.value) items = items.filter((i: Row) => i.end <= dateTo.value)
  timelineItems.value = items
  selectedItem.value = null
}

function recoverableQuery() {
  const params = new URLSearchParams()
  if (recFilters.value.dataset) params.set('dataset', recFilters.value.dataset)
  if (recFilters.value.verification) params.set('verification', recFilters.value.verification)
  if (recFilters.value.date_from) params.set('date_from', recFilters.value.date_from)
  if (recFilters.value.date_to) params.set('date_to', recFilters.value.date_to)
  if (recFilters.value.only_recoverable) params.set('only_recoverable', 'true')
  return params
}

async function loadRecoverable() {
  try {
    const res = await request(`${ENDPOINT}/recoverable?${recoverableQuery().toString()}`)
    if (!res.ok) throw new Error('可恢复清单读取失败')
    const data = await res.json()
    recData.value = {
      items: data.items || [],
      groups: data.groups || [],
      summary: data.summary || {},
      datasets: data.datasets || recData.value.datasets || [],
      selection: data.selection || {},
      total: data.total || 0,
    }
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '可恢复清单读取失败'
  }
}

function resetRecoverableFilters() {
  recFilters.value = { dataset: '', verification: '', date_from: '', date_to: '', only_recoverable: false }
  void loadRecoverable()
}

async function refreshRecoverable() {
  refreshing.value = true
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/recoverable/refresh`, { method: 'POST' })
    const data = await res.json()
    if (!res.ok) throw new Error('重新解析失败')
    checkpointMessage.value = `已重新解析 ${data.已执行快照 ?? 0} 份已执行快照，新增依据包 ${data.新增依据包 ?? 0} 个`
    await Promise.all([loadRecoverable(), loadCheckpoints(), reload()])
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '重新解析失败'
  } finally {
    refreshing.value = false
  }
}

async function loadCheckpoints() {
  try {
    const res = await request(`${ENDPOINT}/checkpoints`)
    if (!res.ok) return
    const data = await res.json()
    checkpoints.value = data.items || []
  } catch (e) {
    // 检查点加载失败不阻断主清单
  }
}

async function loadBasisVersions(snapshotId: number) {
  try {
    const res = await request(`${ENDPOINT}/${snapshotId}/basis/versions`)
    if (!res.ok) return
    const data = await res.json()
    basisVersions.value = data.items || []
    const sources: string[] = data.sources || []
    basisSourceOptions.value = Array.from(new Set(sources))
  } catch (e) {
    basisVersions.value = []
  }
}

async function openBasis(item: Row) {
  selectedSnapshot.value = item
  basisVersion.value = null
  basisSource.value = ''
  crossDomainItems.value = []
  crossDomainError.value = ''
  await Promise.all([loadBasisVersions(item.快照记录id), loadBasis()])
}

async function loadBasis() {
  if (!selectedSnapshot.value) return
  basisError.value = ''
  const params = new URLSearchParams()
  if (basisVersion.value !== null && basisVersion.value !== undefined) params.set('version', String(basisVersion.value))
  if (basisSource.value) params.set('source', basisSource.value)
  try {
    const res = await request(`${ENDPOINT}/${selectedSnapshot.value.快照记录id}/basis?${params.toString()}`)
    if (!res.ok) throw new Error('该快照还没有可回查的恢复依据包')
    basisView.value = await res.json()
  } catch (e) {
    basisView.value = null
    basisError.value = e instanceof Error ? e.message : '依据包读取失败'
  }
}

function reloadBasis() {
  void loadBasis()
}

async function loadCrossDomain() {
  if (!selectedSnapshot.value) return
  crossDomainError.value = ''
  try {
    const snapshotId = selectedSnapshot.value.快照记录id
    const [auditRes, exportRes] = await Promise.all([
      request(`/api/audit/snapshot-basis?snapshot_id=${snapshotId}`),
      request(`/api/export/snapshot-basis?snapshot_id=${snapshotId}`),
    ])
    if (!auditRes.ok || !exportRes.ok) throw new Error('跨领域依据包读取失败')
    const audit = await auditRes.json()
    const exported = await exportRes.json()
    crossDomainItems.value = [
      ...(audit.items || []).map((row: Row) => ({ ...row, 模块: '数据审计' })),
      ...(exported.items || []).map((row: Row) => ({ ...row, 模块: '数据导出' })),
    ]
  } catch (e) {
    crossDomainError.value = e instanceof Error ? e.message : '跨领域依据包读取失败'
  }
}

function openRecovery(item: Row) {
  recoveryTarget.value = item
  recoveryReceipt.value = null
  recoveryError.value = ''
}

function closeRecovery() {
  recoveryTarget.value = null
  recoveryReceipt.value = null
  recoveryError.value = ''
}

async function confirmRecovery() {
  if (!recoveryTarget.value) return
  recovering.value = true
  recoveryError.value = ''
  try {
    const res = await request(`${ENDPOINT}/${recoveryTarget.value.快照记录id}/recover`, {
      method: 'POST',
      body: JSON.stringify({ operator: '值班管理员' }),
    })
    const data = await res.json()
    if (!res.ok || !data.ok) throw new Error(data.message || '恢复请求未受理')
    recoveryReceipt.value = data.entry
    checkpointMessage.value = data.message
    await loadRecoverable()
    if (selectedSnapshot.value) await loadBasis()
  } catch (e) {
    recoveryError.value = e instanceof Error ? e.message : '恢复请求未受理'
  } finally {
    recovering.value = false
  }
}

async function submitCheckpointEvent(checkpoint: Row, event: string) {
  checkpointMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/checkpoints/${checkpoint.id}/events`, {
      method: 'POST',
      body: JSON.stringify({ event }),
    })
    const data = await res.json()
    if (!res.ok) throw new Error(data.message || '检查点事件提交失败')
    checkpointMessage.value = data.message
    await Promise.all([loadCheckpoints(), loadRecoverable()])
    if (selectedSnapshot.value) await loadBasis()
  } catch (e) {
    checkpointMessage.value = e instanceof Error ? e.message : '检查点事件提交失败'
    await loadCheckpoints()
  }
}

function openCreate() {
  createValues.value = {}
  showCreate.value = true
}

async function submitCreate() {
  try {
    const res = await request(ENDPOINT, { method: 'POST', body: JSON.stringify({ values: createValues.value }) })
    if (!res.ok) throw new Error('创建失败')
    showCreate.value = false
    await reload()
  } catch (e: any) {
    errorMessage.value = e.message
  }
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function exportRecoverable() {
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/recoverable/export?${recoverableQuery().toString()}`)
    if (!res.ok) throw new Error('可恢复清单导出失败')
    const url = URL.createObjectURL(await res.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'snapshot_recoverable.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (e) {
    errorMessage.value = e instanceof Error ? e.message : '可恢复清单导出失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.section-title { font-size: 15px; margin: 20px 0 8px; }
.filter-item { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--muted); }
.filter-item select, .filter-item input[type='date'] { border: 1px solid var(--border); border-radius: 6px; padding: 6px 10px; font-size: 13px; background: #fff; }
.badge { display: inline-block; border-radius: 10px; padding: 1px 8px; font-size: 12px; }
.badge-ok { background: #dcfce7; color: #166534; }
.badge-bad { background: #fee2e2; color: #991b1b; }
.mono { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; }
.dataset-group { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; margin-bottom: 10px; }
.dataset-group-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; font-size: 13px; color: var(--muted); }
.dataset-group-head strong { color: #1f2937; font-size: 14px; }
.selection-note { font-size: 12px; color: var(--muted); margin: 6px 0; }
.basis-panel { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 12px; }
.basis-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 12px; flex-wrap: wrap; margin-bottom: 8px; }
.basis-overview h4, .basis-detail h4, .basis-cross h4 { margin: 8px 0; font-size: 13px; }
.basis-cross { border-top: 1px dashed var(--border); padding-top: 8px; margin-top: 8px; }
.event-chip { display: inline-block; margin: 2px 4px 2px 0; border-radius: 10px; padding: 1px 8px; font-size: 11px; }
.checkpoint-wrap { background: #fff; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; }
</style>
