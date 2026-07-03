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
                    <div class="timeshift-records-container__header">
                        <h2 class="timeshift-records-container__title">{{recorder_title}}</h2>
                        <v-btn v-if="buffer_start_time !== null && buffer_end_time !== null" variant="tonal" color="secondary"
                            size="small" @click="is_cutout_dialog_open = true">
                            <Icon icon="fluent:cut-20-regular" width="16px" height="16px" />
                            <span class="ml-1">時間を指定して切り出す</span>
                        </v-btn>
                    </div>
                    <div v-if="active_save_jobs.length > 0" class="timeshift-records-container__save-jobs">
                        <div v-for="job in active_save_jobs" :key="job.id" class="timeshift-save-job">
                            <Icon icon="fluent:save-20-regular" width="16px" height="16px" />
                            <span class="timeshift-save-job__title">{{job.title}} を保存中…</span>
                            <span class="timeshift-save-job__progress">{{Math.round(job.progress * 100)}}%</span>
                        </div>
                    </div>
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
                            <button type="button" class="timeshift-record-card__save-button"
                                v-ftooltip="'この番組を保存'" @click.stop.prevent="openSaveDialog(record)">
                                <Icon icon="fluent:save-20-regular" width="19px" height="19px" />
                            </button>
                        </router-link>
                    </div>
                </div>
            </div>
        </main>
        <SaveDialog v-if="save_dialog_record !== null" v-model="is_save_dialog_open"
            :record="save_dialog_record" @saved="onSaved" />
        <CutOutDialog v-if="buffer_start_time !== null && buffer_end_time !== null" v-model="is_cutout_dialog_open"
            :recorder-id="recorder_id" :recorder-title="recorder_title"
            :buffer-start-time="buffer_start_time" :buffer-end-time="buffer_end_time" @saved="onSaved" />
    </div>
</template>
<script lang="ts" setup>

import { computed, onMounted, onUnmounted, ref } from 'vue';
import { useRoute } from 'vue-router';

import Breadcrumbs from '@/components/Breadcrumbs.vue';
import HeaderBar from '@/components/HeaderBar.vue';
import Navigation from '@/components/Navigation.vue';
import SPHeaderBar from '@/components/SPHeaderBar.vue';
import CutOutDialog from '@/components/Timeshift/CutOutDialog.vue';
import SaveDialog from '@/components/Timeshift/SaveDialog.vue';
import Timeshift, { ITimeshiftRecord, ITimeshiftSaveJob } from '@/services/Timeshift';
import Utils, { dayjs } from '@/utils';

const route = useRoute();
const recorder_id = computed(() => route.params.recorder_id as string);

const records = ref<ITimeshiftRecord[]>([]);
const recorder_title = ref('タイムシフト');
const is_loading = ref(true);

// 保存ダイアログの表示状態と対象 record
const is_save_dialog_open = ref(false);
const save_dialog_record = ref<ITimeshiftRecord | null>(null);

// 切り出しダイアログの表示状態
const is_cutout_dialog_open = ref(false);

// レコーダーのリングバッファ全体の開始/終了時刻 (切り出しダイアログで選択できる範囲の境界として使う)
// records は個々の番組(record) の集合であり、レコーダー自体の情報 API を別途呼ばずに済むよう、ここから算出する
const buffer_start_time = computed<string | null>(() => {
    if (records.value.length === 0) {
        return null;
    }
    return records.value.reduce((earliest, record) => (
        dayjs(record.start_time).isBefore(dayjs(earliest)) ? record.start_time : earliest
    ), records.value[0].start_time);
});
const buffer_end_time = computed<string | null>(() => {
    if (records.value.length === 0) {
        return null;
    }
    return records.value.reduce((latest, record) => (
        dayjs(record.end_time).isAfter(dayjs(latest)) ? record.end_time : latest
    ), records.value[0].end_time);
});

// タイムシフト保存ジョブ一覧 (進行中のもののみ画面上部に表示する)
const save_jobs = ref<ITimeshiftSaveJob[]>([]);
const active_save_jobs = computed(() => save_jobs.value.filter(job => job.status === 'Pending' || job.status === 'Running'));
let save_jobs_poll_timer: ReturnType<typeof setInterval> | null = null;

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

// 保存ダイアログを開く
const openSaveDialog = (record: ITimeshiftRecord) => {
    save_dialog_record.value = record;
    is_save_dialog_open.value = true;
};

// 保存ジョブ一覧を取得し、進行中のジョブがあればポーリングを継続する
const fetchSaveJobs = async () => {
    const result = await Timeshift.fetchTimeshiftSaveJobs();
    if (result) {
        save_jobs.value = result.save_jobs;
    }
    if (active_save_jobs.value.length === 0 && save_jobs_poll_timer !== null) {
        clearInterval(save_jobs_poll_timer);
        save_jobs_poll_timer = null;
    }
};

// 保存ジョブのポーリングを開始する (既に開始済みの場合は何もしない)
const startSaveJobsPolling = () => {
    if (save_jobs_poll_timer !== null) {
        return;
    }
    save_jobs_poll_timer = setInterval(fetchSaveJobs, 3000);
};

// SaveDialog で保存が開始されたときに呼ばれる
const onSaved = () => {
    fetchSaveJobs();
    startSaveJobsPolling();
};

onMounted(async () => {
    await fetchRecords();
    // 他画面から開始された保存ジョブが進行中の場合に備え、初回にも取得しておく
    await fetchSaveJobs();
    if (active_save_jobs.value.length > 0) {
        startSaveJobsPolling();
    }
});

onUnmounted(() => {
    if (save_jobs_poll_timer !== null) {
        clearInterval(save_jobs_poll_timer);
        save_jobs_poll_timer = null;
    }
});

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

    &__header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-top: 16px;
        min-width: 0;
    }

    &__title {
        font-size: 22px;
        font-weight: bold;
        color: rgb(var(--v-theme-text));
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        min-width: 0;
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

    &__save-jobs {
        display: flex;
        flex-direction: column;
        gap: 6px;
        margin-top: 16px;
    }
}

.timeshift-save-job {
    display: flex;
    align-items: center;
    gap: 8px;
    padding: 8px 14px;
    border-radius: 8px;
    background: rgb(var(--v-theme-background-lighten-1));
    color: rgb(var(--v-theme-secondary-lighten-1));
    font-size: 13px;

    &__title {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    &__progress {
        flex-shrink: 0;
        margin-left: auto;
        font-weight: bold;
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

    &__save-button {
        display: flex;
        flex-shrink: 0;
        align-items: center;
        justify-content: center;
        width: 32px;
        height: 32px;
        margin-left: 12px;
        border-radius: 50%;
        color: rgb(var(--v-theme-text-darken-1));
        transition: background-color 0.15s, color 0.15s;

        &:hover {
            background: rgba(var(--v-theme-secondary-lighten-1), 0.15);
            color: rgb(var(--v-theme-secondary-lighten-1));
        }
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
