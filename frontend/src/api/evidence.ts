/** 规则证据 API：质量门禁执行、异常核对文件导出/下载/导入、恢复补偿。 */
import { request } from './client'

export type RuleEvidence = {
  rule_id: string
  rule_name: string
  target_table: string
  check_field: string
  check_type: string
  threshold: number
  level: string
  total_rows: number
  passed_rows: number
  failed_rows: number
  failed_ratio: number
  threshold_exceeded: boolean
  blocked: boolean
  evidence_status: '可核对' | '待补证' | '无失败'
  locator_status: 'ACTIVE' | 'STALE'
  missing_reason: string
  partitions: string[]
  samples: Array<Record<string, unknown>>
}

export type EvidenceExecution = {
  execution_id: string
  pipeline_id: string
  target_table: string
  source_table: string
  partition: string
  source_date: string
  created_at: string
  overall_pass: boolean
  decision: string
  lineage_active: boolean
  stale_rule_ids: string[]
  parent_execution_id: string
  parent_package_id: string
  revision_of: string
  note: string
  verifiable_rules: string[]
  pending_rules: Array<{ rule_id: string; rule_name: string; missing_reason: string }>
  rule_snapshot: Array<Record<string, unknown>>
  rule_results: RuleEvidence[]
}

export type EvidencePackage = {
  package_id: string
  evidence_version: string
  execution_id: string
  pipeline_id: string
  target_table: string
  partition: string
  source_date: string
  exported_at: string
  decision: string
  content_hash: string
  summary: {
    failed_rule_count: number
    verifiable_rule_count: number
    pending_rule_count: number
    sample_count: number
  }
}

export type EvidenceImport = {
  import_id: string
  package_id: string
  evidence_version: string
  content_hash: string
  execution_id: string
  pipeline_id: string
  target_table: string
  partition: string
  source_date: string
  imported_at: string
  registered_exception_ids: number[]
  verifiable_rules: string[]
  pending_rules: Array<{ rule_id: string; rule_name: string; missing_reason: string }>
}

export type RecoveryScope = {
  execution_id: string
  package_ids: string[]
  rerunnable_partitions: Array<{ partition: string; evidence_rules: string[] }>
  blocked_partitions: Array<{ partition: string; rule_id: string; rule_name: string; missing_reason: string }>
  can_submit_compensation: boolean
}

async function jsonOrThrow<T>(response: Response, fallback: string): Promise<T> {
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(payload?.detail ?? fallback)
  }
  return payload as T
}

export async function runEvidenceGate(body: Record<string, unknown>): Promise<EvidenceExecution> {
  const response = await request('/api/evidence/executions', {
    method: 'POST',
    body: JSON.stringify(body),
  })
  return jsonOrThrow(response, '质量门禁执行失败')
}

export async function listExecutions(): Promise<EvidenceExecution[]> {
  const response = await request('/api/evidence/executions')
  const payload = await jsonOrThrow<{ items: EvidenceExecution[] }>(response, '执行批次读取失败')
  return payload.items
}

export async function markLocatorStale(
  executionId: string,
  ruleId: string,
  reason: string,
): Promise<EvidenceExecution> {
  const response = await request(`/api/evidence/executions/${executionId}/mark-stale`, {
    method: 'POST',
    body: JSON.stringify({ rule_id: ruleId, reason }),
  })
  return jsonOrThrow(response, '定位失效标记失败')
}

export async function exportEvidencePackage(
  executionId: string,
  remark = '',
): Promise<EvidencePackage> {
  const response = await request(`/api/evidence/executions/${executionId}/export`, {
    method: 'POST',
    body: JSON.stringify({ remark }),
  })
  return jsonOrThrow(response, '核对文件导出失败')
}

export async function listPackages(): Promise<EvidencePackage[]> {
  const response = await request('/api/evidence/packages')
  const payload = await jsonOrThrow<{ items: EvidencePackage[] }>(response, '核对文件读取失败')
  return payload.items
}

export async function downloadEvidencePackage(packageId: string): Promise<void> {
  const response = await request(`/api/evidence/packages/${packageId}/download`)
  if (!response.ok) throw new Error('核对文件下载失败')
  const url = URL.createObjectURL(await response.blob())
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${packageId}.json`
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  URL.revokeObjectURL(url)
}

export async function importEvidencePackage(packageContent: unknown): Promise<EvidenceImport> {
  const response = await request('/api/evidence/imports', {
    method: 'POST',
    body: JSON.stringify(packageContent),
  })
  return jsonOrThrow(response, '核对文件导入失败')
}

export async function listImports(): Promise<EvidenceImport[]> {
  const response = await request('/api/evidence/imports')
  const payload = await jsonOrThrow<{ items: EvidenceImport[] }>(response, '导入登记读取失败')
  return payload.items
}

export async function getRecoveryScope(executionId: string): Promise<RecoveryScope> {
  const response = await request(`/api/evidence/executions/${executionId}/recovery-scope`)
  return jsonOrThrow(response, '补偿范围读取失败')
}

export async function submitCompensation(
  executionId: string,
  partitions: string[],
  reason: string,
): Promise<{ compensation_id: number; task_name: string; partitions: string[]; status: string }> {
  const response = await request(`/api/evidence/executions/${executionId}/compensations`, {
    method: 'POST',
    body: JSON.stringify({ partitions, reason }),
  })
  return jsonOrThrow(response, '补偿提交失败')
}

export async function reverifyExecution(
  executionId: string,
  sampleData: Array<Record<string, unknown>>,
  note: string,
): Promise<EvidenceExecution> {
  const response = await request(`/api/evidence/executions/${executionId}/reverify`, {
    method: 'POST',
    body: JSON.stringify({ sample_data: sampleData, note }),
  })
  return jsonOrThrow(response, '重新核验失败')
}
