<script setup lang="ts">
import type { RouteLocationRaw } from 'vue-router';
import { NButton } from 'naive-ui';

export interface NextStepAction {
  label: string;
  to?: RouteLocationRaw;
  href?: string;
  download?: boolean;
  primary?: boolean;
  onClick?: () => void;
}

defineProps<{
  title: string;
  summary?: string;
  tone?: 'success' | 'info' | 'warning';
  steps: NextStepAction[];
  dismissible?: boolean;
}>();

const emit = defineEmits<{ dismiss: [] }>();
</script>

<template>
  <section class="next-step-banner" :class="'tone-' + (tone || 'success')" role="region" :aria-label="title">
    <div class="next-step-head">
      <div class="next-step-title-row">
        <span class="next-step-dot" aria-hidden="true">
          <svg v-if="(tone || 'success') === 'success'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="20 6 9 17 4 12" />
          </svg>
          <svg v-else-if="tone === 'warning'" width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <path d="M10.29 3.86 1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" /><line x1="12" y1="9" x2="12" y2="13" /><line x1="12" y1="17" x2="12.01" y2="17" />
          </svg>
          <svg v-else width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">
            <circle cx="12" cy="12" r="10" /><line x1="12" y1="16" x2="12" y2="12" /><line x1="12" y1="8" x2="12.01" y2="8" />
          </svg>
        </span>
        <strong>{{ title }}</strong>
      </div>
      <button v-if="dismissible" type="button" class="next-step-close" aria-label="关闭引导" @click="emit('dismiss')">×</button>
    </div>
    <p v-if="summary" class="next-step-summary">{{ summary }}</p>
    <div v-if="steps.length" class="next-step-actions">
      <template v-for="(step, idx) in steps" :key="idx + ':' + step.label">
        <RouterLink
          v-if="step.to"
          :to="step.to"
          class="next-step-link"
          :class="{ 'is-primary': step.primary }"
        >
          {{ step.label }}
        </RouterLink>
        <a
          v-else-if="step.href"
          :href="step.href"
          :download="step.download ? '' : undefined"
          class="next-step-link"
          :class="{ 'is-primary': step.primary }"
        >
          {{ step.label }}
        </a>
        <NButton
          v-else
          :type="step.primary ? 'primary' : 'default'"
          :secondary="!step.primary"
          size="medium"
          @click="step.onClick?.()"
        >
          {{ step.label }}
        </NButton>
      </template>
    </div>
  </section>
</template>

<style scoped>
.next-step-banner {
  margin: 16px 0;
  padding: 16px 20px;
  border-radius: var(--radius-lg);
  border: 1px solid var(--success-border);
  background: var(--success-bg);
  box-shadow: var(--shadow-card);
}
.next-step-banner.tone-info {
  border-color: var(--brand-border);
  background: var(--brand-soft);
}
.next-step-banner.tone-warning {
  border-color: var(--warning-border);
  background: var(--warning-bg);
}
.next-step-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
}
.next-step-title-row {
  display: flex;
  align-items: center;
  gap: 10px;
  font-size: 15.5px;
  color: var(--text-primary);
}
.next-step-dot {
  width: 26px;
  height: 26px;
  border-radius: 999px;
  display: grid;
  place-items: center;
  flex-shrink: 0;
  background: var(--success-fg);
  color: #ffffff;
}
.tone-info .next-step-dot { background: var(--brand); }
.tone-warning .next-step-dot { background: var(--warning-fg); }

.next-step-close {
  border: none;
  background: transparent;
  color: var(--text-muted);
  font-size: 20px;
  line-height: 1;
  cursor: pointer;
  padding: 2px 6px;
  border-radius: var(--radius-sm);
}
.next-step-close:hover { background: rgba(15, 23, 42, 0.06); color: var(--text-primary); }

.next-step-summary {
  margin: 8px 0 0 36px;
  color: var(--text-secondary);
  font-size: 14px;
  line-height: 1.55;
}
.next-step-actions {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 10px;
  margin: 14px 0 0 36px;
}
.next-step-link {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  min-height: 36px;
  padding: 6px 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border-strong);
  background: var(--bg-surface);
  color: var(--text-primary);
  font-size: 14px;
  font-weight: 600;
  transition: all 0.15s ease;
}
.next-step-link:hover {
  text-decoration: none;
  border-color: var(--brand);
  color: var(--brand);
  background: var(--brand-soft);
}
.next-step-link.is-primary {
  background: var(--brand);
  border-color: var(--brand);
  color: #ffffff;
}
.next-step-link.is-primary:hover {
  background: var(--brand-hover);
  border-color: var(--brand-hover);
  color: #ffffff;
}
</style>
