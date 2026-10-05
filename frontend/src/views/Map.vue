<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api } from '../api'
const data = ref<any>(null)
const candidates = ref<any[]>([])
const violKeys = ref<Set<string>>(new Set())
const selected = ref<number[]>([])
const busy = ref(false)
const message = ref('')

function applyPlan(plan: any) {
  // 成功路径：图、违规、统计、当前方案版本整体切到新方案
  data.value = { ...plan, violations: plan.violations || data.value?.violations }
  const keys = new Set<string>()
  for (const x of plan.violations || []) {
    if (x.a_id != null) keys.add(String(x.a_id))
    if (x.b_id != null) keys.add(String(x.b_id))
  }
  violKeys.value = keys
}

async function run() {
  busy.value = true
  message.value = ''
  try {
    applyPlan(await api('/seating/run?hall_id=1', { method: 'POST' }))
  } catch (e) {
    message.value = '重新排座失败：' + (e as Error).message
  } finally {
    busy.value = false
  }
}

async function loadLatest() {
  const plan = await api('/seating/latest?hall_id=1')
  applyPlan(plan)
  return plan
}

async function swap() {
  if (selected.value.length !== 2 || busy.value) return
  const [a, b] = selected.value
  const before = data.value // 失败时视图必须停留在操作前版本
  busy.value = true
  message.value = ''
  try {
    // 对调是一次方案版本切换：成功后服务端返回全新版本
    const plan = await api('/seating/swap?hall_id=1', {
      method: 'POST',
      body: JSON.stringify({ candidate_a_id: a, candidate_b_id: b }),
    })
    selected.value = []
    applyPlan(plan)
  } catch (e) {
    // 整次失败：不采用任何局部改动，图/违规/统计/版本全部保持操作前
    data.value = before
    message.value = '对调失败，已回滚到操作前方案：' + (e as Error).message
  } finally {
    busy.value = false
  }
}

async function voidPlan() {
  if (busy.value) return
  busy.value = true
  message.value = ''
  try {
    const r = await api('/seating/void?hall_id=1', { method: 'POST', body: JSON.stringify({ hall_id: 1 }) })
    selected.value = []
    // 作废只标记版本：存在上一生效版本则回退到它，否则重新排座开新版本
    const cur = await loadLatest()
    message.value = r.version === cur.id
      ? `方案版本 v${r.version} 已作废`
      : `方案版本 v${r.version} 已作废，当前生效 v${cur.id}`
  } catch (e) {
    message.value = '作废失败：' + (e as Error).message
  } finally {
    busy.value = false
  }
}

function toggleSelect(id: number) {
  const i = selected.value.indexOf(id)
  if (i >= 0) selected.value.splice(i, 1)
  else if (selected.value.length < 2) selected.value.push(id)
}

onMounted(async () => {
  candidates.value = await api('/candidates')
  await loadLatest()
})

const gridStyle = computed(() => data.value ? ({ gridTemplateColumns: `repeat(${data.value.cols}, 72px)` }) : {})
const seatedIds = computed(() => new Set((data.value?.assignments || []).map((a: any) => a.candidate_id)))
const cells = computed(() => {
  if (!data.value) return []
  const map = new Map<string, any>()
  for (const a of data.value.assignments || []) map.set(a.row + ',' + a.col, a)
  const out: any[] = []
  for (let r = 0; r < data.value.rows; r++) {
    for (let c = 0; c < data.value.cols; c++) {
      out.push(map.get(r + ',' + c) || { empty: true, row: r, col: c })
    }
  }
  return out
})
function isViol(cell: any) {
  if (cell.empty) return false
  const id = cell.candidate_id ?? cell.id
  return id != null && violKeys.value.has(String(id))
}
function isPicked(cell: any) {
  return !cell.empty && selected.value.includes(cell.candidate_id)
}
function paperClass(pid: number) {
  return pid % 2 === 0 ? 'b' : 'a'
}
</script>
<template>
  <h1>考场课桌网格</h1>
  <p class="sub">课桌网格为主视图 · 点两张课桌对调（整版切换、失败整版回滚）· 违规课桌高亮</p>
  <div class="swap-bar">
    <button class="btn" :disabled="busy" @click="run">重新排座</button>
    <button class="btn" :disabled="busy || selected.length !== 2" @click="swap">
      对调所选两人{{ selected.length === 2 ? '' : '（选 2 人）' }}
    </button>
    <button class="btn" :disabled="busy" @click="voidPlan">作废当前版本</button>
    <span v-if="data" class="version-tag">
      当前方案版本：v{{ data.id }}<template v-if="data.based_on_id">（由 v{{ data.based_on_id }} 对调生成）</template>
    </span>
    <span v-if="data" class="muted">已排 {{ data.stats?.seated }} · 未排 {{ data.stats?.unplaced }} · 违规 {{ data.stats?.violations }}</span>
  </div>
  <p v-if="message" class="swap-msg">{{ message }}</p>
  <div class="hs-classroom" style="margin-top:0.85rem">
    <aside class="hs-clipboard">
      <h2>考生名册</h2>
      <div
        v-for="c in candidates" :key="c.id"
        class="hs-roster-row"
        :class="{ 'roster-selectable': seatedIds.has(c.id), 'roster-picked': selected.includes(c.id) }"
        @click="seatedIds.has(c.id) && toggleSelect(c.id)"
      >
        <div>
          <div>{{ c.name }}</div>
          <div class="hs-ticket">{{ c.ticket_no }}</div>
        </div>
        <div>卷{{ c.paper_id }}</div>
      </div>
    </aside>
    <div class="hs-desk-stage" v-if="data">
      <div class="hs-grid-board" :style="gridStyle">
        <div
          v-for="(cell,i) in cells" :key="i"
          class="hs-desk"
          :class="{ empty: cell.empty, 'hs-viol': isViol(cell), 'hs-picked': isPicked(cell) }"
          @click="!cell.empty && toggleSelect(cell.candidate_id)"
        >
          <template v-if="!cell.empty">
            <span class="hs-paper-tag" :class="paperClass(cell.paper_id)">卷{{ cell.paper_id }}</span>
            <div>{{ cell.name }}</div>
          </template>
          <template v-else>·</template>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped>
.swap-bar { display: flex; align-items: center; gap: 0.6rem; flex-wrap: wrap; margin: 0.5rem 0; }
.version-tag { font-weight: 600; }
.swap-msg { color: #b42318; margin: 0.3rem 0; }
.hs-desk { cursor: pointer; user-select: none; }
.hs-desk.empty { cursor: default; }
.hs-picked { outline: 3px solid #2563eb; outline-offset: -3px; }
.hs-roster-row { cursor: default; }
.hs-roster-row.roster-selectable { cursor: pointer; }
.hs-roster-row.roster-picked { background: #dbeafe; }
</style>
