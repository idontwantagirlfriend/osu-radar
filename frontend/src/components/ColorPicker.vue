<script setup>
// 调色板：左侧 SV 色谱区 + 色相条，右侧 R/G/B 与 H/S/B 输入，下方一行六位色号
// 任何改动立即 emit（父级即存即用）；点击调色板以外区域由父级负责关闭
import { reactive, computed, watch } from "vue"

const props = defineProps({
  modelValue: { type: Array, required: true }, // [h, s, l]（HSL，与主题存储一致）
})
const emit = defineEmits(["update:modelValue"])

// 内部以 HSV 维护（色谱区与 H/S/B fields 都是 HSV 语义）
const st = reactive({ h: 0, s: 0, v: 100 })
let syncing = false

const rnd = Math.round
const clamp = (x, lo, hi) => Math.min(hi, Math.max(lo, x))

// ---------- 色彩空间换算 ----------
function hsl2hsv(h, s, l) {
  s /= 100; l /= 100
  const v = l + s * Math.min(l, 1 - l)
  const sv = v === 0 ? 0 : 2 * (1 - l / v)
  return [h, sv * 100, v * 100]
}
function hsv2hsl(h, s, v) {
  s /= 100; v /= 100
  const l = v * (1 - s / 2)
  const sl = l === 0 || l === 1 ? 0 : (v - l) / Math.min(l, 1 - l)
  return [rnd(h), rnd(sl * 100), rnd(l * 100)]
}
function hsv2rgb(h, s, v) {
  s /= 100; v /= 100
  const c = v * s
  const x = c * (1 - Math.abs(((h / 60) % 2) - 1))
  const m = v - c
  let r, g, b
  if (h < 60) [r, g, b] = [c, x, 0]
  else if (h < 120) [r, g, b] = [x, c, 0]
  else if (h < 180) [r, g, b] = [0, c, x]
  else if (h < 240) [r, g, b] = [0, x, c]
  else if (h < 300) [r, g, b] = [x, 0, c]
  else [r, g, b] = [c, 0, x]
  return [rnd((r + m) * 255), rnd((g + m) * 255), rnd((b + m) * 255)]
}
function rgb2hsv(r, g, b) {
  r /= 255; g /= 255; b /= 255
  const mx = Math.max(r, g, b), mn = Math.min(r, g, b), d = mx - mn
  let h = 0
  if (d) {
    if (mx === r) h = 60 * (((g - b) / d + 6) % 6)
    else if (mx === g) h = 60 * ((b - r) / d + 2)
    else h = 60 * ((r - g) / d + 4)
  }
  return [h, mx === 0 ? 0 : (d / mx) * 100, mx * 100]
}

const rgb = computed(() => {
  const [r, g, b] = hsv2rgb(st.h, st.s, st.v)
  return { r, g, b }
})
const hex = computed(() =>
  [rgb.value.r, rgb.value.g, rgb.value.b]
    .map((x) => x.toString(16).padStart(2, "0"))
    .join("")
    .toUpperCase()
)
const svStyle = computed(() => ({
  background:
    `linear-gradient(to top, #000, rgba(0,0,0,0)),` +
    `linear-gradient(to right, #fff, rgba(255,255,255,0)),` +
    `hsl(${st.h}, 100%, 50%)`,
}))

watch(
  () => props.modelValue,
  (val) => {
    if (!val) return
    syncing = true
    const [h, s, v] = hsl2hsv(val[0], val[1], val[2])
    st.h = h
    st.s = s
    st.v = v
    syncing = false
  },
  { immediate: true, deep: true }
)

function commit() {
  if (syncing) return
  emit("update:modelValue", hsv2hsl(st.h, st.s, st.v))
}

// ---------- 数字输入 ----------
function setRgb(key, raw) {
  const c = { ...rgb.value, [key]: clamp(parseInt(raw, 10) || 0, 0, 255) }
  ;[st.h, st.s, st.v] = rgb2hsv(c.r, c.g, c.b)
  commit()
}
function setHsv(key, raw) {
  st[key] = clamp(parseFloat(raw) || 0, 0, key === "h" ? 360 : 100)
  commit()
}
function setHex(e) {
  let t = e.target.value.trim().replace(/^#/, "")
  if (/^[0-9a-fA-F]{3}$/.test(t)) t = t.split("").map((c) => c + c).join("")
  if (!/^[0-9a-fA-F]{6}$/.test(t)) return
  const r = parseInt(t.slice(0, 2), 16)
  const g = parseInt(t.slice(2, 4), 16)
  const b = parseInt(t.slice(4, 6), 16)
  ;[st.h, st.s, st.v] = rgb2hsv(r, g, b)
  commit()
}

// ---------- 色谱区 / 色相条拖拽 ----------
function drag(e, fn) {
  fn(e)
  const move = (ev) => fn(ev)
  const up = () => {
    window.removeEventListener("pointermove", move)
    window.removeEventListener("pointerup", up)
  }
  window.addEventListener("pointermove", move)
  window.addEventListener("pointerup", up)
}
function svDown(e) {
  const el = e.currentTarget
  drag(e, (ev) => {
    const r = el.getBoundingClientRect()
    st.s = clamp(((ev.clientX - r.left) / r.width) * 100, 0, 100)
    st.v = clamp((1 - (ev.clientY - r.top) / r.height) * 100, 0, 100)
    commit()
  })
}
function hueDown(e) {
  const el = e.currentTarget
  drag(e, (ev) => {
    const r = el.getBoundingClientRect()
    st.h = clamp(((ev.clientX - r.left) / r.width) * 360, 0, 360)
    commit()
  })
}
</script>

<template>
  <div class="picker">
    <div class="picker-main">
      <div class="sv-pad" :style="svStyle" @pointerdown="svDown">
        <div class="sv-cursor" :style="{ left: st.s + '%', top: 100 - st.v + '%' }"></div>
      </div>
      <div class="hue-bar" @pointerdown="hueDown">
        <div class="hue-cursor" :style="{ left: (st.h / 360) * 100 + '%' }"></div>
      </div>
    </div>
    <div class="picker-fields">
      <div class="field-col">
        <label>R <input :value="rgb.r" inputmode="numeric" @input="setRgb('r', $event.target.value)" /></label>
        <label>G <input :value="rgb.g" inputmode="numeric" @input="setRgb('g', $event.target.value)" /></label>
        <label>B <input :value="rgb.b" inputmode="numeric" @input="setRgb('b', $event.target.value)" /></label>
      </div>
      <div class="field-col">
        <label>H <input :value="rnd(st.h)" inputmode="numeric" @input="setHsv('h', $event.target.value)" /></label>
        <label>S <input :value="rnd(st.s)" inputmode="numeric" @input="setHsv('s', $event.target.value)" /></label>
        <label>B <input :value="rnd(st.v)" inputmode="numeric" @input="setHsv('v', $event.target.value)" /></label>
      </div>
      <label class="hex-row"># <input :value="hex" maxlength="7" spellcheck="false" @input="setHex" /></label>
    </div>
  </div>
</template>
