// 安全等级定义与运行时主题（颜色 / rank 贴图版本）—— 雷达图 / overlay / 配置页共用
// 颜色与贴图由配置页（/）修改后经 /api/theme 立即生效；overlay 每 3s 轮询同步
import { reactive } from "vue"

export const GRADE_DEFS = [
  { g: "D", p: 60 },
  { g: "C", p: 70 },
  { g: "B", p: 80 },
  { g: "A", p: 90 },
  { g: "S", p: 100 },
]

// 与 server.py 的 DEFAULT_THEME 保持一致（接口不可达时的回退）
export const DEFAULT_COLORS = {
  D: [347, 75, 60],
  C: [272, 60, 62],
  B: [214, 75, 60],
  A: [121, 70, 45],
  S: [56, 85, 55],
}

export const theme = reactive({
  colors: Object.fromEntries(Object.entries(DEFAULT_COLORS).map(([g, c]) => [g, [...c]])),
  iconsVersion: "",
})

// 当前生效的等级表（含 hsl 色），OffsetRadar 每帧读取
export const grades = () =>
  GRADE_DEFS.map((d) => ({ ...d, hsl: theme.colors[d.g] || DEFAULT_COLORS[d.g] }))

export const iconUrl = (g) =>
  `/assets/ranking-${g.toLowerCase()}-small@2x.png?v=${theme.iconsVersion}`

export async function refreshTheme() {
  try {
    const r = await fetch("/api/theme")
    if (!r.ok) return
    const d = await r.json()
    if (d.theme) {
      for (const g of Object.keys(theme.colors)) {
        if (Array.isArray(d.theme[g])) theme.colors[g] = d.theme[g]
      }
    }
    if (d.icons_version != null) theme.iconsVersion = String(d.icons_version)
  } catch {
    /* 静默：接口不可达时沿用当前主题 */
  }
}
