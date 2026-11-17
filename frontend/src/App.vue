<script setup lang="ts">
</script>

<template>
  <router-view v-slot="{ Component }">
    <keep-alive include="WorkbenchView">
      <component :is="Component" />
    </keep-alive>
  </router-view>
</template>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
}
</style>