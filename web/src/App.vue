<template>
  <el-container class="layout">
    <el-aside width="210px" class="aside">
      <div class="logo"><img src="/favicon.svg" class="logo-ico" alt="" />AI Web<span>维护台</span></div>
      <el-menu :default-active="active" router>
        <el-menu-item index="/submissions"><el-icon><List /></el-icon><span>队列 / 执行记录</span></el-menu-item>
        <el-menu-item index="/assets"><el-icon><Folder /></el-icon><span>素材库</span></el-menu-item>
        <el-menu-item index="/sites"><el-icon><Connection /></el-icon><span>站点 / 免登</span></el-menu-item>
        <el-menu-item index="/config"><el-icon><Setting /></el-icon><span>配置</span></el-menu-item>
      </el-menu>
    </el-aside>
    <el-container>
      <el-header class="header">{{ title }}</el-header>
      <el-main><router-view /></el-main>
    </el-container>
  </el-container>
</template>

<script setup>
import { computed } from "vue";
import { useRoute } from "vue-router";
const route = useRoute();
const active = computed(() => route.path);
const title = computed(() => route.meta.title || "AI Web");
</script>

<style>
body { margin: 0; }
.layout { height: 100vh; }
.aside { background: #1f2937; color: #fff; }
.logo { display: flex; align-items: center; font-size: 20px; font-weight: 700; padding: 18px 20px; color: #fff; }
.logo .logo-ico { width: 22px; height: 22px; margin-right: 8px; }
.logo span { font-size: 12px; font-weight: 400; color: #9ca3af; margin-left: 6px; }
.aside .el-menu { background: transparent; border-right: none; }
.aside .el-menu-item { color: #cbd5e1; }
.aside .el-menu-item.is-active { color: #fff; background: #374151; }
.header { display: flex; align-items: center; font-weight: 600; background: #fff; border-bottom: 1px solid #e5e7eb; }
.el-main { background: #f8fafc; }
</style>
