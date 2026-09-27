<script setup>
document.title = "osu! Radar | debug"
import { ref, computed, watch, onMounted, onUnmounted } from "vue"
import OffsetRadar from "../components/OffsetRadar.vue"
import { refreshTheme } from "../theme"

const BUCKET = 0.5
const P_LIST = [50, 75, 90, 95, 99, 99.9]

const model = ref("ar")
const buckets = ref([])
const value = ref(null)

const player = ref("")
const mods = ref("")
const noMods = ref("")
const minObjects = ref(0)
const since = ref("6m")

const profile = ref(null)
const loading = ref(false)
const error = ref("")

// 实时模式：跟随 osu! 当前图（tosu），按维度自动取图 AR/CS 并预测安全等级
const live = ref(false)
const liveInfo = ref(null)
let liveTimer = null

async function pollLive() {
  if (!live.value) return
  refreshTheme() // 实时轮询顺带同步主题/贴图配置
  const my = ++seq
  const p = new URLSearchParams({
    model: model.value,
    bucket: String(BUCKET),
    player: player.value,
    mods: mods.value,
    no_mods: noMods.value,
    min_objects: String(minObjects.value),
    since: since.value,
  })
  try {
    const r = await fetch(`/api/live?${p}`)
    const d = await r.json()
    if (my !== seq) return
    if (!r.ok) {
      liveInfo.value = null
      profile.value = null
      error.value = d.error || `实时数据不可用 (${r.status})`
      return
    }
    error.value = ""
    liveInfo.value = d
    if (d.profile) {
      profile.value = { ...d.profile, focus_cs: d.focus_cs }
      value.value = d.focus_value
    } else {
      profile.value = null
    }
  } catch {
    if (my !== seq) return
    error.value = "实时请求失败"
  }
}

function setLive(on) {
  if (liveTimer) { clearInterval(liveTimer); liveTimer = null }
  if (on) {
    liveInfo.value = null
    pollLive()
    liveTimer = setInterval(pollLive, 3000)
  }
}

watch(live, setLive)
onUnmounted(() => setLive(false))

const bucketLabel = {
  ar: "等效 AR",
  cs: "等效 CS",
}

const sinceOptions = [
  { value: "1m", label: "1 个月" },
  { value: "3m", label: "3 个月" },
  { value: "6m", label: "6 个月" },
  { value: "1y", label: "1 年" },
  { value: "2y", label: "2 年" },
  { value: "all", label: "全部" },
]

const sliderRange = computed(() => {
  if (!buckets.value.length) return { min: 0, max: 10 }
  let min = Math.round(Math.min(...buckets.value.map((b) => b.value)) * 10) / 10
  let max = Math.round(Math.max(...buckets.value.map((b) => b.value)) * 10) / 10
  if (max - min < 0.5) {
    const mid = (min + max) / 2
    min = Math.round((mid - 0.25) * 10) / 10
    max = Math.round((mid + 0.25) * 10) / 10
  }
  return { min, max }
})

const KEY_POINTS = [0, 5, 9, 10]

const ticks = computed(() => {
  const { min, max } = sliderRange.value
  const out = []
  for (let i = Math.ceil(min * 2); i <= Math.floor(max * 2 + 1e-9); i++) {
    out.push({ v: i / 2, major: i % 2 === 0, key: i % 2 === 0 && KEY_POINTS.includes(i / 2) })
  }
  return out
})

const keyMarks = computed(() => {
  const { min, max } = sliderRange.value
  if (max === min) return []
  return KEY_POINTS.filter((k) => k >= min && k <= max).map((k) => ({
    v: k,
    frac: (k - min) / (max - min),
  }))
})

const windowText = computed(() => {
  if (value.value == null) return ""
  const lo = value.value - BUCKET / 2
  const hi = value.value + BUCKET / 2
  return `${lo.toFixed(2)}–${hi.toFixed(2)}`
})

async function loadBuckets() {
  const p = new URLSearchParams({ model: model.value, bucket: String(BUCKET), since: since.value })
  const r = await fetch(`/api/buckets?${p}`)
  const data = await r.json()
  buckets.value = Array.isArray(data) ? data : []
  if (!buckets.value.some((b) => b.value === value.value)) {
    const best = buckets.value.length
      ? buckets.value.reduce((a, b) => (b.n_objects > a.n_objects ? b : a))
      : null
    value.value = best ? best.value : null
  }
}

const THROTTLE_MS = 200 // 5 Hz
let seq = 0
let lastRun = 0
let trailTimer = null

function requestProfile() {
  const now = Date.now()
  const elapsed = now - lastRun
  if (elapsed >= THROTTLE_MS) {
    lastRun = now
    loadProfile()
  } else if (trailTimer == null) {
    trailTimer = setTimeout(() => {
      trailTimer = null
      lastRun = Date.now()
      loadProfile()
    }, THROTTLE_MS - elapsed)
  }
}

async function loadProfile() {
  if (live.value || value.value == null) return
  const my = ++seq
  loading.value = true
  error.value = ""
  const p = new URLSearchParams({
    model: model.value,
    value: String(value.value),
    bucket: String(BUCKET),
    player: player.value,
    mods: mods.value,
    no_mods: noMods.value,
    min_objects: String(minObjects.value),
    since: since.value,
  })
  try {
    const r = await fetch(`/api/profile?${p}`)
    if (my !== seq) return
    if (!r.ok) {
      profile.value = null
      error.value = r.status === 404 ? "该查询无数据" : `请求失败 (${r.status})`
    } else {
      profile.value = await r.json()
    }
  } catch {
    if (my !== seq) return
    profile.value = null
    error.value = "请求失败"
  } finally {
    if (my === seq) loading.value = false
  }
}

watch(model, async () => {
  value.value = null
  await loadBuckets()
})
watch(since, async () => {
  const prev = value.value
  await loadBuckets()
  if (value.value === prev) requestProfile()
})
watch(value, requestProfile)
watch([player, mods, noMods, minObjects], loadProfile)

onMounted(() => {
  loadBuckets()
  refreshTheme()
})
</script>

<template>
  <div class="page">
    <header>
      <h1>osu! Radar | debug</h1>
      <p class="sub">
        命中时刻光标偏移分布 — 按等效 {{ bucketLabel[model] }} 分桶加权（p50–p99.9，单位 osu!pixel）
      </p>
    </header>

    <section class="controls card">
      <div class="field">
        <label>维度</label>
        <div class="seg">
          <button :class="{ active: model === 'ar' }" @click="model = 'ar'">AR</button>
          <button :class="{ active: model === 'cs' }" @click="model = 'cs'">CS</button>
        </div>
      </div>
      <div class="field">
        <label>实时</label>
        <div class="seg">
          <button :class="{ active: live }" @click="live = true">跟随 osu!</button>
          <button :class="{ active: !live }" @click="live = false">手动</button>
        </div>
      </div>
      <div class="field field-range">
        <label>{{ bucketLabel[model] }} 值：<b>{{ value != null ? value.toFixed(1) : "—" }}</b></label>
        <input
          type="range"
          v-model.number="value"
          :min="sliderRange.min"
          :max="sliderRange.max"
          step="0.1"
          :disabled="live"
        />
        <div class="ticks">
          <span v-for="t in ticks" :key="t.v" :class="{ major: t.major, key: t.key }"></span>
        </div>
        <div class="key-marks">
          <span
            v-for="m in keyMarks"
            :key="m.v"
            :style="{ left: `calc(${m.frac} * (100% - 16px) + 8px)` }"
          >{{ m.v }}</span>
        </div>
        <div class="range-labs">
          <span>{{ sliderRange.min.toFixed(1) }}</span>
          <span>窗口 {{ windowText }}</span>
          <span>{{ sliderRange.max.toFixed(1) }}</span>
        </div>
      </div>
      <div class="field">
        <label>时间窗</label>
        <select v-model="since">
          <option v-for="o in sinceOptions" :key="o.value" :value="o.value">{{ o.label }}</option>
        </select>
      </div>
      <div class="field">
        <label>玩家（模糊）</label>
        <input v-model="player" placeholder="如 Tempera" />
      </div>
      <div class="field">
        <label>只含 mods（逗号分隔）</label>
        <input v-model="mods" placeholder="如 DT,HD" />
      </div>
      <div class="field">
        <label>排除 mods（逗号分隔）</label>
        <input v-model="noMods" placeholder="如 EZ" />
      </div>
      <div class="field">
        <label>最少 object 数</label>
        <input v-model.number="minObjects" type="number" min="0" />
      </div>
    </section>

    <div class="grid">
      <section class="card radar-card">
        <div class="radar-wrap">
          <OffsetRadar :profile="profile" :loading="loading" />
        </div>
      </section>

      <section class="card">
        <template v-if="profile">
          <div v-if="liveInfo && liveInfo.map" class="live-info">
            <span class="map">{{ liveInfo.map.title }} [{{ liveInfo.map.difficulty }}]</span>
            <span class="mods">{{ liveInfo.mods.join('+') || 'nomod' }}</span>
            <span v-if="liveInfo.predicted_grade" class="grade">
              预计 {{ liveInfo.predicted_grade }}
            </span>
          </div>
          <div class="stats">
            <span><b>{{ profile.n_replays }}</b> replays</span>
            <span><b>{{ profile.n_objects.toLocaleString() }}</b> objects</span>
            <span>查询窗口 {{ windowText }}</span>
          </div>
          <table>
            <thead>
              <tr>
                <th>分位点</th>
                <th>偏移 (px)</th>
                <th>minCS</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="p in P_LIST" :key="p">
                <td>p{{ p }}</td>
                <td>{{ profile.percentiles[p].toFixed(1) }}</td>
                <td class="mincs">
                  {{ profile.min_cs[p] > -50 && profile.min_cs[p] < 50 ? profile.min_cs[p].toFixed(2) : "n/a" }}
                </td>
              </tr>
            </tbody>
          </table>
          <div class="hint">
            minCS：该 CS 的圆恰好能罩住此等级的偏移（p99 的 minCS 即“此等级 aim 失误所需的最小圈”，负值 = 超出 CS0，任何圆都罩不住）。
          </div>
        </template>
        <div v-else-if="loading" class="empty"><span class="spin"></span> 加载中…</div>
        <div v-else-if="error" class="empty">{{ error }}</div>
        <div v-else class="empty">选择一个分桶查看分布</div>
      </section>
    </div>
  </div>
</template>
