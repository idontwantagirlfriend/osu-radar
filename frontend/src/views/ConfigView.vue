<script setup>
import { reactive, ref, onMounted, onBeforeUnmount } from "vue"
import ColorPicker from "../components/ColorPicker.vue"
import { theme, refreshTheme, iconUrl } from "../theme"

const GRADE_ORDER = ["S", "A", "B", "C", "D"]

document.title = "osu! Radar | config"

const paths = reactive({ osu: "", tosu: "", replays: "", songs: "" })
const status = reactive({ osu: "", tosu: "", replays: "", songs: "" })
const ready = ref(false)
const loaded = ref(false) // 首次 /api/config 返回前不渲染，避免闪烁错误状态

// ---------- 路径 ----------
let saveTimer = null
function scheduleSave() {
  clearTimeout(saveTimer)
  saveTimer = setTimeout(savePaths, 800)
}

function joinPath(base, ...parts) {
  const sep = base.includes("\\") ? "\\" : "/"
  return base.replace(/[\\/]+$/, "") + sep + parts.join(sep)
}

// osu 路径一改，replay / songs 自动随之更新（之后仍可手动覆盖）
function onOsuInput() {
  if (paths.osu.trim()) {
    paths.replays = joinPath(paths.osu, "Data", "r")
    paths.songs = joinPath(paths.osu, "Songs")
  } else {
    paths.replays = ""
    paths.songs = ""
  }
  scheduleSave()
}

async function savePaths() {
  clearTimeout(saveTimer)
  try {
    const r = await fetch("/api/config/paths", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(paths),
    })
    applyConfig(await r.json())
  } catch {
    /* 静默：下一次输入会自动重试 */
  }
}

function applyConfig(d) {
  if (d.paths) {
    // 服务器可能自动补全（如经 tosu 探测到 osu! 安装位置）；不覆盖正在输入的框
    for (const k of ["osu", "tosu", "replays", "songs"]) {
      if (typeof d.paths[k] === "string" && document.activeElement?.dataset?.field !== k) {
        paths[k] = d.paths[k]
      }
    }
  }
  if (d.status) Object.assign(status, d.status)
  if (d.ready) ready.value = !!(d.ready.osu && d.ready.tosu)
}

const fieldClass = (f) => ({
  bad: status[f] === "missing" || status[f] === "unreachable",
})

// ---------- 主题色 ----------
const openGrade = ref(null)
const wrapRefs = {}
function setWrap(g, el) {
  if (el) wrapRefs[g] = el
}
function togglePicker(g) {
  openGrade.value = openGrade.value === g ? null : g
}
function onDocDown(e) {
  const g = openGrade.value
  if (!g) return
  const w = wrapRefs[g]
  if (w && !w.contains(e.target)) openGrade.value = null
}

const hslCss = (c) => `hsl(${c[0]} ${c[1]}% ${c[2]}%)`

let themeTimer = null
function setColor(g, c) {
  theme.colors[g] = c
  clearTimeout(themeTimer)
  themeTimer = setTimeout(async () => {
    try {
      await fetch("/api/theme", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ theme: theme.colors }),
      })
    } catch {
      /* 静默：下次改动会重试 */
    }
  }, 200)
}

// ---------- rank 贴图 ----------
const fileInput = ref(null)
const pendingGrade = ref(null)
function pickFile(g) {
  pendingGrade.value = g
  fileInput.value?.click()
}
async function upload(e) {
  const f = e.target.files && e.target.files[0]
  e.target.value = ""
  if (!f || !pendingGrade.value) return
  try {
    const r = await fetch(`/api/icons/${pendingGrade.value.toLowerCase()}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: f,
    })
    const d = await r.json()
    if (d.icons_version) theme.iconsVersion = String(d.icons_version)
  } catch {
    /* 静默 */
  }
}

onMounted(async () => {
  try {
    const r = await fetch("/api/config")
    applyConfig(await r.json())
  } catch {
    /* 服务不可达时保持空白表单 */
  } finally {
    loaded.value = true
  }
  refreshTheme()
  document.addEventListener("pointerdown", onDocDown, true)
})
onBeforeUnmount(() => document.removeEventListener("pointerdown", onDocDown, true))
</script>

<template>
  <div class="page cfg-page">
    <div v-if="loaded" class="cfg-grid" :class="{ init: !ready }">
      <section class="card cfg-paths">
        <div class="cfg-field">
          <label>osu!安装目录</label>
          <input
            v-model="paths.osu"
            data-field="osu"
            spellcheck="false"
            :class="fieldClass('osu')"
            @input="onOsuInput"
            @change="savePaths"
          />
        </div>
        <div class="cfg-field">
          <label>tosu程序路径</label>
          <input
            v-model="paths.tosu"
            data-field="tosu"
            spellcheck="false"
            :class="fieldClass('tosu')"
            @input="scheduleSave"
            @change="savePaths"
          />
        </div>
        <div class="cfg-field">
          <label>osu! replay目录</label>
          <input
            v-model="paths.replays"
            data-field="replays"
            spellcheck="false"
            :class="fieldClass('replays')"
            @input="scheduleSave"
            @change="savePaths"
          />
        </div>
        <div class="cfg-field">
          <label>osu! Songs目录</label>
          <input
            v-model="paths.songs"
            data-field="songs"
            spellcheck="false"
            :class="fieldClass('songs')"
            @input="scheduleSave"
            @change="savePaths"
          />
        </div>
      </section>

      <section v-if="!ready" class="card cfg-help">
        <h2>首次配置</h2>
        <ul>
          <li>
            <b>osu!安装目录</b>：osu! 安装目录（内含 <code>Data/r</code> 与 <code>Songs</code>）；
            留空则经 tosu 自动探测（需 osu! 正在运行）。
          </li>
          <li>
            <b>tosu程序路径</b>：读取 osu! 实时状态的配套程序。已运行的实例会被自动探测并回填地址，
            无需手填；留空 = 内置 runtime（<code>setup.py</code> 可自动下载）。
          </li>
        </ul>
      </section>

      <section v-if="ready" class="card cfg-guide">
        <a href="/ar/" target="_blank" rel="noopener">ar/</a>
        <a href="/cs/" target="_blank" rel="noopener">cs/</a>
      </section>

      <section v-if="ready" class="card cfg-theme">
        <div class="swatches">
          <div
            v-for="(g, i) in GRADE_ORDER"
            :key="g"
            class="swatch-wrap"
            :ref="(el) => setWrap(g, el)"
          >
            <button
              class="swatch"
              :style="{ background: hslCss(theme.colors[g]) }"
              @click="togglePicker(g)"
            >
              <span :style="{ color: theme.colors[g][2] > 62 ? 'rgba(0,0,0,.7)' : 'rgba(255,255,255,.92)' }">{{ g }}</span>
            </button>
            <div v-if="openGrade === g" class="picker-pop" :class="{ right: i >= 3 }">
              <ColorPicker
                :model-value="theme.colors[g]"
                @update:model-value="(c) => setColor(g, c)"
              />
            </div>
          </div>
        </div>
      </section>

      <section v-if="ready" class="card cfg-icons">
        <div class="icons-row">
          <button
            v-for="g in GRADE_ORDER"
            :key="g"
            class="icon-btn"
            @click="pickFile(g)"
          >
            <img :src="iconUrl(g)" :alt="g" draggable="false" />
          </button>
        </div>
        <p class="icons-note">也可以直接替换assets/中的贴图文件。</p>
        <input ref="fileInput" type="file" accept="image/*" hidden @change="upload" />
      </section>
    </div>
  </div>
</template>
