import { createRouter, createWebHistory } from 'vue-router'

import EvidenceView from '@/views/EvidenceView.vue'
import Overview from '@/views/Overview.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/rule_evidence', name: 'rule_evidence', component: EvidenceView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
