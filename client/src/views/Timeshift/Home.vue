<template>
    <div class="route-container">
        <HeaderBar />
        <main>
            <Navigation />
            <div class="timeshift-home-container-wrapper">
                <SPHeaderBar />
                <div class="timeshift-home-container">
                    <Breadcrumbs :crumbs="[
                        { name: 'ホーム', path: '/' },
                        { name: 'タイムシフト', path: '/timeshift/', disabled: true },
                    ]" />
                    <h2 class="timeshift-home-container__title">タイムシフト</h2>
                    <div v-if="is_loading" class="timeshift-home-container__loading">
                        <v-progress-circular indeterminate color="primary" />
                    </div>
                    <div v-else-if="recorders.length === 0" class="timeshift-home-container__empty">
                        <Icon icon="fluent:rewind-20-regular" width="54px" height="54px" />
                        <div class="timeshift-home-container__empty-message">タイムシフトレコーダーがありません。</div>
                        <div class="timeshift-home-container__empty-submessage">
                            mirakc 側でタイムシフト録画が設定されているチャンネルがある場合にのみ表示されます。
                        </div>
                    </div>
                    <div v-else class="timeshift-home-container__grid">
                        <router-link v-ripple v-for="recorder in recorders" :key="recorder.recorder_id"
                            class="timeshift-recorder-card" :to="`/timeshift/${recorder.recorder_id}`">
                            <img v-if="recorder.channel" class="timeshift-recorder-card__logo" loading="lazy" decoding="async"
                                :src="`${Utils.api_base_url}/channels/${recorder.channel.id}/logo`">
                            <Icon v-else class="timeshift-recorder-card__logo-fallback" icon="fluent:live-20-regular" width="28px" />
                            <div class="timeshift-recorder-card__content">
                                <div class="timeshift-recorder-card__title">
                                    {{recorder.channel ? `Ch: ${recorder.channel.channel_number} ${recorder.channel.name}` : recorder.recorder_id}}
                                </div>
                                <div class="timeshift-recorder-card__meta">
                                    <span v-if="recorder.is_recording" class="timeshift-recorder-card__meta-recording">
                                        <span class="timeshift-recorder-card__meta-recording-dot"></span>
                                        録画中
                                    </span>
                                    {{recorder.total_records}} 件 / 保持期間 約{{formatDurationHours(recorder.duration)}}
                                </div>
                                <div class="timeshift-recorder-card__meta-time">
                                    {{dayjs(recorder.start_time).format('MM/DD HH:mm')}} 〜 {{dayjs(recorder.end_time).format('MM/DD HH:mm')}}
                                </div>
                            </div>
                            <Icon class="timeshift-recorder-card__chevron" icon="fluent:chevron-right-20-regular" width="22px" />
                        </router-link>
                    </div>
                </div>
            </div>
        </main>
    </div>
</template>
<script lang="ts" setup>

import { onMounted, ref } from 'vue';

import Breadcrumbs from '@/components/Breadcrumbs.vue';
import HeaderBar from '@/components/HeaderBar.vue';
import Navigation from '@/components/Navigation.vue';
import SPHeaderBar from '@/components/SPHeaderBar.vue';
import Timeshift, { ITimeshiftRecorder } from '@/services/Timeshift';
import Utils, { dayjs } from '@/utils';

const recorders = ref<ITimeshiftRecorder[]>([]);
const is_loading = ref(true);

// 保持期間 (秒) を「◯時間」形式の文字列に変換する
const formatDurationHours = (duration_seconds: number): string => {
    const hours = duration_seconds / 3600;
    return hours >= 10 ? `${Math.round(hours)}時間` : `${hours.toFixed(1)}時間`;
};

onMounted(async () => {
    const result = await Timeshift.fetchTimeshiftRecorders();
    if (result) {
        recorders.value = result.timeshift_recorders;
    }
    is_loading.value = false;
});

</script>
<style lang="scss" scoped>

.timeshift-home-container-wrapper {
    display: flex;
    flex-direction: column;
    width: 100%;
    min-width: 0;
}

.timeshift-home-container {
    display: flex;
    flex-direction: column;
    width: 100%;
    height: 100%;
    padding: 20px;
    margin: 0 auto;
    min-width: 0;
    max-width: 1000px;
    @include smartphone-horizontal {
        padding: 16px 20px !important;
    }
    @include smartphone-vertical {
        padding: 8px !important;
        padding-bottom: 20px !important;
    }

    &__title {
        margin-top: 16px;
        font-size: 22px;
        font-weight: bold;
        color: rgb(var(--v-theme-text));
    }

    &__loading {
        display: flex;
        justify-content: center;
        padding: 60px 0;
    }

    &__empty {
        display: flex;
        flex-direction: column;
        align-items: center;
        padding: 60px 20px;
        color: rgb(var(--v-theme-text-darken-1));
        text-align: center;

        &-message {
            margin-top: 16px;
            font-size: 16px;
            font-weight: bold;
        }
        &-submessage {
            margin-top: 8px;
            font-size: 13px;
        }
    }

    &__grid {
        display: flex;
        flex-direction: column;
        gap: 12px;
        margin-top: 16px;
    }
}

.timeshift-recorder-card {
    display: flex;
    align-items: center;
    padding: 14px 16px;
    border-radius: 11px;
    background: rgb(var(--v-theme-background-lighten-1));
    color: rgb(var(--v-theme-text));
    text-decoration: none;
    transition: background-color 0.15s;

    &:hover {
        background: rgb(var(--v-theme-background-lighten-2));
    }

    &__logo {
        flex-shrink: 0;
        width: 58px;
        height: 34px;
        object-fit: contain;
        background: rgb(var(--v-theme-background-lighten-2));
        border-radius: 4px;
    }
    &__logo-fallback {
        flex-shrink: 0;
        width: 58px;
        height: 34px;
        display: flex;
        align-items: center;
        justify-content: center;
        color: rgb(var(--v-theme-text-darken-1));
        background: rgb(var(--v-theme-background-lighten-2));
        border-radius: 4px;
    }

    &__content {
        flex-grow: 1;
        min-width: 0;
        margin-left: 16px;
    }

    &__title {
        font-size: 16px;
        font-weight: bold;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    &__meta {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 4px;
        font-size: 13px;
        color: rgb(var(--v-theme-text-darken-1));

        &-recording {
            display: flex;
            align-items: center;
            gap: 4px;
            color: rgb(var(--v-theme-secondary-lighten-1));
            font-weight: bold;

            &-dot {
                width: 7px;
                height: 7px;
                border-radius: 50%;
                background: rgb(var(--v-theme-secondary-lighten-1));
            }
        }
    }

    &__meta-time {
        margin-top: 2px;
        font-size: 12px;
        color: rgb(var(--v-theme-text-darken-1));
    }

    &__chevron {
        flex-shrink: 0;
        margin-left: 8px;
        color: rgb(var(--v-theme-text-darken-1));
    }
}

</style>
