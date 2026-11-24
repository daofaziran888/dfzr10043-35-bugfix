import { fetchJson, request } from './client'

/** 恢复清单条目：一次采集失败唯一对应的规则结论。 */
export type RecoveryResolution = {
  id: number
  failure_key: string
  清单编号: string
  所属模板: string
  异常特征: string
  来源: string
  关联执行id: number | string
  采用规则id: number
  采用规则编号: string
  授权范围: string
  恢复条件: string
  采集范围: string
  策略编号: string
  清单状态: string
  已处置次数: string
  入单时间: string
  更新时间: string
  复核人?: string
  复核备注?: string
}

export type MatchHistoryRow = {
  id: number
  failure_key: string
  规则编号: string
  授权范围: string
  归属模板: string
  生效优先级: string
  是否采用: string
  匹配时间: string
}

export type DispatchHistoryRow = {
  id: number
  failure_key: string
  队列编号: string
  节点编号: string
  采用规则编号: string
  策略编号: string
  恢复执行号: string
  采集范围: string
  派发时间: string
  派发回执: string
}

type QueueResponse = { items: RecoveryResolution[]; total: number }
type ListResponse<T> = { items: T[] }

export async function loadRecoveryQueue(status?: string): Promise<QueueResponse> {
  const query = status ? `?status=${encodeURIComponent(status)}` : ''
  return fetchJson<QueueResponse>(`/api/recovery/queue${query}`)
}

export async function resolveFailure(payload: {
  signature: string
  template_code: string
  flow_run_id?: number
  scope?: string
}): Promise<Record<string, unknown>> {
  const response = await request('/api/recovery/failures/resolve', {
    method: 'POST',
    body: JSON.stringify({ scope: '定位', source: '元素定位漂移', ...payload }),
  })
  return await response.json()
}

export async function reviewResolution(
  resolutionId: number,
  reviewer: string,
  remark: string,
): Promise<{ ok: boolean; reason?: string; approved?: boolean }> {
  const response = await request(`/api/recovery/resolutions/${resolutionId}/review`, {
    method: 'POST',
    body: JSON.stringify({ reviewer, approved: true, remark }),
  })
  return await response.json()
}

export async function dispatchRecovery(
  resolutionId: number,
  taskId: number,
  nodeCode: string,
): Promise<{ ok: boolean; reason?: string; receipt?: string; execution_no?: string }> {
  const response = await request(`/api/recovery/resolutions/${resolutionId}/dispatch`, {
    method: 'POST',
    body: JSON.stringify({ task_id: taskId, node_code: nodeCode }),
  })
  return await response.json()
}

export async function loadMatchHistory(
  templateCode?: string,
  signature?: string,
): Promise<ListResponse<MatchHistoryRow>> {
  const params = new URLSearchParams()
  if (templateCode) params.set('template_code', templateCode)
  if (signature) params.set('signature', signature)
  const query = params.toString()
  return fetchJson<ListResponse<MatchHistoryRow>>(
    `/api/recovery/failures/match-history${query ? `?${query}` : ''}`,
  )
}

export async function loadDispatchHistory(
  templateCode?: string,
  signature?: string,
): Promise<ListResponse<DispatchHistoryRow>> {
  const params = new URLSearchParams()
  if (templateCode) params.set('template_code', templateCode)
  if (signature) params.set('signature', signature)
  const query = params.toString()
  return fetchJson<ListResponse<DispatchHistoryRow>>(
    `/api/recovery/failures/dispatch-history${query ? `?${query}` : ''}`,
  )
}
