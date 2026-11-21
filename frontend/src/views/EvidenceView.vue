<template>
  <section class="page" data-module="rule_evidence">
    <header class="page-head">
      <div>
        <h2>规则证据</h2>
        <p class="page-desc">
          质量门禁坏行样本定位、规则阈值与来源日期一并导出为异常核对文件；
          异常捕获按可验证规则与分区登记，恢复入口只放行证据有效的补偿分区。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="refresh" type="button" @click="reloadAll">刷新</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">执行批次</span>
        <strong class="stat-value">{{ executions.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">核对文件</span>
        <strong class="stat-value">{{ packages.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">导入登记</span>
        <strong class="stat-value">{{ imports.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待补证规则</span>
        <strong class="stat-value">{{ totalPending }}</strong>
      </article>
    </div>

    <!-- 执行质量门禁 -->
    <div class="evidence-panel">
      <h3>执行质量门禁并冻结证据</h3>
      <div class="analysis-form">
        <label>管线标识
          <input v-model="gateForm.pipeline_id" data-testid="gate-pipeline" />
        </label>
        <label>目标表
          <input v-model="gateForm.target_table" data-testid="gate-target" />
        </label>
        <label>来源表
          <input v-model="gateForm.source_table" data-testid="gate-source" />
        </label>
        <label>校验分区
          <input v-model="gateForm.partition" data-testid="gate-partition" />
        </label>
        <label>来源日期
          <input v-model="gateForm.source_date" data-testid="gate-source-date" />
        </label>
        <label>预先失效规则（逗号分隔）
          <input v-model="staleRuleText" data-testid="gate-stale" placeholder="如 R-RANGE-02" />
        </label>
      </div>
      <div class="json-grid">
        <label class="json-field">规则（含阈值、级别）
          <textarea v-model="rulesText" data-testid="gate-rules" rows="10"></textarea>
        </label>
        <label class="json-field">样本数据
          <textarea v-model="sampleText" data-testid="gate-samples" rows="10"></textarea>
        </label>
      </div>
      <p v-if="gateError" class="error-text" data-testid="gate-error">{{ gateError }}</p>
      <button class="btn primary" data-testid="gate-submit" type="button" @click="submitGate">
        执行门禁并冻结证据
      </button>
    </div>

    <!-- 执行批次列表 -->
    <div class="evidence-panel">
      <h3>执行批次与逐规则证据</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>执行批次</th>
            <th>管线 / 目标表</th>
            <th>分区 / 来源日期</th>
            <th>门禁决策</th>
            <th>可核对规则</th>
            <th>待补证规则</th>
            <th>凭证血缘</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in executions" :key="item.execution_id" :data-testid="`exec-${item.execution_id}`">
            <td>{{ item.execution_id }}<br /><small class="muted">{{ item.created_at }}</small></td>
            <td>{{ item.pipeline_id }}<br /><small class="muted">{{ item.target_table }}</small></td>
            <td>{{ item.partition }}<br /><small class="muted">{{ item.source_date }}</small></td>
            <td>
              <span :class="['badge', item.overall_pass ? 'badge-pass' : 'badge-block']">{{ item.decision }}</span>
            </td>
            <td>
              <span v-for="rid in item.verifiable_rules" :key="rid" class="badge badge-pass">{{ rid }}</span>
              <span v-if="!item.verifiable_rules.length" class="muted">—</span>
            </td>
            <td>
              <div v-for="p in item.pending_rules" :key="p.rule_id">
                <span class="badge badge-pending">{{ p.rule_id }}</span>
              </div>
              <span v-if="!item.pending_rules.length" class="muted">—</span>
            </td>
            <td>
              <small v-if="item.revision_of" class="muted">复核自 {{ item.revision_of }}</small>
              <small v-else class="muted">原始批次</small>
            </td>
            <td class="row-actions">
              <button class="link" type="button" @click="selectExecution(item.execution_id)">证据详情</button>
              <button class="link" type="button" @click="exportPackage(item.execution_id)">导出核对文件</button>
            </td>
          </tr>
          <tr v-if="!executions.length">
            <td colspan="8" class="empty-state">暂无执行批次，先在上方执行一轮质量门禁</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 证据详情 -->
    <div v-if="selected" class="evidence-panel" data-testid="evidence-detail">
      <h3>
        批次 {{ selected.execution_id }} 的规则证据
        <span :class="['badge', selected.overall_pass ? 'badge-pass' : 'badge-block']">{{ selected.decision }}</span>
      </h3>
      <p class="muted">
        来源 {{ selected.source_table }} → {{ selected.target_table }}，分区 {{ selected.partition }}，
        来源日期 {{ selected.source_date }}
        <template v-if="selected.revision_of">；本批次为 {{ selected.revision_of }} 的恢复后复核，规则沿用原批次快照</template>
      </p>

      <div v-for="rule in selected.rule_results" :key="rule.rule_id" class="rule-card" :data-testid="`rule-${rule.rule_id}`">
        <div class="rule-head">
          <strong>{{ rule.rule_id }} · {{ rule.rule_name }}</strong>
          <span :class="['badge', evidenceBadgeClass(rule.evidence_status)]">{{ rule.evidence_status }}</span>
          <span :class="['badge', rule.locator_status === 'ACTIVE' ? 'badge-pass' : 'badge-pending']">
            定位{{ rule.locator_status === 'ACTIVE' ? '有效' : '失效' }}
          </span>
        </div>
        <dl class="rule-meta">
          <div><dt>校验类型 / 字段</dt><dd>{{ rule.check_type }} · {{ rule.check_field }}</dd></div>
          <div><dt>规则阈值</dt><dd>{{ rule.threshold }}（{{ rule.level }}）</dd></div>
          <div><dt>坏行数 / 失败率</dt><dd>{{ rule.failed_rows }} 行 / {{ (rule.failed_ratio * 100).toFixed(2) }}%</dd></div>
          <div><dt>覆盖分区</dt><dd>{{ rule.partitions.join(', ') || '—' }}</dd></div>
        </dl>
        <p v-if="rule.missing_reason" class="missing-reason" data-testid="missing-reason">
          缺失说明：{{ rule.missing_reason }}
        </p>
        <table v-if="rule.samples.length" class="data-table sample-table">
          <thead>
            <tr><th>样本序号</th><th>行号</th><th>失败原因</th><th>定位链路</th><th>定位分区</th></tr>
          </thead>
          <tbody>
            <tr v-for="(sample, idx) in rule.samples" :key="idx">
              <td>{{ idx + 1 }}</td>
              <td>{{ sample.row_index ?? '—' }}</td>
              <td>{{ sample.reason ?? '—' }}</td>
              <td><small>{{ locatorPath(sample).join(' → ') }}</small></td>
              <td>{{ locatorPartition(sample) }}</td>
            </tr>
          </tbody>
        </table>
        <div class="row-actions">
          <button
            v-if="rule.locator_status === 'ACTIVE' && rule.failed_rows > 0"
            class="btn"
            type="button"
            :data-testid="`stale-${rule.rule_id}`"
            @click="markStale(rule.rule_id)"
          >
            标记定位失效
          </button>
        </div>
      </div>

      <div class="detail-actions">
        <button class="btn primary" type="button" data-testid="detail-export" @click="exportPackage(selected.execution_id)">
          下载异常核对文件
        </button>
        <button class="btn" type="button" data-testid="scope-btn" @click="loadScope(selected.execution_id)">
          核对可复跑分区
        </button>
        <button class="btn" type="button" data-testid="reverify-btn" @click="openReverify = !openReverify">
          恢复后重新核验
        </button>
      </div>

      <!-- 补偿范围 -->
      <div v-if="scope" class="scope-box" data-testid="scope-box">
        <h4>恢复入口补偿范围</h4>
        <p>
          可复跑分区（证据可核对，可重算实际坏行）：
          <span v-for="p in scope.rerunnable_partitions" :key="p.partition" class="badge badge-pass">
            {{ p.partition }}（{{ p.evidence_rules.join('、') }}）
          </span>
          <span v-if="!scope.rerunnable_partitions.length" class="muted">无</span>
        </p>
        <p>
          等待证据补齐（定位失效，不能复跑）：
        </p>
        <ul v-if="scope.blocked_partitions.length" class="blocked-list">
          <li v-for="b in scope.blocked_partitions" :key="b.rule_id">
            <span class="badge badge-pending">{{ b.rule_id }}</span>
            分区 {{ b.partition }}：{{ b.missing_reason }}
          </li>
        </ul>
        <p v-else class="muted">无受阻分区</p>
        <div class="analysis-form">
          <label>补偿分区（逗号分隔，仅限可复跑分区）
            <input v-model="compensationText" data-testid="comp-partitions"
                   :placeholder="scope.rerunnable_partitions.map((p) => p.partition).join(',')" />
          </label>
          <label>补偿原因
            <input v-model="compensationReason" data-testid="comp-reason" />
          </label>
          <button class="btn primary" type="button" data-testid="comp-submit" @click="submitComp">提交补偿</button>
        </div>
        <p v-if="scopeError" class="error-text">{{ scopeError }}</p>
      </div>

      <!-- 重新核验 -->
      <div v-if="openReverify" class="scope-box">
        <h4>重新核验（沿用本批次冻结的规则快照，不读取当前规则）</h4>
        <textarea v-model="reverifyText" class="json-textarea" rows="8" data-testid="reverify-samples"></textarea>
        <button class="btn primary" type="button" data-testid="reverify-submit" @click="submitReverify">
          提交复核
        </button>
      </div>
    </div>

    <!-- 核对文件与导入 -->
    <div class="evidence-panel">
      <h3>异常核对文件交接</h3>
      <table class="data-table">
        <thead>
          <tr><th>凭证编号</th><th>版本</th><th>执行批次</th><th>分区</th><th>来源日期</th><th>证据概览</th><th>操作</th></tr>
        </thead>
        <tbody>
          <tr v-for="pkg in packages" :key="pkg.package_id" :data-testid="`pkg-${pkg.package_id}`">
            <td>{{ pkg.package_id }}<br /><small class="muted">{{ pkg.content_hash.slice(0, 12) }}…</small></td>
            <td>{{ pkg.evidence_version }}</td>
            <td>{{ pkg.execution_id }}</td>
            <td>{{ pkg.partition }}</td>
            <td>{{ pkg.source_date }}</td>
            <td>
              <span class="badge badge-pass">可核对 {{ pkg.summary.verifiable_rule_count }}</span>
              <span class="badge badge-pending">待补证 {{ pkg.summary.pending_rule_count }}</span>
              <span class="badge">样本 {{ pkg.summary.sample_count }}</span>
            </td>
            <td class="row-actions">
              <button class="link" type="button" @click="downloadPackage(pkg.package_id)">下载文件</button>
            </td>
          </tr>
          <tr v-if="!packages.length">
            <td colspan="7" class="empty-state">暂无核对文件</td>
          </tr>
        </tbody>
      </table>

      <div class="import-box">
        <h4>异常捕获导入核对文件</h4>
        <input type="file" accept="application/json,.json" data-testid="import-file" @change="onFilePicked" />
        <button class="btn primary" type="button" data-testid="import-submit" :disabled="!pickedContent" @click="submitImport">
          导入并登记异常
        </button>
        <p v-if="importMessage" class="muted">{{ importMessage }}</p>
        <p v-if="importError" class="error-text">{{ importError }}</p>
      </div>

      <table v-if="imports.length" class="data-table">
        <thead>
          <tr><th>登记号</th><th>凭证</th><th>版本</th><th>批次</th><th>分区</th><th>登记异常</th><th>待补证规则</th></tr>
        </thead>
        <tbody>
          <tr v-for="imp in imports" :key="imp.import_id" :data-testid="`imp-${imp.import_id}`">
            <td>{{ imp.import_id }}</td>
            <td>{{ imp.package_id }}</td>
            <td>{{ imp.evidence_version }}</td>
            <td>{{ imp.execution_id }}</td>
            <td>{{ imp.partition }}</td>
            <td>{{ imp.registered_exception_ids.length }} 条</td>
            <td>
              <span v-for="p in imp.pending_rules" :key="p.rule_id" class="badge badge-pending">{{ p.rule_id }}</span>
              <span v-if="!imp.pending_rules.length" class="muted">—</span>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  downloadEvidencePackage,
  exportEvidencePackage,
  getRecoveryScope,
  importEvidencePackage,
  listExecutions,
  listImports,
  listPackages,
  markLocatorStale,
  reverifyExecution,
  runEvidenceGate,
  submitCompensation,
  type EvidenceExecution,
  type EvidenceImport,
  type EvidencePackage,
  type RecoveryScope,
} from '@/api/evidence'

const demoRules = [
  {
    rule_id: 'R-NOTNULL-01',
    rule_name: '订单主键非空检查',
    target_table: 'dw.dwd_order_wide',
    check_field: 'order_id',
    check_type: 'NOT_NULL',
    threshold: 0.0001,
    level: 'BLOCK',
  },
  {
    rule_id: 'R-RANGE-02',
    rule_name: '订单金额范围校验',
    target_table: 'dw.dwd_order_wide',
    check_field: 'order_amount',
    check_type: 'RANGE_VALUE',
    threshold: 0.01,
    level: 'WARN',
    extra: { min_value: 0, max_value: 100000 },
  },
]
const demoSamples = [
  { order_id: 'O1001', order_amount: 99.5 },
  { order_id: null, order_amount: 200 },
  { order_id: 'O1003', order_amount: -50 },
  { order_id: 'O1004', order_amount: 88 },
]

const gateForm = ref({
  pipeline_id: 'ODS到DWD订单宽表ETL',
  target_table: 'dw.dwd_order_wide',
  source_table: 'ods.ods_order_info',
  partition: '2026-11-20',
  source_date: '2026-11-20',
})
const staleRuleText = ref('')
const rulesText = ref(JSON.stringify(demoRules, null, 2))
const sampleText = ref(JSON.stringify(demoSamples, null, 2))
const gateError = ref('')

const executions = ref<EvidenceExecution[]>([])
const packages = ref<EvidencePackage[]>([])
const imports = ref<EvidenceImport[]>([])
const selectedId = ref('')
const scope = ref<RecoveryScope | null>(null)
const scopeError = ref('')
const compensationText = ref('')
const compensationReason = ref('')
const openReverify = ref(false)
const reverifyText = ref(JSON.stringify(demoSamples, null, 2))
const pickedContent = ref<unknown>(null)
const importMessage = ref('')
const importError = ref('')

const selected = computed(() =>
  executions.value.find((item) => item.execution_id === selectedId.value) ?? null,
)
const totalPending = computed(() =>
  executions.value.reduce((sum, item) => sum + item.pending_rules.length, 0),
)

function evidenceBadgeClass(status: string): string {
  if (status === '可核对') return 'badge-pass'
  if (status === '待补证') return 'badge-pending'
  return 'badge-muted'
}

type SampleLocator = { lineage_path?: string[]; partition?: string }

function locatorOf(sample: Record<string, unknown>): SampleLocator {
  const locator = sample.locator
  return locator && typeof locator === 'object' ? (locator as SampleLocator) : {}
}

function locatorPath(sample: Record<string, unknown>): string[] {
  return locatorOf(sample).lineage_path ?? []
}

function locatorPartition(sample: Record<string, unknown>): string {
  return locatorOf(sample).partition ?? '—'
}

async function reloadAll() {
  const [executionList, packageList, importList] = await Promise.all([
    listExecutions(),
    listPackages(),
    listImports(),
  ])
  executions.value = executionList
  packages.value = packageList
  imports.value = importList
}

async function submitGate() {
  gateError.value = ''
  try {
    const rules = JSON.parse(rulesText.value)
    const sampleData = JSON.parse(sampleText.value)
    const stale = staleRuleText.value
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    const execution = await runEvidenceGate({
      ...gateForm.value,
      rules,
      sample_data: sampleData,
      stale_rule_ids: stale,
    })
    selectedId.value = execution.execution_id
    scope.value = null
    await reloadAll()
  } catch (error) {
    gateError.value = error instanceof Error ? error.message : '门禁执行失败'
  }
}

function selectExecution(executionId: string) {
  selectedId.value = executionId
  scope.value = null
  scopeError.value = ''
}

async function markStale(ruleId: string) {
  if (!selected.value) return
  try {
    await markLocatorStale(selected.value.execution_id, ruleId, '值班人员确认样本定位链路已失效')
    await reloadAll()
  } catch (error) {
    gateError.value = error instanceof Error ? error.message : '标记失效失败'
  }
}

async function exportPackage(executionId: string) {
  try {
    await exportEvidencePackage(executionId, '值班组交接')
    await reloadAll()
  } catch (error) {
    gateError.value = error instanceof Error ? error.message : '导出失败'
  }
}

async function downloadPackage(packageId: string) {
  try {
    await downloadEvidencePackage(packageId)
  } catch (error) {
    gateError.value = error instanceof Error ? error.message : '下载失败'
  }
}

async function loadScope(executionId: string) {
  scopeError.value = ''
  try {
    scope.value = await getRecoveryScope(executionId)
    compensationText.value = scope.value.rerunnable_partitions.map((p) => p.partition).join(',')
  } catch (error) {
    scopeError.value = error instanceof Error ? error.message : '补偿范围读取失败'
  }
}

async function submitComp() {
  if (!selected.value) return
  scopeError.value = ''
  try {
    const partitions = compensationText.value
      .split(',')
      .map((item) => item.trim())
      .filter(Boolean)
    const result = await submitCompensation(
      selected.value.execution_id,
      partitions,
      compensationReason.value,
    )
    scopeError.value = ''
    window.alert(`补偿任务已提交：${result.task_name}（${result.partitions.join('、')}）`)
  } catch (error) {
    scopeError.value = error instanceof Error ? error.message : '补偿提交失败'
  }
}

async function submitReverify() {
  if (!selected.value) return
  gateError.value = ''
  try {
    const sampleData = JSON.parse(reverifyText.value)
    const next = await reverifyExecution(selected.value.execution_id, sampleData, '恢复后复核')
    selectedId.value = next.execution_id
    openReverify.value = false
    scope.value = null
    await reloadAll()
  } catch (error) {
    gateError.value = error instanceof Error ? error.message : '复核失败'
  }
}

function onFilePicked(event: Event) {
  importError.value = ''
  importMessage.value = ''
  const input = event.target as HTMLInputElement
  const file = input.files?.[0]
  if (!file) {
    pickedContent.value = null
    return
  }
  const reader = new FileReader()
  reader.onload = () => {
    try {
      pickedContent.value = JSON.parse(String(reader.result))
      importMessage.value = `已读取核对文件 ${file.name}，可导入`
    } catch {
      pickedContent.value = null
      importError.value = '文件不是合法 JSON'
    }
  }
  reader.readAsText(file)
}

async function submitImport() {
  importError.value = ''
  importMessage.value = ''
  try {
    const result = await importEvidencePackage(pickedContent.value)
    importMessage.value =
      `登记 ${result.registered_exception_ids.length} 条异常；可核对规则 ${result.verifiable_rules.join('、') || '无'}；` +
      `待补证 ${result.pending_rules.map((p) => p.rule_id).join('、') || '无'}`
    pickedContent.value = null
    await reloadAll()
  } catch (error) {
    importError.value = error instanceof Error ? error.message : '导入失败'
  }
}

onMounted(reloadAll)
</script>

<style scoped>
.evidence-panel {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 14px 16px;
  margin-bottom: 16px;
}
.evidence-panel h3 {
  margin: 0 0 12px;
  font-size: 15px;
}
.json-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  margin-bottom: 10px;
}
.json-field span,
.import-box h4 {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 4px;
}
textarea,
.json-textarea {
  width: 100%;
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 8px;
  font-family: monospace;
  font-size: 12px;
}
.badge {
  display: inline-block;
  border-radius: 10px;
  padding: 2px 8px;
  font-size: 11px;
  background: #e2e8f0;
  color: #334155;
  margin: 1px 2px;
}
.badge-pass {
  background: #dcfce7;
  color: #166534;
}
.badge-block,
.badge-pending {
  background: #fee2e2;
  color: #991b1b;
}
.badge-muted {
  background: #f1f5f9;
  color: var(--muted);
}
.muted {
  color: var(--muted);
}
.rule-card {
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 12px;
  background: #fafbfc;
}
.rule-head {
  display: flex;
  gap: 8px;
  align-items: center;
  margin-bottom: 8px;
}
.rule-meta {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 8px;
  margin: 0 0 8px;
}
.rule-meta dt {
  font-size: 12px;
  color: var(--muted);
}
.rule-meta dd {
  margin: 2px 0 0;
  font-size: 13px;
}
.sample-table {
  margin: 8px 0;
}
.missing-reason {
  color: #991b1b;
  font-size: 13px;
  background: #fef2f2;
  border-radius: 6px;
  padding: 6px 8px;
}
.detail-actions {
  display: flex;
  gap: 8px;
  margin: 10px 0;
}
.scope-box,
.import-box {
  border-top: 1px solid var(--border);
  padding-top: 12px;
  margin-top: 12px;
}
.blocked-list {
  font-size: 13px;
  color: #991b1b;
}
</style>
