import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import RecoveryView from '@/views/RecoveryView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    // 固定路径需在动态 :moduleKey 之前，否则会被工作台路由抢先匹配
    { path: '/recovery-console', name: 'recovery-console', component: RecoveryView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
