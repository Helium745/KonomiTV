<template>
    <div class="route-container">
        <HeaderBar />
        <main>
            <Navigation />
            <div class="timeshift-records-container-wrapper">
                <SPHeaderBar />
                <div class="timeshift-records-container">
                    <Breadcrumbs :crumbs="[
                        { name: 'ホーム', path: '/' },
                        { name: 'タイムシフト', path: '/timeshift/' },
                        { name: recorder_title, path: `/timeshift/${recorder_id}`, disabled: true },
                    ]" />
                    <h2 class="timeshift-records-container__title">{{recorder_title}}</h2>
                    <div v-if="is_loading" class="timeshift-records-container__loading">
                        <v-progress-circular indeterminate color="primary" />
                    </div>
                    <div v-else-if="records.length === 0" class="timeshift-records-container__empty">
                        <Icon icon="fluent:rewind-20-regular" width="54px" height="54px" />
                        <div class="timeshift-records-container__empty-message">まだタイムシフト録画がありません。</div>
                    </div>
                    <div v-else class="timeshift-records-container__grid">
                        <router-link v-ripple v-for="record in records" :key="record.id"
                            class="timeshift-record-card" :to="`/timeshift/watch/${recorder_id}/${record.id}`">
                            <div class="timeshift-record-card__content">
                                <div class="timeshift-record-card__title">{{record.title}}</div>
                                <div class="timeshift-record-card__meta">
                                    <span v-if="record.is_recording" class="timeshift-record-card__meta-recording">
                                        <span class="timeshift-record-card__meta-recording-dot"></span>
                                        録画中
                                    </span>
                                    {{dayjs(record.start_time).format('MM/DD (dd) HH:mm')}}
                                    〜 {{dayjs(record.end_time).format('HH:mm')}}
                                    ({{formatDuration(record.duration)}})
                                </div>
                                <div class="timeshift-record-card__description">{{record.description}}</div>
                            </div>
                            <div class="timeshift-record-card__size">{{Utils.formatBytes(record.file_size)}}</div>
                        </router-link>
                    </div>
                </div>
            </div>
        </main>
    </div>
</template>
<script lang="ts" setup>

import { computed, onMounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import Breadcrumbs from '@/components/Breadcrumbs.vue';
import HeaderBar from '@/components/HeaderBar.vue';
import Navigation from '@/components/Navigation.vue';
import SPHeaderBar from '@/components/SPHeaderBar.vue';
import Timeshift, { ITimeshiftRecord } from '@/services/Timeshift';
import Utils, { dayjs } from '@/utils';

const route = useRoute();
const recorder_id = computed(() => route.params.recorder_id as string);

const records = ref<ITimeshiftRecord[]>([]);
const recorder_title = ref('タイムシフト');
const is_loading = ref(true);

// 秒数を「1:23:45」または「12:34」形式の文字列に変換する
const formatDuration = (duration_seconds: number): string => {
    const hours = Math.floor(duration_seconds / 3600);
    const minutes = Math.floor((duration_seconds % 3600) / 60);
    const seconds = Math.floor(duration_seconds % 60);
    if (hours > 0) {
        return `${hours}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    }
    return `${minutes}:${seconds.toString().padStart(2, '0')}`;
};

const fetchRecords = async () => {
    is_loading.value = true;
    const result = await Timeshift.fetchTimeshiftRecords(recorder_id.value);
    if (result) {
        // 開始時刻が新しい順に表示する
        records.value = result.timeshift_records.slice().sort(
            (a, b) => dayjs(b.start_time).valueOf() - dayjs(a.start_time).valueOf()
        );
        if (records.value.length > 0 && records.value[0].channel) {
            const channel = records.value[0].channel;
            recorder_title.value = `Ch: ${channel.channel_number} ${channel.name}`;
        } else {
            recorder_title.value = recorder_id.value;
        }
    }
    is_loading.value = false;
};

onMounted(fetchRecords);

</script>
<style lang="scss" scoped>

.timeshift-records-container-wrapper {
    display: flex;
    flex-direction: column;
    width: 100%;
    min-width: 0;
}

.timeshift-records-container {
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
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
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
    }

    &__grid {
        display: flex;
        flex-direction: column;
        gap: 10px;
        margin-top: 16px;
    }
}

.timeshift-record-card {
    display: flex;
    align-items: center;
    padding: 12px 16px;
    border-radius: 11px;
    background: rgb(var(--v-theme-background-lighten-1));
    color: rgb(var(--v-theme-text));
    text-decoration: none;
    transition: background-color 0.15s;

    &:hover {
        background: rgb(var(--v-theme-background-lighten-2));
    }

    &__content {
        flex-grow: 1;
        min-width: 0;
    }

    &__title {
        font-size: 15px;
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
        font-size: 12.5px;
        color: rgb(var(--v-theme-text-darken-1));

        &-recording {
            display: flex;
            align-items: center;
            gap: 4px;
            color: rgb(var(--v-theme-secondary-lighten-1));
            font-weight: bold;

            &-dot {
                width: 6px;
                height: 6px;
                border-radius: 50%;
                background: rgb(var(--v-theme-secondary-lighten-1));
            }
        }
    }

    &__description {
        margin-top: 4px;
        font-size: 12.5px;
        color: rgb(var(--v-theme-text-darken-1));
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    &__size {
        flex-shrink: 0;
        margin-left: 16px;
        font-size: 12.5px;
        color: rgb(var(--v-theme-text-darken-1));
    }
}

</style>
