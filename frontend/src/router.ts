import { createRouter, createWebHistory } from 'vue-router'
import { getToken } from './api'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    {
      path: '/login',
      name: 'login',
      component: () => import('./views/LoginView.vue'),
      meta: { public: true },
    },
    {
      path: '/work',
      name: 'work',
      component: () => import('./views/WorkbenchView.vue'),
    },
    {
      path: '/center',
      name: 'center',
      component: () => import('./views/CenterView.vue'),
    },
    {
      path: '/',
      redirect: '/work',
    },
    {
      path: '/:pathMatch(.*)*',
      redirect: '/work',
    },
  ],
})

router.beforeEach((to) => {
  if (!to.meta.public && !getToken()) {
    return { name: 'login' }
  }
  if (to.name === 'login' && getToken()) {
    return { name: 'work' }
  }
})

export default router