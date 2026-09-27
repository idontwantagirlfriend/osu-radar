<script setup>
import OverlayRadar from "./components/OverlayRadar.vue"
import DashboardView from "./views/DashboardView.vue"
import ConfigView from "./views/ConfigView.vue"

// 路由：/ar /cs = OBS overlay；/debug = 调试面板；其余（/）= 配置页
const p = window.location.pathname.replace(/\/+$/, "") || "/"
const overlayModel = p === "/ar" ? "ar" : p === "/cs" ? "cs" : null
const debug = !overlayModel && p === "/debug"
</script>

<template>
  <OverlayRadar v-if="overlayModel" :model="overlayModel" />
  <DashboardView v-else-if="debug" />
  <ConfigView v-else />
</template>
