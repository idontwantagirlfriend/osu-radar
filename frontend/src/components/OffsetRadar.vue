<script setup>
import { ref, watch, onMounted, onBeforeUnmount } from "vue"
import { theme, grades, iconUrl, GRADE_DEFS } from "../theme"

const props = defineProps({
  profile: { type: Object, default: null },
  loading: { type: Boolean, default: false },
})

// 画布加宽：左侧留给背景字列，色环整体右移，互不重叠
const W = 780
const H = 640
const CX = 460
const CY = H / 2
const R = 280
// 最大圈 = CS1 的圆半径，从外往内 CS1/2/3/4/5
const OUTER_PX = 64 * ((1 - 0.7 * (1 - 5) / 5) / 2 * 1.00041)

// 安全等级定义在共享模块 theme.js（S=p100 … D=p60，容纳分位；颜色运行时配置）
// 背景字列自上而下 = 由宽到严（低等级在高等级上方）
// 渐变各锚点的不透明度（向外递增）
const GRAD_ALPHA = [0.10, 0.15, 0.20, 0.25, 0.30]

// 等级贴图（运行时从 /assets/ 加载，配置页替换后 iconsVersion 变化即重载）
// normalize 策略：按宽度等比缩放（just resize，不拉伸）
const IMG_W = 66  // 38 × 1.75
const gradeImgs = {}
function loadIcons() {
  for (const { g } of GRADE_DEFS) {
    const img = new Image()
    img.onload = () => draw()
    img.src = iconUrl(g)
    gradeImgs[g] = img
  }
}
loadIcons()

// 左半边背景字的行布局与命中区（onMove 与 draw 共用）；整体放大至 175%
const ROW_H = 112
const LABEL_BOX = { x0: 14, x1: 176, halfH: 54 }

const canvasRef = ref(null)
const mql = window.matchMedia("(prefers-color-scheme: dark)")
const dark = ref(false)
const hover = ref(null) // { x, y, ring }

function csRadiusPx(cs) {
  return 64 * ((1 - 0.7 * (cs - 5) / 5) / 2 * 1.00041)
}

function minCs(px) {
  return 5 - (2 * (px / (64 * 1.00041)) - 1) / 0.14
}

function percentileFromHist(hist, p) {
  const entries = Object.entries(hist).map(([k, v]) => [Number(k), Number(v)])
  entries.sort((a, b) => a[0] - b[0])
  const total = entries.reduce((s, [, c]) => s + c, 0)
  if (!total) return 0
  const target = Math.ceil((p / 100) * total)
  let cum = 0
  for (const [b, c] of entries) {
    cum += c
    if (cum >= target) return b + 0.5
  }
  return 0
}

// 背景字列显示顺序：S/A/B/C/D 自上而下
const DISPLAY = ["S", "A", "B", "C", "D"]

function ringData() {
  const hist = props.profile && props.profile.hist
  if (!hist) return null
  const rings = grades().map((r) => {
    const px = percentileFromHist(hist, r.p)
    return { ...r, px, radius: Math.min(R, (R * px) / OUTER_PX) }
  })
  rings.forEach((r) => { r.row = DISPLAY.indexOf(r.g) })
  return rings
}

function colA([h, s, l], a) {
  return `hsla(${h}, ${s}%, ${l}%, ${a})`
}

function draw() {
  const canvas = canvasRef.value
  if (!canvas) return
  const ctx = canvas.getContext("2d")
  const dpr = window.devicePixelRatio || 1
  canvas.width = W * dpr
  canvas.height = H * dpr
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, W, H)

  const css = getComputedStyle(canvas)  // 从画布继承主题变量（dashboard 根 / overlay 作用域均可）
  const textColor = css.getPropertyValue("--text").trim()
  const mutedColor = css.getPropertyValue("--muted").trim()
  const ringColor = css.getPropertyValue("--ring").trim()
  const cardColor = css.getPropertyValue("--card").trim()
  const rings = ringData()

  // ---- 焦点 CS 与预计等级（背景字点亮 / 中央贴图 / 实线圈共用） ----
  // 实时模式下两种维度都用当前图的 CS；by cs 模式用选中的 CS
  const focusCS = props.profile
    ? (props.profile.focus_cs ?? (props.profile.model === "cs" ? props.profile.value : null))
    : null
  let achieved = null
  if (focusCS != null) {
    const rr0 = (R * csRadiusPx(focusCS)) / OUTER_PX
    rings && rings.forEach((r) => {
      if (r.radius <= rr0) achieved = r
    })
  }

  // ---- 左半边：分位等级的半透明背景字（预计等级高亮"点亮"） ----
  if (rings) {
    ctx.textBaseline = "alphabetic"
    const n = rings.length
    ;[...rings].sort((a, b) => a.row - b.row).forEach((r) => {
      const y = CY + (r.row - (n - 1) / 2) * ROW_H
      const lit = achieved && achieved.g === r.g  // 预计等级：点亮
      // 色块（标识环色，文字用文本色）
      ctx.fillStyle = colA(r.hsl, lit ? 1 : 0.85)
      ctx.beginPath()
      ctx.roundRect(18, y - 38, 7, 31, 3)
      ctx.fill()
      // 等级贴图（半透明背景字；未加载完成时回退为文字）
      const img = gradeImgs[r.g]
      ctx.globalAlpha = lit ? 0.95 : 0.22
      if (img && img.complete && img.naturalWidth) {
        const h = (IMG_W * img.naturalHeight) / img.naturalWidth
        ctx.drawImage(img, 30, y - 52, IMG_W, h)
      } else {
        ctx.font = "600 47px system-ui, sans-serif"
        ctx.textAlign = "left"
        ctx.fillStyle = textColor
        ctx.fillText(r.g, 32, y - 6)
      }
      // cs 值（半透明；点亮行同样提亮）
      ctx.font = "21px system-ui, sans-serif"
      ctx.fillStyle = mutedColor
      ctx.globalAlpha = lit ? 0.9 : 0.45
      ctx.fillText(`cs${minCs(r.px).toFixed(1)} (p${r.p})`, 33, y + 42)
      ctx.globalAlpha = 1
    })
  }

  // ---- CS 刻度：中心横向往左延伸的标尺 + 垂直辅助线下引到底部标注 ----
  ctx.font = "10px system-ui, sans-serif"
  ctx.lineWidth = 1
  // 横向标尺（中心 -> 左缘 = CS1）
  ctx.globalAlpha = 0.45
  ctx.strokeStyle = ringColor
  ctx.beginPath()
  ctx.moveTo(CX, CY)
  ctx.lineTo(CX - R, CY)
  ctx.stroke()
  ctx.globalAlpha = 1
  const csTicks = [1, 2, 3, 4, 5].map((cs, i) => ({
    cs, i, x: CX - (R * csRadiusPx(cs)) / OUTER_PX,
  }))
  csTicks.forEach(({ cs, i, x }) => {
    // 轴上短刻度
    ctx.strokeStyle = ringColor
    ctx.globalAlpha = 0.8
    ctx.beginPath()
    ctx.moveTo(x, CY - 4)
    ctx.lineTo(x, CY + 4)
    ctx.stroke()
    // 垂直辅助线到图底
    ctx.globalAlpha = 0.3
    ctx.setLineDash([2, 4])
    ctx.beginPath()
    ctx.moveTo(x, CY + 5)
    ctx.lineTo(x, H - 28)
    ctx.stroke()
    ctx.setLineDash([])
    ctx.globalAlpha = 1
    // 底部标注
    ctx.fillStyle = mutedColor
    ctx.textAlign = "center"
    ctx.textBaseline = "alphabetic"
    ctx.fillText(`cs${cs}`, x, H - 10)
  })
  ctx.fillStyle = ringColor
  ctx.beginPath()
  ctx.arc(CX, CY, 2.5, 0, Math.PI * 2)
  ctx.fill()
  ctx.fillStyle = mutedColor
  ctx.textAlign = "center"
  ctx.textBaseline = "alphabetic"
  ctx.fillText("0px", CX, CY - 12)
  if (!rings) return

  // ---- 右下角：估算基数（参与统计的 replay / object 数） ----
  ctx.font = "21px system-ui, sans-serif"
  ctx.fillStyle = mutedColor
  ctx.textAlign = "right"
  ctx.textBaseline = "alphabetic"
  ctx.globalAlpha = 0.45
  ctx.fillText(`${props.profile.n_replays} replays`, W - 16, H - 36)
  ctx.fillText(`${props.profile.n_objects.toLocaleString()} objects`, W - 16, H - 10)
  ctx.globalAlpha = 1

  // ---- 色环：连续色变填充（D→S 由径向渐变连续过渡，无分段描边） ----
  const grad = ctx.createRadialGradient(CX, CY, 0, CX, CY, R)
  grad.addColorStop(0, colA(rings[0].hsl, GRAD_ALPHA[0]))
  rings.forEach((r, i) => {
    grad.addColorStop(Math.min(1, r.radius / R), colA(r.hsl, GRAD_ALPHA[i]))
  })
  grad.addColorStop(1, colA(rings[rings.length - 1].hsl, GRAD_ALPHA[GRAD_ALPHA.length - 1]))
  ctx.beginPath()
  ctx.arc(CX, CY, R, 0, Math.PI * 2)
  ctx.fillStyle = grad
  ctx.fill()

  // ---- 背景中央：预计等级的大号贴图（约 50% 画布高度，居中，幽灵化） ----
  if (achieved) {
    const img = gradeImgs[achieved.g]
    if (img && img.complete && img.naturalWidth) {
      const h0 = H * 0.5
      const w0 = (h0 * img.naturalWidth) / img.naturalHeight
      ctx.globalAlpha = 0.22
      ctx.drawImage(img, CX - w0 / 2, CY - h0 / 2, w0, h0)
      ctx.globalAlpha = 1
    }
  }

  // ---- 等级虚线环（常显；更严的等级容纳分位更高、半径在外） ----
  rings.forEach((r, i) => {
    const hovered = hover.value && hover.value.ring === i
    ctx.save()
    ctx.beginPath()
    ctx.arc(CX, CY, r.radius, 0, Math.PI * 2)
    ctx.setLineDash([4, 5])
    ctx.strokeStyle = colA(r.hsl, hovered ? 0.95 : 0.55)
    ctx.lineWidth = hovered ? 2.2 : 1.25
    ctx.stroke()
    ctx.restore()
  })

  // ---- 焦点 CS 实线圈：颜色 = 该 CS 下预计的安全等级 ----
  if (focusCS != null) {
    const rr = (R * csRadiusPx(focusCS)) / OUTER_PX
    ctx.save()
    ctx.beginPath()
    ctx.arc(CX, CY, Math.min(rr, R), 0, Math.PI * 2)
    ctx.strokeStyle = achieved ? colA(achieved.hsl, 0.95) : ringColor
    ctx.lineWidth = 2.5
    ctx.stroke()
    ctx.restore()
  }

  // ---- 悬停提示 ----
  if (hover.value) {
    const r = rings[hover.value.ring]
    const lines = [
      [`${r.g} (p${r.p})`, textColor, "600 14px system-ui, sans-serif"],
      [`${r.px.toFixed(1)} px`, textColor, "12px system-ui, sans-serif"],
      [`minCS ${minCs(r.px).toFixed(2)}`, mutedColor, "12px system-ui, sans-serif"],
    ]
    const w = Math.max(...lines.map(([t, , f]) => {
      ctx.font = f
      return ctx.measureText(t).width
    })) + 24
    const h = 58
    let x = hover.value.x + 14
    let y = hover.value.y - h / 2
    if (x + w > W - 8) x = hover.value.x - w - 14
    y = Math.min(H - 8 - h, Math.max(8, y))
    ctx.fillStyle = cardColor
    ctx.strokeStyle = ringColor
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.roundRect(x, y, w, h, 8)
    ctx.fill()
    ctx.stroke()
    ctx.textAlign = "left"
    ctx.textBaseline = "alphabetic"
    lines.forEach(([t, c, f], j) => {
      ctx.font = f
      ctx.fillStyle = c
      ctx.fillText(t, x + 12, y + 20 + j * 17)
    })
  }
}

function onMove(e) {
  const canvas = canvasRef.value
  if (!canvas || !props.profile) return
  const rect = canvas.getBoundingClientRect()
  const x = ((e.clientX - rect.left) / rect.width) * W
  const y = ((e.clientY - rect.top) / rect.height) * H
  const rings = ringData()
  if (!rings) return
  const n = rings.length

  // 1) 悬停左半边背景字 → 同样高亮对应的分位环（行号按显示序 S→D）
  const rowI = Math.round((y - CY) / ROW_H + (n - 1) / 2)
  if (rowI >= 0 && rowI < n && x >= LABEL_BOX.x0 && x <= LABEL_BOX.x1
      && Math.abs(y - (CY + (rowI - (n - 1) / 2) * ROW_H)) <= LABEL_BOX.halfH) {
    const ringIdx = rings.findIndex((r) => r.row === rowI)
    if (ringIdx >= 0) {
      hover.value = { x, y, ring: ringIdx }
      return
    }
  }

  // 2) 悬停环位置 → 最近的分位半径
  const d = Math.hypot(x - CX, y - CY)
  let best = -1
  let bestGap = 7
  rings.forEach((r, i) => {
    const gap = Math.abs(d - r.radius)
    if (gap < bestGap) {
      bestGap = gap
      best = i
    }
  })
  hover.value = best >= 0 ? { x, y, ring: best } : null
}

function onLeave() {
  hover.value = null
}

function updateDark() {
  dark.value = mql.matches
}

onMounted(() => {
  updateDark()
  mql.addEventListener("change", updateDark)
  draw()
})
onBeforeUnmount(() => mql.removeEventListener("change", updateDark))

watch([() => props.profile, dark, hover], draw)
watch(() => theme.colors, draw, { deep: true })
watch(() => theme.iconsVersion, loadIcons)
</script>

<template>
  <canvas
    ref="canvasRef"
    @mousemove="onMove"
    @mouseleave="onLeave"
  ></canvas>
</template>
