<template>
  <section class="page" data-module="recovery-console">
    <header class="page-head">
      <div>
        <h2>采集失败恢复清单</h2>
        <p class="page-desc">
          一次采集失败只允许一个有权采用的规则结论进入清单：自有规则按模板归属核验，
          共享规则须人工复核。值班员据此区分「立即单字段补采」与「待复核」，并按清单派发实际采集范围。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" type="button" @click="showTrigger = !showTrigger">
          {{ showTrigger ? '收起触发面板' : '模拟漂移触发' }}
        </button>
        <button class="btn primary" type="button" @click="reload">刷新清单</button>
      </div>
    </header>

    <div v-if="showTrigger" class="modal" data-testid="trigger-panel">
      <h3>触发同名定位失败</h3>
      <p class="page-desc">A、B 可使用相同异常特征，归属按模板编号核验，不会串位。</p>
      <label class="create-field"><span>所属模板</span>
        <input v-model="trigger.template_code" data-testid="trigger-template" placeholder="如 TPL-A / TPL-B" />
      </label>
      <label class="create-field"><span>异常特征</span>
        <input v-model="trigger.signature" data-testid="trigger-signature" placeholder="如 元素定位漂移:ORDER_LIST:drift_x" />
      </label>
      <label class="create-field"><span>关联执行id（可选）</span>
        <input v-model="trigger.flow_run_id" data-testid="trigger-run" placeholder="如 4" />
      </label>
      <div class="page-actions">
        <button class="btn primary" data-testid="trigger-submit" type="button" @click="submitTrigger">提交仲裁</button>
      </div>
      <p v-if="triggerMessage" class="error-text">{{ triggerMessage }}</p>
    </div>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">清单总数</span>
        <strong class="stat-value">{{ rows.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">可立即恢复</span>
        <strong class="stat-value">{{ countByStatus('可恢复') }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待人工复核</span>
        <strong class="stat-value">{{ countByStatus('待复核') }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">已派发/已耗尽</span>
        <strong class="stat-value">{{ countByStatus('已派发') + countByStatus('已耗尽') }}</strong>
      </article>
    </div>

    <p class="status-legend">
      <button class="btn ghost" v-for="tab in statusTabs" :key="tab.value" type="button"
              :data-testid="`tab-${tab.value}`" @click="activeStatus = tab.value">
        {{ tab.label }}（{{ tab.value ? countByStatus(tab.value) : rows.length }}）
      </button>
    </p>

    <table class="data-table" data-testid="recovery-table">
      <thead>
        <tr>
          <th>所属模板</th>
          <th>异常特征</th>
          <th>采用规则</th>
          <th>授权范围</th>
          <th>恢复条件</th>
          <th>采集范围</th>
          <th>关联策略</th>
          <th>清单状态</th>
          <th>已处置</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in visibleRows" :key="row.id" :data-testid="`recovery-row-${row.id}`"
            :data-status="row.清单状态">
          <td>{{ row.所属模板 }}</td>
          <td>{{ row.异常特征 }}</td>
          <td>{{ row.采用规则编号 }}</td>
          <td>{{ row.授权范围 }}</td>
          <td>{{ row.恢复条件 }}</td>
          <td>{{ row.采集范围 }}</td>
          <td>{{ row.策略编号 }}</td>
          <td>{{ row.清单状态 }}</td>
          <td>{{ row.已处置次数 }}</td>
          <td class="row-actions">
            <button v-if="row.清单状态 === '待复核'" class="link" type="button"
                    :data-testid="`review-${row.id}`" @click="openReview(row)">复核通过</button>
            <button v-if="row.清单状态 === '可恢复'" class="link" type="button"
                    :data-testid="`dispatch-${row.id}`" @click="openDispatch(row)">派发恢复</button>
            <button class="link" type="button" :data-testid="`history-${row.id}`"
                    @click="openHistory(row)">回查历史</button>
          </td>
        </tr>
        <tr v-if="!visibleRows.length">
          <td colspan="10" class="empty-state">暂无恢复清单条目，可先模拟漂移触发</td>
        </tr>
      </tbody>
    </table>

    <div v-if="reviewTarget" class="modal-backdrop" data-testid="review-modal">
      <form class="modal" @submit.prevent="submitReview">
        <h3>人工复核 · {{ reviewTarget.采用规则编号 }}</h3>
        <p class="page-desc">
          失败 {{ reviewTarget.failure_key }} 命中共享规则，恢复条件为「{{ reviewTarget.恢复条件 }}」，
          复核通过后才能派发。
        </p>
        <label class="create-field"><span>值班员</span>
          <input v-model="reviewForm.reviewer" data-testid="review-reviewer" required />
        </label>
        <label class="create-field"><span>复核意见</span>
          <input v-model="reviewForm.remark" data-testid="review-remark" placeholder="确认漂移可恢复" />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="reviewTarget = null">取消</button>
          <button class="btn primary" data-testid="review-submit" type="button" @click="submitReview">确认通过</button>
        </div>
      </form>
    </div>

    <div v-if="dispatchTarget" class="modal-backdrop" data-testid="dispatch-modal">
      <form class="modal" @submit.prevent="submitDispatch">
        <h3>派发恢复 · {{ dispatchTarget.所属模板 }}</h3>
        <p class="page-desc">
          采用规则 {{ dispatchTarget.采用规则编号 }}（{{ dispatchTarget.授权范围 }}），
          实际采集范围「{{ dispatchTarget.采集范围 }}」。队列任务归属将与失败模板核验。
        </p>
        <label class="create-field"><span>队列任务id</span>
          <input v-model="dispatchForm.taskId" data-testid="dispatch-task" required />
        </label>
        <label class="create-field"><span>节点编号</span>
          <input v-model="dispatchForm.nodeCode" data-testid="dispatch-node" required />
        </label>
        <div class="page-actions">
          <button class="btn" type="button" @click="dispatchTarget = null">取消</button>
          <button class="btn primary" data-testid="dispatch-submit" type="button" @click="submitDispatch">确认派发</button>
        </div>
        <p v-if="dispatchReceipt" class="page-desc" data-testid="dispatch-receipt">{{ dispatchReceipt }}</p>
      </form>
    </div>

    <div v-if="historyTarget" class="modal-backdrop" data-testid="history-modal">
      <div class="modal">
        <h3>回查历史 · {{ historyTarget.failure_key }}</h3>
        <h4>原匹配记录（含未采用规则）</h4>
        <table class="data-table">
          <thead><tr><th>规则编号</th><th>授权</th><th>归属模板</th><th>优先级</th><th>是否采用</th></tr></thead>
          <tbody>
            <tr v-for="h in matchHistory" :key="h.id">
              <td>{{ h.规则编号 }}</td><td>{{ h.授权范围 }}</td><td>{{ h.归属模板 || '共享' }}</td>
              <td>{{ h.生效优先级 }}</td><td>{{ h.是否采用 }}</td>
            </tr>
          </tbody>
        </table>
        <h4>派发执行记录</h4>
        <table class="data-table">
          <thead><tr><th>恢复执行号</th><th>规则</th><th>节点</th><th>采集范围</th><th>时间</th></tr></thead>
          <tbody>
            <tr v-for="d in dispatchHistory" :key="d.id">
              <td>{{ d.恢复执行号 }}</td><td>{{ d.采用规则编号 }}</td><td>{{ d.节点编号 }}</td>
              <td>{{ d.采集范围 }}</td><td>{{ d.派发时间 }}</td>
            </tr>
            <tr v-if="!dispatchHistory.length"><td colspan="5" class="empty-state">尚未派发</td></tr>
          </tbody>
        </table>
        <div class="page-actions">
          <button class="btn primary" type="button" @click="historyTarget = null">关闭</button>
        </div>
      </div>
    </div>

    <footer class="page-foot">
      <span>共 {{ rows.length }} 条恢复清单</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  dispatchRecovery,
  loadDispatchHistory,
  loadMatchHistory,
  loadRecoveryQueue,
  resolveFailure,
  reviewResolution,
  type DispatchHistoryRow,
  type MatchHistoryRow,
  type RecoveryResolution,
} from '@/api/recovery'

const rows = ref<RecoveryResolution[]>([])
const errorMessage = ref('')
const activeStatus = ref('')
const showTrigger = ref(false)
const triggerMessage = ref('')
const trigger = ref({ template_code: 'TPL-A', signature: '元素定位漂移:ORDER_LIST:drift_x', flow_run_id: '' })

const reviewTarget = ref<RecoveryResolution | null>(null)
const reviewForm = ref({ reviewer: '值班员', remark: '确认漂移可恢复' })
const dispatchTarget = ref<RecoveryResolution | null>(null)
const dispatchForm = ref({ taskId: '', nodeCode: 'ROBO-0002' })
const dispatchReceipt = ref('')
const historyTarget = ref<RecoveryResolution | null>(null)
const matchHistory = ref<MatchHistoryRow[]>([])
const dispatchHistory = ref<DispatchHistoryRow[]>([])

const statusTabs = [
  { label: '全部', value: '' },
  { label: '可恢复', value: '可恢复' },
  { label: '待复核', value: '待复核' },
  { label: '已派发', value: '已派发' },
  { label: '已耗尽', value: '已耗尽' },
]

const visibleRows = computed(() =>
  activeStatus.value ? rows.value.filter((r) => r.清单状态 === activeStatus.value) : rows.value,
)

function countByStatus(status: string): number {
  return rows.value.filter((r) => r.清单状态 === status).length
}

async function reload() {
  errorMessage.value = ''
  try {
    const payload = await loadRecoveryQueue()
    rows.value = payload.items
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '恢复清单读取失败'
  }
}

async function submitTrigger() {
  triggerMessage.value = ''
  try {
    const runId = Number(trigger.value.flow_run_id)
    const result = await resolveFailure({
      template_code: trigger.value.template_code,
      signature: trigger.value.signature,
      ...(Number.isFinite(runId) && trigger.value.flow_run_id ? { flow_run_id: runId } : {}),
    })
    const status = result.status as string | undefined
    triggerMessage.value =
      status === 'existing' ? '该失败已在清单中，沿用既有规则结论（幂等）'
        : status === 'no_rule' ? `未命中可用规则：${String(result.reason ?? '')}`
          : '仲裁完成，规则结论已写入恢复清单'
    await reload()
  } catch (error) {
    triggerMessage.value = error instanceof Error ? error.message : '触发失败'
  }
}

function openReview(row: RecoveryResolution) {
  reviewTarget.value = row
}

async function submitReview() {
  if (!reviewTarget.value) return
  const result = await reviewResolution(
    reviewTarget.value.id,
    reviewForm.value.reviewer,
    reviewForm.value.remark,
  )
  if (!result.ok) {
    errorMessage.value = result.reason ?? '复核失败'
    return
  }
  reviewTarget.value = null
  await reload()
}

function openDispatch(row: RecoveryResolution) {
  dispatchTarget.value = row
  dispatchReceipt.value = ''
  dispatchForm.value = { taskId: '', nodeCode: 'ROBO-0002' }
}

async function submitDispatch() {
  if (!dispatchTarget.value) return
  errorMessage.value = ''
  const result = await dispatchRecovery(
    dispatchTarget.value.id,
    Number(dispatchForm.value.taskId),
    dispatchForm.value.nodeCode,
  )
  if (!result.ok) {
    errorMessage.value = result.reason ?? '派发失败'
    return
  }
  dispatchReceipt.value = result.receipt ?? '派发成功'
  await reload()
}

async function openHistory(row: RecoveryResolution) {
  historyTarget.value = row
  const [matches, dispatches] = await Promise.all([
    loadMatchHistory(row.所属模板, row.异常特征),
    loadDispatchHistory(row.所属模板, row.异常特征),
  ])
  matchHistory.value = matches.items
  dispatchHistory.value = dispatches.items
}

onMounted(reload)
</script>
