<template>
    <div class="reservation-recording-settings">
        <!-- 録画中の警告バナー -->
        <div v-if="reservation.is_recording_in_progress" class="recording-warning-banner">
            <Icon icon="fluent:warning-16-filled" class="recording-warning-banner__icon" />
            <span class="recording-warning-banner__text">
                録画中の録画設定の変更はできません。
            </span>
        </div>

        <!-- 録画予約の優先度 -->
        <div class="reservation-recording-settings__section">
            <div class="reservation-recording-settings__label">録画予約の優先度</div>
            <div class="reservation-recording-settings__description mb-0">
                放送時間が重なりチューナーが足りないときは、より優先度が高い予約から優先的に録画します。
            </div>
            <div class="reservation-recording-settings__slider">
                <v-slider
                    :disabled="reservation.is_recording_in_progress"
                    v-model="settings.priority"
                    :min="1"
                    :max="5"
                    :step="1"
                    color="primary"
                    density="compact"
                    show-ticks="always"
                    thumb-label
                    hide-details
                    @update:model-value="handleChange">
                </v-slider>
            </div>
        </div>
    </div>
</template>
<script lang="ts" setup>

import { objectHash } from 'ohash';
import { ref, computed, watch, toRaw } from 'vue';

import { type IReservation, type IRecordSettings } from '@/services/Reservations';

// Props
const props = defineProps<{
    reservation: IReservation;
    hasChanges: boolean;
}>();

// Emits
const emit = defineEmits<{
    (e: 'updateSettings', settings: IRecordSettings): void;
    (e: 'changesDetected', hasChanges: boolean): void;
}>();

// 設定のコピーを作成（元の設定を変更しないため）
const settings = ref<IRecordSettings>(structuredClone(toRaw(props.reservation.record_settings)));

// 初期設定を保存（変更検知用）
const initialSettings = ref<IRecordSettings>(structuredClone(toRaw(props.reservation.record_settings)));

// 変更があるかどうかを計算
const hasChangesComputed = computed(() => {
    return objectHash(settings.value) !== objectHash(initialSettings.value);
});

// 変更を監視
watch(hasChangesComputed, (newValue) => {
    emit('changesDetected', newValue);
});

// 保存後に initialSettings を更新
watch(() => props.hasChanges, (newValue, oldValue) => {
    if (oldValue === true && newValue === false) {
        initialSettings.value = structuredClone(toRaw(settings.value));
    }
});

// 変更時の処理
const handleChange = () => {
    emit('updateSettings', settings.value);
};

// props の予約情報が変更された時の処理
watch(() => props.reservation, (newReservation) => {
    settings.value = structuredClone(toRaw(newReservation.record_settings));
    initialSettings.value = structuredClone(toRaw(newReservation.record_settings));
}, { deep: true });

</script>
<style lang="scss" scoped>

.reservation-recording-settings {
    display: flex;
    flex-direction: column;
    gap: 16px;

    &__section {
        display: flex;
        flex-direction: column;
    }

    &__label {
        font-size: 13.5px;
        font-weight: 700;
        line-height: 1.5;
        letter-spacing: 0.04em;
        color: rgb(var(--v-theme-text));
    }

    &__description {
        font-size: 12px;
        font-weight: 500;
        line-height: 1.55;
        color: rgb(var(--v-theme-text-darken-1));
        margin-top: 6px;
        margin-bottom: 10px;
    }

    &__slider {
        margin-top: 8px;
    }
}

// 録画中の警告バナー
.recording-warning-banner {
    display: flex;
    align-items: center;
    padding: 12px 16px;
    margin-bottom: 4px;
    background-color: rgb(var(--v-theme-warning-darken-3), 0.5);
    border-radius: 6px;

    &__icon {
        color: rgb(var(--v-theme-warning));
        width: 22px;
        height: 22px;
        margin-right: 8px;
        flex-shrink: 0;
    }

    &__text {
        font-size: 13px;
        font-weight: 500;
        line-height: 1.5;
        color: rgb(var(--v-theme-warning-lighten-1));
    }
}

</style>
