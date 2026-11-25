import { createApp } from 'vue';
import { createPinia } from 'pinia';
import { createRouter, createWebHashHistory } from 'vue-router';
import App from './App.vue';
import Page from './Page.vue';
import './style.css';

const router = createRouter({
  history: createWebHashHistory(),
  routes: ['/', '/new', '/projects/:project?', '/worksheets/:sheet', '/import', '/specifications', '/catalog', '/scans', '/trends', '/deliveries']
    .map(path => ({ path, component: Page })),
});
createApp(App).use(createPinia()).use(router).mount('#app');
