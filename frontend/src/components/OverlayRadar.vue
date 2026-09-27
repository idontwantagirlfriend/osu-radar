<script setup>
import { ref, onMounted, onUnmounted } from "vue"
import OffsetRadar from "./OffsetRadar.vue"
import { refreshTheme } from "../theme"

const props = defineProps({
  model: { type: String, required: true }, // 'ar' | 'cs'（来自路由 /ar /cs）
})

document.title = `osu! Radar | ${props.model === "ar" ? "AR" : "CS"} mode`

// 全部配置走 URL 传参（OBS browser source 场景：改参数 = 改 URL）
const q = new URLSearchParams(window.location.search)
const cfg = {
  since: q.get("since") || "6m",          // 时间窗，默认 6 个月
  player: q.get("player") || "",          // 玩家，默认不过滤
  mods: q.get("mods") || "",              // 只含 mods，默认不过滤
  no_mods: q.get("no_mods") || "",        // 排除 mods，默认不过滤
  min_objects: q.get("min_objects") || "10", // 最少 object 数，默认 10
  sr_range: q.get("sr_range") || "0.5",      // mod 星数过滤半径（±SR★），0 关闭
}
const themeClass = q.get("theme") === "light" ? "overlay-light" : "overlay-dark"

const profile = ref(null)
let timer = null

async function poll() {
  refreshTheme() // 主题/贴图配置改动后即时同步（配置页保存即生效）
  const p = new URLSearchParams({ model: props.model, ...cfg })
  try {
    const r = await fetch(`/api/live?${p}`)
    const d = await r.json()
    if (!r.ok) {
      profile.value = null
      return
    }
    profile.value = d.profile ? { ...d.profile, focus_cs: d.focus_cs } : null
  } catch {
    /* 静默：overlay 不展示任何状态文字 */
  }
}

onMounted(() => {
  document.body.classList.add("overlay-mode")
  poll()
  timer = setInterval(poll, 3000)
})
onUnmounted(() => {
  document.body.classList.remove("overlay-mode")
  if (timer) clearInterval(timer)
})
</script>

<template>
  <div class="overlay-stage" :class="themeClass">
    <div class="overlay-card">
      <div class="radar-wrap">
        <OffsetRadar :profile="profile" />
      </div>
    </div>
  </div>
</template>
